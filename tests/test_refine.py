import json
from pathlib import Path

import pytest
from fakes import GeminiDailyQuota

from social2shorts.ai.metadata import LLMQuotaExceeded
from social2shorts.ai.refine import RefiningTranscriber, build_prompt, parse_refined
from social2shorts.montage.types import Word

DRAFT = [
    Word(start=1.26, end=1.48, text="ستو"),
    Word(start=1.48, end=1.68, text="بير"),
    Word(start=1.68, end=2.16, text="ادفانتر"),
    Word(start=12.6, end=13.1, text="بوات"),
    Word(start=13.1, end=13.9, text="اوتوماتيك"),
]


def reply(*segments: tuple[int, int, str]) -> str:
    return json.dumps({"segments": [{"first": a, "last": b, "text": t} for a, b, t in segments]})


def test_prompt_numbers_the_draft_words():
    prompt = build_prompt(DRAFT)
    assert "0:ستو 1:بير 2:ادفانتر 3:بوات 4:اوتوماتيك" in prompt
    assert "Darija" in prompt and "Latin script" in prompt and "song lyrics" in prompt


def test_parse_times_corrected_text_from_the_draft():
    words = parse_refined(reply((0, 2, "Super Adventure"), (3, 4, "boîte automatique")), DRAFT)
    assert [w.text for w in words] == ["Super", "Adventure", "boîte", "automatique"]
    assert words[0].start == 1.26 and words[1].end == pytest.approx(2.16)  # span of draft 0..2
    assert words[2].start == 12.6 and words[3].end == pytest.approx(13.9)  # span of draft 3..4
    assert words[0].end == pytest.approx(words[1].start)  # spread inside the span, in order


def test_parse_skips_placeholders_bad_ranges_and_overlaps():
    raw = reply((0, 1, "[...]"), (2, 2, "ok"), (1, 3, "overlap"), (4, 9, "out of range"))
    assert [w.text for w in parse_refined(raw, DRAFT)] == ["ok"]


@pytest.mark.parametrize("raw", ["not json", "{}", reply((3, 1, "x")), reply((0, 9, "x"))])
def test_parse_rejects_unusable_replies(raw):
    with pytest.raises(ValueError):
        parse_refined(raw, DRAFT)


class Base:
    def __init__(self, words):
        self.words = words

    def transcribe(self, path: Path) -> list[Word]:
        return list(self.words)


class Refiner:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0

    def refine(self, path: Path, draft) -> list[Word]:
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def test_refining_transcriber_returns_refined_words():
    fixed = [Word(start=1.26, end=2.16, text="Super Adventure")]
    assert RefiningTranscriber(Base(DRAFT), Refiner(fixed)).transcribe(Path("v.mp4")) == fixed


def test_refining_transcriber_falls_back_to_draft_on_error():
    out = RefiningTranscriber(Base(DRAFT), Refiner(error=RuntimeError("429"))).transcribe(Path("v"))
    assert out == DRAFT


@pytest.mark.parametrize(("key", "model", "refined"), [("k", "m", True), ("k", "", False),
                                                       (None, "m", False)])  # fmt: skip
def test_pipeline_refines_only_with_a_key_and_a_model(monkeypatch, key, model, refined):
    from social2shorts.ai.transcribe import WhisperTranscriber
    from social2shorts.config import Settings
    from social2shorts.pipeline import PipelineDeps

    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    if key:
        monkeypatch.setenv("GEMINI_API_KEY", key)
    s = Settings(transcript_refine_model=model, whisper_language="ar", whisper_vad=False)
    tr = PipelineDeps.default(s).transcriber
    assert isinstance(tr, RefiningTranscriber) is refined
    base = tr.base if refined else tr
    assert isinstance(base, WhisperTranscriber) and base.language == "ar" and base.vad is False


def test_refining_transcriber_skips_silent_clips():
    refiner = Refiner([Word(start=0, end=1, text="x")])
    assert RefiningTranscriber(Base([]), refiner).transcribe(Path("v.mp4")) == []
    assert refiner.calls == 0


@pytest.mark.parametrize("raw", ['{"segments": []}', reply((0, 2, "[music]"))])
def test_parse_no_speech_gives_no_subtitles(raw):
    assert parse_refined(raw, DRAFT) == []  # music only: the draft's words were invented


def test_refining_transcriber_stops_on_spent_daily_quota():
    with pytest.raises(LLMQuotaExceeded):
        RefiningTranscriber(Base(DRAFT), Refiner(error=GeminiDailyQuota())).transcribe(Path("v"))


def test_refining_transcriber_falls_back_on_per_minute_limit():
    minute = RuntimeError("429 RESOURCE_EXHAUSTED GenerateRequestsPerMinutePerProjectPerModel")
    minute.code = 429
    assert RefiningTranscriber(Base(DRAFT), Refiner(error=minute)).transcribe(Path("v")) == DRAFT
