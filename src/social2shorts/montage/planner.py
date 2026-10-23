"""The editor: turns an :class:`Analysis` into an :class:`EditPlan` (what to keep, order, speed)."""

from __future__ import annotations

import bisect
import logging
import os
from collections.abc import Sequence

import numpy as np

from .analyze import Analysis
from .style import make_variation, seed_for
from .types import Cut, EditPlan, MediaInfo, MontageMode, MontageSettings, Variation, Word

log = logging.getLogger(__name__)

# Window score = weighted signals (each normalised to 0..1 within the clip) minus penalties.
W_MOTION = 0.45  # visual action drives retention for riding footage
W_AUDIO = 0.30  # engine revs / loud moments
W_SPEECH = 0.25  # the rider talking to camera
W_REV = 0.10  # small bonus for engine revs / acceleration (they also drive hook, cuts, zooms)
P_STATIC = 0.30  # nothing moves (parked bike, phone on the ground)
P_DARK = 0.20  # near-black frames (pocket, tunnel); scaled by how dark
STATIC_LEVEL = 0.08  # normalised motion below this counts as static
DARK_LEVEL = 0.06  # mean luma below this counts as near black (night footage sits above)
SILENT_LEVEL = 0.05  # loudness below this (~-57 dBFS) counts as silent

SEG_MIN_S, SEG_MAX_S = 1.5, 6.0  # segment rhythm: high energy → short, calm → long
SNAP_S = 0.5  # boundaries jump to a scene cut / audio onset this close
BREATH_S = 0.15  # breathing room kept around speech at cut points
RAMP_MOTION = 0.6  # normalised motion above which a speechless stretch is sped up
RAMP_MAX = 1.25  # maximum speed ramp
RAMP_MAX_SPEECH = 0.05  # speech density allowed in a sped-up segment
POLISH_MAX_S = 180.0  # YouTube Shorts hard limit
POLISH_MIN_TRIM_S = 0.5  # only trim dead head/tail longer than this
CROP_ASPECT_SLACK = 1.2  # sources up to 1.2x wider than 9:16 are simply cropped
CROP_SPREAD_MAX = 0.22  # horizontal energy spread below which a 9:16 crop keeps the subject
REV_HOOK_LEAD_S = 0.6  # a rev hook starts this early so the build-up is heard
REV_HOOK_MIN_RATIO = 0.8  # a rev hook must score >= 80 % of the best window overall
LOOP_XFADE_S = 0.3  # highlight tail crossfades into the opening frame for seamless replays
SUBJECT_SMOOTH_S = 1.0  # subject_x smoothing before choosing a cut's stationary crop
CROP_MAX_SHIFT = 0.08  # max crop_x change between neighbouring cuts of the same shot
MUSIC_EXTS = (".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus")


def _norm(values: Sequence[float], lo_pct: float = 0, hi_pct: float = 95) -> np.ndarray:
    """Scale to 0..1 relative to the clip's own distribution (robust to night/quiet clips)."""
    arr = np.asarray(values, dtype=np.float64)
    if arr.size == 0:
        return arr
    lo, hi = np.percentile(arr, lo_pct), np.percentile(arr, hi_pct)
    return np.clip((arr - lo) / max(hi - lo, 1e-6), 0.0, 1.0)


def _smooth(arr: np.ndarray, samples: int) -> np.ndarray:
    if samples <= 1 or arr.size == 0:
        return arr
    return np.convolve(arr, np.ones(samples) / samples, mode="same")


def score_samples(a: Analysis) -> np.ndarray:
    """Per-sample interest score (higher = more watchable), smoothed over ~1 s."""
    motion = _norm(a.motion)
    loud = _norm(a.loudness, lo_pct=10)
    speech = np.asarray(a.speech, dtype=np.float64)
    bright = np.asarray(a.brightness, dtype=np.float64)
    static = (motion < STATIC_LEVEL).astype(np.float64)
    dark = np.clip((DARK_LEVEL - bright) / DARK_LEVEL, 0.0, 1.0)
    rev = np.zeros_like(motion)
    rev[: len(a.rev)] = a.rev[: len(rev)]
    raw = (
        W_MOTION * motion
        + W_AUDIO * loud
        + W_SPEECH * speech
        + W_REV * rev
        - P_STATIC * static
        - P_DARK * dark
    )
    return _smooth(raw, round(a.sample_hz))


