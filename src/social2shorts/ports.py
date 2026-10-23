"""Ports: the ``typing.Protocol`` interfaces the pipeline needs from the outside world.

Adapters live in ``sources/``, ``ai/`` and ``publish/``; tests plug in fakes.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol

from social2shorts.models import CatalogEntry, Claim, ShortMeta, UploadResult
from social2shorts.montage.types import MontageSettings, Word


class CatalogSource(Protocol):
    """Discovers published posts and downloads low-res references for matching."""

    def discover(self) -> list[CatalogEntry]: ...

    def download_reference(self, url: str, dest_dir: Path) -> Path: ...


class OriginalsSource(Protocol):
    """Makes the phone originals available as local files."""

    def sync(self, dest_dir: Path) -> list[Path]: ...


class MediaProbe(Protocol):
    """Cheap technical facts about a local video file."""

    def duration(self, path: Path) -> float: ...

    def phashes(self, path: Path, n_frames: int) -> list[str]: ...


class Transcriber(Protocol):
    """Speech-to-text with word timings."""

    def transcribe(self, path: Path) -> list[Word]: ...


class LLM(Protocol):
    """Text model that answers a prompt with a JSON string."""

    def complete_json(self, prompt: str) -> str: ...


class Renderer(Protocol):
    """Turns a source clip into a finished vertical Short."""

    def render(
        self,
        src: Path,
        dst: Path,
        *,
        words: list[Word],
        settings: MontageSettings,
        hook: str | None,
    ) -> Path: ...


class VideoPlatform(Protocol):
    """Where Shorts are published (YouTube)."""

    def upload(
        self, path: Path, meta: ShortMeta, *, privacy: str, publish_at: datetime | None
    ) -> UploadResult: ...

    def set_privacy(self, video_id: str, privacy: str) -> None: ...

    def check_claims(self, video_ids: list[str]) -> list[Claim]: ...
