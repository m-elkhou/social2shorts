import json

import pytest

from insta2youtube import youtube_upload as yu
from insta2youtube.models import ShortMeta
from insta2youtube.youtube_upload import AuthRefreshError, build_body, get_credentials

META = ShortMeta(title="t", description="d", tags=["moto"])


def test_build_body_sets_privacy_and_kids():
    b = build_body(META, "unlisted")
    assert b["status"]["privacyStatus"] == "unlisted"
    assert b["status"]["selfDeclaredMadeForKids"] is False
    assert b["snippet"]["title"] == "t"
    assert b["snippet"]["categoryId"] == yu.CATEGORY_ID


def test_refresh_failure_raises(monkeypatch, tmp_path):
    tok = tmp_path / "token.json"
    tok.write_text(json.dumps({
        "token": "old", "refresh_token": "r", "client_id": "c",
        "client_secret": "s", "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": yu.SCOPES, "expiry": "2000-01-01T00:00:00Z",
    }))

    def boom(self, request):
        raise RuntimeError("invalid_grant")

    monkeypatch.setattr("google.oauth2.credentials.Credentials.refresh", boom)
    with pytest.raises(AuthRefreshError):
        get_credentials("nope.json", str(tok))


class _Req:
    def __init__(self, resp):
        self.resp = resp

    def next_chunk(self):
        return None, self.resp

    def execute(self):
        return self.resp


class _Videos:
    def __init__(self):
        self.calls = []

    def insert(self, **kw):
        self.calls.append(("insert", kw))
        return _Req({"id": "VID123"})

    def update(self, **kw):
        self.calls.append(("update", kw))
        return _Req({})


class _Svc:
    def __init__(self):
        self.v = _Videos()

    def videos(self):
        return self.v


def test_upload_short_uses_body_and_returns_id(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    svc = _Svc()
    vid = yu.upload_short(str(f), META, "unlisted", service=svc)
    assert vid == "VID123"
    kind, kw = svc.v.calls[0]
    assert kind == "insert" and kw["body"] == build_body(META, "unlisted")
    assert kw["part"] == "snippet,status"


def test_set_privacy_updates_status():
    svc = _Svc()
    yu.set_privacy("VID123", "public", service=svc)
    kind, kw = svc.v.calls[0]
    assert kind == "update" and kw["part"] == "status"
    assert kw["body"]["id"] == "VID123"
    assert kw["body"]["status"]["privacyStatus"] == "public"
