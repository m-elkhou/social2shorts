"""YouTube metadata writer: Gemini prompt, ``humanize()`` post-filter and a never-failing fallback.

Only public content (the owner's caption and transcript) is sent to the LLM. Every video gets
varied, content-derived text so 200 uploads do not read as templated ("inauthentic content").
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
CTAS: dict[str, list[str | None]] = {
    "fr": [
        "Tu roulerais où, toi ?",
        "D'autres sorties arrivent, reste dans le coin.",
        "Dis-moi ce que tu conduis en commentaire.",
        "Roulez prudemment.",
        "Ton meilleur spot pour rouler ? Dis-le en commentaire.",
        None,
        None,
    ],
    "en": [
        "Where would you ride this one?",
        "More rides coming, stick around.",
        "Tell me what bike you ride.",
        "Ride safe out there.",
        None,
        None,
    ],
    "ar": [
        "فين كتحب تسوق؟",
        "قولّي شنو كتسوق فالتعليقات.",
        "سوقو بالعقل.",
        None,
        None,
    ],
}
# Fallback titles are composed from pools, so 200 clips without an LLM still read differently.
_TIME_RE = re.compile(r"(20\d{2})(\d{2})(\d{2})(?:[_-]?(\d{2})(\d{2}))?")
FALLBACK = {
    "fr": {
        "night": ["Ride de nuit", "Sortie nocturne", "Balade de nuit", "Run de minuit",
                  "La ville la nuit"],
        "evening": ["Sortie du soir", "Ride au coucher du soleil", "Balade en fin de journée",
                    "Dernière sortie de la journée"],
        "morning": ["Ride du matin", "Sortie matinale", "Petit run du matin",
                    "Réveil à moto"],
        "day": ["Petite sortie", "Balade moto", "Ride en solo", "Tour rapide",
                "Sortie du jour", "Un tour au guidon"],
        "tail": ["", " en solo", " sans filtre", " en POV", ", son d'origine",
                 ", juste le moteur", " tranquille", ", caméra embarquée"],
        "body": ["Petite sortie filmée en POV, son d'origine.", "Juste la route et le moteur.",
                 "Une sortie de plus au guidon.", "Caméra embarquée, rien de retouché.",
                 "Pas de musique, juste la moto.", "Un moment de route comme je les aime."],
        "months": ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.",
                   "oct.", "nov.", "déc."],
    },
    "en": {
        "night": ["Night ride", "Late night run", "Midnight spin", "City lights ride"],
        "evening": ["Evening ride", "Sunset ride", "After-work spin", "Last ride of the day"],
        "morning": ["Morning ride", "Early run", "Sunrise spin", "Morning commute"],
        "day": ["Quick ride", "Solo ride", "Short spin", "Out on the bike", "Back on the bike"],
        "tail": ["", ", no filter", ", POV", ", raw sound", ", just the engine", ", helmet cam",
                 " in town"],
        "body": ["Shot from the bike, original sound.", "Just the road and the engine.",
                 "Another ride, nothing edited.", "No music, just the bike."],
        "months": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
                   "Nov", "Dec"],
    },
}  # fmt: skip
_STOPWORDS = frozenset(
    {
        "alors", "avec", "avoir", "comme", "dans", "elle", "encore", "faire", "juste", "mais",
        "même", "parce", "pour", "quand", "quoi", "rien", "sont", "tout", "très", "vraiment",
        "voilà", "about", "after", "again", "because", "before", "being", "could", "really",
        "right", "there", "their", "these", "thing", "think", "going", "gonna", "yeah", "would",
    }
)  # fmt: skip


# ------------------------------------------------------------------ adapter
@functools.cache
def _genai_client() -> Any:
    from google import genai

    return genai.Client()  # reads GEMINI_API_KEY / GOOGLE_API_KEY


class GeminiLLM:
    """``LLM`` adapter over the ``google-genai`` SDK (JSON response mode)."""

    def __init__(self, model: str = DEFAULT_GEMINI_MODEL):
        self.model = model or DEFAULT_GEMINI_MODEL

    def complete_json(self, prompt: str) -> str:
        """Send ``prompt``; return the raw JSON text."""
        resp = _genai_client().models.generate_content(
            model=self.model,
            contents=prompt,
            config={"response_mime_type": "application/json"},
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
        if tag and tag not in tags and not _SPAM_TAG.fullmatch(tag) and not _PLATFORM.search(tag):
            tags.append(tag)
    return tags[:MAX_TAGS] or ["moto"]


def _description(body: str, key: str, language: str) -> str:
    cta = pick(CTAS.get(language, CTAS[DEFAULT_LANGUAGE]), key, "cta")
    return "\n\n".join(part for part in (humanize(body), cta) if part)


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
        description=_description(_strip_noise(str(raw.get("description", ""))), key, language),
        tags=content_tags(tags, caption),
        hook=_clip(humanize(_strip_noise(str(raw.get("hook", ""))), max_emojis=0), MAX_HOOK),
    )


def keywords(transcript: str, limit: int = 2) -> list[str]:
    """Most frequent meaningful words of a transcript (>= 5 letters, no filler words)."""
    counts: dict[str, int] = {}
    for word in re.findall(r"[^\W\d_]{5,}", transcript.lower()):
        if word not in _STOPWORDS:
            counts[word] = counts.get(word, 0) + 1
    return sorted(counts, key=lambda w: (-counts[w], w))[:limit]


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


def fallback_title(key: str, language: str, transcript: str = "", hint: str = "") -> str:
    """A varied, deterministic rider title built from time of day, date and transcript words."""
    pools = FALLBACK.get(language, FALLBACK[DEFAULT_LANGUAGE])
    bucket, date = _moment(hint)
    openers = pools[bucket] if bucket else [*pools["day"], *pools["evening"], *pools["night"]]
    title = f"{pick(openers, key, 'opener')}{pick(pools['tail'], key, 'tail')}"
    words = keywords(transcript, 1)
    if words:
        title += f" : {words[0]}"
    elif date and pick([True, False], key, "date"):
        day, month = date.split()
        title += f" ({day} {pools['months'][int(month) - 1]})"
    return _clip(title, 60)


def fallback_meta(
    caption: str,
    key: str,
    language: str = DEFAULT_LANGUAGE,
    transcript: str = "",
    hint: str = "",
) -> ShortMeta:
    """Metadata without the LLM: caption-derived when possible, else composed per item."""
    text = humanize(_strip_noise(caption))
    pools = FALLBACK.get(language, FALLBACK[DEFAULT_LANGUAGE])
    title = _clip(text, 60) or fallback_title(key, language, transcript, hint)
    body = text if text and text != title else pick(pools["body"], key, "body") or ""
    return ShortMeta(
        title=title,
        description=_description(body, key, language),
        tags=content_tags(keywords(transcript, 3), caption),
    )


# ------------------------------------------------------------------- prompt
def build_prompt(caption: str, transcript: str, language: str | None) -> str:
    """Prompt asking for a natural first-person rider voice, as JSON."""
    lang = f"Write in this language: {language}." if language else (
        "Write in the language of the caption/transcript (French if there is none)."
    )  # fmt: skip
    banned = ", ".join(f'"{w}"' for w in (
        "unleash", "epic journey", "dive into", "elevate", "ultimate", "breathtaking",
        "adrenaline-fueled",
    ))  # fmt: skip
    return (
        "You are a motorcyclist posting your own ride as a YouTube Short on your channel. "
        "Write like a real rider texting a friend: casual, first person, concrete details "
        "taken from the caption and what is said in the transcript (place, time of day, bike, "
        "weather, what happened). Mix short and longer sentences. At most 1-2 emojis in total. "
        "No clickbait, no hype clichés, no hashtags in the title, no mention of TikTok, "
        f"Instagram or @handles. Never use these words: {banned}. {lang}\n"
        'Reply ONLY with JSON: {"title": str (<= 70 chars, specific, not generic), '
        '"description": str (2-4 sentences, no link, no hashtag wall), '
        '"tags": [str, ... up to 12, from the actual content], '
        '"hook": str (<= 40 chars, the line shown on screen in the first second, '
        "makes people keep watching, no emoji)}.\n\n"
        f"Caption: {caption or '(none: raw footage)'}\n"
        f"Transcript: {transcript or '(no speech)'}\n"
    )


def _parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text.strip(), re.S)
    data = json.loads(match.group(0) if match else text)
    if not isinstance(data, dict):
        raise ValueError("LLM did not return a JSON object")
    return data


def generate_metadata(
    llm: LLM | None,
    caption: str,
    transcript: str,
    *,
    key: str,
    language: str | None = None,
    hint: str = "",
) -> ShortMeta:
    """LLM-written metadata; any LLM failure falls back to :func:`fallback_meta` (never raises).

    Args:
        llm: The model, or None to use the fallback directly.
        caption: Original caption (empty for raw footage).
        transcript: Spoken words.
        key: Item key (seeds the deterministic variety).
        language: ``fr`` / ``en`` / ``ar``; None means French text for the fallback and CTAs.
        hint: File name or ``YYYYMMDD`` date, used for time-of-day wording in the fallback.
    """
    lang = language or DEFAULT_LANGUAGE
    if llm is None:
        return fallback_meta(caption, key, lang, transcript, hint)
    try:
        raw = _parse_json(llm.complete_json(build_prompt(caption, transcript, language)))
        return sanitize_meta(raw, caption=caption, key=key, language=lang)
    except Exception as exc:  # quota, network, bad JSON, empty title...
        log.warning("LLM metadata failed for %s (%s); using fallback title", key, exc)
        return fallback_meta(caption, key, lang, transcript, hint)
