import pytest
from unittest.mock import MagicMock, patch
import insta2youtube.contentid as contentid

def test_guard_after_upload_no_claims():
    service = MagicMock()
    on_claim = MagicMock()
    video_id = "vid123"

    with patch("insta2youtube.contentid.check_claims", return_value=[]), \
         patch("insta2youtube.contentid.youtube_upload", create=True) as mock_yt:

        contentid.guard_after_upload(video_id, on_claim, service)
        on_claim.assert_not_called()
        mock_yt.set_privacy.assert_not_called()

def test_guard_after_upload_with_claims():
    service = MagicMock()
    on_claim = MagicMock()
    video_id = "vid123"

    # We need to mock the import of youtube_upload.
    # Since it happens inside guard_after_upload, we can patch the import mechanism.
    # Or more simply, mock the youtube_upload module in sys.modules.
    import sys
    mock_yt = MagicMock()
    sys.modules["insta2youtube.youtube_upload"] = mock_yt

    try:
        with patch("insta2youtube.contentid.check_claims", return_value=[contentid.Claim(reason="Copyrighted Music", severity="high")]):
            contentid.guard_after_upload(video_id, on_claim, service)

            # Verify privacy set to private
            mock_yt.set_privacy.assert_called_once_with(video_id, "private", service=service)
            # Verify on_claim called with the reason
            on_claim.assert_called_once_with("Copyrighted Music")
    finally:
        del sys.modules["insta2youtube.youtube_upload"]
