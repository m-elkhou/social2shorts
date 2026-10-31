"""ASS generation (timing, karaoke tags, escaping, safe-zone layout, hook card) and SRT."""

from __future__ import annotations

import re

import pytest

from social2shorts.montage import CaptionStyle, MontageSettings, Variation, Word, build_ass
from social2shorts.montage.captions import ass_time, build_srt, escape_ass, safe_box, srt_time

VAR = Variation(accent="&H0000E5FF", caption_y=0.66, hook_y=0.13)
WORDS = [
    Word(start=0.5, end=0.9, text=" Salut"),
    Word(start=0.95, end=1.3, text="les"),
    Word(start=1.35, end=1.8, text="motards!"),
    Word(start=3.0, end=3.4, text="on"),
    Word(start=3.45, end=3.9, text="roule"),
]


def _events(doc: str) -> list[str]:
    return [line for line in doc.splitlines() if line.startswith("Dialogue:")]


def _style(doc: str, name: str) -> list[str]:
    line = next(x for x in doc.splitlines() if x.startswith(f"Style: {name},"))
    return line.split(":", 1)[1].split(",")


def test_ass_time_format() -> None:
    assert ass_time(0) == "0:00:00.00"
    assert ass_time(3661.239) == "1:01:01.24"
    assert ass_time(-1) == "0:00:00.00"


def test_escape_ass() -> None:
    assert escape_ass("a{\\b1}c\nd") == "a(\uff3cb1)c d"


def test_karaoke_highlight_and_pop() -> None:
    doc = build_ass(WORDS, MontageSettings(), duration=10, variation=VAR)
    events = _events(doc)
    assert len(events) == len(WORDS)  # one event per spoken word
    assert all(r"{\c&H00E5FF&}" in e for e in events)
    assert events[0].count(r"\t(") == 1 and r"\t(" not in events[1]  # pop on cue entrance only
    assert "SALUT" in events[0] and "{\\c&H00E5FF&}LES" in events[1]
    assert events[3].split(",")[1] == "0:00:03.00"  # pause starts a new cue
    assert "MOTARDS!" not in events[3]
    assert all(len(re.sub(r"\{[^}]*\}", "", e.split(",,", 2)[-1]).split()) <= 3 for e in events)


def test_safe_zone_position() -> None:
    s = MontageSettings()
    doc = build_ass(WORDS, s, duration=10, variation=VAR)
    cap = _style(doc, "Caption")
    alignment, margin_r, margin_v = int(cap[18]), int(cap[20]), int(cap[21])
    assert alignment == 2
    baseline = 1 - margin_v / s.height
    assert 0.62 <= baseline <= 0.70
    assert margin_r >= int(s.width * 0.12)  # clear of the right-side Shorts buttons
    assert cap[1].strip() == s.caption_font and int(cap[2]) == s.caption_size


def test_hook_card_and_no_captions() -> None:
    s = MontageSettings(captions=CaptionStyle.none)
    doc = build_ass(WORDS, s, duration=10, hook_text="200 km/h {la nuit}", variation=VAR)
    events = _events(doc)
    assert len(events) == 1
    assert events[0].startswith("Dialogue: 0,0:00:00.00,0:00:02.50,Hook,")
    assert "(la nuit)" in events[0] and r"\fad(" in events[0]
    hook = _style(doc, "Hook")
    assert int(hook[18]) == 8 and int(hook[21]) < s.height / 3  # top third


def test_minimal_phrases_and_clipping() -> None:
    s = MontageSettings(captions=CaptionStyle.minimal)
    events = _events(build_ass(WORDS, s, duration=3.6, variation=VAR))
    assert [e.split(",", 9)[-1] for e in events] == ["Salut les motards!", "on roule"]
    assert events[-1].split(",")[2] == "0:00:03.60"  # clipped to the output duration


@pytest.mark.parametrize(("w", "h"), [(1080, 1920), (720, 1280), (270, 480)])
@pytest.mark.parametrize("var", [Variation(caption_y=0.69, hook_y=0.10), VAR])
def test_text_stays_inside_safe_box(w: int, h: int, var: Variation) -> None:
    s = MontageSettings(width=w, height=h)
    doc = build_ass(WORDS, s, duration=10, hook_text="hook", variation=var)
    left, top, right, bottom = safe_box(w, h)
    assert abs((right - left) - 900 * w / 1080) <= 1
    assert abs((bottom - top) - 1400 * h / 1920) <= 1
    for name in ("Caption", "Hook"):
        st = _style(doc, name)
        size, ml, mr, mv = int(st[2]), int(st[19]), int(st[20]), int(st[21])
        assert ml >= left and w - mr <= right
        if name == "Caption":  # bottom-aligned
            assert h - mv <= bottom and h - mv - size >= top
        else:  # top-aligned hook card
            assert mv >= top and mv + size <= h / 3


def test_arabic_words_escaped_and_in_order() -> None:
    words = [
        Word(start=0.0, end=0.4, text="مرحبا"),
        Word(start=0.45, end=0.8, text="{بكم}"),
        Word(start=0.85, end=1.2, text="يا\\شباب"),
    ]
    minimal = MontageSettings(captions=CaptionStyle.minimal)
    phrase = _events(build_ass(words, minimal, duration=5, variation=VAR))[0].split(",,", 2)[-1]
    assert phrase.split() == ["مرحبا", "(بكم)", "يا\uff3cشباب"]  # logical order, escaped
    karaoke = _events(build_ass(words, MontageSettings(), duration=5, variation=VAR))
    assert "{\\c&H00E5FF&}(بكم)" in karaoke[1]
    assert re.sub(r"\{[^}]*\}", "", karaoke[1].split(",,", 2)[-1]).split() == ["مرحبا", "(بكم)"]


# ------------------------------------------------------------------------- SRT
def test_srt_phrase_cues_last_at_least_a_second():
    assert build_srt(WORDS).splitlines() == [
        "1", "00:00:00,500 --> 00:00:01,800", "Salut les motards!", "",
        "2", "00:00:03,000 --> 00:00:04,000", "on roule",
    ]  # fmt: skip


def test_srt_cue_never_runs_into_the_next_one():
    words = [Word(start=0.0, end=0.2, text="vas-y."), Word(start=0.5, end=0.9, text="go")]
    assert build_srt(words).splitlines()[:3] == ["1", "00:00:00,000 --> 00:00:00,500", "vas-y."]


def test_srt_splits_long_phrases_and_skips_blank_words():
    words = [Word(start=i * 0.3, end=i * 0.3 + 0.25, text="motorcycle") for i in range(10)]
    lines = build_srt([*words, Word(start=3.1, end=3.2, text=" ")]).splitlines()
    texts = [line for line in lines if line.startswith("motorcycle")]
    assert [len(t.split()) for t in texts] == [7, 3]  # 84 characters (two lines) max per cue
    assert build_srt([]) == ""


def test_srt_time_format():
    assert srt_time(3725.5) == "01:02:05,500" and srt_time(-1) == "00:00:00,000"
