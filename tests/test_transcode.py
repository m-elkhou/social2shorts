from insta2youtube.transcode import build_args


def test_build_args_scales_pads_and_burns():
    a = build_args("in.mp4", "out.mp4", subtitle_ass="subs.ass")
    s = " ".join(a)
    assert "scale=" in s and "pad=" in s and "1080" in s and "1920" in s
    assert "ass=" in s and "libx264" in s and "+faststart" in s


def test_build_args_without_subtitles_has_no_ass():
    s = " ".join(build_args("in.mp4", "out.mp4"))
    assert "ass=" not in s
    assert "scale=" in s and "pad=" in s


def test_build_args_escapes_windows_ass_path():
    a = build_args("in.mp4", "out.mp4", subtitle_ass=r"C:\a\b.ass")
    vf = a[a.index("-vf") + 1]
    assert r"C\:/a/b.ass" in vf
    assert "C:\\" not in vf
