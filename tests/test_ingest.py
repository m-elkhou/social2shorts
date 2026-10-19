import pytest

from insta2youtube.ingest import FakePhoneSource, FileNotOnPhone, ingest_originals


def test_fake_copy_and_missing(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"x")
    fake = FakePhoneSource({"Movies/Edits": [("VID_a.mp4", tmp_path / "a.mp4")]})
    assert any(v.name == "VID_a.mp4" for v in fake.list_videos(["Movies/Edits"]))
    with pytest.raises(FileNotOnPhone):
        fake.copy("VID_missing.mp4", str(tmp_path))


def test_ingest_originals_with_fake_probe(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "b.mp4").write_bytes(b"yy")
    fake = FakePhoneSource({"Movies/Edits": [("a.mp4", tmp_path / "a.mp4"), ("b.mp4", tmp_path / "b.mp4")]})
    dest = tmp_path / "out"
    res = ingest_originals(fake, ["Movies/Edits"], str(dest), probe=lambda p: 15.5)
    assert len(res) == 2
    assert all(o.phashes == [] and o.duration == 15.5 for o in res)
    assert all((dest / o.path.split("\\")[-1].split("/")[-1]).exists() for o in res)
