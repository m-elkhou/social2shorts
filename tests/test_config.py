import pytest
from pydantic import ValidationError

from social2shorts.config import ScheduleSettings, load_config
from social2shorts.models import Privacy
from social2shorts.montage.types import MontageMode


def _write(tmp_path, text: str):
    p = tmp_path / "c.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_defaults(tmp_path):
    s = load_config(_write(tmp_path, ""))
    assert s.min_duration_s == 12.0 and s.phash_frames == 6
    assert s.youtube.default_privacy is Privacy.unlisted
    assert s.youtube.daily_quota_units == 10_000 and s.youtube.upload_cost_units == 1600
    assert s.schedule.publish_slots == ["12:00", "18:00"] and s.gemini_model == "gemini-2.5-flash"
    assert s.montage.mode is MontageMode.highlight


def test_transcription_defaults_and_overrides(tmp_path):
    s = load_config(_write(tmp_path, ""))
    assert s.whisper_language is None and s.transcript_refine_model == "" and s.whisper_vad is True
    s = load_config(_write(tmp_path, 'whisper_language = "ar"\ntranscript_refine_model = "m"\n'))
    assert s.whisper_language == "ar" and s.transcript_refine_model == "m"


def test_sections(tmp_path):
    s = load_config(_write(tmp_path, """
[sources]
inbox_dir = "raw"
instagram_profile_url = "https://instagram.com/x"
[youtube]
language = "fr"
[schedule]
publish_slots = ["18:00", "09:30"]
max_per_day = 1
[montage]
target_duration_s = 25
"""))  # fmt: skip
    assert s.sources.inbox_dir == "raw" and s.youtube.language == "fr"
    assert s.schedule.publish_slots == ["09:30", "18:00"] and s.schedule.max_per_day == 1
    assert s.montage.target_duration_s == 25


def test_legacy_flat_keys(tmp_path):
    s = load_config(_write(tmp_path, """
tiktok_profile_url = "https://www.tiktok.com/@motoride.mh"
default_privacy = "private"
gemini_model = ""
"""))  # fmt: skip
    assert s.sources.tiktok_profile_url.endswith("@motoride.mh")
    assert s.youtube.default_privacy is Privacy.private
    assert s.gemini_model == "gemini-2.5-flash"


def test_env_overrides_file(tmp_path, monkeypatch):
    path = _write(tmp_path, 'db_path = "file.sqlite"\n[youtube]\ndaily_quota_units = 9000\n')
    monkeypatch.setenv("S2S_DB_PATH", "env.sqlite")
    monkeypatch.setenv("S2S_YOUTUBE__DAILY_QUOTA_UNITS", "5000")
    monkeypatch.setenv("S2S_SCHEDULE__PUBLISH_SLOTS", '["07:00"]')
    s = load_config(path)
    assert s.db_path == "env.sqlite" and s.youtube.daily_quota_units == 5000
    assert s.youtube.upload_cost_units == 1600  # untouched sibling kept
    assert s.schedule.publish_slots == ["07:00"]


@pytest.mark.parametrize("slots", [[], ["25:00"], ["9h"]])
def test_invalid_slots_rejected(slots):
    with pytest.raises(ValidationError):
        ScheduleSettings(publish_slots=slots)


def test_unknown_key_rejected(tmp_path):
    with pytest.raises(ValidationError):
        load_config(_write(tmp_path, "phash_treshold = 3\n"))
