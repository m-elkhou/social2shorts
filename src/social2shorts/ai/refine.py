"""Transcript refinement: an audio LLM rewrites a speech model's draft and keeps its timing.

A Darija speech model hears every word and times it precisely, but spells French words
phonetically in Arabic letters and mishears some Darija. Gemini spells Darija and French
correctly but skips passages and times words loosely. So the draft gives the timing and the
LLM gives the text: it answers with segments ("draft words 12..15 say <text>") and the times
are computed here from the draft, never copied from the model. Only the clip's own audio and
the draft are sent to the LLM.
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ValidationError

from social2shorts.ai.metadata import (
    LLMQuotaExceeded,
    LLMUnavailable,
    generate_with_fallback,
    is_daily_quota_error,
)
from social2shorts.montage.types import Word
from social2shorts.ports import Transcriber

log = logging.getLogger(__name__)

_PLACEHOLDER = re.compile(r"^\W*$|^\[.*\]$")  # "[...]", "...", lone punctuation

PROMPT = """You are correcting the subtitles of a Moroccan moto vlog. The speaker speaks Moroccan \
Arabic (Darija) mixed with French (motorcycle vocabulary) and sometimes English.
Below is a DRAFT from a Darija speech model, one numbered word per spoken word. It hears every \
word and its timing is reliable, but it spells French words phonetically in Arabic letters and \
mishears some Darija.

DRAFT: {draft}

Listen to the audio first and say what the voice is in "voice": moroccan_darija (the speaker \
talks Moroccan Arabic, possibly mixed with French), french_or_english, standard_arabic (Modern \
Standard Arabic read aloud or recited), other_arabic_dialect (Egyptian, Gulf, Levantine, \
Algerian...), song_or_music (singing, rap, lyrics, instrumental) or no_speech. Only \
moroccan_darija and french_or_english get segments; for the others answer no segments.
\
Then rewrite the draft correctly, phrase by phrase:
- Each segment covers draft words first..last (inclusive indices) and gives their correct text. \
Segments in order, no overlap, 1 to 6 draft words each.
- Subtitle only people talking. Leave out singing and song lyrics, music, noise, wind, and \
speech you cannot make out: a missing subtitle is better than a wrong one. The draft model \
writes words even over pure music; if nobody talks, answer with no segments.
- Darija in Arabic script exactly as Moroccans write it on social media: ديال, واش, بزاف, دابا, \
فين, كيفاش, علاش, شنو, غادي, كاين, ماشي, والو, هاد, مزيان, خويا. Keep the speaker's Moroccan \
forms; never "correct" them toward Modern Standard Arabic (هذا, ماذا, لماذا, كيف, الآن, جداً, \
لا يوجد) nor toward Egyptian, Gulf, Levantine or Algerian forms (دلوقتي, عايز, شو, هلق, وين, كي).
- French and English in Latin script, correctly spelled. Spoken model numbers as digits \
(mille trois cent quatre-vingt-dix -> 1390).
- Write only what is actually said: do not summarise, shorten, translate, guess or invent. A word \
you cannot make out is left out, never replaced by a plausible one.
- A song, rap or lyrics playing over the clip is not the speaker: no segments for it, even when \
the words are clear."""


class RefinedSegment(BaseModel):
    """Corrected text for the draft words ``first..last`` (inclusive)."""

    first: int
    last: int
    text: str


Voice = Literal[
    "moroccan_darija", "french_or_english", "standard_arabic", "other_arabic_dialect",
    "song_or_music", "no_speech",
]  # fmt: skip
SUBTITLED_VOICES = ("moroccan_darija", "french_or_english")  # the track is named "Darija"


class RefinedTranscript(BaseModel):
    """The LLM's answer (also the JSON schema it must follow)."""

    voice: Voice = "moroccan_darija"
    segments: list[RefinedSegment]


class Refiner(Protocol):
    """Turns a timed speech-model draft into a corrected, equally timed transcript."""

    def refine(self, path: Path, draft: Sequence[Word]) -> list[Word]: ...


def build_prompt(draft: Sequence[Word]) -> str:
    """The correction prompt, with the draft words numbered from 0."""
    return PROMPT.format(draft=" ".join(f"{i}:{w.text}" for i, w in enumerate(draft)))