def _window_means(values: np.ndarray, length: int) -> np.ndarray:
    """Mean of every window of ``length`` samples (index = window start)."""
    csum = np.concatenate([[0.0], np.cumsum(values)])
    return (csum[length:] - csum[:-length]) / length


def _nearest(points: Sequence[float], t: float) -> float | None:
    """Closest sorted ``points`` entry within :data:`SNAP_S` of ``t``."""
    i = bisect.bisect_left(points, t)
    near = [p for p in points[max(i - 1, 0) : i + 1] if abs(p - t) <= SNAP_S]
    return min(near, key=lambda p: abs(p - t)) if near else None


class _Snapper:
    """Moves cut points onto revs / scene cuts / audio onsets and keeps words whole."""

    def __init__(
        self,
        words: Sequence[Word],
        points: Sequence[float],
        duration: float,
        revs: Sequence[float] = (),
    ):
        self.words = sorted(words, key=lambda w: w.start)
        self.points = sorted(points)
        self.revs = sorted(revs)
        self.duration = duration

    def _to_point(self, t: float) -> tuple[float, str]:
        rev = _nearest(self.revs, t)  # revs win: cutting on the throttle feels intentional
        if rev is not None:
            return rev, "cut on rev peak"
        p = _nearest(self.points, t)
        return (t, "") if p is None else (p, "cut on scene change/audio peak")

    def _gap_bounds(self, k: int) -> tuple[float, float]:
        """End of the word before ``k`` and start of the word after ``k``."""
        prev_end = self.words[k - 1].end if k else 0.0
        next_start = self.words[k + 1].start if k + 1 < len(self.words) else self.duration
        return prev_end, next_start

    def _breathe(self, t: float, before: bool) -> tuple[float, str]:
        """Push ``t`` out of any word (and its breathing room) towards the cut's outside."""
        for k, w in enumerate(self.words):
            if not w.start - BREATH_S < t < w.end + BREATH_S:
                continue
            prev_end, next_start = self._gap_bounds(k)
            ahead = max(w.start - BREATH_S, (prev_end + w.start) / 2)
            after = min(w.end + BREATH_S, (w.end + next_start) / 2)
            inside = w.start < t < w.end
            if inside:  # keep the whole word: extend outward
                return (ahead if before else after), "kept words whole"
            return (ahead if t <= w.start else after), "breathing room around speech"
        return t, ""

    def start(self, t: float, use_points: bool = True) -> tuple[float, str]:
        t, note = self._to_point(t) if use_points else (t, "")
        t, moved = self._breathe(t, before=True)
        return max(t, 0.0), moved or note

    def end(self, t: float, use_points: bool = True) -> tuple[float, str]:
        t, note = self._to_point(t) if use_points else (t, "")
        t, moved = self._breathe(t, before=False)
        return min(t, self.duration), moved or note


def _ramp(motion: np.ndarray, speech: np.ndarray, i0: int, i1: int) -> float:
    """Mild speed-up for high-motion, speechless stretches; 1.0 otherwise."""
    if i1 <= i0 or speech[i0:i1].mean() > RAMP_MAX_SPEECH:
        return 1.0
    m = float(motion[i0:i1].mean())
    if m <= RAMP_MOTION:
        return 1.0
    boost = (RAMP_MAX - 1.0) * min((m - RAMP_MOTION) / (1.0 - RAMP_MOTION), 1.0)
    return round(1.0 + round(boost / 0.05) * 0.05, 2)


def resolve_reframe(info: MediaInfo, a: Analysis | None, settings: MontageSettings) -> str:
    """Pick the concrete framing (``crop``/``blur``/``pad``) for ``settings.reframe``."""
    if settings.reframe != "smart":
        return settings.reframe
    src_aspect = info.width / max(info.height, 1)
    if src_aspect <= settings.width / settings.height * CROP_ASPECT_SLACK:
        return "crop"
    spread = a.subject_spread if a is not None else 1.0
    return "crop" if spread <= CROP_SPREAD_MAX else "blur"


