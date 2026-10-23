"""Filter graph / command builder (pure, no ffmpeg)."""

from __future__ import annotations

import pytest

from social2shorts.montage import Cut, EditPlan, MediaInfo, MontageSettings, build_command
from social2shorts.montage import build_filter_graph as graph
from social2shorts.montage.filters import escape_filter_path

S = MontageSettings(width=1080, height=1920, fps=30)


def _plan(
    reframe: str | None = "crop", audio: bool = True, music: str | None = None, transfer: str = ""
) -> EditPlan:
    info = MediaInfo(
        path="/in/ride.mp4",
        duration=30,
        width=1920,
        height=1080,
        fps=30,
        has_audio=audio,
        color_transfer=transfer,
    )
    cuts = [Cut(start=10, end=12, zoom=1.08), Cut(start=1, end=4, speed=1.2), Cut(start=5, end=7)]
    return EditPlan(source=info, cuts=cuts, crop_x=0.4, reframe=reframe, music=music)


@pytest.mark.parametrize(
    ("mode", "expected", "absent"),
    [
        ("crop", ["crop=w=", "0.400*iw", "scale=1080:1920"], ["boxblur", ",pad="]),
        ("blur", ["boxblur", "overlay=(W-w)/2:(H-h)/2", "split=2[c0bg][c0fg]"], [",pad="]),
        ("pad", ["force_original_aspect_ratio=decrease", "pad=1080:1920"], ["boxblur"]),
    ],
)
def test_reframe_strategies(mode: str, expected: list[str], absent: list[str]) -> None:
    g = graph(_plan(mode), S)
    for token in expected:
        assert token in g
    for token in absent:
        assert token not in g


def test_smart_unresolved_uses_blur_for_wide_sources() -> None:
    assert "boxblur" in graph(_plan(None), S.model_copy(update={"reframe": "smart"}))


def test_cut_effects_and_concat() -> None:
    g = graph(_plan(), S)
    assert g.count("zoompan=") == 1 and "0.0800*min(on/60,1)" in g
    assert "setpts=(PTS-STARTPTS)/1.2" in g and "atempo=1.2" in g
    assert "[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[vc][ac]" in g
    assert "loudnorm=I=-14:TP=-1.5:LRA=11" in g
    assert "eq=contrast" in g and "unsharp" in g
    assert "fade=t=out:st=" in g and "[vout]" in g and "[aout]" in g


def test_progress_bar_and_grade_toggles() -> None:
    g = graph(_plan(), S)
    assert "overlay=x='-w+W*t/" in g and "color=c=0x" in g
    bare = graph(_plan(), S.model_copy(update={"progress_bar": False, "color_grade": False}))
    assert "color=c=" not in bare and "eq=contrast" not in bare


def test_no_audio_generates_silence() -> None:
    g = graph(_plan(audio=False), S)
    assert g.count("anullsrc") == 3 and "[0:a]" not in g


def test_music_branch() -> None:
    plan = _plan(music="/music/track.mp3")
    g = graph(plan, S)
    assert "[3:a]atrim" in g and "sidechaincompress" in g and "amix=inputs=2" in g
    assert "volume=-20dB" in g
    cmd = build_command(plan, "/out/x.mp4", S)
    assert cmd[cmd.index("-stream_loop") + 3] == "/music/track.mp3"
    assert "sidechaincompress" not in graph(_plan(), S)


def test_command_inputs_and_encoder() -> None:
    cmd = build_command(_plan(), "/out/x.mp4", S, ass_path="/tmp/c.ass")
    assert cmd.count("-i") == 3 and cmd[cmd.index("-ss") + 1] == "10.000"
    for flag, value in [("-c:v", "libx264"), ("-profile:v", "high"), ("-pix_fmt", "yuv420p"),
                        ("-r", "30"), ("-g", "60"), ("-c:a", "aac"), ("-b:a", "192k"),
                        ("-ar", "48000"), ("-movflags", "+faststart"), ("-crf", "18")]:  # fmt: skip
        assert cmd[cmd.index(flag) + 1] == value
    assert "ass='/tmp/c.ass'" in cmd[cmd.index("-filter_complex") + 1]
    assert cmd[-1] == "/out/x.mp4"


def test_escape_windows_path() -> None:
    assert escape_filter_path(r"C:\Users\me\caps.ass") == r"'C\:/Users/me/caps.ass'"


def test_per_cut_crop_x_overrides_plan() -> None:
    plan = _plan()
    plan.cuts[1].crop_x = 0.7
    g = graph(plan, S)
    assert "0.700*iw" in g and g.count("0.400*iw") == 2


def test_loop_crossfade_into_opening_frame() -> None:
    plan = _plan(music="/music/track.mp3").model_copy(update={"loop_s": 0.3})
    g = graph(plan, S)
    assert "[3:v]setpts=PTS-STARTPTS" in g and "loop=loop=8:size=1" in g
    assert f"xfade=transition=fade:duration=0.3:offset={plan.duration - 0.3:.3f}" in g
    assert "fade=t=in:st=0" not in g  # no dip to black at the loop point
    assert "[4:a]atrim" in g  # music input shifts after the still
    cmd = build_command(plan, "/out/x.mp4", S)
    assert cmd.count("-i") == 5 and cmd[cmd.index("-stream_loop") - 5] == "10.000"
    assert "xfade" not in graph(_plan(), S)


def test_hdr_is_tonemapped_to_sdr() -> None:
    hlg = _plan(transfer="arib-std-b67").model_copy(update={"loop_s": 0.3})
    g = graph(hlg, S, zscale=True)
    assert g.count("zscale=tin=arib-std-b67:") == 4  # every cut + the loop still
    assert "tonemap=hable:desat=0" in g and "zscale=t=bt709:m=bt709:r=tv" in g
    assert g.index("zscale") < g.index("setpts")  # tonemap before any other processing
    fallback = graph(_plan(transfer="smpte2084"), S, zscale=False)
    assert "colorspace=all=bt709:iall=bt2020" in fallback and "zscale" not in fallback
    sdr = graph(_plan(transfer="bt709"), S, zscale=True)
    assert "zscale" not in sdr and "colorspace=" not in sdr


def test_output_always_tagged_bt709() -> None:
    for plan in (_plan(), _plan(transfer="arib-std-b67")):
        cmd = build_command(plan, "/out/x.mp4", S)
        for flag in ("-color_primaries", "-color_trc", "-colorspace"):
            assert cmd[cmd.index(flag) + 1] == "bt709"
