"""faster-whisper adapter (``Transcriber``): word-level transcript of a clip, model loaded once."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

from social2shorts.montage.types import Word


@functools.cache
def load_model(size: str) -> Any:
    """Load (once per process) a faster-whisper model; multilingual sizes handle FR/AR/EN."""
    from faster_whisper import WhisperModel

    return WhisperModel(size, device="auto", compute_type="auto")


class WhisperTranscriber:
    """Transcribes speech with per-word timestamps; silent clips give an empty list."""

    def __init__(self, model_size: str = "small", language: str | None = None, model: Any = None):
        self.model_size = model_size
        self.language = language
        self._model = model

    @property
    def model(self) -> Any:
        """The injected model, or the process-wide cached one."""
        return self._model if self._model is not None else load_model(self.model_size)

    def transcribe(self, path: Path) -> list[Word]:
        """Words spoken in ``path`` with their start/end in seconds."""
        segments, _info = self.model.transcribe(
            str(path), vad_filter=True, word_timestamps=True, language=self.language
        )
        words = []
        for seg in segments:
            for w in seg.words or []:
                text = (w.word or "").strip()
                if text:
                    words.append(Word(start=float(w.start), end=float(w.end), text=text))
        return words
