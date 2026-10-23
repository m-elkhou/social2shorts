"""Shared fixtures for montage tests: synthetic analyses and tiny lavfi-generated clips."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from social2shorts.montage import Analysis, MediaInfo, MontageSettings


def make_analysis(
    duration: float = 60.0,
    hz: float = 5.0,
    peaks: tuple[tuple[float, float], ...] = ((30.0, 33.0),),
    scene_cuts: tuple[float, ...] = (),
    speech: list[float] | None = None,
) -> Analysis:
    """Analysis with gently varying motion/loudness and strong bumps inside ``peaks``."""
    n = int(duration * hz)
    t = np.arange(n) / hz
    motion = 0.02 + 0.01 * (1 + np.sin(t / 3.0))
    loud = 0.4 + 0.1 * np.sin(t / 5.0)
    for lo, hi in peaks:
        inside = (t >= lo) & (t < hi)
        motion[inside] += 0.08
        loud[inside] += 0.4
    return Analysis(
        sample_hz=hz,
        duration=duration,
        motion=motion.tolist(),
        brightness=[0.3] * n,
        loudness=np.clip(loud, 0, 1).tolist(),
        speech=speech if speech is not None else [0.0] * n,
        subject_x=[0.5] * n,
        subject_spread=0.25,
        scene_cuts=list(scene_cuts),
    )


def make_info(
    duration: float = 60.0, w: int = 1920, h: int = 1080, audio: bool = True
) -> MediaInfo:
    return MediaInfo(
        path="/clips/ride_001.mp4", duration=duration, width=w, height=h, fps=30, has_audio=audio
    )


@pytest.fixture
def fast_settings() -> MontageSettings:
    """Tiny, fast render settings for integration tests."""
    return MontageSettings(
        width=270, height=480, preset="ultrafast", crf=30, target_duration_s=8, min_duration_s=4
    )


def _lavfi(
    dst: Path, video: str, seconds: float, audio: str | None = None, extra: tuple = ()
) -> Path:
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", video]
    if audio:
        cmd += ["-f", "lavfi", "-i", audio]
    cmd += ["-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac"] if audio else []
    subprocess.run([*cmd, *extra, str(dst)], check=True)
    return dst


@pytest.fixture(scope="session")
def clips(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Synthetic sources: horizontal+audio, vertical+audio, horizontal silent, music track."""
    d = tmp_path_factory.mktemp("clips")
    sine = "sine=frequency={f}:sample_rate=48000"
    return {
        "horizontal": _lavfi(d / "h.mp4", "testsrc2=size=640x360:rate=30", 20, sine.format(f=220)),
        "vertical": _lavfi(d / "v.mp4", "testsrc2=size=360x640:rate=30", 8, sine.format(f=330)),
        "silent": _lavfi(d / "s.mp4", "testsrc2=size=640x360:rate=30", 10),
    }


@pytest.fixture(scope="session")
def music_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("music")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=44100",
         "-t", "3", str(d / "track.m4a")],
        check=True,
    )  # fmt: skip
    return d


@pytest.fixture
def analysis_factory() -> Callable[..., Analysis]:
    return make_analysis


@pytest.fixture
def info_factory() -> Callable[..., MediaInfo]:
    return make_info
