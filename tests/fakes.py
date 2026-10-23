"""Test doubles (one per port) and factories shared by the test suite."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from social2shorts.models import CatalogEntry, Claim, Platform, ShortMeta, UploadResult
from social2shorts.montage.types import MontageSettings, Word

NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)


def entry(sid: str, dur: float = 20.0, track: str = "original sound", **kw) -> CatalogEntry:
    """A catalog post; ``caption`` defaults to something unique per id."""
    data = dict(
        platform=Platform.tiktok, source_id=sid, url=f"http://x/{sid}", caption=f"cap {sid}",
        duration=dur, view_count=1000, like_count=10, comment_count=1,
        upload_date="20260101", track=track, artist="",
    )  # fmt: skip
    data.update(kw)
    return CatalogEntry(**data)


@dataclass
class FakeCatalog:
    entries: list[CatalogEntry] = field(default_factory=list)
    downloads: list[str] = field(default_factory=list)

    def discover(self) -> list[CatalogEntry]:
        return list(self.entries)

    def download_reference(self, url: str, dest_dir: Path) -> Path:
        self.downloads.append(url)
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"ref-{url.rsplit('/', 1)[-1]}.mp4"
        path.write_bytes(b"ref")
        return path


@dataclass
class FakeOriginals:
    paths: list[Path] = field(default_factory=list)

    def sync(self, dest_dir: Path) -> list[Path]:
        return list(self.paths)


@dataclass
class FakeMedia:
    """Duration and pHash keyed by file stem (refs are named ``ref-<id>``)."""

    durations: dict[str, float] = field(default_factory=dict)
    hashes: dict[str, str] = field(default_factory=dict)

    def duration(self, path: Path) -> float:
        return self.durations.get(path.stem, 0.0)

    def phashes(self, path: Path, n_frames: int) -> list[str]:
        return [self.hashes.get(path.stem.removeprefix("ref-"), "0f0f0f0f")]


@dataclass
class FakeTranscriber:
    words: list[Word] = field(default_factory=lambda: [Word(start=0, end=0.5, text="salut")])

    def transcribe(self, path: Path) -> list[Word]:
        return list(self.words)


@dataclass
class FakeLLM:
    reply: str = '{"title": "Late ride by the sea", "description": "d", "tags": ["sea"], "hook": "Wait for the turn"}'  # noqa: E501
    error: Exception | None = None

    def complete_json(self, prompt: str) -> str:
        if self.error:
            raise self.error
        return self.reply


@dataclass
class FakeRenderer:
    calls: list[dict] = field(default_factory=list)

    def render(self, src, dst, *, words, settings: MontageSettings, hook):
        self.calls.append({"src": src, "mode": settings.mode, "hook": hook, "words": words})
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"short")
        return dst


@dataclass
class FakePlatform:
    uploads: list[dict] = field(default_factory=list)
    privacy_changes: list[tuple[str, str]] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    upload_error: Exception | None = None
    claims_error: Exception | None = None
    force_privacy: str | None = None

    def upload(self, path: Path, meta: ShortMeta, *, privacy: str, publish_at) -> UploadResult:
        if self.upload_error:
            raise self.upload_error
        vid = f"yt{len(self.uploads) + 1}"
        self.uploads.append({"path": path, "meta": meta, "privacy": privacy, "at": publish_at})
        if self.force_privacy:
            return UploadResult(video_id=vid, privacy=self.force_privacy)
        return UploadResult(video_id=vid, privacy=privacy, publish_at=publish_at)

    def set_privacy(self, video_id: str, privacy: str) -> None:
        self.privacy_changes.append((video_id, privacy))

    def check_claims(self, video_ids: list[str]) -> list[Claim]:
        if self.claims_error:
            raise self.claims_error
        return [c for c in self.claims if c.video_id in video_ids]
