"""Planner heuristics on synthetic analyses (no ffmpeg)."""

from __future__ import annotations

import os
from itertools import pairwise

import pytest

from social2shorts.montage import Cut, MontageMode, MontageSettings, Word, plan_edit, remap_words
from social2shorts.montage.planner import (
    BREATH_S,
    CROP_MAX_SHIFT,
    LOOP_XFADE_S,
    REV_HOOK_LEAD_S,
    pick_music,
    resolve_reframe,
)
from social2shorts.montage.style import PUNCH_RANGE

SETTINGS = MontageSettings(target_duration_s=20, min_duration_s=12, hook_s=2.0)


def _words(start: float, end: float, step: float = 0.7, length: float = 0.4) -> list[Word]:
    out, t, k = [], start, 0
    while t + length <= end:
        out.append(Word(start=round(t, 3), end=round(t + length, 3), text=f"w{k}"))
        t, k = t + step, k + 1
    return out


def test_deterministic(analysis_factory, info_factory) -> None:
    a, info = analysis_factory(), info_factory()
    assert plan_edit(info, a, [], SETTINGS) == plan_edit(info, a, [], SETTINGS)


def test_duration_bounds_and_no_overlap(analysis_factory, info_factory) -> None:
    plan = plan_edit(info_factory(), analysis_factory(), [], SETTINGS)
    assert SETTINGS.min_duration_s <= plan.duration <= SETTINGS.target_duration_s * 1.3
    spans = sorted((c.start, c.end) for c in plan.cuts)
    assert all(a[1] <= b[0] + 1e-9 for a, b in pairwise(spans))
    assert all(0 <= c.start < c.end <= 60 for c in plan.cuts)


def test_hook_first_then_chronological(analysis_factory, info_factory) -> None:
    plan = plan_edit(info_factory(), analysis_factory(peaks=((30.0, 33.0),)), [], SETTINGS)
    hook, body = plan.cuts[0], plan.cuts[1:]
    assert hook.reason.startswith("hook")
    assert 29.0 <= hook.start <= 33.0
    assert [c.start for c in body] == sorted(c.start for c in body)
    assert all(c.reason for c in plan.cuts)


def test_hook_disabled(analysis_factory, info_factory) -> None:
    s = SETTINGS.model_copy(update={"hook_s": 0.0})
    plan = plan_edit(info_factory(), analysis_factory(), [], s)
    assert not any(c.reason.startswith("hook") for c in plan.cuts)
    assert [c.start for c in plan.cuts] == sorted(c.start for c in plan.cuts)


def test_never_cuts_through_words_and_breathes(analysis_factory, info_factory) -> None:
    words = _words(0.3, 59.0)  # continuous talking, 0.3 s gaps
    a = analysis_factory(speech=[0.6] * 300)
    plan = plan_edit(info_factory(), a, words, SETTINGS)
    for c in plan.cuts:
        for b in (c.start, c.end):
            assert not any(w.start < b < w.end for w in words), (b, c)
    sparse = _words(0.5, 59.0, step=2.0, length=0.5)  # 1.5 s gaps: full breathing room fits
    plan = plan_edit(info_factory(), analysis_factory(), sparse, SETTINGS)
    for c in plan.cuts:
        for b in (c.start, c.end):
            assert not any(w.start - BREATH_S + 1e-6 < b < w.end + BREATH_S - 1e-6 for w in sparse)


def test_snaps_to_scene_cut(analysis_factory, info_factory) -> None:
    a = analysis_factory(peaks=((30.0, 36.0),), scene_cuts=(29.8,))
    s = SETTINGS.model_copy(update={"hook_s": 0.0})
    plan = plan_edit(info_factory(), a, [], s)
    assert any(abs(c.start - 29.8) < 1e-6 and "scene" in c.reason for c in plan.cuts)


