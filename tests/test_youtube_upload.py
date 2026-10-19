import os
import stat
import sys
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


def test_first_auth_writes_token_file(monkeypatch, tmp_path):
    class FakeCreds:
        def to_json(self):
            return '{"refresh_token": "r"}'

    class FakeFlow:
        def run_local_server(self, port=0):
            return FakeCreds()

    monkeypatch.setattr(
        yu.InstalledAppFlow, "from_client_secrets_file",
        classmethod(lambda cls, *a, **k: FakeFlow()),
    )
    tok = tmp_path / "secrets" / "token.json"
    creds = get_credentials("cs.json", str(tok))
    assert isinstance(creds, FakeCreds)
    assert json.loads(tok.read_text())["refresh_token"] == "r"


def test_no_refresh_token_raises(tmp_path):
    tok = tmp_path / "token.json"
    tok.write_text(json.dumps({
        "token": "old", "refresh_token": None, "client_id": "c",
        "client_secret": "s", "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": yu.SCOPES, "expiry": "2000-01-01T00:00:00Z",
    }))
    with pytest.raises(AuthRefreshError):
        get_credentials("nope.json", str(tok))


@pytest.mark.parametrize("content", ["{not json", '{"token": "x"}'])
def test_corrupt_or_incomplete_token_raises(tmp_path, content):
    tok = tmp_path / "token.json"
    tok.write_text(content)
    with pytest.raises(AuthRefreshError):
        get_credentials("nope.json", str(tok))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes only")
def test_token_file_mode_0600(monkeypatch, tmp_path):
    class FakeCreds:
        def to_json(self):
            return "{}"

    class FakeFlow:
        def run_local_server(self, port=0):
            return FakeCreds()

    monkeypatch.setattr(
        yu.InstalledAppFlow, "from_client_secrets_file",
        classmethod(lambda cls, *a, **k: FakeFlow()),
    )
    tok = tmp_path / "secrets" / "token.json"
    get_credentials("cs.json", str(tok))
    assert stat.S_IMODE(os.stat(tok).st_mode) == 0o600
    yu._save(FakeCreds(), str(tok))
    tok.chmod(0o644)
    yu._save(FakeCreds(), str(tok))
    assert stat.S_IMODE(os.stat(tok).st_mode) == 0o600
