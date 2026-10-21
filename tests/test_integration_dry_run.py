import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from social2shorts.config import Settings
from social2shorts.db import Store
from social2shorts.pipeline import PipelineDeps, run_backlog
from social2shorts.ingest import FakePhoneSource
from social2shorts.models import CatalogEntry, Original

def test_full_pipeline_dry_run(tmp_path):
    # 1. Setup Settings
    settings = Settings(
        tiktok_profile_url="https://www.tiktok.com/@motoride.mh",
        youtube_handle="@motoride-mh",
        originals_dir=str(tmp_path / "originals"),
        work_dir=str(tmp_path / "work"),
        db_path=str(tmp_path / "dry_run.sqlite"),
        phone_name_match="VirtualPhone",
        phone_video_folders=["Movies/Edits"],
        gemini_model="gemini-1.5-flash"
    )
    
    # 2. Create a fake original file to simulate a phone video
    orig_dir = Path(settings.originals_dir)
    orig_dir.mkdir(parents=True, exist_ok=True)
    video_path = orig_dir / "test_video.mp4"
    video_path.write_text("fake mp4 content") # In real life this is a binary file

    # 3. Setup Dependencies
    # We use real Discovery and Enrichment, but fake Ingest and Upload
    deps = PipelineDeps.default(settings)
    
    # Mock Ingest to return our fake file
    deps.ingest = lambda phone, folders, dest: [
        Original(path=str(video_path), duration=20.0, phashes=["ffff0000"])
    ]
    
    # Mock pHash to match the reference
    deps.phash_file = lambda path, n: ["ffff0000"]
    
    # Mock Transcode to just return the source path
    deps.transcode = lambda src, dst, ass: src
    
    # Mock Upload
    upload_calls = []
    def mock_upload(path, meta, privacy):
        upload_calls.append((path, meta))
        return "yt_mock_123"
    deps.upload = mock_upload

    # Mock Transcription (to avoid needing a real audio file in this test)
    deps.transcribe = lambda p: [] 

    # 4. Run the pipeline
    phone = FakePhoneSource({})
    report = run_backlog(settings, phone, deps, limit=1, privacy="unlisted")

    # 5. Assertions
    assert len(report.uploaded) == 1, "Should have uploaded one video"
    uploaded_path, meta = upload_calls[0]
    assert uploaded_path == str(video_path)
    assert meta.title is not None
    print(f"\nDry Run Success! Uploaded with title: {meta.title}")

