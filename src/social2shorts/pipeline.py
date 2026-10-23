"""Pipeline orchestration: one shared item processor, with a strategy per source kind.

Per item: dedupe -> prepare (catalog: audio gate, reference download, match | raw: inbox file)
-> transcribe -> metadata -> render -> quota/schedule -> upload -> ledger -> Content-ID guard.
"""

from __future__ import annotations

import functools
import logging
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from itertools import zip_longest
from pathlib import Path
from typing import Protocol

from social2shorts.ai.metadata import GeminiLLM, generate_metadata
from social2shorts.ai.transcribe import WhisperTranscriber
from social2shorts.config import Settings
from social2shorts.curate import curate
from social2shorts.db import Store
from social2shorts.match import index_originals, match_one
from social2shorts.media import LocalMedia
from social2shorts.models import (
    Item,
    ItemStatus,
    Original,
    Privacy,
    ReviewKind,
    ShortMeta,
    SourceKind,
    Upload,
)
from social2shorts.montage.types import MontageMode
from social2shorts.ports import (
    LLM,
    CatalogSource,
    MediaProbe,
    OriginalsSource,
    Renderer,
    Transcriber,
    VideoPlatform,
)
from social2shorts.publish.contentid import BATCH
from social2shorts.publish.quota import QuotaBudget
from social2shorts.publish.scheduler import ScheduleFull, next_slot
from social2shorts.publish.youtube import (
    COST_LIST,
    COST_UPDATE,
    AuthRefreshError,
    QuotaExceededError,
    YouTubeClient,
)
from social2shorts.renderer import MontageRenderer
from social2shorts.sources.inbox import scan_inbox
from social2shorts.sources.phone import MtpPhoneSource, PhoneOriginals
from social2shorts.sources.tiktok_instagram import SocialCatalog

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
GUARD_LOOKBACK = timedelta(days=3)
AUTH_ALERT = (
    "YouTube authorization failed ({}). Uploads are paused: run `uv run social2shorts auth` "
    "on the PC, then the next run resumes where it stopped."
)


def utcnow() -> datetime:
    """Current time, timezone-aware (UTC)."""
    return datetime.now(UTC)


class SourceChoice(StrEnum):
    """Which source kinds a run processes."""

    catalog = "catalog"
    raw = "raw"
    all = "all"


@dataclass
class PipelineDeps:
    """External systems the pipeline talks to (ports), swappable in tests."""

    catalog: CatalogSource
    originals: OriginalsSource
    media: MediaProbe
    transcriber: Transcriber
    llm: LLM | None
    renderer: Renderer
    platform: VideoPlatform
    clock: Callable[[], datetime] = utcnow

    @classmethod
    def default(cls, settings: Settings) -> PipelineDeps:
        """Production adapters built from settings (heavy clients are created on first use)."""
        src, yt = settings.sources, settings.youtube
        has_key = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
        if not has_key:
            log.warning("GEMINI_API_KEY is not set: titles will come from captions/templates")
        return cls(
            catalog=SocialCatalog(
                src.tiktok_profile_url, src.instagram_profile_url, src.instagram_cookies
            ),
            originals=PhoneOriginals(
                lambda: MtpPhoneSource(settings.phone_name_match, settings.phone_video_folders),
                settings.phone_video_folders,
            ),
            media=LocalMedia(),
            transcriber=WhisperTranscriber(settings.whisper_model, yt.language),
            llm=GeminiLLM(settings.gemini_model) if has_key else None,
            renderer=MontageRenderer(),
            platform=YouTubeClient(
                yt.client_secrets,
                yt.token_path,
                category_id=yt.category_id,
                language=yt.language,
                made_for_kids=yt.made_for_kids,
                synthetic_media=yt.contains_synthetic_media,
            ),  # fmt: skip
        )


@dataclass
class RunOptions:
    """What one run should do.

    Attributes:
        source: Source kinds to process (``all`` interleaves catalog and raw items).
        limit: Max uploads (renders when ``dry_run``); None = until the daily quota is spent.
        privacy: Upload visibility; ``scheduled`` uses the next free publish slot.
        dry_run: Render only, never touch YouTube.
    """

    source: SourceChoice = SourceChoice.all
    limit: int | None = 3
    privacy: Privacy = Privacy.unlisted
    dry_run: bool = False


@dataclass
class RunReport:
    """What happened during a run (item keys per bucket)."""

    uploaded: list[Upload] = field(default_factory=list)
    rendered: list[str] = field(default_factory=list)
    review: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    claimed: list[str] = field(default_factory=list)
    alert: str | None = None
    stop_reason: str | None = None


