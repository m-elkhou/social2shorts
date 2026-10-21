from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    tiktok_profile_url: str
    youtube_handle: str
    min_duration_s: float = 12.0
    duration_tol_s: float = 1.0
    phash_threshold: int = 10
    phash_frames: int = 6
    originals_dir: str
    work_dir: str
    db_path: str
    phone_name_match: str = ""
    phone_video_folders: list[str] = []
    gemini_model: str = ""
    default_privacy: Literal["public", "unlisted", "private"] = "unlisted"


def load_config(path: str) -> Settings:
    with Path(path).open("rb") as f:
        data = tomllib.load(f)
    return Settings(**data)
