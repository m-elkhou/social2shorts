"""
Configuration management module.

This module handles loading application settings from a TOML file and
validating them using Pydantic.
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Application settings for social2shorts.

    Attributes:
        tiktok_profile_url (str): URL of the TikTok profile to discover.
        youtube_handle (str): Handle of the target YouTube channel.
        min_duration_s (float): Minimum video duration to be eligible.
        duration_tol_s (float): Tolerance for duration matching.
        phash_threshold (int): Maximum Hamming distance for pHash matching.
        phash_frames (int): Number of frames to hash per video.
        originals_dir (str): Directory where original high-quality videos are stored.
        work_dir (str): Directory for temporary processing files.
        db_path (str): Path to the SQLite database file.
        phone_name_match (str): Pattern to match the phone device name in MTP.
        phone_video_folders (list[str]): Folders on the phone containing videos.
        gemini_model (str): Name of the Gemini model for metadata generation.
        default_privacy (Literal["public", "unlisted", "private"]): Default YouTube privacy.
    """
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
    """
    Load settings from a TOML file.

    Args:
        path (str): Path to the TOML configuration file.

    Returns:
        Settings: A validated Settings instance.
    """
    with Path(path).open("rb") as f:
        data = tomllib.load(f)
    return Settings(**data)