def pick_music(music_dir: str | None, seed_path: str) -> str | None:
    """Deterministically choose a track from ``music_dir`` for this source (None if empty)."""
    if not music_dir or not os.path.isdir(music_dir):
        return None
    tracks = sorted(f for f in os.listdir(music_dir) if f.lower().endswith(MUSIC_EXTS))
    if not tracks:
        return None
    return os.path.join(music_dir, tracks[seed_for(seed_path) % len(tracks)])


def _crop_x(a: Analysis, cuts: Sequence[Cut]) -> float:
    xs = [
        a.subject_x[i]
        for c in cuts
        for i in range(int(c.start * a.sample_hz), min(int(c.end * a.sample_hz), len(a.subject_x)))
    ]
    return round(float(np.median(xs)), 3) if xs else 0.5


def _plan_polish(a: Analysis, duration: float) -> list[Cut]:
    """Whole clip, minus an obviously dead (static + silent + speechless) head/tail."""
    motion = _norm(a.motion)
    dead = (
        (motion < STATIC_LEVEL)
        & (np.asarray(a.loudness) < SILENT_LEVEL)
        & (np.asarray(a.speech) < 0.01)
    )
    head = int(np.argmin(dead)) if not dead.all() else 0
    tail = int(np.argmin(dead[::-1])) if not dead.all() else 0
    start, end, notes = 0.0, duration, ["polish: full clip"]
    if head / a.sample_hz >= POLISH_MIN_TRIM_S:
        start = head / a.sample_hz
        notes.append(f"trimmed {start:.1f}s dead head")
    if tail / a.sample_hz >= POLISH_MIN_TRIM_S:
        end = duration - tail / a.sample_hz
        notes.append(f"trimmed {tail / a.sample_hz:.1f}s dead tail")
    if end - start < 0.5:
        start, end = 0.0, duration
    if end - start > POLISH_MAX_S:
        end = start + POLISH_MAX_S
        notes.append("capped at 180s")
    return [Cut(start=start, end=end, reason="; ".join(notes))]


def _describe(a: Analysis, i0: int, i1: int, score: float) -> str:
    m, lo, sp = (
        float(np.mean(x[i0:i1])) for x in (_norm(a.motion), _norm(a.loudness, 10), a.speech)
    )
    return f"score {score:.2f} (motion {m:.2f}, audio {lo:.2f}, speech {sp:.2f})"


