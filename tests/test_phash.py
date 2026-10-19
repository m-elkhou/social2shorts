import shutil
import subprocess

import pytest

from insta2youtube.phash import min_distance, phash_file


def test_min_distance_zero_on_identical():
    assert min_distance(["ffff0000"], ["ffff0000"]) == 0


def test_min_distance_picks_minimum_and_handles_empty():
    assert min_distance(["ffff0000", "00000000"], ["ffff0001"]) == 1
    assert min_distance([], ["ffff0000"]) > 1000


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")
def test_phash_file_returns_n_hex(tmp_path):
    p = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=red:s=64x64:d=1", "-y", str(p)], check=True)
    out = phash_file(str(p), 4)
    assert len(out) == 4
    assert all(int(h, 16) >= 0 for h in out)
