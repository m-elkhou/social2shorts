"""Application settings: TOML file + ``S2S_*`` environment overrides, validated by pydantic."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from social2shorts.models import Platform, Privacy
from social2shorts.montage.types import MontageSettings

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class SourcesSettings(BaseModel):
    """Where videos come from (``[sources]``)."""

    tiktok_profile_url: str = ""
    instagram_profile_url: str = ""
    instagram_cookies: str | None = None
    inbox_dir: str = "inbox"
    prefer_platform: Platform = Platform.tiktok  # kept when a post exists on both platforms


class YouTubeSettings(BaseModel):
    """Upload target and API budget (``[youtube]``)."""

    client_secrets: str = "secrets/client_secret.json"
    token_path: str = "secrets/token.json"
    category_id: str = "2"  # Autos & Vehicles
    default_privacy: Privacy = Privacy.unlisted
    daily_quota_units: int = Field(10_000, gt=0)
    upload_cost_units: int = Field(1_600, gt=0)
    max_uploads_per_day: int = Field(100, gt=0)  # separate per-project videos.insert cap
    made_for_kids: bool = False  # status.selfDeclaredMadeForKids
    contains_synthetic_media: bool = False  # status.containsSyntheticMedia (AI disclosure)
    language: str | None = None  # e.g. "fr": sets defaultLanguage + defaultAudioLanguage


class ScheduleSettings(BaseModel):
    """Publication cadence for ``--privacy scheduled`` (``[schedule]``)."""

    timezone: str = "Africa/Casablanca"
    publish_slots: list[str] = Field(default_factory=lambda: ["12:00", "18:00"])
    max_per_day: int = Field(2, gt=0)
    min_lead_minutes: int = Field(60, ge=15)  # YouTube needs publishAt in the future

    @field_validator("publish_slots")
    @classmethod
    def _check_slots(cls, slots: list[str]) -> list[str]:
        bad = [s for s in slots if not _HHMM.match(s)]
        if bad or not slots:
            raise ValueError(f"publish_slots must be non-empty HH:MM values, got {slots}")
        return sorted(set(slots))


class Settings(BaseSettings):
    """All settings. Environment variables (``S2S_DB_PATH``, ``S2S_YOUTUBE__LANGUAGE``…)
    override values from the TOML file and from explicit arguments."""

    model_config = SettingsConfigDict(env_prefix="S2S_", env_nested_delimiter="__")

    youtube_handle: str = ""
    originals_dir: str = "originals"
    work_dir: str = "work"
    db_path: str = "social2shorts.sqlite"
    min_duration_s: float = 12.0
    duration_tol_s: float = 1.0
    phash_threshold: int = 10
    phash_frames: int = 6
    phone_name_match: str = ""
    phone_video_folders: list[str] = Field(default_factory=list)
    gemini_model: str = "gemini-2.5-flash"
    whisper_model: str = "small"
    sources: SourcesSettings = Field(default_factory=SourcesSettings)
    youtube: YouTubeSettings = Field(default_factory=YouTubeSettings)
    schedule: ScheduleSettings = Field(default_factory=ScheduleSettings)
    montage: MontageSettings = Field(default_factory=MontageSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Give environment variables priority over file/argument values."""
        return env_settings, init_settings, file_secret_settings


# Old flat keys (pre-sections config.toml) -> (section, key).
_LEGACY_KEYS = {
    "tiktok_profile_url": ("sources", "tiktok_profile_url"),
    "instagram_profile_url": ("sources", "instagram_profile_url"),
    "instagram_cookies": ("sources", "instagram_cookies"),
    "inbox_dir": ("sources", "inbox_dir"),
    "default_privacy": ("youtube", "default_privacy"),
}


def upgrade_legacy(data: dict[str, Any]) -> dict[str, Any]:
    """Move old flat keys into their section (section values win) and fix empty ones."""
    out = dict(data)
    for old, (section, key) in _LEGACY_KEYS.items():
        if old in out:
            value = out.pop(old)
            out.setdefault(section, {}).setdefault(key, value)
    if out.get("gemini_model") == "":
        out.pop("gemini_model")
    return out


def load_config(path: str | Path) -> Settings:
    """Load and validate settings from a TOML file (plus ``S2S_*`` env overrides).

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    with Path(path).open("rb") as f:
        data = tomllib.load(f)
    return Settings(**upgrade_legacy(data))
