"""Pipeline orchestration: one shared item processor, with a strategy per source kind.

Per item: dedupe -> prepare (catalog: audio gate, reference download, match | raw: inbox file)
-> transcribe -> metadata -> render -> quota/schedule -> upload -> ledger -> subtitle track
-> Content-ID guard.
"""

from __future__ import annotations

import contextlib
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

from social2shorts.ai.metadata import GeminiLLM, LLMQuotaExceeded, generate_metadata
from social2shorts.ai.refine import GeminiAudioRefiner, RefiningTranscriber
from social2shorts.ai.transcribe import WhisperTranscriber
from social2shorts.config import Settings
from social2shorts.curate import curate
from social2shorts.db import Store
from social2shorts.lock import AlreadyRunning, single_instance
from social2shorts.match import index_originals, match_one
from social2shorts.media import LocalMedia, content_hash
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
from social2shorts.montage.captions import build_srt
from social2shorts.montage.types import MontageMode, Word
from social2shorts.phash import NoVideoStream
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
from social2shorts.publish.quota import QuotaBudget, upload_limit_until
from social2shorts.publish.scheduler import ScheduleFull, next_slot
from social2shorts.publish.youtube import (
    COST_CAPTION,
    COST_LIST,
    COST_UPDATE,
    UPLOAD_LIMIT,
    AuthRefreshError,
    QuotaExceededError,
    YouTubeClient,
    upload_units,
)
from social2shorts.renderer import MontageRenderer
from social2shorts.sources.inbox import scan_inbox
from social2shorts.sources.phone import MtpPhoneSource, PhoneOriginals
from social2shorts.sources.tiktok_instagram import SocialCatalog

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
SYSTEMIC_FAILURES = 3  # same error on this many items in a row = system fault, stop the run
LOCK_NAME = "social2shorts.lock"
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
        transcriber: Transcriber = WhisperTranscriber(
            settings.whisper_model,
            settings.whisper_language or yt.language,
            vad=settings.whisper_vad,
        )
        if has_key and settings.transcript_refine_model:
            transcriber = RefiningTranscriber(
                transcriber, GeminiAudioRefiner(settings.transcript_refine_model)
            )
        return cls(
            catalog=SocialCatalog(
                src.tiktok_profile_url, src.instagram_profile_url, src.instagram_cookies
            ),
            originals=PhoneOriginals(
                lambda: MtpPhoneSource(settings.phone_name_match, settings.phone_video_folders),
                settings.phone_video_folders,
            ),
            media=LocalMedia(),
            transcriber=transcriber,
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
    not_video: list[str] = field(default_factory=list)
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
        try:
            paths = self.deps.originals.sync(Path(self.settings.originals_dir))
            return index_originals(paths, self.store, self.deps.media, self.settings.phash_frames)
        except Exception as exc:  # cached either way: never re-synced once per item
            log.error("Could not load the phone originals (%s); catalog posts wait", exc)
            return []

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


@dataclass(frozen=True)
class Source:
    """The file to render and how hard the montage engine should edit it."""

    path: Path
    mode: MontageMode


class SourceStrategy(Protocol):
    """How one source kind refreshes, orders and prepares its items."""

    kind: SourceKind

    def refresh(self, run: Run) -> None: ...

    def candidates(self, run: Run) -> list[Item]: ...

    def prepare(self, run: Run, item: Item) -> Source | None: ...


class CatalogStrategy:
    """Published posts: best engagement first, polished from the matched phone original."""

    kind = SourceKind.catalog

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

    def prepare(self, run: Run, item: Item) -> Source | None:
        """The phone original, or None when the item was routed to review/pending."""
        if item.source_path and Path(item.source_path).is_file():
            return Source(Path(item.source_path), MontageMode.polish)
        entry = item.entry
        assert entry is not None, "catalog items always have an entry"
        if not entry.is_original_audio and item.status is not ItemStatus.ready:
            _to_review(
                run, item, ItemStatus.review_audio, ReviewKind.audio,
                f"licensed music ({entry.track} - {entry.artist}). If you own the rights or it "
                f"is royalty-free: review --approve {item.key}",
            )  # fmt: skip
            return None
        try:
            phashes = _ref_phashes(run, item)
        except NoVideoStream:
            run.store.set_status(item.key, ItemStatus.not_video)
            run.report.not_video.append(item.key)
            log.info("Skipping %s: a photo slideshow, not a video", item.key)
            return None
        s = run.settings
        m = match_one(entry, run.originals, phashes, s.duration_tol_s, s.phash_threshold)
        run.store.set_match(m)
        if m.status is ItemStatus.ambiguous:
            _to_review(
                run, item, ItemStatus.ambiguous, ReviewKind.ambiguous,
                f"{len(m.candidates)} phone originals look like this post. Pick one: "
                f"review --approve {item.key} --original <path>",
            )  # fmt: skip
            return None
        if m.status is not ItemStatus.matched or m.original_path is None:
            run.store.set_status(item.key, ItemStatus.pending_original)
            run.report.pending.append(item.key)
            return None
        run.store.set_source(item.key, m.original_path)
        return Source(Path(m.original_path), MontageMode.polish)


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
        _remove(ref)
    run.store.cache_ref(item.key, phashes)
    return phashes


class RawStrategy:
    """Never-published inbox footage: long clips are re-cut into highlights, short ones polished."""

    kind = SourceKind.raw

    def refresh(self, run: Run) -> None:
        """Register new inbox files (deduped by content hash)."""
        inbox = Path(run.settings.sources.inbox_dir)
        for video in scan_inbox(inbox, run.store, now=run.deps.clock().timestamp()):
            run.store.upsert_raw(video.key, video.sha256, str(video.path))

    def candidates(self, run: Run) -> list[Item]:
        """Approved items first, then inbox order."""
        items = [i for i in run.store.items(self.kind) if _eligible(i, run.options.dry_run)]
        return sorted(items, key=lambda i: i.status is not ItemStatus.ready)

    def prepare(self, run: Run, item: Item) -> Source | None:
        """The inbox file, or None when it is too short (sent to review).

        Raises:
            FileNotFoundError: If the file was moved or deleted since it was registered.
        """
        path = Path(item.source_path or "")
        if not path.is_file():
            raise FileNotFoundError(f"{path} is no longer in the inbox")
        duration = run.deps.media.duration(path)
        minimum = run.settings.min_duration_s
        if duration < minimum and item.status is not ItemStatus.ready:
            _to_review(
                run, item, ItemStatus.too_short, ReviewKind.short,
                f"{path.name} is too short ({duration:.1f}s < {minimum:.0f}s). To render it "
                f"anyway: review --approve {item.key}; otherwise review --reject {item.key}",
            )  # fmt: skip
            return None
        short = duration <= run.settings.montage.target_duration_s
        return Source(path, MontageMode.polish if short else MontageMode.highlight)


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


def _remove(path: Path) -> None:
    """Best-effort delete (on Windows a file can stay locked by a scanner or a player)."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.info("Could not delete %s (%s); it can be removed by hand", path, exc)


def _already_uploaded(run: Run, item: Item) -> bool:
    """Cheap check before any work: this item, its raw content, or a legacy ledger row."""
    if run.store.has_upload(item.key):
        return True
    if item.kind is SourceKind.raw:
        return run.store.is_content_uploaded(item.fingerprint)
    return run.store.is_legacy_uploaded(item.fingerprint)


def _skip_duplicate(run: Run, item: Item, why: str) -> None:
    if item.status is not ItemStatus.uploaded:
        run.store.set_status(item.key, ItemStatus.duplicate)
    run.report.skipped.append(item.key)
    log.info("Skipping %s: %s", item.key, why)


def _hint(item: Item, source: Path) -> str:
    """File name or post date: lets fallback titles mention the time of day."""
    return item.entry.upload_date if item.entry else source.stem


def process_item(run: Run, strategy: SourceStrategy, item: Item) -> None:
    """Take one item as far as it can go (shared by every source kind).

    Raises before the upload API call only; once YouTube accepted the video every later
    problem is logged and recorded, never retried.
    """
    if _already_uploaded(run, item):
        _skip_duplicate(run, item, "already on YouTube")
        return
    source = strategy.prepare(run, item)
    if source is None:
        return
    sha = content_hash(source.path, run.store)
    if run.store.is_content_uploaded(sha):
        _skip_duplicate(run, item, f"{source.path.name} was already uploaded for another post")
        return
    words = run.deps.transcriber.transcribe(source.path)
    meta = generate_metadata(
        run.deps.llm,
        item.caption,
        " ".join(w.text for w in words),
        key=item.key,
        language=run.settings.youtube.language,
        hint=_hint(item, source.path),
    )
    rendered = run.deps.renderer.render(
        source.path,
        run.work_dir / "renders" / f"{item.key}.mp4",
        words=words,
        settings=run.settings.montage.model_copy(update={"mode": source.mode}),
        hook=meta.hook or None,
    )
    out = rendered.path
    srt = _save_subtitles(run, item, rendered.words)
    if run.options.dry_run:
        run.store.set_status(item.key, ItemStatus.rendered)
        run.report.rendered.append(item.key)
        log.info("Rendered %s -> %s (%r)", item.key, out, meta.title)
        return
    upload = _upload(run, item, out, meta, sha)
    _add_captions(run, item, upload, srt)
    _guard(run, [upload])
    _remove(out)


def _subtitles_path(run: Run, item: Item) -> Path:
    return run.work_dir / "subtitles" / f"{item.key}.srt"


def _save_subtitles(run: Run, item: Item, words: list[Word]) -> str | None:
    """Write the Short's subtitles (SRT) under ``work/subtitles``; kept for review and re-upload.

    Returns the SRT text, or None when there is no speech.
    """
    srt = build_srt(words)
    path = _subtitles_path(run, item)
    if not srt:
        path.unlink(missing_ok=True)  # an earlier attempt's file is not this video's subtitles
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(srt, encoding="utf-8")
    return srt


def _add_captions(run: Run, item: Item, upload: Upload, srt: str | None) -> None:
    """Publish the subtitle track. Never raises: the video is already on YouTube."""
    yt = run.settings.youtube
    if not (yt.upload_captions and srt):
        return
    language = yt.caption_language or run.settings.whisper_language or yt.language
    if not language:  # a setup choice, not a failure: no alert
        log.info("No subtitle track for %s: set [youtube] caption_language", item.key)
        return
    why = None
    if not run.budget.can_afford(COST_CAPTION):
        why = "not enough YouTube quota left today"
    else:
        try:
            run.deps.platform.add_captions(
                upload.youtube_id, srt, language=language, name=yt.caption_name
            )
            run.budget.spend(COST_CAPTION)
            log.info("Added %s subtitles to https://youtu.be/%s", language, upload.youtube_id)
            return
        except Exception as exc:  # the upload is recorded: report, never retry the item
            if isinstance(exc, QuotaExceededError):
                run.budget.exhaust()
            elif not isinstance(exc, AuthRefreshError):
                run.budget.spend(COST_CAPTION)
            why = _signature(exc)
    message = (
        f"Subtitles of {item.key} (https://youtu.be/{upload.youtube_id}) were not added ({why}). "
        f"Upload {_subtitles_path(run, item)} in YouTube Studio > Subtitles."
    )
    log.warning(message)
    with contextlib.suppress(Exception):
        run.store.add_alert(message)


def _publish_target(run: Run) -> tuple[str, datetime | None]:
    if run.options.privacy is not Privacy.scheduled:
        return run.options.privacy.value, None
    return "private", next_slot(run.deps.clock(), run.store.publish_times(), run.settings.schedule)


def _upload(run: Run, item: Item, video: Path, meta: ShortMeta, sha: str) -> Upload:
    """Upload, then record it immediately. Only failures of the API call itself propagate."""
    privacy, publish_at = _publish_target(run)
    cost = run.settings.youtube.upload_cost_units
    run.store.set_status(item.key, ItemStatus.uploading)
    try:
        result = run.deps.platform.upload(video, meta, privacy=privacy, publish_at=publish_at)
    except Exception as exc:  # the API said no (Ctrl-C keeps 'uploading': a human checks)
        run.store.set_status(item.key, item.status)
        if isinstance(exc, QuotaExceededError) and UPLOAD_LIMIT in exc.reasons:
            run.store.record_upload_limit(run.deps.clock())  # the API quota itself is fine
        elif isinstance(exc, QuotaExceededError):
            run.budget.exhaust()
        elif not isinstance(exc, AuthRefreshError):
            run.budget.spend(cost, uploads=1)  # one conservative charge per failed insert
        raise
    upload = Upload(
        youtube_id=result.video_id, item_key=item.key, fingerprint=item.fingerprint,
        content_sha256=sha, privacy=result.privacy, publish_at=result.publish_at,
        uploaded_at=run.deps.clock(), quota_units=cost,
    )  # fmt: skip
    _record(run, item, upload)
    if (result.privacy, result.publish_at) != (privacy, publish_at):
        asked = f"{privacy} at {publish_at:%Y-%m-%d %H:%M} UTC" if publish_at else privacy
        log.warning(
            "YouTube set %s to %r (asked %s). Unaudited API projects are locked to private: "
            "request an API audit, or publish it by hand in YouTube Studio.",
            item.key, result.privacy, asked,
        )  # fmt: skip
    log.info("Uploaded %s as https://youtu.be/%s (%s)", item.key, result.video_id, result.privacy)
    return upload


def _record(run: Run, item: Item, upload: Upload) -> None:
    """Write the ledger; if the DB fails, shout (the item stays 'uploading', never retried)."""
    run.report.uploaded.append(upload)
    try:
        run.store.record_upload(upload)
        run.store.set_status(item.key, ItemStatus.uploaded)
        run.store.resolve_review(item.key)
        run.budget.spend(upload.quota_units, uploads=1)
    except Exception as exc:
        message = (
            f"{item.key} WAS UPLOADED as https://youtu.be/{upload.youtube_id} but could not be "
            f"recorded ({exc}). It will not be retried; fix the database, then check it in "
            "YouTube Studio."
        )
        log.critical(message)
        with contextlib.suppress(Exception):
            run.store.add_alert(message)


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
        f"`publish {upload.item_key} --privacy public --include-claimed`, otherwise "
        f"`review --reject {upload.item_key}`.",
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


def _recover_interrupted(run: Run) -> None:
    """Items left 'uploading' by a crashed run may be on YouTube: a human must check."""
    for item in run.store.items():
        if item.status is ItemStatus.uploading:
            _to_review(
                run, item, ItemStatus.failed, ReviewKind.interrupted,
                "a previous run stopped while uploading this video. Look for it in YouTube "
                f"Studio: if it is there, `review --reject {item.key}`; if not, "
                f"`review --approve {item.key}`.",
            )  # fmt: skip
            run.store.set_attempts(item.key, MAX_ATTEMPTS)  # no automatic retry


def _fail(run: Run, item: Item, exc: Exception) -> None:
    """Count a failed attempt; after MAX_ATTEMPTS the item goes to review."""
    attempts = run.store.add_attempt(item.key)
    reason = _signature(exc)
    log.warning("%s failed (attempt %d/%d): %s", item.key, attempts, MAX_ATTEMPTS, reason)
    if attempts >= MAX_ATTEMPTS:
        _to_review(
            run, item, ItemStatus.failed, ReviewKind.error,
            f"gave up after {attempts} attempts: {reason}. Fix the cause, then "
            f"`review --approve {item.key}` to retry.",
        )  # fmt: skip
        return
    keep = ItemStatus.ready if item.status is ItemStatus.ready else ItemStatus.failed
    run.store.set_status(item.key, keep)
    run.report.failed.append(item.key)


def _signature(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


@dataclass
class FailureStreak:
    """Consecutive identical failures. Penalties are deferred: if the same error hits
    ``SYSTEMIC_FAILURES`` items in a row it is a system fault (no network, model download
    failed...) and no item is blamed."""

    run: Run
    failures: list[tuple[Item, Exception]] = field(default_factory=list)

    def add(self, item: Item, exc: Exception) -> bool:
        """Record a failure; True when the run should stop (systemic fault)."""
        if self.failures and _signature(self.failures[-1][1]) != _signature(exc):
            self.settle()
        self.failures.append((item, exc))
        return len(self.failures) >= SYSTEMIC_FAILURES

    def settle(self) -> None:
        """Charge the pending failures to their items (they were item-specific)."""
        for item, exc in self.failures:
            _fail(self.run, item, exc)
        self.failures.clear()


def _queue(run: Run, kinds: list[SourceKind]) -> Iterator[tuple[SourceStrategy, Item]]:
    """Refresh each source, then interleave their candidates (round robin)."""
    queues = []
    for kind in kinds:
        strategy = STRATEGIES[kind]
        strategy.refresh(run)
        queues.append([(strategy, item) for item in strategy.candidates(run)])
    for group in zip_longest(*queues):
        yield from (pair for pair in group if pair is not None)


def _quota_stop(run: Run) -> str | None:
    if run.options.dry_run:
        return None
    until = upload_limit_until(run.store)
    if until is not None and run.deps.clock() < until:
        return (
            f"the channel's upload limit ({UPLOAD_LIMIT}) still holds: the next upload fits "
            f"after {until:%Y-%m-%d %H:%M} UTC, when the last uploads are 24 hours old"
        )
    budget, yt = run.budget, run.settings.youtube
    cost = upload_units(yt.upload_cost_units, yt.upload_captions)
    if budget.uploads_left(cost) > 0:
        return None
    return (
        f"daily YouTube quota spent ({budget.used()}/{budget.daily_units} units, "
        f"{budget.uploads()}/{budget.max_uploads} uploads); the next run after midnight "
        "Pacific time continues"
    )


def _process_queue(run: Run, kinds: list[SourceKind]) -> None:
    report, streak = run.report, FailureStreak(run)
    for strategy, item in _queue(run, kinds):
        if run.options.limit is not None and run.done() >= run.options.limit:
            break
        if stop := _quota_stop(run):
            report.stop_reason = stop
            break
        try:
            process_item(run, strategy, item)
        except (AuthRefreshError, QuotaExceededError, LLMQuotaExceeded, ScheduleFull):
            streak.settle()
            raise
        except Exception as exc:
            if streak.add(item, exc):
                report.stop_reason = (
                    f"{SYSTEMIC_FAILURES} videos in a row failed with the same error "
                    f"({_signature(exc)}). This looks like a system problem, not the videos: "
                    "fix it and run again (no video was penalized)"
                )
                run.store.add_alert(report.stop_reason)
                log.error(report.stop_reason)
                return
            continue
        streak.settle()
    streak.settle()


def run_pipeline(settings: Settings, deps: PipelineDeps, options: RunOptions) -> RunReport:
    """Process items until the limit, the daily quota or the queue runs out.

    Never raises for per-item problems; an OAuth failure stops the run and records an alert.
    Only one run at a time: a second one stops immediately with a ``stop_reason``.
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
        with single_instance(Path(settings.work_dir) / LOCK_NAME):
            _recover_interrupted(run)
            if not options.dry_run:
                _sweep_recent(run)
            _process_queue(run, kinds)
    except AlreadyRunning as exc:
        report.stop_reason = f"{exc}; this run did nothing"
    except AuthRefreshError as exc:
        report.alert = AUTH_ALERT.format(exc)
        store.add_alert(report.alert)
        log.error(report.alert)
    except QuotaExceededError as exc:
        report.stop_reason = (
            f"YouTube refused the upload: the channel's upload limit is reached ({UPLOAD_LIMIT}; "
            "the API quota is not spent); runs wait until the last uploads are 24 hours old"
            if UPLOAD_LIMIT in exc.reasons
            else "YouTube reports today's API quota is spent; continuing tomorrow"
        )
    except LLMQuotaExceeded as exc:
        report.stop_reason = f"{exc}; continuing when it resets (midnight Pacific time)"
    except ScheduleFull as exc:
        report.stop_reason = f"publication schedule is full ({exc})"
    return report
