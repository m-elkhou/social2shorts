"""faster-whisper adapter (``Transcriber``): word-level transcript of a clip, model loaded once."""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any

from social2shorts.montage.types import Word

# What speech models write over music, wind or silence (seen on the channel's clips: "subscribe to
# the channel" on a muted video, "music" on a song, "yeah yeah yeah" loops). Whole phrases, compared
# after dropping punctuation and Arabic vowel marks.
HALLUCINATIONS = (
    "اشتركوا في القناة", "شتركوا في القناة", "اشترك في القناة", "اشتركوا في القناه",
    "لا تنسوا الاشتراك في القناة", "موسيقى", "موسيقي", "ترجمة نانسي قنقر", "ترجمه نانسي قنقر",
    "شكرا للمشاهدة", "شكرا على المشاهدة", "شكرا لكم",
    "sous titres réalisés par la communauté d amara org", "sous titrage société radio canada",
    "merci d avoir regardé cette vidéo", "merci d avoir regardé", "musique", "applaudissements",
    "thanks for watching", "thank you for watching", "please subscribe", "music", "applause",
    "subtitles by the amara org community",
)  # fmt: skip
MIN_REAL_WORDS = 3  # fewer words than this left after the phrases above: nobody is talking
MAX_REPEAT_RATIO = 0.35  # unique / total words under this, over 6+ words: a loop, not speech
_MARKS = re.compile("[\u064b-\u065f\u0670\u0640]")  # Arabic vowel marks and tatweel
_PUNCT = re.compile(r"[\W_]+")
_ALTERNATIVES = "|".join(sorted(map(re.escape, HALLUCINATIONS), key=len, reverse=True))
_NOISE = re.compile(rf"(?<!\w)(?:{_ALTERNATIVES})(?!\w)", re.IGNORECASE)


def _normalize(text: str) -> str:
    return _PUNCT.sub(" ", _MARKS.sub("", text.lower())).strip()


def drop_hallucinations(words: list[Word]) -> list[Word]:
    """No words when the transcript is only a known hallucination or a repeated loop.

    Real speech with a stray hallucinated phrase is kept as is: the refinement step removes it.
    """
    text = _normalize(" ".join(w.text for w in words))
    rest = _normalize(_NOISE.sub(" ", text))
    tokens = rest.split()
    if len(tokens) < MIN_REAL_WORDS:
        return []
    if len(tokens) >= 6 and len(set(tokens)) / len(tokens) <= MAX_REPEAT_RATIO:
        return []
    return words


@functools.cache
def load_model(size: str) -> Any:
    """Load (once per process) a faster-whisper model; multilingual sizes handle FR/AR/EN."""
    from faster_whisper import WhisperModel

    # CPU + int8 on purpose: device/compute_type "auto" segfaults ctranslate2 4.8.3 on CPU (word
    # timestamps) and picks a CUDA GPU whose cuBLAS 12 DLL may be missing. The YouTube quota caps
    # uploads at a few per day, so CPU speed is enough. Use device="cuda" once the CUDA libs exist.
    return WhisperModel(size, device="cpu", compute_type="int8")


class WhisperTranscriber:
    """Transcribes speech with per-word timestamps; silent clips and hallucinations give an
    empty list."""

    def __init__(
        self,
        model_size: str = "small",
        language: str | None = None,
        model: Any = None,
        vad: bool = True,
    ):
        self.model_size = model_size
        self.language = language
        self._model = model
        self.vad = vad  # skip silence; off for the Darija model, whose VAD cut quiet speech

    @property
    def model(self) -> Any:
        """The injected model, or the process-wide cached one."""
        return self._model if self._model is not None else load_model(self.model_size)

    def transcribe(self, path: Path) -> list[Word]:
        """Words spoken in ``path`` with their start/end in seconds."""
        segments, _info = self.model.transcribe(
            str(path), vad_filter=self.vad, word_timestamps=True, language=self.language
        )
        words = []
        for seg in segments:
            for w in seg.words or []:
                text = (w.word or "").strip()
                if text:
                    words.append(Word(start=float(w.start), end=float(w.end), text=text))
        return drop_hallucinations(words)
