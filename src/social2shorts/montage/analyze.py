"""Cheap signal analysis of a clip: motion, scene cuts, loudness, speech and subject position."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Sequence

import numpy as np
from pydantic import BaseModel

from .errors import MontageError
from .probe import probe
from .types import MediaInfo, Word

log = logging.getLogger(__name__)

SAMPLE_HZ = 5.0  # analysis frames per second
ANALYSIS_WIDTH = 96  # frames are downscaled to this width (grayscale)
AUDIO_RATE = 8000  # mono PCM rate used for the loudness envelope
SCENE_HIST_MIN = 0.35  # histogram distance (0..1) above which a frame change is a cut
SCENE_MEDIAN_RATIO = 3.0  # ...and it must also stand out from the clip's typical change
SILENCE_DB = -60.0  # dBFS mapped to loudness 0.0 (0 dBFS maps to 1.0)
SUBJECT_MOTION_WEIGHT = 0.7  # subject centre: frame-difference energy vs edge energy
REV_BAND_HZ = (80.0, 2000.0)  # engine fundamental + low harmonics; skips rumble and wind hiss
REV_FRAME = 256  # FFT frame (32 ms at 8 kHz): short enough to follow a throttle blip
REV_HOP = 128  # 50 % overlap
REV_PEAK_MIN = 0.6  # normalised rev level a peak must reach
REV_PEAK_GAP_S = 1.5  # minimum spacing between rev peaks (one rev = one event)


class Analysis(BaseModel):
    """Per-sample signals (one value every ``1 / sample_hz`` seconds) plus clip-level facts."""

    sample_hz: float
    duration: float
    motion: list[float]  # mean absolute frame difference, 0..1
    brightness: list[float]  # mean luma, 0..1
    loudness: list[float]  # RMS loudness, 0 (silence) .. 1 (full scale)
    speech: list[float]  # fraction of each sample covered by spoken words
    subject_x: list[float]  # horizontal centre of visual energy, 0..1
    subject_spread: float = 0.25  # horizontal std of visual energy (0 = point, 0.29 = uniform)
    scene_cuts: list[float] = []  # source timestamps of detected hard cuts
    audio_peaks: list[float] = []  # source timestamps of loudness onsets (revs, beats)
    rev: list[float] = []  # engine rev / acceleration strength (in-band spectral flux), 0..1
    rev_peaks: list[float] = []  # source timestamps of the strongest revs

    @property
    def n(self) -> int:
        """Number of samples."""
        return len(self.motion)


def _run(cmd: list[str]) -> bytes:
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        tail = proc.stderr.decode(errors="replace").strip()[-500:]
        raise MontageError(f"analysis decode failed: {tail}")
    return proc.stdout


def decode_gray_frames(src: str, info: MediaInfo, sample_hz: float) -> np.ndarray:
    """Decode downscaled grayscale frames as a ``(n, h, w)`` uint8 array."""
    w = ANALYSIS_WIDTH
    h = max(2, round(w * info.height / max(info.width, 1) / 2) * 2)
    cmd = [
        "ffmpeg", "-v", "error", "-nostdin", "-i", src, "-an", "-sn",
        "-vf", f"fps={sample_hz},scale={w}:{h},format=gray",
        "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]  # fmt: skip
    raw = np.frombuffer(_run(cmd), dtype=np.uint8)
    n = raw.size // (w * h)
    return raw[: n * w * h].reshape(n, h, w)


def decode_audio(src: str) -> np.ndarray:
    """Decode mono PCM at :data:`AUDIO_RATE` as float32 in -1..1."""
    cmd = [
        "ffmpeg", "-v", "error", "-nostdin", "-i", src, "-vn", "-sn",
        "-ac", "1", "-ar", str(AUDIO_RATE), "-f", "s16le", "-",
    ]  # fmt: skip
    return np.frombuffer(_run(cmd), dtype=np.int16).astype(np.float32) / 32768.0


def video_features(frames: np.ndarray) -> dict:
    """Motion, brightness, scene cuts (sample indices) and subject position from frames."""
    n = len(frames)
    if n == 0:
        return {"motion": [], "brightness": [], "cut_idx": [], "subject_x": [], "spread": 0.25}
    f = frames.astype(np.float32) / 255.0
    diff = np.abs(np.diff(f, axis=0))  # (n-1, h, w)
    motion = np.concatenate([[0.0], diff.mean(axis=(1, 2))])
    brightness = f.mean(axis=(1, 2))

    hists = np.stack([np.histogram(fr, bins=32, range=(0, 256))[0] for fr in frames])
    hists = hists / max(frames[0].size, 1)
    hdist = np.concatenate([[0.0], 0.5 * np.abs(np.diff(hists, axis=0)).sum(axis=1)])
    typical = float(np.median(hdist)) + 1e-3
    cut_idx = [
        i
        for i in range(1, n)
        if hdist[i] > SCENE_HIST_MIN and hdist[i] > SCENE_MEDIAN_RATIO * typical
    ]

    edges = np.abs(np.diff(f, axis=2)).mean(axis=1)  # (n, w-1) column edge energy
    motion_cols = np.concatenate([np.zeros((1, *f.shape[1:])), diff]).mean(axis=1)[:, 1:]
    energy = SUBJECT_MOTION_WEIGHT * motion_cols + (1 - SUBJECT_MOTION_WEIGHT) * edges
    xs = (np.arange(energy.shape[1]) + 0.5) / energy.shape[1]
    totals = energy.sum(axis=1) + 1e-9
    subject_x = (energy * xs).sum(axis=1) / totals
    profile = energy.sum(axis=0) + 1e-9
    mean_x = float((profile * xs).sum() / profile.sum())
    spread = float(np.sqrt((profile * (xs - mean_x) ** 2).sum() / profile.sum()))
    return {
        "motion": motion.tolist(),
        "brightness": brightness.tolist(),
        "cut_idx": cut_idx,
        "subject_x": subject_x.tolist(),
        "spread": spread,
    }


def loudness_envelope(pcm: np.ndarray, rate: int, n: int, sample_hz: float) -> list[float]:
    """RMS loudness per analysis sample, mapped from dBFS to 0..1."""
    hop = int(rate / sample_hz)
    out = np.zeros(n, dtype=np.float32)
    for i in range(n):
        chunk = pcm[i * hop : (i + 1) * hop]
        if chunk.size:
            db = 20 * np.log10(np.sqrt(np.mean(chunk**2)) + 1e-9)
            out[i] = np.clip((db - SILENCE_DB) / -SILENCE_DB, 0.0, 1.0)
    return out.tolist()


def audio_onsets(
    loudness: Sequence[float], sample_hz: float, min_rise: float = 0.08
) -> list[float]:
    """Timestamps where loudness jumps up sharply (local maxima of its rise)."""
    rise = np.diff(np.asarray(loudness, dtype=np.float32), prepend=0.0)
    return [
        i / sample_hz
        for i in range(1, len(rise) - 1)
        if rise[i] >= min_rise and rise[i] >= rise[i - 1] and rise[i] > rise[i + 1]
    ]


def rev_envelope(pcm: np.ndarray, rate: int, n: int, sample_hz: float) -> list[float]:
    """Per-sample "rev" strength: positive spectral flux in :data:`REV_BAND_HZ`, 0..1 per clip.

    A throttle blip makes the engine harmonics jump up in level and pitch, which shows up as
    a burst of positive flux; steady cruising and constant wind stay near the clip's floor.
    """
    if pcm.size < REV_FRAME or n == 0:
        return [0.0] * n
    count = (pcm.size - REV_FRAME) // REV_HOP + 1
    idx = np.arange(REV_FRAME)[None, :] + REV_HOP * np.arange(count)[:, None]
    spec = np.abs(np.fft.rfft(pcm[idx] * np.hanning(REV_FRAME).astype(np.float32), axis=1))
    freqs = np.fft.rfftfreq(REV_FRAME, 1.0 / rate)
    band = spec[:, (freqs >= REV_BAND_HZ[0]) & (freqs <= REV_BAND_HZ[1])]
    flux = np.concatenate([[0.0], np.maximum(np.diff(band, axis=0), 0).sum(axis=1)])
    owner = (np.arange(count) * REV_HOP / rate * sample_hz).astype(int)  # sample of each frame
    sums = np.bincount(owner, weights=flux, minlength=n)[:n]
    counts = np.maximum(np.bincount(owner, minlength=n)[:n], 1)
    per_sample = sums / counts
    lo, hi = np.percentile(per_sample, 10), np.percentile(per_sample, 95)
    # Floor the range at half the p95 level so steady noise (wind) doesn't stretch to 0..1.
    return np.clip((per_sample - lo) / max(hi - lo, 0.5 * hi, 1e-6), 0.0, 1.0).tolist()


def rev_peaks(rev: Sequence[float], sample_hz: float) -> list[float]:
    """Timestamps of the strongest rev moments, at least :data:`REV_PEAK_GAP_S` apart."""
    arr = np.asarray(rev, dtype=np.float64)
    gap = int(REV_PEAK_GAP_S * sample_hz)
    picked: list[int] = []
    for i in np.argsort(-arr, kind="stable"):
        if arr[i] < REV_PEAK_MIN:
            break
        if all(abs(i - j) >= gap for j in picked):
            picked.append(int(i))
    return sorted(i / sample_hz for i in picked)


def speech_density(words: Sequence[Word], n: int, sample_hz: float) -> list[float]:
    """Fraction of each sample interval covered by words."""
    out = np.zeros(n, dtype=np.float32)
    step = 1.0 / sample_hz
    for w in words:
        first, last = int(w.start * sample_hz), int(np.ceil(w.end * sample_hz))
        for i in range(max(first, 0), min(last, n)):
            lo, hi = i * step, (i + 1) * step
            out[i] += max(0.0, min(hi, w.end) - max(lo, w.start)) / step
    return np.clip(out, 0.0, 1.0).tolist()


def analyze(
    src: str,
    info: MediaInfo | None = None,
    words: Sequence[Word] = (),
    sample_hz: float = SAMPLE_HZ,
) -> Analysis:
    """Decode a low-rate, low-resolution version of ``src`` and extract editing signals.

    Args:
        src: Source clip path.
        info: Probe result (probed if omitted).
        words: Transcript words in source time, for speech density.
        sample_hz: Analysis rate.

    Returns:
        The :class:`Analysis` with aligned per-sample lists.
    """
    info = info or probe(src)
    vf = video_features(decode_gray_frames(src, info, sample_hz))
    n = len(vf["motion"])
    pcm = decode_audio(src) if info.has_audio else np.zeros(0, dtype=np.float32)
    loud = loudness_envelope(pcm, AUDIO_RATE, n, sample_hz)
    rev = rev_envelope(pcm, AUDIO_RATE, n, sample_hz)
    log.debug("analysed %s: %d samples, %d scene cuts", src, n, len(vf["cut_idx"]))
    return Analysis(
        sample_hz=sample_hz,
        duration=info.duration,
        motion=vf["motion"],
        brightness=vf["brightness"],
        loudness=loud,
        speech=speech_density(words, n, sample_hz),
        subject_x=vf["subject_x"],
        subject_spread=vf["spread"],
        scene_cuts=[i / sample_hz for i in vf["cut_idx"]],
        audio_peaks=audio_onsets(loud, sample_hz),
        rev=rev,
        rev_peaks=rev_peaks(rev, sample_hz),
    )