@dataclass
class Run:
    """State shared by the steps of one run."""

    settings: Settings
    deps: PipelineDeps
    options: RunOptions
    store: Store
    budget: QuotaBudget
    report: RunReport = field(default_factory=RunReport)

    @property
    def work_dir(self) -> Path:
        """Scratch folder for references and renders."""
        return Path(self.settings.work_dir)

    @functools.cached_property
    def originals(self) -> list[Original]:
        """Phone originals (synced and indexed once per run, only if a match is needed)."""
        paths = self.deps.originals.sync(Path(self.settings.originals_dir))
        return index_originals(paths, self.store, self.deps.media, self.settings.phash_frames)

    def done(self) -> int:
        """Items counted against ``limit``."""
        return len(self.report.rendered if self.options.dry_run else self.report.uploaded)


# ------------------------------------------------------------------ strategies
def _eligible(item: Item, dry_run: bool) -> bool:
    if item.status is ItemStatus.failed:
        return item.attempts < MAX_ATTEMPTS
    open_statuses = {ItemStatus.discovered, ItemStatus.pending_original, ItemStatus.matched,
                     ItemStatus.ready}  # fmt: skip
    return item.status in open_statuses or (item.status is ItemStatus.rendered and not dry_run)


class SourceStrategy(Protocol):
    """How one source kind refreshes, orders and prepares its items."""

    kind: SourceKind
    montage_mode: MontageMode

    def refresh(self, run: Run) -> None: ...

    def candidates(self, run: Run) -> list[Item]: ...

    def prepare(self, run: Run, item: Item) -> Path | None: ...


class CatalogStrategy:
    """Published posts: best engagement first, rendered from the matched phone original."""

    kind = SourceKind.catalog
    montage_mode = MontageMode.polish

    def refresh(self, run: Run) -> None:
        """Discover posts on the configured profiles and upsert them."""
        for entry in run.deps.catalog.discover():
            run.store.upsert_catalog(entry)

    def candidates(self, run: Run) -> list[Item]:
        """Approved items first, then the curated shortlist (deduped across platforms)."""
        items = {i.key: i for i in run.store.items(self.kind) if _eligible(i, run.options.dry_run)}
        ready = [i for i in items.values() if i.status is ItemStatus.ready]
        s = run.settings
        entries = [i.entry for i in items.values() if i.entry and i.status is not ItemStatus.ready]
        shortlist = curate(entries, s.min_duration_s, s.sources.prefer_platform)
        return ready + [items[e.source_id] for e in shortlist]

    def prepare(self, run: Run, item: Item) -> Path | None:
        """The phone original to render, or None when the item was routed to review/pending."""
        if item.source_path and Path(item.source_path).is_file():
            return Path(item.source_path)
        entry = item.entry
        assert entry is not None, "catalog items always have an entry"
        if not entry.is_original_audio and item.status is not ItemStatus.ready:
            reason = (
                f"licensed music ({entry.track} - {entry.artist}). If you own the rights or it "
                f"is royalty-free: review --approve {item.key}"
            )
            return _to_review(run, item, ItemStatus.review_audio, ReviewKind.audio, reason)
        s = run.settings
        m = match_one(entry, run.originals, _ref_phashes(run, item), s.duration_tol_s,
                      s.phash_threshold)  # fmt: skip
        run.store.set_match(m)
        if m.status is ItemStatus.ambiguous:
            reason = (
                f"{len(m.candidates)} phone originals look like this post. Pick one: "
                f"review --approve {item.key} --original <path>"
            )
            return _to_review(run, item, ItemStatus.ambiguous, ReviewKind.ambiguous, reason)
        if m.status is not ItemStatus.matched or m.original_path is None:
            run.store.set_status(item.key, ItemStatus.pending_original)
            run.report.pending.append(item.key)
            return None
        run.store.set_source(item.key, m.original_path)
        return Path(m.original_path)


def _ref_phashes(run: Run, item: Item) -> list[str]:
    """pHashes of the post's 540p reference (downloaded once, then cached in the DB)."""
    cached = run.store.cached_ref(item.key)
    if cached:
        return cached
    assert item.entry is not None
    ref = run.deps.catalog.download_reference(item.entry.url, run.work_dir / "refs")
    try:
        phashes = run.deps.media.phashes(ref, run.settings.phash_frames)
    finally:
        ref.unlink(missing_ok=True)
    run.store.cache_ref(item.key, phashes)
    return phashes


