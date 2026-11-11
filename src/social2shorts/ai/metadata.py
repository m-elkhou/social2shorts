"""YouTube metadata writer: Gemini prompt (with frames of the video), checks and a fallback.

Only public content (the owner's caption, the transcript and a few frames of the clip) is sent to
the LLM. Every video gets varied, content-derived text so 200 uploads do not read as templated
("inauthentic content"). Titles, descriptions and the on-screen hook are French only (owner,
2026-11-03: the Arabic ones were not Moroccan Darija), and what the model says about the riders
must match the picture: a rejected answer is asked again, never published.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import re
from collections.abc import Sequence
from typing import Any

from social2shorts.models import ShortMeta
from social2shorts.ports import LLM

log = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
MAX_TITLE = 100
MAX_HOOK = 40
MAX_TAGS = 15
MAX_EMOJIS = 2

BANNED = {
    r"unleash(?:es|ed|ing)?": "open up",
    r"epic journey": "ride",
    r"dive into": "check out",
    r"elevat(?:e|es|ed|ing)": "improve",
    r"ultimate": "",
    r"breathtaking": "beautiful",
    r"adrenaline[- ]fu?ell?ed": "fast",
    r"you won'?t believe": "",
    r"must[- ](?:see|watch)": "",
}
_BANNED_RE = [(re.compile(rf"\b{p}\b", re.I), r) for p, r in BANNED.items()]
_EMOJI = re.compile("[\U0001f000-\U0001faff☀-➿⬀-⯿⌀-⏿][️‍]*")
_ALL_CAPS = re.compile(r"\b[A-Z]{4,}\b")
_HANDLE = re.compile(r"(^|\s)@\w[\w.]*")
_HASHTAG = re.compile(r"#(\w+)")
_SPAM_TAG = re.compile(r"(fyp|foryou|foryoupage|fy|viral|xyzbca|pourtoi)\w*", re.I)
_PLATFORM = re.compile(r"\b(tik\s?tok|instagram|insta|reels?)\b", re.I)

DEFAULT_LANGUAGE = "fr"  # the audience is mostly francophone (Morocco)
LANGUAGES = {"fr": "French", "en": "English"}
# Engagement question closing the text when the model gives none (owner, 2026-11-11: descriptions
# were too thin to drive comments). The model's own question, tied to the video, is preferred.
CTAS: dict[str, list[str]] = {
    "fr": [
        "Tu roulerais où, toi ? Dis-le en commentaire",
        "Ton meilleur spot pour rouler ? Partage-le en commentaire",
        "Tu roules quoi en ce moment ? Dis-le en commentaire",
        "Tague le pote avec qui tu ferais cette balade",
        "Tu ferais cette route ? Réponds en commentaire",
        "Plutôt balade tranquille ou grosse virée ? Dis-le en commentaire",
    ],
    "en": [
        "Where would you ride this one? Tell me in the comments",
        "Your favourite road to ride? Share it in the comments",
        "What are you riding right now? Tell me in the comments",
        "Tag the friend you would ride this with",
        "Would you ride this road? Answer in the comments",
        "Easy cruise or long ride? Tell me in the comments",
    ],
}
CHANNEL_LINE = {
    "fr": (
        "🏍️ Des sorties moto filmées au guidon, entre routes de montagne, balades "
        "entre amis et voyages. Un nouveau Short chaque jour : abonne-toi et active la 🔔 pour ne "
        "rien rater !"
    ),
    "en": (
        "🏍️ Rides filmed from the saddle, mountain roads, rides with friends and "
        "trips. A new Short every day: subscribe and turn on the 🔔 so you never miss one!"
    ),
}
# First line of the description (shown under the Short in the feed) when the model gives none.
TEASERS = {
    "fr": ["🏍️ Une sortie à moto comme on les aime.", "🛣️ La route, la moto et rien d'autre.",
           "🔥 Encore une balade qui fait du bien.", "🌄 Un moment de route à partager."],
    "en": ["🏍️ A ride the way we like them.", "🛣️ The road, the bike, nothing else.",
           "🔥 Another ride that feels good.", "🌄 A piece of road worth sharing."],
}  # fmt: skip
MAX_TEASER = 100
MAX_QUESTION = 110
_PARA = re.compile(r"\n\s*\n")
# Fallback titles are composed from pools, so 200 clips without an LLM still read differently.
# They say nothing about who rides, how many, the sound or the place: nobody looked at the clip.
_TIME_RE = re.compile(r"(20\d{2})(\d{2})(\d{2})(?:[_-]?(\d{2})(\d{2}))?")
FALLBACK = {
    "fr": {
        "night": ["Sortie de nuit", "Balade de nuit", "La ville la nuit à moto"],
        "evening": ["Sortie du soir", "Balade en fin de journée", "Moto au coucher du soleil"],
        "morning": ["Sortie du matin", "Balade matinale", "Moto au petit matin"],
        "day": ["Balade moto", "Sortie moto", "Un tour à moto", "Sortie du jour"],
        "tail": ["", " en images", ", petit extrait"],
        "body": ["Un extrait d'une sortie à moto.", "Quelques images d'une balade à moto.",
                 "Une sortie à moto filmée sur la route."],
        "months": ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.",
                   "oct.", "nov.", "déc."],
    },
    "en": {
        "night": ["Night ride", "Late night run", "City lights ride"],
        "evening": ["Evening ride", "Sunset ride", "Last ride of the day"],
        "morning": ["Morning ride", "Early run", "Sunrise ride"],
        "day": ["Quick ride", "Motorbike ride", "Out on the bike", "Ride of the day"],
        "tail": ["", " in pictures", ", short clip"],
        "body": ["A clip from a motorbike ride.", "A few moments from a ride.",
                 "A ride filmed on the road."],
        "months": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
                   "Nov", "Dec"],
    },
}  # fmt: skip
_ARABIC = re.compile("[\u0600-\u06ff\u0750-\u077f\ufb50-\ufdff\ufe70-\ufeff]")
_SOLO = re.compile(r"\b(solo|seule?|en solitaire|alone)\b", re.I)
_RIDERS = ("solo", "duo", "group", "none", "unknown")


# ------------------------------------------------------------------ adapter
# Not 429: the per-day quota must stop the run at once (a per-minute 429 stops it too, for an hour).
GEMINI_RETRY_STATUS = (408, 500, 502, 503, 504)
# Per attempt: the longest healthy answer (audio refinement) takes about a minute.
GEMINI_TIMEOUT_MS = 150_000


def gemini_http_options() -> Any:
    """Retry temporary errors (about 1 min in all): the SDK tries once by default, and a
    "503 high demand" (2026-11-01) put a template title on a Short."""
    from google.genai import types

    retry = types.HttpRetryOptions(
        attempts=4, initial_delay=10, max_delay=60, http_status_codes=list(GEMINI_RETRY_STATUS)
    )
    # A call has no deadline by default: on 2026-11-03 an overloaded model left one hanging for
    # over 20 minutes. Past this it fails, the run stops (LLMUnavailable) and the next one retries.
    return types.HttpOptions(retry_options=retry, timeout=GEMINI_TIMEOUT_MS)


def gemini_client() -> Any:
    """``genai.Client`` with :func:`gemini_http_options`; reads GEMINI_API_KEY / GOOGLE_API_KEY."""
    from google import genai

    return genai.Client(http_options=gemini_http_options())


@functools.cache
def _genai_client() -> Any:
    return gemini_client()


class LLMUnavailable(Exception):
    """The model could not answer (overloaded, network down). The run stops and the next one
    tries again: a template title on a Short is worse than a Short that waits an hour."""

    def __init__(self, message: str = "Gemini is unavailable"):
        super().__init__(message)


class LLMQuotaExceeded(Exception):
    """The model's daily request quota is spent. The run stops rather than publish videos with
    template titles and uncorrected subtitles."""

    def __init__(self, message: str = "Gemini daily request quota is spent"):
        super().__init__(message)


def is_daily_quota_error(exc: BaseException) -> bool:
    """True for Gemini's per-day 429 (the free tier allows 20 requests per model per day).

    Per-minute 429s are not matched: the usual fallback applies to them.
    """
    return getattr(exc, "code", None) == 429 and "PerDay" in str(exc)


def _next_model_helps(exc: BaseException) -> bool:
    """True when another model may answer: this one's day is spent or it is overloaded."""
    return is_daily_quota_error(exc) or getattr(exc, "code", None) in GEMINI_RETRY_STATUS


