import json
import os
import stat
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from googleapiclient.errors import HttpError, ResumableUploadError

from social2shorts.models import ShortMeta
from social2shorts.publish import youtube as yt
from social2shorts.publish.contentid import check_claims, claims_from_item
from social2shorts.publish.youtube import (
    AuthRefreshError,
    QuotaExceededError,
    build_body,
    get_credentials,
    parse_upload_response,
    run_resumable,
)

META = ShortMeta(title="t", description="Night ride", tags=["moto"])


def http_error(status: int, reason: str = "", cls=HttpError) -> HttpError:
    content = json.dumps({"error": {"errors": [{"reason": reason}]}}).encode()
    return cls(SimpleNamespace(status=status, reason=reason), content)


# ---------------------------------------------------------------------- body
def test_build_body_defaults():
    b = build_body(META, "unlisted")
    assert b["status"] == {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False,
                           "containsSyntheticMedia": False}  # fmt: skip
    assert b["snippet"]["description"].endswith("#Shorts") and b["snippet"]["categoryId"] == "2"
    assert "defaultLanguage" not in b["snippet"]


def test_description_ends_with_content_hashtags_then_shorts():
    meta = META.model_copy(
        update={"tags": ["ktm1390superadventure", "moto", "ktm", "road trip", "Moto", "lc8!"]}
    )
    last = build_body(meta, "unlisted")["snippet"]["description"].splitlines()[-1]
    assert last == "#ktm #moto #lc8 #roadtrip #ktm1390superadventure #Shorts"


def test_hashtag_line_stays_under_youtube_limit():
    line = yt.hashtag_line([f"tag{i}" for i in range(20)])
    assert line.count("#") == yt.MAX_DESC_HASHTAGS and line.endswith("#Shorts")


def test_build_body_schedule_language_and_flags():
    at = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    b = build_body(META.model_copy(update={"description": "x #shorts"}), "public",
                   language="fr", publish_at=at, synthetic_media=True)  # fmt: skip
    assert b["status"]["privacyStatus"] == "private"
    assert b["status"]["publishAt"] == "2026-10-24T12:00:00Z"
    assert b["status"]["containsSyntheticMedia"] is True
    assert b["snippet"]["defaultLanguage"] == b["snippet"]["defaultAudioLanguage"] == "fr"
    assert b["snippet"]["description"].lower().count("#shorts") == 1


def test_parse_upload_response_reports_applied_privacy():
    body = build_body(META, "public")
    r = parse_upload_response({"id": "v", "status": {"privacyStatus": "private"}}, body)
    assert (r.video_id, r.privacy, r.publish_at) == ("v", "private", None)
    r = parse_upload_response(
        {"id": "v", "status": {"privacyStatus": "private", "publishAt": "2026-10-24T12:00:00Z"}},
        body,
    )
    assert r.publish_at == datetime(2026, 10, 24, 12, tzinfo=UTC)


# --------------------------------------------------------------------- retry
class Chunks:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)

    def next_chunk(self):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return None, outcome


def test_retries_transient_errors_with_backoff():
    sleeps = []
    req = Chunks(http_error(503), ConnectionResetError(), http_error(500, cls=ResumableUploadError),
                 None, {"id": "V"})  # fmt: skip
    assert run_resumable(req, sleep=sleeps.append) == {"id": "V"}
    assert len(sleeps) == 3 and 2 <= sleeps[0] < 3 and 4 <= sleeps[1] < 5 and 8 <= sleeps[2] < 9


def test_gives_up_after_max_retries():
    sleeps = []
    with pytest.raises(HttpError):
        run_resumable(Chunks(*[http_error(502)] * 4), sleep=sleeps.append, max_retries=3)
    assert len(sleeps) == 3


def test_non_transient_and_quota_errors_are_not_retried():
    with pytest.raises(HttpError):
        run_resumable(Chunks(http_error(400, "invalidTitle")), sleep=pytest.fail)
    with pytest.raises(QuotaExceededError):
        run_resumable(Chunks(http_error(403, "quotaExceeded")), sleep=pytest.fail)


class Videos:
    def __init__(self, response):
        self.response, self.calls = response, []

    def insert(self, **kw):
        self.calls.append(("insert", kw))
        return Chunks(self.response)

    def update(self, **kw):
        self.calls.append(("update", kw))
        return SimpleNamespace(execute=lambda num_retries=0: {})


def test_client_upload_and_set_privacy(tmp_path):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    videos = Videos({"id": "VID", "status": {"privacyStatus": "unlisted"}})
    client = yt.YouTubeClient("cs.json", "tok.json", language="fr",
                              service=SimpleNamespace(videos=lambda: videos))  # fmt: skip
    result = client.upload(video, META, privacy="unlisted")
    assert result.video_id == "VID" and result.privacy == "unlisted"
    kind, kw = videos.calls[0]
    assert kind == "insert" and kw["part"] == "snippet,status"
    assert kw["body"]["snippet"]["defaultLanguage"] == "fr"
    client.set_privacy("VID", "public")
    assert videos.calls[1][1]["body"]["status"]["privacyStatus"] == "public"


