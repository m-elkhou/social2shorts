"""Sound level and frame sampling on real ffmpeg-generated clips."""

import shutil
import subprocess
from pathlib import Path

import pytest

from social2shorts.ai.frames import sample_frames
from social2shorts.media import audio_level

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing"),
]


def _clip(path: Path, audio: str | None) -> Path:
    cmd = ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=144x256:d=3:r=15"]
    if audio:
        cmd += ["-f", "lavfi", "-i", audio, "-shortest", "-c:a", "aac"]
    subprocess.run([*cmd, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(path)], check=True)
    return path


def test_a_clip_with_engine_like_sound_is_audible(tmp_path):
    level = audio_level(_clip(tmp_path / "loud.mp4", "sine=frequency=440:duration=3"))
    assert level.has_audio and level.audible and -30 < level.mean_db < -10


def test_a_muted_track_is_not_audible(tmp_path):
    level = audio_level(_clip(tmp_path / "mute.mp4", "anullsrc=r=44100:cl=mono:d=3"))
    assert level.has_audio and not level.audible and level.mean_db < -60
    assert "limit" in level.describe()


def test_a_near_silent_track_is_not_audible(tmp_path):
    clip = _clip(tmp_path / "quiet.mp4", "sine=frequency=440:duration=3,volume=-30dB")
    assert not audio_level(clip).audible  # about -51 dB: a muted clip with a click in it


def test_a_clip_without_audio_track_is_not_audible(tmp_path):
    level = audio_level(_clip(tmp_path / "video-only.mp4", None))
    assert not level.has_audio and not level.audible and level.describe() == "no audio track"


def test_sample_frames_returns_jpegs_spread_over_the_clip(tmp_path):
    frames = sample_frames(_clip(tmp_path / "c.mp4", None), count=4, height=96)
    assert len(frames) == 4 and all(f.startswith(b"\xff\xd8") for f in frames)
    assert len(set(frames)) > 1  # testsrc changes over time: the frames are not the same picture


def test_sample_frames_of_an_unreadable_file_is_empty(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    assert sample_frames(bad) == []