def test_speed_ramp_only_without_speech(analysis_factory, info_factory) -> None:
    words = _words(10.0, 20.0)
    speech = [1.0 if 50 <= i < 100 else 0.0 for i in range(300)]
    plan = plan_edit(info_factory(), analysis_factory(peaks=((0, 60),)), words, SETTINGS)
    plan2 = plan_edit(
        info_factory(), analysis_factory(peaks=((0, 60),), speech=speech), words, SETTINGS
    )
    for c in [*plan.cuts, *plan2.cuts]:
        assert 1.0 <= c.speed <= 1.25
    for c in plan2.cuts:
        if c.speed > 1.0:
            assert not any(c.start < w.end and w.start < c.end for w in words)


def test_punch_in_alternates_within_bounds(analysis_factory, info_factory) -> None:
    plan = plan_edit(info_factory(), analysis_factory(), [], SETTINGS)
    zooms = [c.zoom for c in plan.cuts]
    assert PUNCH_RANGE[0] <= zooms[0] <= PUNCH_RANGE[1]
    assert all(z == 1.0 for z in zooms[1::2])
    off = plan_edit(
        info_factory(), analysis_factory(), [], SETTINGS.model_copy(update={"punch_in": False})
    )
    assert all(c.zoom == 1.0 for c in off.cuts)


def test_rhythm_varies_per_video(analysis_factory, info_factory) -> None:
    a = analysis_factory(duration=90, peaks=())
    plans = [
        plan_edit(info_factory(90).model_copy(update={"path": f"/r/{k}.mp4"}), a, [], SETTINGS)
        for k in range(6)
    ]
    assert len({tuple((c.start, c.end) for c in p.cuts) for p in plans}) > 1


def test_polish_keeps_everything(analysis_factory, info_factory) -> None:
    s = MontageSettings(mode=MontageMode.polish)
    plan = plan_edit(info_factory(), analysis_factory(), [], s)
    assert len(plan.cuts) == 1
    assert (plan.cuts[0].start, plan.cuts[0].end, plan.cuts[0].speed) == (0.0, 60.0, 1.0)


def test_polish_trims_dead_head_and_caps(analysis_factory, info_factory) -> None:
    a = analysis_factory(duration=200, peaks=((0, 200),))
    for i in range(10):  # 2 s static + silent head
        a.motion[i], a.loudness[i] = 0.0, 0.0
    plan = plan_edit(info_factory(200), a, [], MontageSettings(mode=MontageMode.polish))
    cut = plan.cuts[0]
    assert cut.start == pytest.approx(2.0)
    assert cut.out_duration == pytest.approx(180.0)


def test_resolve_reframe(analysis_factory, info_factory) -> None:
    a = analysis_factory()
    s = MontageSettings()
    assert resolve_reframe(info_factory(w=1080, h=1920), a, s) == "crop"
    assert (
        resolve_reframe(info_factory(), a.model_copy(update={"subject_spread": 0.1}), s) == "crop"
    )
    assert (
        resolve_reframe(info_factory(), a.model_copy(update={"subject_spread": 0.28}), s) == "blur"
    )
    assert resolve_reframe(info_factory(), a, s.model_copy(update={"reframe": "pad"})) == "pad"


def test_pick_music_deterministic(tmp_path) -> None:
    assert pick_music(None, "x.mp4") is None
    assert pick_music(str(tmp_path), "x.mp4") is None
    for name in ("b.mp3", "a.m4a", "notes.txt"):
        (tmp_path / name).write_bytes(b"")
    picks = {pick_music(str(tmp_path), f"clip{k}.mp4") for k in range(20)}
    assert picks <= {os.path.join(tmp_path, "a.m4a"), os.path.join(tmp_path, "b.mp3")}
    assert pick_music(str(tmp_path), "clip1.mp4") == pick_music(str(tmp_path), "clip1.mp4")