class RawStrategy:
    """Never-published inbox footage: re-cut into highlights by the montage engine."""

    kind = SourceKind.raw
    montage_mode = MontageMode.highlight

    def refresh(self, run: Run) -> None:
        """Register new inbox files (deduped by content hash)."""
        for video in scan_inbox(Path(run.settings.sources.inbox_dir), run.store):
            run.store.upsert_raw(video.key, video.sha256, str(video.path))

    def candidates(self, run: Run) -> list[Item]:
        """Approved items first, then inbox order."""
        items = [i for i in run.store.items(self.kind) if _eligible(i, run.options.dry_run)]
        return sorted(items, key=lambda i: i.status is not ItemStatus.ready)

    def prepare(self, run: Run, item: Item) -> Path | None:
        """The inbox file itself.

        Raises:
            FileNotFoundError: If the file was moved or deleted since it was registered.
        """
        path = Path(item.source_path or "")
        if not path.is_file():
            raise FileNotFoundError(f"{path} is no longer in the inbox")
        return path


STRATEGIES: dict[SourceKind, SourceStrategy] = {
    SourceKind.catalog: CatalogStrategy(),
    SourceKind.raw: RawStrategy(),
}


# ------------------------------------------------------------------- the steps
def _to_review(run: Run, item: Item, status: ItemStatus, kind: ReviewKind, reason: str) -> None:
    run.store.set_status(item.key, status)
    run.store.add_review(item.key, kind, reason)
    run.report.review.append(item.key)
    log.info("%s needs review: %s", item.key, reason)
    return None


def process_item(run: Run, strategy: SourceStrategy, item: Item) -> None:
    """Take one item as far as it can go (shared by every source kind)."""
    if run.store.is_uploaded(item.fingerprint):
        if item.status is not ItemStatus.uploaded:
            run.store.set_status(item.key, ItemStatus.duplicate)
        run.report.skipped.append(item.key)
        return
    source = strategy.prepare(run, item)
    if source is None:
        return
    words = run.deps.transcriber.transcribe(source)
    meta = generate_metadata(
        run.deps.llm,
        item.caption,
        " ".join(w.text for w in words),
        key=item.key,
        language=run.settings.youtube.language,
    )
    montage = run.settings.montage.model_copy(update={"mode": strategy.montage_mode})
    out = run.deps.renderer.render(
        source,
        run.work_dir / "renders" / f"{item.key}.mp4",
        words=words,
        settings=montage,
        hook=meta.hook or None,
    )
    if run.options.dry_run:
        run.store.set_status(item.key, ItemStatus.rendered)
        run.report.rendered.append(item.key)
        log.info("Rendered %s -> %s (%r)", item.key, out, meta.title)
        return
    upload = _upload(run, item, out, meta)
    out.unlink(missing_ok=True)
    _guard(run, [upload])


def _upload(run: Run, item: Item, video: Path, meta: ShortMeta) -> Upload:
    """Upload, then write the ledger immediately so a later failure never causes a re-upload."""
    privacy, publish_at = run.options.privacy.value, None
    if run.options.privacy is Privacy.scheduled:
        now = run.deps.clock()
        privacy, publish_at = (
            "private",
            next_slot(now, run.store.publish_times(), run.settings.schedule),
        )
    cost = run.settings.youtube.upload_cost_units
    try:
        result = run.deps.platform.upload(video, meta, privacy=privacy, publish_at=publish_at)
    except QuotaExceededError:
        run.budget.exhaust()
        raise
    except AuthRefreshError:
        raise
    except Exception:
        run.budget.spend(cost, uploads=1)  # a failed insert may still be charged: be conservative
        raise
    upload = Upload(
        youtube_id=result.video_id, item_key=item.key, fingerprint=item.fingerprint,
        privacy=result.privacy, publish_at=result.publish_at, uploaded_at=run.deps.clock(),
        quota_units=cost,
    )  # fmt: skip
    run.store.record_upload(upload)
    run.budget.spend(cost, uploads=1)
    if (result.privacy, result.publish_at) != (privacy, publish_at):
        asked = f"{privacy} at {publish_at:%Y-%m-%d %H:%M} UTC" if publish_at else privacy
        log.warning(
            "YouTube set %s to %r (asked %s). Unaudited API projects are locked to private: "
            "request an API audit, or publish it by hand in YouTube Studio.",
            item.key, result.privacy, asked,
        )  # fmt: skip
    run.store.set_status(item.key, ItemStatus.uploaded)
    run.store.resolve_review(item.key)
    run.report.uploaded.append(upload)
    log.info("Uploaded %s as https://youtu.be/%s (%s)", item.key, result.video_id, result.privacy)
    return upload


