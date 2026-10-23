from pathlib import Path

from fakes import FakeMedia, entry

from social2shorts.db import Store
from social2shorts.match import index_originals, match_one
from social2shorts.models import ItemStatus, Original


def test_match_ambiguous_lists_candidates_best_first():
    o1 = Original(path="A.mp4", duration=20.0, phashes=["ffff0003"])
    o2 = Original(path="B.mp4", duration=20.3, phashes=["ffff0000"])
    m = match_one(entry("s", 20.0), [o1, o2], ["ffff0000"], dur_tol=1.0, phash_threshold=10)
    assert m.status is ItemStatus.ambiguous and m.original_path is None
    assert m.candidates == ["B.mp4", "A.mp4"]


def test_match_unique_winner():
    o1 = Original(path="A.mp4", duration=20.0, phashes=["ffff0000"])
    o2 = Original(path="B.mp4", duration=45.0, phashes=["00000000"])
    m = match_one(entry("s", 20.0), [o1, o2], ["ffff0000"], dur_tol=1.0, phash_threshold=5)
    assert m.status is ItemStatus.matched and m.original_path == "A.mp4" and m.confidence == 1.0


def test_match_pending_when_no_candidate():
    o = Original(path="B.mp4", duration=45.0, phashes=["ffff0000"])
    m = match_one(entry("s", 20.0), [o], ["ffff0000"], dur_tol=1.0, phash_threshold=5)
    assert m.status is ItemStatus.pending_original and m.confidence == 0.0


def test_index_originals_uses_cache(tmp_path: Path):
    (tmp_path / "A.mp4").write_bytes(b"a")
    store = Store(tmp_path / "db.sqlite")
    store.init_schema()
    media = FakeMedia(durations={"A": 20.0}, hashes={"A": "ff"})
    first = index_originals([tmp_path / "A.mp4"], store, media, 6)
    media.durations["A"] = 99.0  # would change if recomputed
    again = index_originals([tmp_path / "A.mp4"], store, media, 6)
    assert first == again and again[0].duration == 20.0 and again[0].phashes == ["ff"]
