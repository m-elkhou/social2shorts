"""Pytest fixtures: tmp-dir settings and fully faked pipeline dependencies."""

from __future__ import annotations

from pathlib import Path

import pytest
from fakes import (
    NOW,
    FakeCatalog,
    FakeLLM,
    FakeMedia,
    FakeOriginals,
    FakePlatform,
    FakeRenderer,
    FakeTranscriber,
    entry,
)

from social2shorts.config import Settings
from social2shorts.pipeline import PipelineDeps


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        originals_dir=str(tmp_path / "originals"),
        work_dir=str(tmp_path / "work"),
        db_path=str(tmp_path / "db.sqlite"),
        sources={"inbox_dir": str(tmp_path / "inbox")},
    )


@pytest.fixture
def deps(tmp_path: Path) -> PipelineDeps:
    """Catalog A (matches /originals/A.mp4), B (licensed music), C (no original)."""
    orig = tmp_path / "originals"
    orig.mkdir()
    (orig / "A.mp4").write_bytes(b"original A")
    return PipelineDeps(
        catalog=FakeCatalog(
            [entry("A", 20.0), entry("B", 30.0, track="Hit Song"), entry("C", 40.0)]
        ),
        originals=FakeOriginals([orig / "A.mp4"]),
        media=FakeMedia(durations={"A": 20.0}, hashes={"A": "ffff0000"}),
        transcriber=FakeTranscriber(),
        llm=FakeLLM(),
        renderer=FakeRenderer(),
        platform=FakePlatform(),
        clock=lambda: NOW,
    )
