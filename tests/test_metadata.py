import json
from types import SimpleNamespace

import pytest
from fakes import FakeLLM

from social2shorts.ai.metadata import (
    BANNED,
    CTAS,
    MAX_HOOK,
    LLMQuotaExceeded,
    LLMUnavailable,
    MetadataRejected,
    build_prompt,
    check_meta,
    content_tags,
    fallback_meta,
    gemini_http_options,
    generate_metadata,
    humanize,
    sanitize_meta,
)
from social2shorts.ai.transcribe import WhisperTranscriber, drop_hallucinations
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


@pytest.mark.parametrize("llm", [FakeLLM(reply="not json"),
                                 FakeLLM(reply=json.dumps({"title": ""})), None])  # fmt: skip
def test_generate_metadata_falls_back(llm):
    m = generate_metadata(llm, "@me Sunset run on the coast #moto #fyp", "", key="A")
    assert m.title == "Sunset run on the coast" and m.tags == ["moto"]


def test_a_model_that_cannot_answer_stops_instead_of_publishing_a_template():
    with pytest.raises(LLMUnavailable):
        generate_metadata(FakeLLM(error=RuntimeError("503 UNAVAILABLE")), "cap", "", key="A")


def test_fallback_for_raw_footage_is_varied():
    titles = {fallback_meta("", key=f"raw-{i}").title for i in range(30)}
    assert len(titles) >= 4


def test_prompt_asks_for_french_and_for_what_the_frames_show():
    p = build_prompt("Sunday ride", "on y va", "fr", frames=6)
    assert "Sunday ride" in p and "on y va" in p and "French ONLY" in p
    assert "6 frames" in p and "ground truth" in p and '"riders"' in p and '"onscreen_text"' in p
    assert '"hook"' in p and all(w in p for w in ("unleash", "breathtaking"))
    assert len(BANNED) >= 7


def test_prompt_without_frames_forbids_describing_the_picture():
    assert "no frames" in build_prompt("", "", "fr")


def test_language_other_than_fr_or_en_means_french():
    assert "French ONLY" in build_prompt("", "", "ar")


def test_transcriber_returns_words():
    seg = SimpleNamespace(words=[SimpleNamespace(start=0, end=0.4, word=" Salut"),
                                 SimpleNamespace(start=0.4, end=0.5, word=" "),
                                 SimpleNamespace(start=0.5, end=0.8, word="tout"),
                                 SimpleNamespace(start=0.8, end=1.0, word="le"),
                                 SimpleNamespace(start=1.0, end=1.2, word="monde")])  # fmt: skip
    seen = {}

    class Model:
        def transcribe(self, path, **kw):
            seen.update(kw)
            return iter([seg, SimpleNamespace(words=None)]), None

    words = WhisperTranscriber(model=Model(), language="fr").transcribe("x.mp4")
    assert [w.text for w in words] == ["Salut", "tout", "le", "monde"]
    assert seen["word_timestamps"] is True and seen["language"] == "fr"
    assert seen["vad_filter"] is True  # default: skip silence

    WhisperTranscriber(model=Model(), vad=False).transcribe("x.mp4")
    assert seen["vad_filter"] is False  # Darija model: VAD cut quiet speech


def test_gemini_retries_busy_server_but_not_spent_quota():
    retry = gemini_http_options().retry_options
    assert retry.attempts > 1 and 503 in retry.http_status_codes
    assert 429 not in retry.http_status_codes  # a spent daily quota stops the run at once
    assert 0 < gemini_http_options().timeout <= 300_000  # an overloaded model must not hang a run


# --- speech-model hallucinations (2026-11-03: "subscribe to the channel" on muted videos) -----
def _words(text: str) -> list[Word]:
    return [Word(start=i, end=i + 0.5, text=t) for i, t in enumerate(text.split())]


@pytest.mark.parametrize("text", [
    "اشتركوا في القناة", "شتركوا في القناة", "(موسيقى) موسيقى", "موسيقى موسيقى",
    "وي وي وي تترى تترى تترى تترى", "Thanks for watching!", "محمد ام",
    "شتركوا في القناة موسيقى",
])  # fmt: skip
def test_hallucinations_and_loops_give_no_words(text):
    assert drop_hallucinations(_words(text)) == []