def generate_with_fallback(models: Sequence[str], contents: Any, config: Any) -> Any:
    """``generate_content`` on the first model that answers; the next one is tried when a model's
    day is spent or it stays overloaded. Any other error, or the last model's, is raised."""
    for i, model in enumerate(models):
        try:
            return _genai_client().models.generate_content(
                model=model, contents=contents, config=config
            )
        except Exception as exc:
            if i + 1 == len(models) or not _next_model_helps(exc):
                raise
            log.warning("Gemini %s cannot answer (%s); trying %s", model, exc, models[i + 1])
    raise AssertionError("unreachable: the last model's error is raised above")


class GeminiLLM:
    """``LLM`` adapter over the ``google-genai`` SDK (JSON response mode).

    Each model has its own free daily quota and its own overloads (3.7 and 3.8 answered 503 all
    morning of 2026-11-03 while 3.5 and 3.6 did not): ``fallbacks`` are tried in order when a
    model's day is spent or it stays overloaded.
    """

    def __init__(self, model: str = DEFAULT_GEMINI_MODEL, fallbacks: Sequence[str] = ()):
        self.model = model or DEFAULT_GEMINI_MODEL
        self.fallbacks = tuple(fallbacks)

    def complete_json(self, prompt: str, images: Sequence[bytes] = ()) -> str:
        """Send ``prompt`` (after the JPEG ``images``, if any); return the raw JSON text."""
        from google.genai import types

        parts = [types.Part.from_bytes(data=img, mime_type="image/jpeg") for img in images]
        contents = [*parts, prompt] if parts else prompt
        resp = generate_with_fallback(
            (self.model, *self.fallbacks), contents, {"response_mime_type": "application/json"}
        )
        return resp.text or ""


