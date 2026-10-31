from pathlib import Path
from types import SimpleNamespace

from social2shorts import montage
from social2shorts.montage.types import Cut, MontageSettings, Word
from social2shorts.renderer import MontageRenderer


def test_render_returns_words_in_the_shorts_time(monkeypatch, tmp_path):
    def make_short(src, dst, **kw):
        return SimpleNamespace(path=dst, plan=SimpleNamespace(cuts=[Cut(start=10, end=20)]))

    monkeypatch.setattr(montage, "make_short", make_short)
    words = [Word(start=2, end=3, text="cut"), Word(start=12, end=13, text="kept")]
    out = MontageRenderer().render(Path("src.mp4"), tmp_path / "out.mp4", words=words,
                                   settings=MontageSettings(), hook=None)  # fmt: skip
    assert out.path == tmp_path / "out.mp4"
    assert [(w.start, w.end, w.text) for w in out.words] == [(2.0, 3.0, "kept")]
