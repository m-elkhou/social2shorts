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

CTAS = {
    "en": [
        "Where would you ride this one?",
        "More night rides coming, stick around.",
        "Tell me what bike you ride.",
        "Ride safe out there.",
        None,
        None,
    ],
    "fr": [
        "Tu roulerais où, toi ?",
        "D'autres rides arrivent, reste dans le coin.",
        "Dis-moi ce que tu conduis en commentaire.",
        "Roulez prudemment.",
        None,
        None,
    ],
}
FALLBACK_TITLES = [
    "Night ride, no filter",
    "Quick spin before sunset",
    "Just me and the road",
    "Back on the bike today",
    "Took the long way home",
    "Empty road, clean lines",
    "One more lap around town",
    "Short ride, good mood",
]


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

    Rewrites banned AI phrases, keeps at most ``max_emojis`` emojis, turns ALL-CAPS words
    into Capitalized ones and collapses repeated punctuation and spaces.
    """
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


def pick(options: Sequence[str | None], key: str, salt: str) -> str | None:
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
    cta = pick(CTAS.get(language, CTAS["en"]), key, "cta")
    return "\n\n".join(part for part in (humanize(body), cta) if part)


def sanitize_meta(raw: dict, *, caption: str, key: str, language: str = "en") -> ShortMeta:
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


def fallback_meta(caption: str, key: str, language: str = "en") -> ShortMeta:
    """Metadata without the LLM: caption-derived when possible, else a varied rider title."""
    text = humanize(_strip_noise(caption))
    title = _clip(text, 60) or pick(FALLBACK_TITLES, key, "title") or FALLBACK_TITLES[0]
    return ShortMeta(
        title=title,
        description=_description(text if text != title else "", key, language),
        tags=content_tags([], caption),
    )


# ------------------------------------------------------------------- prompt
def build_prompt(caption: str, transcript: str, language: str | None) -> str:
    """Prompt asking for a natural first-person rider voice, as JSON."""
    lang = f"Write in this language: {language}." if language else (
        "Write in the language of the caption/transcript (English if there is none)."
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
    llm: LLM | None, caption: str, transcript: str, *, key: str, language: str | None = None
) -> ShortMeta:
    """LLM-written metadata; any LLM failure falls back to :func:`fallback_meta` (never raises)."""
    lang = language or "en"
    if llm is None:
        return fallback_meta(caption, key, lang)
    try:
        raw = _parse_json(llm.complete_json(build_prompt(caption, transcript, language)))
        return sanitize_meta(raw, caption=caption, key=key, language=lang)
    except Exception as exc:  # quota, network, bad JSON, empty title...
        log.warning("LLM metadata failed for %s (%s); using fallback title", key, exc)
        return fallback_meta(caption, key, lang)