# ---------------------------------------------------------------- humanizer
def humanize(text: str, max_emojis: int = MAX_EMOJIS) -> str:
    """Make generated text read like a rider wrote it.

    Drops ``<``/``>``, rewrites banned AI phrases, keeps at most ``max_emojis`` emojis, turns
    ALL-CAPS words into Capitalized ones and collapses repeated punctuation and spaces.
    """
    text = text.replace("<", "").replace(">", "")  # YouTube rejects them (invalidTitle)
    for pattern, replacement in _BANNED_RE:
        text = pattern.sub(replacement, text)
    kept = 0

    def _emoji(m: re.Match[str]) -> str:
        nonlocal kept
        kept += 1
        return m.group(0) if kept <= max_emojis else ""

    text = _EMOJI.sub(_emoji, text)
    text = _ALL_CAPS.sub(lambda m: m.group(0).capitalize(), text)
    text = re.sub(r"\.{4,}", "...", text)
    text = re.sub(r"([!?,])\1+", r"\1", text)
    text = re.sub(r"[!?]{2,}", lambda m: m.group(0)[0], text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +([,.!?])", r"\1", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def pick[T](options: Sequence[T], key: str, salt: str) -> T:
    """Deterministic, evenly spread choice per item (same key -> same choice on retries)."""
    digest = hashlib.sha256(f"{salt}:{key}".encode()).digest()
    return options[int.from_bytes(digest[:4], "big") % len(options)]


# ---------------------------------------------------------------- sanitizing
def _strip_noise(text: str) -> str:
    """Remove @handles, hashtags and other-platform mentions."""
    text = _HANDLE.sub(" ", text)
    text = _HASHTAG.sub(" ", text)
    text = _PLATFORM.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip(" -|:,.")


def _strip_noise_paragraphs(text: str) -> str:
    """:func:`_strip_noise` paragraph by paragraph: the blank line between paragraphs and the
    final punctuation of each sentence are kept (a description is prose, not a title)."""
    out = []
    for part in _PARA.split(text):
        part = _PLATFORM.sub(" ", _HASHTAG.sub(" ", _HANDLE.sub(" ", part)))
        part = re.sub(r"\s+", " ", part).strip(" -|:,")
        if part:
            out.append(part)
    return "\n\n".join(out)


def _clip(text: str, limit: int) -> str:
    """Cut at a word boundary to ``limit`` chars."""
    if len(text) <= limit:
        return text
    head = text[: limit + 1]
    cut = head.rsplit(" ", 1)[0] if " " in head.strip() else text[:limit]
    return cut[:limit].rstrip(" ,;:-")


def _norm_tag(tag: str) -> str:
    return re.sub(r"\s+", "", str(tag).lstrip("#")).lower()


def content_tags(llm_tags: Sequence[str], caption: str) -> list[str]:
    """Tags from the LLM and the caption hashtags, deduped, without spam or platform names."""
    tags: list[str] = []
    for raw in [*llm_tags, *_HASHTAG.findall(caption)]:
        tag = _norm_tag(raw)
        usable = tag and not tag.isdigit() and not _ARABIC.search(tag)
        spam = _SPAM_TAG.fullmatch(tag) or _PLATFORM.search(tag)
        if usable and tag not in tags and not spam:
            tags.append(tag)
    return tags[:MAX_TAGS] or ["moto"]


def _paragraphs(text: str) -> str:
    """Humanized body with its paragraph breaks kept (the model writes two short paragraphs)."""
    return "\n\n".join(p for p in (humanize(x) for x in _PARA.split(text) if x.strip()) if p)


def _description(body: str, key: str, language: str, teaser: str = "", question: str = "") -> str:
    """Viral but truthful text (owner, 2026-11-11): a first line that hooks (shown under the
    Short in the feed), the story in two short paragraphs, a question that invites comments, then
    the channel line. The signature and the hashtags are added at upload
    (``publish.youtube.with_hashtags``)."""
    lang = language if language in CTAS else DEFAULT_LANGUAGE
    teaser = _clip(humanize(teaser, max_emojis=1), MAX_TEASER) or pick(TEASERS[lang], key, "teaser")
    question = _clip(humanize(question, max_emojis=0).rstrip(" .👇"), MAX_QUESTION)
    question = f"{question or pick(CTAS[lang], key, 'cta')} 👇"
    channel = CHANNEL_LINE[lang]
    return "\n\n".join(part for part in (teaser, _paragraphs(body), question, channel) if part)


def sanitize_meta(
    raw: dict, *, caption: str, key: str, language: str = DEFAULT_LANGUAGE
) -> ShortMeta:
    """Enforce YouTube limits, strip cross-platform noise and humanize LLM output.

    Raises:
        ValueError: If no usable title remains.
    """
    title = _clip(humanize(_strip_noise(str(raw.get("title", "")))), MAX_TITLE)
    if not title:
        raise ValueError("empty title")
    tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    return ShortMeta(
        title=title,
        description=_description(
            _strip_noise_paragraphs(str(raw.get("description", ""))),
            key,
            language,
            teaser=_strip_noise(str(raw.get("teaser") or "")),
            question=_strip_noise(str(raw.get("question") or "")),
        ),
        tags=content_tags(tags, caption),
        hook=_hook(raw),
    )


def _hook(raw: dict) -> str:
    """The on-screen line; empty when the model has none or the clip already carries text."""
    if raw.get("onscreen_text") is True:
        return ""
    return _clip(humanize(_strip_noise(str(raw.get("hook") or "")), max_emojis=0), MAX_HOOK)


def _moment(hint: str) -> tuple[str | None, str | None]:
    """(time-of-day bucket, 'D month' label) read from a camera file name or YYYYMMDD date."""
    match = _TIME_RE.search(hint)
    if not match:
        return None, None
    _, month, day, hour, _minute = match.groups()
    if not 1 <= int(month) <= 12:
        return None, None
    bucket = None
    if hour is not None:
        h = int(hour)
        bucket = ("night" if h >= 21 or h < 5 else "evening" if h >= 17
                  else "morning" if h < 11 else "day")  # fmt: skip
    return bucket, f"{int(day)} {int(month)}"


def fallback_title(key: str, language: str, hint: str = "") -> str:
    """A varied, deterministic, neutral title built from the time of day and the date.

    The transcript is not used: it is often music or a speech-model hallucination.
    """
    pools = FALLBACK.get(language, FALLBACK[DEFAULT_LANGUAGE])
    bucket, date = _moment(hint)
    openers = pools[bucket] if bucket else pools["day"]
    title = f"{pick(openers, key, 'opener')}{pick(pools['tail'], key, 'tail')}"
    if date and pick([True, False], key, "date"):
        day, month = date.split()
        title += f" ({day} {pools['months'][int(month) - 1]})"
    return _clip(title, 60)


def fallback_meta(
    caption: str,
    key: str,
    language: str = DEFAULT_LANGUAGE,
    hint: str = "",
) -> ShortMeta:
    """Metadata without the LLM: caption-derived when possible, else composed per item.

    A caption in Arabic script is not used (titles and descriptions are French only).
    """
    text = humanize(_strip_noise(caption))
    if _ARABIC.search(text):
        text = ""
    pools = FALLBACK.get(language, FALLBACK[DEFAULT_LANGUAGE])
    title = _clip(text, 60) or fallback_title(key, language, hint)
    body = text if text and text != title else pick(pools["body"], key, "body") or ""
    return ShortMeta(
        title=title,
        description=_description(body, key, language),
        tags=content_tags([], caption),
    )


# ------------------------------------------------------------------- prompt
def build_prompt(caption: str, transcript: str, language: str | None, frames: int = 0) -> str:
    """Prompt asking for truthful, French metadata as JSON, written from what the frames show."""
    lang = LANGUAGES.get(language or DEFAULT_LANGUAGE, LANGUAGES[DEFAULT_LANGUAGE])
    banned = ", ".join(f'"{w}"' for w in (
        "unleash", "epic journey", "dive into", "elevate", "ultimate", "breathtaking",
        "adrenaline-fueled",
    ))  # fmt: skip
    seen = (
        f"You are given {frames} frames sampled evenly across the video, in order. Look at them "
        "first: they are the ground truth."
        if frames
        else "You get no frames: say nothing about what is on screen."
    )
    return (
        "You write the YouTube Short metadata for a motorcyclist's moto vlog channel. "
        f"{seen} You also get the owner's original caption (his own words, may be empty or in "
        "Arabic/Darija) and a machine transcript of the sound (often wrong or just music: use "
        "it only to understand the topic, never quote it).\n"
        "Rules:\n"
        "- Say only what is visible in the frames or stated in the caption. Never invent a "
        "place, bike model, weather, time of day, speed, distance or anecdote. When unsure, "
        "leave it out.\n"
        '- "riders": count the motorcyclists you can see: "solo" (one rider alone), "duo" (two '
        'riders or two on one bike), "group" (three or more bikes), "none" (no rider or bike '
        'visible), "unknown". Never write "solo", "seul" or "en solitaire" (a camera only shows '
        "who is in front of it: the rest of the group may be out of frame) unless the caption "
        'says so; for a duo or a group say "en groupe" or "entre potes".\n'
        '- "onscreen_text": true when words are already written on the video (overlay, meme, '
        "subtitle); a brand logo does not count.\n"
        f"- Language: {lang} ONLY, in Latin script. No Arabic script, no other language. "
        "Translate the meaning of an Arabic or Darija caption or on-screen text into "
        "French (quote it as « ... » in the description). Place names and bike brands stay as "
        "they are.\n"
        "- Title: 70 characters at most, specific and truthful, no clickbait, no hashtag, "
        "no emoji (they make every title look the same).\n"
        '- "teaser": the FIRST line of the description, shown under the Short in the feed: one '
        "punchy, truthful sentence that makes people stop scrolling, 90 characters at most; it "
        "may start with ONE emoji that fits the scene.\n"
        "- Description: 3 to 4 sentences in two short paragraphs separated by a blank line: what "
        "happens, the place or the bike when visible, the feeling of the ride. Vivid and concrete "
        "but strictly factual, no hashtag, no link, no call to subscribe.\n"
        '- "question": one question to the viewer, tied to THIS video (their bike, their favourite '
        "road, who they would ride with), that invites a comment, 90 characters at most.\n"
        "- Hook: the line burned on screen in the first seconds, 40 characters at most, "
        'describes the moment shown; null when "onscreen_text" is true.\n'
        "- Tags: up to 8, taken from what is visible or in the caption (place, bike brand, kind "
        "of ride).\n"
        "- Never mention TikTok, Instagram, @handles or music tracks. Write like a rider, not an "
        f"ad. Never use these words: {banned}.\n"
        'Reply ONLY with JSON: {"scene": str (one sentence: what the frames show), "riders": '
        '"solo"|"duo"|"group"|"none"|"unknown", "onscreen_text": bool, "title": str, '
        '"teaser": str, "description": str, "question": str, "tags": [str], "hook": str|null}.\n\n'
        f"Caption: {caption or '(none: raw footage)'}\n"
        f"Transcript: {transcript or '(no speech)'}\n"
    )


def _parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text.strip(), re.S)
    data = json.loads(match.group(0) if match else text)
    if not isinstance(data, dict):
        raise ValueError("LLM did not return a JSON object")
    return data