class Captions:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    def insert(self, **kw):
        self.calls.append(kw)

        def execute(num_retries=0):
            if self.error:
                raise self.error
            return {"id": "CAP"}

        return SimpleNamespace(execute=execute)


def test_client_add_captions_publishes_the_srt():
    captions = Captions()
    client = yt.YouTubeClient("cs.json", "tok.json",
                              service=SimpleNamespace(captions=lambda: captions))  # fmt: skip
    srt = "1 | 00:00:00,000 --> 00:00:01,000 | سلام".replace(" | ", chr(10))
    client.add_captions("VID", srt, language="ar", name="Darija")
    kw = captions.calls[0]
    assert kw["part"] == "snippet"
    assert kw["body"]["snippet"] == {"videoId": "VID", "language": "ar", "name": "Darija",
                                     "isDraft": False}  # fmt: skip
    media = kw["media_body"]
    assert "سلام".encode() in media.getbytes(0, media.size())


def test_client_add_captions_reports_spent_quota():
    client = yt.YouTubeClient("cs.json", "tok.json", service=SimpleNamespace(
        captions=lambda: Captions(http_error(403, "quotaExceeded"))))  # fmt: skip
    with pytest.raises(QuotaExceededError):
        client.add_captions("VID", "srt", language="ar")


# ----------------------------------------------------------------- contentid
def test_claims_from_item_signals():
    item = {"id": "v", "status": {"uploadStatus": "rejected", "rejectionReason": "copyright"},
            "contentDetails": {"regionRestriction": {"blocked": ["DE", "FR"]},
                               "licensedContent": True}}  # fmt: skip
    reasons = [c.reason for c in claims_from_item(item)]
    assert reasons == [
        "rejected: copyright",
        "blocked in 2 countries",
        "claimed as licensed content",
    ]
    assert claims_from_item({"id": "v", "status": {"uploadStatus": "processed"}}) == []


def test_check_claims_batches_and_flags_missing():
    calls = []

    def list_(part, id):
        calls.append(id)
        items = [{"id": v, "status": {"uploadStatus": "processed"}} for v in id.split(",")
                 if v != "gone"]  # fmt: skip
        return SimpleNamespace(execute=lambda num_retries=0: {"items": items})

    service = SimpleNamespace(videos=lambda: SimpleNamespace(list=list_))
    ids = [f"v{i}" for i in range(60)] + ["gone"]
    claims = check_claims(service, ids)
    assert len(calls) == 2 and [c.video_id for c in claims] == ["gone"]


# --------------------------------------------------------------------- OAuth
def _token(tmp_path, **kw):
    tok = tmp_path / "token.json"
    data = {"token": "old", "refresh_token": "r", "client_id": "c", "client_secret": "s",
            "token_uri": "https://oauth2.googleapis.com/token", "scopes": yt.SCOPES,
            "expiry": "2000-01-01T00:00:00Z", **kw}  # fmt: skip
    tok.write_text(json.dumps(data))
    return tok


def test_refresh_failure_raises(monkeypatch, tmp_path):
    def boom(self, request):
        raise RuntimeError("invalid_grant")

    monkeypatch.setattr("google.oauth2.credentials.Credentials.refresh", boom)
    with pytest.raises(AuthRefreshError):
        get_credentials("nope.json", str(_token(tmp_path)))


def test_no_refresh_token_raises(tmp_path):
    with pytest.raises(AuthRefreshError):
        get_credentials("nope.json", str(_token(tmp_path, refresh_token=None)))


@pytest.mark.parametrize("content", ["{not json", '{"token": "x"}'])
def test_corrupt_token_raises(tmp_path, content):
    tok = tmp_path / "token.json"
    tok.write_text(content)
    with pytest.raises(AuthRefreshError):
        get_credentials("nope.json", str(tok))


def test_unattended_never_opens_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(yt.InstalledAppFlow, "from_client_secrets_file", pytest.fail)
    with pytest.raises(AuthRefreshError, match="no YouTube token"):
        get_credentials("cs.json", str(tmp_path / "missing.json"), interactive=False)


class FakeCreds:
    def to_json(self):
        return '{"refresh_token": "r"}'


def test_first_auth_writes_token(monkeypatch, tmp_path):
    flow = SimpleNamespace(run_local_server=lambda port=0: FakeCreds())
    monkeypatch.setattr(yt.InstalledAppFlow, "from_client_secrets_file",
                        classmethod(lambda cls, *a, **k: flow))  # fmt: skip
    tok = tmp_path / "secrets" / "token.json"
    assert isinstance(get_credentials("cs.json", str(tok)), FakeCreds)
    assert json.loads(tok.read_text())["refresh_token"] == "r"
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(tok).st_mode) == 0o600
        tok.chmod(0o644)
        yt._save(FakeCreds(), str(tok))
        assert stat.S_IMODE(os.stat(tok).st_mode) == 0o600
