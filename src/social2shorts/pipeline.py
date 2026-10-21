"""
Pipeline orchestration module.

This module coordinates the end-to-end flow: discovery, ingestion,
matching, enrichment, transcoding, and uploading of videos.
"""
from __future__ import annotations

import functools
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from social2shorts import audio_gate, contentid, curate, enrich, match, transcode, youtube_upload
from social2shorts.config import Settings
from social2shorts.db import Store
from social2shorts.discover import discover_tiktok
from social2shorts.discover import download_reference as _download_reference
from social2shorts.ingest import PhoneSource, ingest_originals
from social2shorts.models import CatalogEntry, ItemStatus, Original, ShortMeta
from social2shorts.phash import phash_file as _phash_file

AUTH_ALERT = "OAuth token refresh failed — run paused; re-authenticate."
DEFAULT_TITLE = "Moto ride"


@dataclass
class PipelineDeps:
    """
    Dependency container for the pipeline.
    """
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


def run_continuous(
    settings: Settings,
    phone: PhoneSource,
    deps: PipelineDeps,
    limit: int,
    privacy: str = "unlisted",
) -> RunReport:
    store = Store(settings.db_path)
    store.init_schema()
    all_entries = deps.discover()
    new_entries = []
    for e in all_entries:
        fp = store.fingerprint(e)
        if not store.is_uploaded(fp):
            new_entries.append(e)
    old_discover = deps.discover
    deps.discover = lambda: new_entries
    try:
        return run_backlog(settings, phone, deps, limit, privacy)
    finally:
        deps.discover = old_discover


def run_backlog(
    settings: Settings,
    phone: PhoneSource,
    deps: PipelineDeps,
    limit: int,
    privacy: str = "unlisted",
) -> RunReport:
    os.makedirs(settings.work_dir, exist_ok=True)
    os.makedirs(settings.originals_dir, exist_ok=True)
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
            vid = None
            try:
                fp = store.fingerprint(entry)
                if store.is_uploaded(fp):
                    report.skipped.append(sid)
                    continue
                if audio_gate.classify(entry) == "licensed":
                    store.add_review(sid, "licensed music")
                    store.set_status(sid, ItemStatus.review_audio)
                    report.review.append(sid)
                    continue
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
                try:
                    out = deps.transcode(m.original_path, os.path.join(settings.work_dir, f"{sid}.mp4"), ass)
                finally:
                    if ass:
                        try:
                            os.unlink(ass)
                        except OSError:
                            pass
                vid = deps.upload(out, meta, privacy)

                service = youtube_upload.build_service()
                contentid.guard_after_upload(
                    vid,
                    on_claim=lambda reason: (
                        store.add_review(sid, f"Content-ID claim: {reason}"),
                        store.set_status(sid, ItemStatus.review_audio),
                        report.review.append(sid)
                    ),
                    service=service
                )

                store.mark_uploaded(fp, sid, vid)
                store.set_status(sid, ItemStatus.uploaded)
                report.uploaded.append({"source_id": sid, "youtube_id": vid})
            except youtube_upload.AuthRefreshError:
                raise
            except Exception as exc:
                reason = f"processing error: {type(exc).__name__}: {exc}"
                if vid:
                    reason += f" (uploaded as {vid}; not in ledger)"
                try:
                    store.add_review(sid, reason)
                except Exception:
                    pass
                report.review.append(sid)
    except youtube_upload.AuthRefreshError:
        report.alert = AUTH_ALERT
    return report


def execute_top_pipeline(
    settings: Settings,
    phone: PhoneSource,
    deps: PipelineDeps,
    limit: int,
    privacy: str = 'unlisted',
) -> RunReport:
    store = Store(settings.db_path)
    store.init_schema()
    report = RunReport()

    all_entries = deps.discover()
    for e in all_entries:
        store.upsert_catalog(e)

    import social2shorts.curate as curate
    shortlist = curate.curate(all_entries, settings)[:limit]

    originals = deps.ingest(phone, settings.phone_video_folders, settings.originals_dir)
    for o in originals:
        if not o.phashes:
            o.phashes = deps.phash_file(o.path, settings.phash_frames)

    try:
        for entry in shortlist:
            if len(report.uploaded) >= limit:
                break
            sid = entry.source_id
            vid = None
            try:
                fp = store.fingerprint(entry)
                if store.is_uploaded(fp):
                    report.skipped.append(sid)
                    continue
                import social2shorts.audio_gate as audio_gate
                if audio_gate.classify(entry) == 'licensed':
                    store.add_review(sid, 'licensed music')
                    store.set_status(sid, ItemStatus.review_audio)
                    report.review.append(sid)
                    continue
                ref = deps.download_reference(entry.url, settings.work_dir)
                ref_ph = deps.phash_file(ref, settings.phash_frames)
                import social2shorts.match as match
                m = match.match_one(entry, originals, ref_ph,
                                    settings.duration_tol_s, settings.phash_threshold)
                store.set_match(m)
                if m.status == ItemStatus.ambiguous:
                    store.add_review(sid, 'ambiguous match')
                    store.set_status(sid, ItemStatus.ambiguous)
                    report.review.append(sid)
                    continue
                if m.status != ItemStatus.matched:
                    store.set_status(sid, ItemStatus.pending_original)
                    report.pending.append(sid)
                    continue
                segs = deps.transcribe(m.original_path)
                text = ' '.join(s.text for s in segs)
                meta = deps.generate_meta(entry, text)
                if not meta.title.strip():
                    meta = meta.model_copy(update={'title': _fallback_title(entry.caption)})
                import social2shorts.enrich as enrich
                ass = enrich.build_ass(segs) if segs else None
                try:
                    out = deps.transcode(m.original_path, os.path.join(settings.work_dir, f'{sid}.mp4'), ass)
                finally:
                    if ass:
                        try:
                            os.unlink(ass)
                        except OSError:
                            pass
                vid = deps.upload(out, meta, privacy)

                import social2shorts.youtube_upload as youtube_upload
                service = youtube_upload.build_service()
                import social2shorts.contentid as contentid
                contentid.guard_after_upload(
                    vid,
                    on_claim=lambda reason: (
                        store.add_review(sid, f'Content-ID claim: {reason}'),
                        store.set_status(sid, ItemStatus.review_audio),
                        report.review.append(sid)
                    ),
                    service=service
                )

                store.mark_uploaded(fp, sid, vid)
                store.set_status(sid, ItemStatus.uploaded)
                report.uploaded.append({'source_id': sid, 'youtube_id': vid})
            except Exception as exc:
                if 'AuthRefreshError' in str(type(exc)):
                    raise
                reason = f'processing error: {type(exc).__name__}: {exc}'
                if vid:
                    reason += f' (uploaded as {vid}; not in ledger)'
                try:
                    store.add_review(sid, reason)
                except Exception:
                    pass
                report.review.append(sid)
    except Exception as exc:
        if 'AuthRefreshError' in str(type(exc)):
            report.alert = 'OAuth token refresh failed — run paused; re-authenticate.'
        else:
            raise exc
    return report
