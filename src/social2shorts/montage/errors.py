"""Montage engine exceptions: one error type carrying ffmpeg diagnostics."""

from __future__ import annotations


class MontageError(RuntimeError):
    """Raised when probing, analysing or rendering a clip fails."""
