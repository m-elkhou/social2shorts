from __future__ import annotations

import functools
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from insta2youtube import audio_gate, curate, enrich, match, transcode, youtube_upload
from insta2youtube.config import Settings
from insta2youtube.db import Store
from insta2youtube.discover import discover_tiktok
from insta2youtube.discover import download_reference as _download_reference
from insta2youtube.ingest import PhoneSource, ingest_originals
from insta2youtube.models import CatalogEntry, ItemStatus, Original, ShortMeta
from insta2youtube.phash import phash_file as _phash_file

AUTH_ALERT = "OAuth token refresh failed — run paused; re-authenticate."
DEFAULT_TITLE = "Moto ride"


@dataclass
class PipelineDeps:
    discover: Callable[[], list[CatalogEntry]]
    download_reference: Callable[[str, str], str]
    ingest: Callable[[PhoneSource, list[str], str], list[Original]]
    phash_file: Callable[[str, int], list[str]]
    transcribe: Callable[[str], list]
    generate_meta: Callable[[CatalogEntry, str], ShortMeta]
    upload: Callable[[str, ShortMeta, str], str]
    transcode: Callable[[str, str, str | None], str]

    @classmethod
    def default(cls, settings: Settings) -> "PipelineDeps":
        llm = functools.partial(enrich.gemini_call, model_name=settings.gemini_model)
        return cls(
            discover=lambda: discover_tiktok(settings.tiktok_profile_url, None),
            download_reference=lambda url, work_dir: _download_reference(url, work_dir),
            ingest=ingest_originals,
            phash_file=_phash_file,
            transcribe=enrich.transcribe,
            generate_meta=lambda e, t: enrich.generate_metadata(e, t, llm=llm),
            upload=lambda path, meta, privacy: youtube_upload.upload_short(path, meta, privacy),
            transcode=transcode.to_short,
        )


@dataclass
class RunReport:
    uploaded: list = field(default_factory=list)
    review: list = field(default_factory=list)
    pending: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    alert: str | None = None


def _fallback_title(caption: str) -> str:
    text = re.sub(r"[@#]\w+", " ", caption)
    text = " ".join(text.split())[:60].strip()
    return text or DEFAULT_TITLE


def run_backlog(
    settings: Settings,
    phone: PhoneSource,
    deps: PipelineDeps,
    limit: int,
    privacy: str = "unlisted",
) -> RunReport:
    store = Store(settings.db_path)
    store.init_schema()
    report = RunReport()

    entries = deps.discover()
    for e in entries:
        store.upsert_catalog(e)
    shortlist = curate.curate(entries, settings)

    originals = deps.ingest(phone, settings.phone_video_folders, settings.originals_dir)
    for o in originals:
        if not o.phashes:
            o.phashes = deps.phash_file(o.path, settings.phash_frames)

    try:
        for entry in shortlist:
            if len(report.uploaded) >= limit:
                break
            sid = entry.source_id
            fp = store.fingerprint(entry)
            if store.is_uploaded(fp):
                report.skipped.append(sid)
                continue
            if audio_gate.classify(entry) == "licensed":
                store.add_review(sid, "licensed music")
                store.set_status(sid, ItemStatus.review_audio)
                report.review.append(sid)
                continue
            try:
                ref = deps.download_reference(entry.url, settings.work_dir)
                ref_ph = deps.phash_file(ref, settings.phash_frames)
                m = match.match_one(entry, originals, ref_ph,
                                    settings.duration_tol_s, settings.phash_threshold)
                store.set_match(m)
                if m.status == ItemStatus.ambiguous:
                    store.add_review(sid, "ambiguous match")
                    store.set_status(sid, ItemStatus.ambiguous)
                    report.review.append(sid)
                    continue
                if m.status != ItemStatus.matched:
                    store.set_status(sid, ItemStatus.pending_original)
                    report.pending.append(sid)
                    continue
                segs = deps.transcribe(m.original_path)
                text = " ".join(s.text for s in segs)
                meta = deps.generate_meta(entry, text)
                if not meta.title.strip():
                    meta = meta.model_copy(update={"title": _fallback_title(entry.caption)})
                ass = enrich.build_ass(segs) if segs else None
                out = deps.transcode(m.original_path, os.path.join(settings.work_dir, f"{sid}.mp4"), ass)
                vid = deps.upload(out, meta, privacy)
                store.mark_uploaded(fp, sid, vid)
                store.set_status(sid, ItemStatus.uploaded)
                report.uploaded.append({"source_id": sid, "youtube_id": vid})
            except youtube_upload.AuthRefreshError:
                raise
            except Exception as exc:  # one bad video must not abort the batch
                store.add_review(sid, f"processing error: {type(exc).__name__}")
                report.review.append(sid)
    except youtube_upload.AuthRefreshError:
        report.alert = AUTH_ALERT
    return report
