import shutil
import subprocess

import pytest

from social2shorts.phash import min_distance, phash_file, sample_times


def test_min_distance():
    assert min_distance(["ffff0000"], ["ffff0000"]) == 0
    assert min_distance(["ffff0000", "00000000"], ["ffff0001"]) == 1
    assert min_distance([], ["ffff0000"]) > 1000


def test_sample_times_are_inside_the_clip():
    assert sample_times(10.0, 4) == [1.25, 3.75, 6.25, 8.75]


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")
def test_phash_file_seeks_n_frames(tmp_path):
    p = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=s=64x64:d=3:r=10", "-y", str(p)], check=True)  # fmt: skip
    out = phash_file(p, 4)
    assert len(out) == 4 and all(int(h, 16) >= 0 for h in out)
    assert phash_file(p, 0) == []