class MetadataRejected(ValueError):
    """The model's answer breaks a rule (script, rider count): it is asked again."""


def check_meta(raw: dict, caption: str = "") -> None:
    """Reject an answer that is not French, or that says the rider is alone.

    A camera shows who is in front of it, not who rides behind: "solo" is only allowed when the
    owner's caption says it (a group ride went online as "Balade solo", then "En solo sur la
    Route des Grandes Alpes", 2026-11-03).

    Raises:
        MetadataRejected: With the reason, worded for the model's second try.
    """
    tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    texts = tuple(
        str(raw.get(k) or "") for k in ("title", "description", "hook", "teaser", "question")
    )
    if any(_ARABIC.search(text) for text in (*texts, *map(str, tags))):
        raise MetadataRejected(
            "it contains Arabic script: write French only and translate any Arabic into French"
        )
    riders = str(raw.get("riders", "unknown")).lower()
    if riders not in _RIDERS:
        raise MetadataRejected(f'"riders" must be one of {", ".join(_RIDERS)}')
    claims_solo = any(_SOLO.search(text) for text in texts)
    if claims_solo and not _SOLO.search(caption):
        raise MetadataRejected(
            'you wrote "solo"/"seul"/"alone": never say the rider is alone, the camera does not '
            "show who rides out of frame"
        )