def _spread(text: str, start: float, end: float) -> list[Word]:
    """Split ``text`` into words timed across ``start..end`` in proportion to their length."""
    tokens = text.split()
    total = sum(len(t) for t in tokens)
    words, t = [], start
    for token in tokens:
        step = (end - start) * len(token) / total
        words.append(Word(start=t, end=t + step, text=token))
        t += step
    return words


def parse_refined(raw: str, draft: Sequence[Word]) -> list[Word]:
    """Words of the LLM reply, timed from the draft words each segment covers.

    A voice that is not Moroccan Darija or French/English (Modern Standard Arabic, a song) gives
    no words. Segments holding only a placeholder like ``[...]`` are dropped; no segment at all
    means nobody talks (music only), so the result is empty. Segments with indices outside the draft
    or overlapping an earlier segment are skipped.

    Raises:
        ValueError: If the reply does not match :class:`RefinedTranscript`, or every segment
            with text has unusable indices.
    """
    try:
        reply = RefinedTranscript.model_validate_json(raw)
    except ValidationError as exc:
        raise ValueError(f"refined transcript does not match the schema: {exc}") from exc
    if reply.voice not in SUBTITLED_VOICES:  # standard Arabic, a song...: not what the track is
        log.info("Voice is %s: no subtitles", reply.voice)
        return []
    segments = reply.segments
    words: list[Word] = []
    last, bad = -1, 0
    for seg in segments:
        text = seg.text.strip()
        if not text or _PLACEHOLDER.match(text):
            continue
        if not (last < seg.first <= seg.last < len(draft)):
            bad += 1
            continue
        words += _spread(text, draft[seg.first].start, draft[seg.last].end)
        last = seg.last
    if bad and not words:
        raise ValueError("refined transcript has no usable segment")
    return words


def extract_audio(path: Path) -> bytes:
    """The clip's sound as 16 kHz mono WAV (small enough to send inline)."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
           "-f", "wav", "pipe:1"]  # fmt: skip
    return subprocess.run(cmd, capture_output=True, check=True).stdout


class GeminiAudioRefiner:
    """``Refiner`` that sends the audio and the draft to a Gemini 3 model.

    The reply is constrained to :class:`RefinedTranscript` (no malformed JSON), and thinking is
    kept low: on a 52 s clip it answered in 3 s instead of 189 s with the same text.
    """

    def __init__(self, model: str, fallbacks: Sequence[str] = ()):
        self.model = model
        self.fallbacks = tuple(fallbacks)

    def refine(self, path: Path, draft: Sequence[Word]) -> list[Word]:
        """Corrected words for ``path``, timed like ``draft``."""
        from google.genai import types

        resp = generate_with_fallback(
            (self.model, *self.fallbacks),
            [
                types.Part.from_bytes(data=extract_audio(path), mime_type="audio/wav"),
                build_prompt(draft),
            ],
            types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=RefinedTranscript,
                temperature=0,
                thinking_config=types.ThinkingConfig(thinking_level="low"),
            ),
        )  # the shared client is cached: the SDK closes a client as soon as it is collected
        return parse_refined(resp.text or "", draft)


class RefiningTranscriber:
    """``Transcriber`` that refines another transcriber's draft.

    A draft that cannot be refined is never published: the Darija speech model spells French
    phonetically and mishears words (2026-11-03: gibberish tracks on 6 Shorts).
    """

    def __init__(self, base: Transcriber, refiner: Refiner):
        self.base = base
        self.refiner = refiner

    def transcribe(self, path: Path) -> list[Word]:
        """Draft from ``base``, corrected by ``refiner``; no words if the reply is unusable.

        Raises:
            LLMQuotaExceeded: If the refiner's daily quota is spent.
            LLMUnavailable: If the refiner cannot answer (the run stops and retries later).
        """
        draft = self.base.transcribe(path)
        if not draft:
            return draft
        try:
            return self.refiner.refine(path, draft)
        except ValueError as exc:  # the reply does not fit the draft: no subtitles beat bad ones
            log.warning("Transcript refinement gave no usable text (%s); no subtitles", exc)
            return []
        except Exception as exc:  # network, overloaded model
            if is_daily_quota_error(exc):
                raise LLMQuotaExceeded() from exc
            raise LLMUnavailable(f"Gemini is unavailable ({type(exc).__name__}: {exc})") from exc