def _guard(run: Run, uploads: list[Upload]) -> None:
    """Content-ID guard: make flagged videos private and queue them for review.

    Guard errors (other than auth) are logged, never fatal: the upload is already recorded.
    """
    calls = -(-len(uploads) // BATCH)
    if not uploads or not run.budget.can_afford(calls * COST_LIST):
        return
    try:
        claims = run.deps.platform.check_claims([u.youtube_id for u in uploads])
        run.budget.spend(calls * COST_LIST)
        by_video = {u.youtube_id: u for u in uploads}
        reasons: dict[str, list[str]] = {}
        for claim in claims:
            reasons.setdefault(claim.video_id, []).append(claim.reason)
        for video_id, why in reasons.items():
            _privatize(run, by_video[video_id], "; ".join(why))
    except AuthRefreshError:
        raise
    except Exception as exc:
        log.warning("Content-ID check failed (%s); it will be retried next run", exc)


def _privatize(run: Run, upload: Upload, reason: str) -> None:
    run.deps.platform.set_privacy(upload.youtube_id, "private")
    run.budget.spend(COST_UPDATE)
    run.store.set_upload_privacy(upload.youtube_id, "private")
    run.store.set_status(upload.item_key, ItemStatus.claimed)
    run.store.add_review(
        upload.item_key,
        ReviewKind.claim,
        f"made private: {reason}. Check YouTube Studio; if it is fine run "
        f"`publish {upload.item_key}`, otherwise `review --reject {upload.item_key}`.",
    )
    run.report.claimed.append(upload.item_key)
    log.warning("%s (%s) flagged and made private: %s", upload.item_key, upload.youtube_id, reason)


def _sweep_recent(run: Run) -> None:
    """Re-check recent public/unlisted/scheduled uploads (claims show up after a delay)."""
    since = run.deps.clock() - GUARD_LOOKBACK
    claimed = {i.key for i in run.store.items() if i.status is ItemStatus.claimed}
    recent = [
        u for u in run.store.uploads(since)
        if u.item_key not in claimed and (u.privacy != "private" or u.publish_at)
    ]  # fmt: skip
    _guard(run, recent)


def _fail(run: Run, item: Item, exc: Exception) -> None:
    """Count a failed attempt; after MAX_ATTEMPTS the item goes to review."""
    attempts = run.store.add_attempt(item.key)
    reason = f"{type(exc).__name__}: {exc}"
    log.warning("%s failed (attempt %d/%d): %s", item.key, attempts, MAX_ATTEMPTS, reason)
    if attempts >= MAX_ATTEMPTS:
        run.store.set_status(item.key, ItemStatus.failed)
        run.store.add_review(
            item.key,
            ReviewKind.error,
            f"gave up after {attempts} attempts: {reason}. Fix the cause, then "
            f"`review --approve {item.key}` to retry.",
        )
        run.report.review.append(item.key)
        return
    keep = ItemStatus.ready if item.status is ItemStatus.ready else ItemStatus.failed
    run.store.set_status(item.key, keep)
    run.report.failed.append(item.key)


def _queue(run: Run, kinds: list[SourceKind]) -> Iterator[tuple[SourceStrategy, Item]]:
    """Refresh each source, then interleave their candidates (round robin)."""
    queues = []
    for kind in kinds:
        strategy = STRATEGIES[kind]
        strategy.refresh(run)
        queues.append([(strategy, item) for item in strategy.candidates(run)])
    for group in zip_longest(*queues):
        yield from (pair for pair in group if pair is not None)


def run_pipeline(settings: Settings, deps: PipelineDeps, options: RunOptions) -> RunReport:
    """Process items until the limit, the daily quota or the queue runs out.

    Never raises for per-item problems; an OAuth failure stops the run and records an alert.
    """
    Path(settings.work_dir).mkdir(parents=True, exist_ok=True)
    store = Store(settings.db_path)
    store.init_schema()
    yt = settings.youtube
    budget = QuotaBudget(store, yt.daily_quota_units, deps.clock, yt.max_uploads_per_day)
    run = Run(settings, deps, options, store, budget)
    report = run.report
    kinds = [k for k in SourceKind if options.source in (SourceChoice.all, k.value)]
    try:
        if not options.dry_run:
            _sweep_recent(run)
        for strategy, item in _queue(run, kinds):
            if options.limit is not None and run.done() >= options.limit:
                break
            if not options.dry_run and budget.uploads_left(yt.upload_cost_units) <= 0:
                report.stop_reason = (
                    f"daily YouTube quota spent ({budget.used()}/{budget.daily_units} units, "
                    f"{budget.uploads()}/{budget.max_uploads} uploads); the next run after "
                    "midnight Pacific time continues"
                )
                break
            try:
                process_item(run, strategy, item)
            except (AuthRefreshError, QuotaExceededError, ScheduleFull):
                raise
            except Exception as exc:
                _fail(run, item, exc)
    except AuthRefreshError as exc:
        report.alert = AUTH_ALERT.format(exc)
        store.add_alert(report.alert)
        log.error(report.alert)
    except QuotaExceededError:
        report.stop_reason = "YouTube reports today's quota is spent; continuing tomorrow"
    except ScheduleFull as exc:
        report.stop_reason = f"publication schedule is full ({exc})"
    return report