def _plan_highlight(
    a: Analysis, words: Sequence[Word], settings: MontageSettings, var: Variation, duration: float
) -> list[Cut]:
    hz, n = a.sample_hz, a.n
    score = score_samples(a)
    energy = np.clip(_smooth(score, int(3 * hz)), 0.0, 1.0)
    motion, speech = _norm(a.motion), np.asarray(a.speech)
    snap = _Snapper(words, [*a.scene_cuts, *a.audio_peaks], duration, a.rev_peaks)
    taken = np.zeros(n + 1, dtype=bool)

    def mark(start: float, end: float) -> None:
        taken[max(int(start * hz), 0) : min(int(np.ceil(end * hz)), n)] = True

    hook: Cut | None = None
    hook_len = round(settings.hook_s * hz)
    if settings.hook_s > 0 and duration >= settings.hook_s + 2 * SEG_MIN_S and 0 < hook_len <= n:
        means = _window_means(score, hook_len)
        i = _hook_start(means, a.rev_peaks, hz)
        s, _ = snap.start(i / hz, use_points=False)
        e, _ = snap.end((i + hook_len) / hz, use_points=False)
        hook = Cut(
            start=s,
            end=e,
            reason=f"hook: peak moment teased first, {_describe(a, i, i + hook_len, means[i])}",
        )
        mark(s, e)

    body_target = settings.target_duration_s - (hook.out_duration if hook else 0.0)
    min_len = round(SEG_MIN_S * hz)
    body: list[Cut] = []
    total = 0.0
    while total < body_target - 0.25:
        remaining = body_target - total
        free = np.concatenate([[0], np.cumsum(taken[:n])])
        best: tuple[float, int, int] | None = None
        for i in range(n - min_len + 1):
            seg_s = (SEG_MAX_S - (SEG_MAX_S - SEG_MIN_S) * energy[i]) * var.rhythm
            for length in (round(min(seg_s, max(SEG_MIN_S, remaining)) * hz), min_len):
                j = i + length
                if j <= n and free[j] == free[i]:
                    mean = float(score[i:j].mean())
                    if best is None or mean > best[0]:
                        best = (mean, i, j)
                    break
        if best is None:
            break
        mean, i, j = best
        s, note_s = snap.start(i / hz)
        e, note_e = snap.end(j / hz)
        if e - s < 0.5:
            mark(i / hz, j / hz)
            continue
        speed = _ramp(motion, speech, int(s * hz), int(e * hz))
        notes = [_describe(a, i, j, mean), *sorted({note_s, note_e} - {""})]
        if speed > 1.0:
            notes.append(f"speed x{speed:.2f} (high motion, no speech)")
        body.append(Cut(start=s, end=e, speed=speed, reason="; ".join(notes)))
        mark(s, e)
        total += (e - s) / speed

    body = _merge(sorted(body, key=lambda c: c.start))
    hook_out = hook.out_duration if hook else 0.0
    _loop_friendly_end(body, score, snap, hz, settings.min_duration_s - hook_out)
    cuts = ([hook] if hook else []) + body
    if sum(c.out_duration for c in cuts) < settings.min_duration_s:
        cuts = [c.model_copy(update={"speed": 1.0}) for c in cuts]
    return _cap(cuts)


def _cap(cuts: list[Cut]) -> list[Cut]:
    """Hard-limit the output to the Shorts maximum by shortening the tail."""
    out, total = [], 0.0
    for c in cuts:
        room = POLISH_MAX_S - total
        if room <= 0.05:
            break
        if c.out_duration > room:
            c = c.model_copy(update={"end": c.start + room * c.speed})
        out.append(c)
        total += c.out_duration
    return out


def _hook_start(means: np.ndarray, revs: Sequence[float], hz: float) -> int:
    """Window start for the hook: the best rev-led window if it is nearly as good as the best."""
    best = int(np.argmax(means))
    starts = [round((t - REV_HOOK_LEAD_S) * hz) for t in revs]
    starts = [i for i in starts if 0 <= i < len(means)]
    if not starts:
        return best
    rev_best = max(starts, key=lambda i: (means[i], -i))
    return rev_best if means[rev_best] >= REV_HOOK_MIN_RATIO * means[best] else best


def _merge(cuts: list[Cut]) -> list[Cut]:
    """Merge chronologically sorted cuts that overlap after snapping."""
    out: list[Cut] = []
    for c in cuts:
        if out and c.start < out[-1].end:
            prev = out[-1]
            speed = prev.speed if prev.speed == c.speed else 1.0
            out[-1] = prev.model_copy(
                update={
                    "end": max(prev.end, c.end),
                    "speed": speed,
                    "reason": f"{prev.reason} + merged",
                }
            )
        else:
            out.append(c)
    return out


def _loop_friendly_end(
    body: list[Cut], score: np.ndarray, snap: _Snapper, hz: float, min_s: float
) -> None:
    """End the last segment on its energy peak so the Short loops back into the hook."""
    if not body:
        return
    last = body[-1]
    i0, i1 = int((last.start + (last.end - last.start) / 2) * hz), int(last.end * hz)
    if i1 - i0 < 2:
        return
    peak = (i0 + int(np.argmax(score[i0:i1])) + 1) / hz
    end, _ = snap.end(peak, use_points=False)
    shorter = sum(c.out_duration for c in body) - (last.end - end) / last.speed
    if end - last.start >= SEG_MIN_S and end < last.end and shorter >= min_s:
        body[-1] = last.model_copy(
            update={"end": end, "reason": f"{last.reason}; ends on peak for loop"}
        )


