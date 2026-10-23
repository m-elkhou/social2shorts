"""End-to-end renders with real ffmpeg on tiny synthetic clips."""

from __future__ import annotations

import subprocess

import numpy as np
import pytest

from social2shorts.montage import (
    CaptionStyle,
    MontageError,
    MontageMode,
    Word,
    analyze,
    make_short,
    plan_edit,
    probe,
    render,
)

pytestmark = pytest.mark.integration

WORDS = [
    Word(start=1.0 + 0.6 * i, end=1.4 + 0.6 * i, text=t)
    for i, t in enumerate(["on", "roule", "de", "nuit", "ce", "soir"])
]


def _gray(path: str, t: float) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]  # fmt: skip
    return np.frombuffer(
        subprocess.run(cmd, capture_output=True, check=True).stdout, np.uint8
    ).astype(float)


def _check(result, settings) -> None:
    info = probe(result.path)
    assert (info.width, info.height) == (settings.width, settings.height)
    assert info.has_audio
    assert abs(info.duration - result.plan.duration) < 0.5


def test_probe_and_analyze(clips, tmp_path) -> None:
    info = probe(str(clips["horizontal"]))
    assert (info.width, info.height, info.has_audio) == (640, 360, True)
    assert info.duration == pytest.approx(20, abs=0.1)
    a = analyze(str(clips["horizontal"]), info, WORDS)
    assert 95 <= a.n <= 101
    assert len(a.loudness) == len(a.speech) == len(a.subject_x) == a.n
    assert max(a.loudness) > 0.5 and max(a.speech) > 0
    rotated = tmp_path / "rot.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-display_rotation", "90", "-i", str(clips["silent"]),
         "-c", "copy", str(rotated)],
        check=True,
    )  # fmt: skip
    rinfo = probe(str(rotated))
    assert (rinfo.width, rinfo.height, rinfo.rotation, rinfo.has_audio) == (360, 640, 270, False)


def test_highlight_with_words_and_hook(clips, tmp_path, fast_settings) -> None:
    result = make_short(
        str(clips["horizontal"]), str(tmp_path / "out.mp4"), words=WORDS,
        settings=fast_settings, hook_text="Night ride 🏍️ {200 km/h}",
    )  # fmt: skip
    _check(result, fast_settings)
    assert result.plan.cuts[0].reason.startswith("hook")
    assert result.plan.loop_s > 0
    first, last, mid = (_gray(result.path, t) for t in (0, result.duration - 0.05, 3.0))
    assert np.abs(first - last).mean() < 0.5 * np.abs(first - mid).mean()  # seamless loop
    assert len(result.plan.cuts) >= 3
    assert not list(tmp_path.glob("*.ass"))


def test_polish_vertical_no_words(clips, tmp_path, fast_settings) -> None:
    s = fast_settings.model_copy(
        update={"mode": MontageMode.polish, "captions": CaptionStyle.minimal}
    )
    result = make_short(str(clips["vertical"]), str(tmp_path / "p.mp4"), words=[], settings=s)
    _check(result, s)
    assert result.duration == pytest.approx(8, abs=0.3)
    assert result.plan.reframe == "crop"


def test_silent_source_gets_audio_and_music(clips, tmp_path, fast_settings, music_dir) -> None:
    s = fast_settings.model_copy(update={"music_dir": str(music_dir), "reframe": "pad"})
    result = make_short(str(clips["silent"]), str(tmp_path / "m.mp4"), words=[], settings=s)
    _check(result, s)
    assert result.plan.music and result.plan.music.endswith("track.m4a")


def test_silent_source_without_music_blur(clips, tmp_path, fast_settings) -> None:
    s = fast_settings.model_copy(update={"reframe": "blur", "hook_s": 0.0})
    result = make_short(str(clips["silent"]), str(tmp_path / "b.mp4"), words=WORDS, settings=s)
    _check(result, s)


def test_render_failure_raises(clips, tmp_path, fast_settings) -> None:
    info = probe(str(clips["vertical"]))
    plan = plan_edit(info, analyze(str(clips["vertical"]), info), [], fast_settings)
    plan.source.path = str(tmp_path / "missing.mp4")
    with pytest.raises(MontageError, match="ffmpeg failed"):
        render(plan, str(tmp_path / "x.mp4"), [], fast_settings)


def test_hdr_hlg_10bit_is_tonemapped(tmp_path, fast_settings) -> None:
    src = tmp_path / "hlg.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30",
         "-t", "5", "-vf", "format=yuv420p10le", "-c:v", "libx264", "-preset", "ultrafast",
         "-color_trc", "arib-std-b67", "-color_primaries", "bt2020", "-colorspace", "bt2020nc",
         str(src)],
        check=True,
    )  # fmt: skip
    assert probe(str(src)).is_hdr
    s = fast_settings.model_copy(update={"min_duration_s": 2.0})
    result = make_short(str(src), str(tmp_path / "sdr.mp4"), words=[], settings=s)
    _check(result, s)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
         "stream=pix_fmt,color_transfer,color_primaries,color_space", "-of", "csv=p=0",
         result.path],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.strip()  # fmt: skip
    assert out.split(",") == ["yuv420p", "bt709", "bt709", "bt709"]