@pytest.mark.parametrize("text", [
    "هاني عاوتاني على Super Adventure S في 1390 البيض بالبيض",
    "ديش خدامي زماني غادي كيطيح وقع لي الوشواش",
    "Eh oui boîte automatique sensation bizarre",
])  # fmt: skip
def test_real_speech_is_kept(text):
    assert drop_hallucinations(_words(text)) == _words(text)


# --- checks on the model's answer ------------------------------------------------------------
def _answer(**kw) -> dict:
    base = {"title": "Sortie en groupe dans les Cévennes", "description": "Des motos en file.",
            "tags": ["cévennes"], "hook": "Les virages d'automne", "riders": "group",
            "onscreen_text": False}  # fmt: skip
    return base | kw


def test_a_good_answer_passes():
    check_meta(_answer())


@pytest.mark.parametrize("bad", [
    {"title": "لفة سريعة بالدراجة"}, {"description": "خرجة بالموطو"}, {"hook": "ركز في حياتك"},
    {"tags": ["موطو"]}, {"riders": "everyone"},
    {"title": "Balade solo dans les Cévennes"}, {"description": "Je roule seul."},
    {"hook": "Solo ride", "riders": "duo"}, {"hook": "En solo sur la route", "riders": "solo"},
])  # fmt: skip
def test_answers_in_arabic_or_solo_over_a_group_are_rejected(bad):
    with pytest.raises(MetadataRejected):
        check_meta(_answer(**bad))


def test_solo_is_only_allowed_when_the_owner_says_it():
    answer = _answer(riders="solo", title="Balade solo au coucher du soleil")
    with pytest.raises(MetadataRejected):
        check_meta(answer)  # a POV camera does not show who rides out of frame
    check_meta(answer, caption="Ma balade solo du dimanche")


def test_a_rejected_answer_is_asked_again_with_the_reason_and_the_frames():
    class Twice(FakeLLM):
        def complete_json(self, prompt, images=()):
            super().complete_json(prompt, images)
            return json.dumps(_answer(title="خرجة بالموطو") if len(self.prompts) == 1
                              else _answer(title="Sortie en groupe"))  # fmt: skip

    llm = Twice()
    m = generate_metadata(llm, "", "", key="k", images=[b"jpeg1", b"jpeg2"])
    assert m.title == "Sortie en groupe" and len(llm.prompts) == 2
    assert "Arabic script" in llm.prompts[1] and llm.images == [(b"jpeg1", b"jpeg2")] * 2
    assert "2 frames" in llm.prompts[0]


def test_two_rejected_answers_give_the_neutral_fallback_never_the_rejected_text():
    llm = FakeLLM(reply=json.dumps(_answer(title="Balade solo", riders="group")))
    m = generate_metadata(llm, "", "", key="raw-1", hint="VID_20260808_070000")
    assert len(llm.prompts) == 2 and "solo" not in m.title.lower()


def test_no_hook_when_the_clip_already_has_text():
    m = sanitize_meta(_answer(onscreen_text=True), caption="", key="k")
    assert m.hook == ""
    assert sanitize_meta(_answer(), caption="", key="k").hook == "Les virages d'automne"


def test_fallback_claims_nothing_about_riders_sound_or_place():
    claims = ("solo", "seul", "son d'origine", "musique", "moteur", "guidon", "pov", "avenue")
    for i in range(200):
        m = fallback_meta("", key=f"raw-{i}", hint=f"VID_2026{i % 12 + 1:02d}15_{i % 24:02d}0000")
        text = f"{m.title} {m.description}".lower().replace("au guidon", "")  # channel line
        assert not any(c in text for c in claims), text


def test_fallback_ignores_an_arabic_caption():
    m = fallback_meta("غير كيطفى الأنتركوم #moto", key="k")
    assert not any("\u0600" <= ch <= "\u06ff" for ch in m.title + m.description)


def test_tags_drop_post_ids_and_arabic():
    assert content_tags(["Ktm"], "TikTok video #7638936057915985172 #موطو #balademoto") == [
        "ktm",
        "balademoto",
    ]


def test_quota_error_from_the_model_is_not_hidden():
    from fakes import GeminiDailyQuota

    with pytest.raises(LLMQuotaExceeded):
        generate_metadata(FakeLLM(error=GeminiDailyQuota()), "cap", "", key="A")


# --- model fallbacks ---------------------------------------------------------------------------
class _Models:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append(model)
        result = self.behaviour[model]
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(text=result)