def _apply_punch_in(cuts: list[Cut], zoom: float, revs: Sequence[float]) -> list[Cut]:
    """Punch in on cuts that contain a rev; without revs, alternate starting with the opener."""
    has_rev = [any(c.start <= t < c.end for t in revs) for c in cuts]
    out = []
    for k, (c, rev) in enumerate(zip(cuts, has_rev, strict=True)):
        if any(has_rev):
            update = {"zoom": zoom, "reason": f"{c.reason}; punch-in on rev"} if rev else {}
        else:
            update = {"zoom": zoom if k % 2 == 0 else 1.0}
        out.append(c.model_copy(update={"zoom": 1.0, **update}))
    return out


def _assign_crops(cuts: list[Cut], a: Analysis) -> list[Cut]:
    """AutoFlip-style stationary crop per cut, clamped between cuts of the same shot."""
    hz = a.sample_hz
    xs = _smooth(np.asarray(a.subject_x, dtype=np.float64), round(SUBJECT_SMOOTH_S * hz))
    out: list[Cut] = []
    for c in cuts:
        window = xs[int(c.start * hz) : max(int(c.end * hz), int(c.start * hz) + 1)]
        x = float(np.median(window)) if window.size else 0.5
        if out:
            prev = out[-1]
            lo, hi = sorted((prev.start, c.start))
            same_shot = not any(lo < t <= hi for t in a.scene_cuts)
            if same_shot:
                x = min(max(x, prev.crop_x - CROP_MAX_SHIFT), prev.crop_x + CROP_MAX_SHIFT)
        out.append(c.model_copy(update={"crop_x": round(x, 3)}))
    return out


def plan_edit(
    info: MediaInfo,
    analysis: Analysis,
    words: Sequence[Word] = (),
    settings: MontageSettings | None = None,
    music: str | None = None,
) -> EditPlan:
    """Decide the edit for one clip. Deterministic for the same inputs.

    Args:
        info: Probe result of the source.
        analysis: Signals from :func:`analyze`.
        words: Transcript in source time (cuts never split a word).
        settings: Montage settings (defaults if omitted).
        music: Background track path, if any.

    Returns:
        The :class:`EditPlan`; cuts are in output order.
    """
    settings = settings or MontageSettings()
    var = make_variation(info.path, settings)
    duration = info.duration or analysis.n / analysis.sample_hz
    if settings.mode == MontageMode.polish:
        cuts = _plan_polish(analysis, duration)
    else:
        cuts = _plan_highlight(analysis, words, settings, var, duration)
        if settings.punch_in:
            cuts = _apply_punch_in(cuts, var.punch_zoom, analysis.rev_peaks)
    if not cuts:
        cuts = [Cut(start=0.0, end=min(duration, POLISH_MAX_S), reason="fallback: full clip")]
    loop = settings.mode == MontageMode.highlight and len(cuts) > 1
    plan = EditPlan(
        source=info,
        cuts=_assign_crops(cuts, analysis),
        loop_s=LOOP_XFADE_S if loop else 0.0,
        crop_x=_crop_x(analysis, cuts),
        music=music,
        reframe=resolve_reframe(info, analysis, settings),
        variation=var,
    )
    log.info("planned %s: %d cuts, %.1fs, %s", info.path, len(cuts), plan.duration, plan.reframe)
    return plan


def remap_words(words: Sequence[Word], cuts: Sequence[Cut]) -> list[Word]:
    """Map source-time words to output time through the cuts (order, trims and speed).

    A word is kept in a cut when at least half of it lies inside; words in a repeated
    region (e.g. the hook) appear once per occurrence.
    """
    out: list[Word] = []
    offset = 0.0
    for c in cuts:
        for w in words:
            lo, hi = max(w.start, c.start), min(w.end, c.end)
            if hi - lo >= 0.5 * max(w.end - w.start, 1e-6):
                start = offset + (lo - c.start) / c.speed
                out.append(Word(start=start, end=offset + (hi - c.start) / c.speed, text=w.text))
        offset += c.out_duration
    return out