def _ask(llm: LLM, prompt: str, images: Sequence[bytes]) -> dict:
    """One model call. Raises LLMQuotaExceeded / LLMUnavailable, or ValueError on bad JSON."""
    try:
        text = llm.complete_json(prompt, images)
    except Exception as exc:  # network, overloaded model, spent quota
        if is_daily_quota_error(exc):
            raise LLMQuotaExceeded() from exc
        raise LLMUnavailable(f"Gemini is unavailable ({type(exc).__name__}: {exc})") from exc
    return _parse_json(text)


def generate_metadata(
    llm: LLM | None,
    caption: str,
    transcript: str,
    *,
    key: str,
    language: str | None = None,
    hint: str = "",
    images: Sequence[bytes] = (),
) -> ShortMeta:
    """LLM-written metadata. An answer that breaks a rule is asked again once, then the neutral
    :func:`fallback_meta` is used; with no LLM at all the fallback is used directly.

    Raises:
        LLMQuotaExceeded: If the model's daily quota is spent.
        LLMUnavailable: If the model cannot answer (the run stops, the next one retries).

    Args:
        llm: The model, or None to use the fallback directly.
        caption: Original caption (empty for raw footage).
        transcript: Spoken words (already cleaned of speech-model hallucinations).
        key: Item key (seeds the deterministic variety).
        language: ``fr`` / ``en``; anything else, or None, means French.
        hint: File name or ``YYYYMMDD`` date, used for time-of-day wording in the fallback.
        images: JPEG frames of the clip, in order.
    """
    lang = language if language in LANGUAGES else DEFAULT_LANGUAGE
    if llm is None:
        return fallback_meta(caption, key, lang, hint)
    prompt = build_prompt(caption, transcript, lang, len(images))
    feedback = ""
    for attempt in (1, 2):
        try:
            raw = _ask(llm, prompt + feedback, images)
            if lang == "fr":
                check_meta(raw, caption)
            return sanitize_meta(raw, caption=caption, key=key, language=lang)
        except ValueError as exc:  # bad JSON, empty title, Arabic script, solo over a group
            log.warning("Metadata of %s rejected (try %d/2): %s", key, attempt, exc)
            feedback = f"\n\nYour previous answer was rejected: {exc}. Answer again, fixed."
    return fallback_meta(caption, key, lang, hint)