def test_remap_words_through_hook_cuts_and_speed() -> None:
    words = [
        Word(start=1.0, end=1.5, text="a"),
        Word(start=5.0, end=5.4, text="b"),
        Word(start=10.2, end=10.6, text="c"),
        Word(start=20.0, end=20.5, text="gone"),
    ]
    cuts = [
        Cut(start=10.0, end=11.0),  # hook (out 0..1)
        Cut(start=0.0, end=2.0),  # out 1..3
        Cut(start=4.0, end=6.0, speed=2.0),  # out 3..4
    ]
    out = remap_words(words, cuts)
    assert [w.text for w in out] == ["c", "a", "b"]
    assert out[0].start == pytest.approx(0.2) and out[0].end == pytest.approx(0.6)
    assert out[1].start == pytest.approx(2.0) and out[1].end == pytest.approx(2.5)
    assert out[2].start == pytest.approx(3.5) and out[2].end == pytest.approx(3.7)


def test_highlight_never_exceeds_shorts_limit(analysis_factory, info_factory) -> None:
    words = _words(0.3, 599.0)
    a = analysis_factory(duration=600, peaks=((0, 600),), speech=[0.6] * 3000)
    s = MontageSettings(target_duration_s=180, min_duration_s=12)
    plan = plan_edit(info_factory(600), a, words, s)
    assert 150 <= plan.duration <= 180.0 + 1e-6


def _with_rev(a, peaks: list[float]):
    rev = [0.0] * a.n
    for t in peaks:
        rev[int(t * a.sample_hz)] = 1.0
    return a.model_copy(update={"rev": rev, "rev_peaks": peaks})


def test_rev_peak_leads_hook_and_triggers_punch_in(analysis_factory, info_factory) -> None:
    a = _with_rev(analysis_factory(peaks=((30.0, 33.0), (45.0, 48.0))), [45.5])
    plan = plan_edit(info_factory(), a, [], SETTINGS)
    hook = plan.cuts[0]
    assert hook.start == pytest.approx(45.5 - REV_HOOK_LEAD_S, abs=0.21)
    for c in plan.cuts:
        has_rev = c.start <= 45.5 < c.end
        assert (c.zoom > 1.0) == has_rev
        assert ("punch-in on rev" in c.reason) == has_rev


def test_weak_rev_does_not_steal_hook(analysis_factory, info_factory) -> None:
    a = _with_rev(analysis_factory(peaks=((30.0, 33.0),)), [10.0])
    assert 29.0 <= plan_edit(info_factory(), a, [], SETTINGS).cuts[0].start <= 33.0


def test_cuts_snap_to_rev_peaks(analysis_factory, info_factory) -> None:
    a = _with_rev(analysis_factory(peaks=((30.0, 36.0),), scene_cuts=(29.7,)), [29.9])
    s = SETTINGS.model_copy(update={"hook_s": 0.0})
    plan = plan_edit(info_factory(), a, [], s)
    assert any(abs(c.start - 29.9) < 1e-6 and "rev" in c.reason for c in plan.cuts)


def test_per_cut_crop_is_clamped_within_a_shot(analysis_factory, info_factory) -> None:
    a = analysis_factory()
    xs = [0.2 if i < 150 else 0.8 for i in range(a.n)]  # subject jumps sides at 30 s
    s = SETTINGS.model_copy(update={"hook_s": 0.0})
    plan = plan_edit(info_factory(), a.model_copy(update={"subject_x": xs}), [], s)
    crops = [c.crop_x for c in plan.cuts]
    assert all(x is not None for x in crops)
    assert all(abs(b - a_) <= CROP_MAX_SHIFT + 1e-9 for a_, b in pairwise(crops))
    cut_plan = plan_edit(
        info_factory(), a.model_copy(update={"subject_x": xs, "scene_cuts": [30.0]}), [], s
    )
    crops = [c.crop_x for c in cut_plan.cuts]
    assert min(crops) <= 0.25 and max(crops) >= 0.75  # free change across a scene cut


def test_loop_crossfade_only_in_highlight(analysis_factory, info_factory) -> None:
    assert plan_edit(info_factory(), analysis_factory(), [], SETTINGS).loop_s == LOOP_XFADE_S
    polish = SETTINGS.model_copy(update={"mode": MontageMode.polish})
    assert plan_edit(info_factory(), analysis_factory(), [], polish).loop_s == 0.0