def _gemini(monkeypatch, behaviour):
    from social2shorts.ai import metadata

    models = _Models(behaviour)
    monkeypatch.setattr(metadata, "_genai_client", lambda: SimpleNamespace(models=models))
    return models


def _server_error(code=503):
    exc = RuntimeError(f"{code} UNAVAILABLE high demand")
    exc.code = code
    return exc


def test_an_overloaded_model_falls_through_to_the_next(monkeypatch):
    from social2shorts.ai.metadata import GeminiLLM

    models = _gemini(monkeypatch, {"a": _server_error(), "b": "{}"})
    assert GeminiLLM("a", ["b"]).complete_json("p") == "{}" and models.calls == ["a", "b"]


def test_a_spent_daily_quota_falls_through_to_the_next(monkeypatch):
    from fakes import GeminiDailyQuota

    from social2shorts.ai.metadata import GeminiLLM

    models = _gemini(monkeypatch, {"a": GeminiDailyQuota(), "b": "ok"})
    assert GeminiLLM("a", ["b"]).complete_json("p") == "ok" and models.calls == ["a", "b"]


def test_every_model_failing_raises_the_last_error(monkeypatch):
    from fakes import GeminiDailyQuota

    from social2shorts.ai.metadata import GeminiLLM

    _gemini(monkeypatch, {"a": _server_error(), "b": GeminiDailyQuota()})
    llm = GeminiLLM("a", ["b"])
    with pytest.raises(LLMQuotaExceeded):
        generate_metadata(llm, "cap", "", key="k")


def test_a_client_error_does_not_burn_the_other_models(monkeypatch):
    from social2shorts.ai.metadata import GeminiLLM

    bad = RuntimeError("400 INVALID_ARGUMENT")
    bad.code = 400
    models = _gemini(monkeypatch, {"a": bad, "b": "ok"})
    with pytest.raises(RuntimeError):
        GeminiLLM("a", ["b"]).complete_json("p")
    assert models.calls == ["a"]


def test_the_audio_refiner_also_falls_through_to_the_next_model(monkeypatch, tmp_path):
    from social2shorts.ai import refine

    models = _gemini(
        monkeypatch,
        {"a": _server_error(), "b": json.dumps({"voice": "moroccan_darija", "segments": [
            {"first": 0, "last": 0, "text": "salam"}]})},
    )  # fmt: skip
    monkeypatch.setattr(refine, "extract_audio", lambda path: b"wav")
    out = refine.GeminiAudioRefiner("a", ["b"]).refine(
        tmp_path / "v.mp4", [Word(start=0, end=1, text="x")]
    )
    assert [w.text for w in out] == ["salam"] and models.calls == ["a", "b"]


def test_description_hooks_tells_asks_and_presents_the_channel():
    """Owner, 2026-11-11: descriptions were too thin. First line = teaser, then the story in two
    paragraphs, a question for the comments, the channel line; signature and hashtags at upload."""
    from social2shorts.publish.youtube import description_problems, with_hashtags

    raw = {"title": "Balade dans les Cévennes", "teaser": "🔥 Les Cévennes entre potes",
           "description": "Une file de motos dans les virages.\n\nLa forêt défile, ciel bleu.",
           "question": "Tu connais les Cévennes ?", "tags": ["cevennes"]}  # fmt: skip
    d = sanitize_meta(raw, caption="", key="k", language="fr").description
    parts = d.split("\n\n")
    assert parts[0] == "🔥 Les Cévennes entre potes"
    assert parts[1:3] == [
        "Une file de motos dans les virages.",
        "La forêt défile, ciel bleu.",
    ]
    assert parts[3].replace(" ?", "?") == "Tu connais les Cévennes? 👇"  # humanize() spacing
    assert parts[4].startswith("🏍️ Des sorties moto")
    final = with_hashtags(d, ["cevennes"], "📸 Suis-moi sur Instagram : @moi")
    assert description_problems(final, "📸 Suis-moi sur Instagram : @moi") == []


def test_description_without_teaser_or_question_still_follows_the_format():
    from social2shorts.publish.youtube import description_problems, with_hashtags

    for i in range(30):
        m = fallback_meta("", key=f"raw-{i}")
        first, *_, question, channel = m.description.split("\n\n")
        assert first and question.endswith(" 👇") and channel.startswith("🏍️")
        final = with_hashtags(m.description, m.tags, "📸 Instagram : @moi")
        assert description_problems(final, "📸 Instagram : @moi") == []
