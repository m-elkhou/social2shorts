import json
from types import SimpleNamespace

import pytest
from fakes import FakeLLM

from social2shorts.ai.metadata import (
    BANNED,
    CTAS,
    MAX_HOOK,
    build_prompt,
    fallback_meta,
    generate_metadata,
    humanize,
    sanitize_meta,
)
from social2shorts.ai.transcribe import WhisperTranscriber
from social2shorts.montage.types import Word


def test_humanize_strips_ai_tells():
    text = humanize(
        "UNLEASH the ultimate EPIC JOURNEY!!! 🔥🔥🔥🏍️ Dive into this breathtaking, "
        "adrenaline-fueled ride??"
    )
    lowered = text.lower()
    for phrase in (
        "unleash",
        "ultimate",
        "epic journey",
        "dive into",
        "breathtaking",
        "adrenaline",
    ):
        assert phrase not in lowered
    assert "!!" not in text and "??" not in text and "  " not in text
    assert sum(ch in "🔥🏍" for ch in text) == 2
    assert "UNLEASH" not in text and "Epic" not in text


def test_humanize_keeps_short_acronyms_and_normal_text():
    assert humanize("Night ride on my KTM, 120 km") == "Night ride on my KTM, 120 km"


def test_sanitize_meta_limits_and_noise():
    m = sanitize_meta(
        {"title": "@bob " + "x" * 200 + " #fyp TikTok", "description": "Ride.",
         "tags": ["#FYP", "Night Ride", "instagram"] + [f"t{i}" for i in range(30)],
         "hook": "This corner was way too close for comfort honestly"},
        caption="#moto #casablanca", key="k",
    )  # fmt: skip
    assert len(m.title) <= 100 and "@" not in m.title and "tiktok" not in m.title.lower()
    assert "fyp" not in m.tags and "instagram" not in m.tags and m.tags[0] == "nightride"
    assert len(m.tags) <= 15 and len(m.hook) <= MAX_HOOK


def test_sanitize_requires_title():
    with pytest.raises(ValueError):
        sanitize_meta({"title": "  #fyp "}, caption="", key="k")


def test_descriptions_vary_across_items():
    descriptions = {
        sanitize_meta(
            {"title": "t", "description": "Ride."}, caption="", key=f"raw-{i}"
        ).description
        for i in range(40)
    }
    assert len(descriptions) >= 3  # rotating CTA lines, sometimes none
    ctas = [c for c in CTAS["en"] if c]
    assert all(sum(c in d for c in ctas) <= 1 for d in descriptions)


def test_generate_metadata_with_llm():
    m = generate_metadata(FakeLLM(), "cap", "bonjour", key="A")
    assert m.title == "Late ride by the sea" and m.hook == "Wait for the turn"
    assert m.tags == ["sea"]


@pytest.mark.parametrize("llm", [FakeLLM(error=RuntimeError("429")), FakeLLM(reply="not json"),
                                 FakeLLM(reply=json.dumps({"title": ""})), None])  # fmt: skip
def test_generate_metadata_falls_back(llm):
    m = generate_metadata(llm, "@me Sunset run on the coast #moto #fyp", "", key="A")
    assert m.title == "Sunset run on the coast" and m.tags == ["moto"]


def test_fallback_for_raw_footage_is_varied():
    titles = {fallback_meta("", key=f"raw-{i}").title for i in range(30)}
    assert len(titles) >= 4


def test_prompt_asks_for_human_voice():
    p = build_prompt("Sunday ride", "on y va", "fr")
    assert "first person" in p and "Sunday ride" in p and "on y va" in p and "fr" in p
    assert '"hook"' in p and all(w in p for w in ("unleash", "breathtaking"))
    assert len(BANNED) >= 7


def test_transcriber_returns_words():
    seg = SimpleNamespace(words=[SimpleNamespace(start=0, end=0.4, word=" Salut"),
                                 SimpleNamespace(start=0.4, end=0.5, word=" ")])  # fmt: skip
    seen = {}

    class Model:
        def transcribe(self, path, **kw):
            seen.update(kw)
            return iter([seg, SimpleNamespace(words=None)]), None

    words = WhisperTranscriber(model=Model(), language="fr").transcribe("x.mp4")
    assert words == [Word(start=0.0, end=0.4, text="Salut")]
    assert seen["word_timestamps"] is True and seen["language"] == "fr"
    assert seen["vad_filter"] is True  # default: skip silence

    WhisperTranscriber(model=Model(), vad=False).transcribe("x.mp4")
    assert seen["vad_filter"] is False  # Darija model: VAD cut quiet speech
