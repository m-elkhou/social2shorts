"""Per-video variation: deterministic, bounded, and actually varied."""

from __future__ import annotations

from social2shorts.montage import MontageSettings
from social2shorts.montage.style import (
    ACCENT_PALETTE,
    CAPTION_Y_RANGE,
    HOOK_Y_RANGE,
    PUNCH_RANGE,
    RHYTHM_RANGE,
    make_variation,
    seed_for,
)

PATHS = [f"/raw/motoride_{i:03d}.mp4" for i in range(60)]


def test_variation_is_deterministic() -> None:
    s = MontageSettings()
    assert make_variation("/a/clip.mp4", s) == make_variation("/a/clip.mp4", s)
    assert seed_for("/a/clip.mp4") == seed_for("/other/dir/clip.mp4")  # unreadable: name fallback


def test_seed_comes_from_content(tmp_path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    one, two = tmp_path / "a" / "clip.mp4", tmp_path / "b" / "clip.mp4"
    one.write_bytes(b"ride one" * 1000)
    two.write_bytes(b"ride two" * 1000)
    assert seed_for(str(one)) != seed_for(str(two))  # same name, different clips
    renamed = tmp_path / "renamed.mp4"
    renamed.write_bytes(one.read_bytes())
    assert seed_for(str(renamed)) == seed_for(str(one))  # same clip, any name


def test_variation_bounds() -> None:
    s = MontageSettings()
    for path in PATHS:
        v = make_variation(path, s)
        assert PUNCH_RANGE[0] <= v.punch_zoom <= PUNCH_RANGE[1]
        assert RHYTHM_RANGE[0] <= v.rhythm <= RHYTHM_RANGE[1]
        assert CAPTION_Y_RANGE[0] <= v.caption_y <= CAPTION_Y_RANGE[1]
        assert HOOK_Y_RANGE[0] <= v.hook_y <= HOOK_Y_RANGE[1]
        assert v.accent in ACCENT_PALETTE


def test_variation_differs_between_videos() -> None:
    vs = [make_variation(p, MontageSettings()) for p in PATHS]
    assert len({v.accent for v in vs}) >= 4
    assert len({v.punch_zoom for v in vs}) > 20
    assert len({v.rhythm for v in vs}) > 20


def test_explicit_highlight_overrides_palette() -> None:
    s = MontageSettings(caption_highlight="&H000000FF")
    assert all(make_variation(p, s).accent == "&H000000FF" for p in PATHS[:10])
