"""End-to-end raw path on a real ffmpeg-generated clip (fake render/upload, no network)."""

import shutil
import subprocess
from pathlib import Path

import pytest
from fakes import NOW, FakeLLM, FakeOriginals, FakePlatform, FakeTranscriber

from social2shorts.config import Settings
from social2shorts.db import Store
from social2shorts.media import LocalMedia
from social2shorts.models import ItemStatus
from social2shorts.pipeline import PipelineDeps, RunOptions, SourceChoice, run_pipeline
from social2shorts.sources.tiktok_instagram import SocialCatalog

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing"),
]


def _clip(path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=144x256:d=2:r=15",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-shortest",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-y", str(path)],
        check=True,
    )  # fmt: skip


class CopyRenderer:
    """Stands in for the montage engine: copies the source and checks it is a real video."""

    def __init__(self):
        self.durations = []

    def render(self, src, dst, *, words, settings, hook):
        self.durations.append(LocalMedia().duration(src))
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        return dst


def test_raw_inbox_dry_run_then_upload(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _clip(inbox / "VID_0001.mp4")
    settings = Settings(
        work_dir=str(tmp_path / "work"), db_path=str(tmp_path / "db.sqlite"),
        originals_dir=str(tmp_path / "originals"), sources={"inbox_dir": str(inbox)},
    )  # fmt: skip
    renderer, platform = CopyRenderer(), FakePlatform()
    deps = PipelineDeps(
        catalog=SocialCatalog(), originals=FakeOriginals(), media=LocalMedia(),
        transcriber=FakeTranscriber(), llm=FakeLLM(), renderer=renderer, platform=platform,
        clock=lambda: NOW,
    )  # fmt: skip

    dry = run_pipeline(settings, deps, RunOptions(source=SourceChoice.raw, dry_run=True))
    key = dry.rendered[0]
    out = Path(settings.work_dir) / "renders" / f"{key}.mp4"
    assert out.stat().st_size > 0 and 1.5 < renderer.durations[0] < 2.5
    assert platform.uploads == []

    real = run_pipeline(settings, deps, RunOptions(source=SourceChoice.raw))
    assert [u.item_key for u in real.uploaded] == [key]
    assert Store(settings.db_path).item(key).status is ItemStatus.uploaded
    assert not out.exists()  # render cleaned after upload
    assert run_pipeline(settings, deps, RunOptions(source=SourceChoice.raw)).uploaded == []
