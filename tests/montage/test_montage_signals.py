"""Probe parsing and analysis signal extraction (pure parts)."""

from __future__ import annotations

import numpy as np
import pytest

from social2shorts.montage import MontageError, Word
from social2shorts.montage.analyze import (
    REV_PEAK_GAP_S,
    audio_onsets,
    loudness_envelope,
    rev_envelope,
    rev_peaks,
    speech_density,
    video_features,
)
from social2shorts.montage.probe import parse_probe


def _probe_json(**video) -> dict:
    stream = {"codec_type": "video", "width": 1920, "height": 1080, "avg_frame_rate": "30000/1001"}
    stream.update(video)
    return {"streams": [stream, {"codec_type": "audio"}], "format": {"duration": "12.5"}}


def test_parse_probe_basic() -> None:
    info = parse_probe(_probe_json(), "a.mp4")
    assert (info.width, info.height, info.has_audio, info.rotation) == (1920, 1080, True, 0)
    assert info.fps == pytest.approx(29.97, abs=0.01) and info.duration == 12.5


@pytest.mark.parametrize(
    "extra",
    [
        {"side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}]},
        {"tags": {"rotate": "90"}},
    ],
)
def test_parse_probe_rotation_swaps_dims(extra: dict) -> None:
    info = parse_probe(_probe_json(**extra), "a.mp4")
    assert (info.width, info.height, info.rotation) == (1080, 1920, 90)


def test_parse_probe_requires_video() -> None:
    with pytest.raises(MontageError):
        parse_probe({"streams": [{"codec_type": "audio"}]}, "a.m4a")


def test_video_features_motion_cut_and_subject() -> None:
    frames = np.zeros((10, 36, 64), dtype=np.uint8)
    for i in range(5):  # small moving square on the right third
        frames[i, 10:20, 44 + i : 50 + i] = 255
    frames[5:] = 200  # hard cut to a bright frame
    vf = video_features(frames)
    assert len(vf["motion"]) == 10 and vf["motion"][0] == 0.0
    assert vf["cut_idx"] == [5]
    assert vf["subject_x"][2] > 0.6
    assert vf["brightness"][6] == pytest.approx(200 / 255)


def test_loudness_and_onsets() -> None:
    rate, hz = 8000, 5.0
    pcm = np.concatenate([np.zeros(rate), 0.5 * np.sin(np.arange(rate) / 3.0)]).astype(np.float32)
    loud = loudness_envelope(pcm, rate, 10, hz)
    assert loud[0] == 0.0 and loud[7] > 0.8
    assert audio_onsets(loud, hz) == [1.0]


def test_speech_density() -> None:
    dens = speech_density([Word(start=0.0, end=0.3, text="a")], 3, 5.0)
    assert dens == pytest.approx([1.0, 0.5, 0.0])


def test_rev_envelope_finds_throttle_blip() -> None:
    rate, hz = 8000, 5.0
    t = np.arange(10 * rate) / rate
    rng = np.random.default_rng(0)
    freq = 120 + 500 * np.clip(t - 6.0, 0, 1) * (t < 7.0)  # pitch sweep 6-7 s = rev
    pcm = 0.3 * np.sin(2 * np.pi * np.cumsum(freq) / rate) + 0.02 * rng.standard_normal(t.size)
    rev = rev_envelope(pcm.astype(np.float32), rate, 50, hz)
    assert len(rev) == 50 and all(0.0 <= v <= 1.0 for v in rev)
    assert 30 <= int(np.argmax(rev)) <= 36
    assert max(rev[:25]) < 0.5  # steady engine + noise stays low
    peaks = rev_peaks(rev, hz)
    assert peaks and all(6.0 <= p <= 7.2 for p in peaks)


def test_rev_peaks_spacing_and_threshold() -> None:
    rev = [0.0] * 50
    rev[10], rev[12], rev[30], rev[40] = 0.9, 0.8, 0.7, 0.3
    assert rev_peaks(rev, 5.0) == [2.0, 6.0]  # 12 is too close to 10; 40 is too weak
    assert REV_PEAK_GAP_S * 5.0 > 2


def test_rev_envelope_silence() -> None:
    assert rev_envelope(np.zeros(8000, dtype=np.float32), 8000, 5, 5.0) == [0.0] * 5


def test_parse_probe_colour_fields() -> None:
    hlg = _probe_json(
        pix_fmt="yuv420p10le",
        color_transfer="arib-std-b67",
        color_primaries="bt2020",
        color_space="bt2020nc",
    )
    info = parse_probe(hlg, "hdr.mp4")
    assert (info.pix_fmt, info.color_primaries, info.color_space) == (
        "yuv420p10le",
        "bt2020",
        "bt2020nc",
    )
    assert info.is_hdr
    assert not parse_probe(_probe_json(color_transfer="bt709"), "sdr.mp4").is_hdr
    assert parse_probe(_probe_json(color_transfer="smpte2084"), "pq.mp4").is_hdr
