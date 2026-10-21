from typer.testing import CliRunner

from social2shorts import cli
from social2shorts.cli import app
from social2shorts.db import Store
from social2shorts.models import CatalogEntry, ItemStatus, Platform
from social2shorts.pipeline import PipelineDeps, RunReport

runner = CliRunner()


def _cfg(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        'tiktok_profile_url = "u"\nyoutube_handle = "h"\n'
        f'originals_dir = "{(tmp_path / "o").as_posix()}"\n'
        f'work_dir = "{(tmp_path / "w").as_posix()}"\n'
        f'db_path = "{(tmp_path / "db.sqlite").as_posix()}"\n'
    )
    return str(p), Store(str(tmp_path / "db.sqlite"))


def _entry(sid):
    return CatalogEntry(platform=Platform.tiktok, source_id=sid, url="u", caption="c",
                        duration=20, view_count=1, like_count=1, comment_count=0,
                        upload_date="20260101", track="", artist="")


def test_run_invokes_pipeline(monkeypatch, tmp_path):
    cfg, _ = _cfg(tmp_path)
    called = {}

    def fake(*a, **k):
        called["args"] = a
        return RunReport(uploaded=[{"source_id": "A", "youtube_id": "y"}])

    monkeypatch.setattr(cli, "run_backlog", fake)
    monkeypatch.setattr(PipelineDeps, "default", classmethod(lambda cls, s: object()))
    monkeypatch.setattr(cli, "MtpPhoneSource", lambda *a, **k: object())
    result = runner.invoke(app, ["run", "--limit", "3", "-c", cfg])
    assert result.exit_code == 0 and called["args"][3] == 3 and called["args"][4] == "unlisted"


def _patch_run(monkeypatch):
    called = {}

    def fake(*a, **k):
        called["args"] = a
        return RunReport()

    monkeypatch.setattr(cli, "run_backlog", fake)
    monkeypatch.setattr(PipelineDeps, "default", classmethod(lambda cls, s: object()))
    monkeypatch.setattr(cli, "MtpPhoneSource", lambda *a, **k: object())
    return called


def test_run_rejects_invalid_privacy(monkeypatch, tmp_path):
    cfg, _ = _cfg(tmp_path)
    called = _patch_run(monkeypatch)
    result = runner.invoke(app, ["run", "--privacy", "bogus", "-c", cfg])
    assert result.exit_code != 0 and "args" not in called


def test_run_uses_config_default_privacy(monkeypatch, tmp_path):
    cfg, _ = _cfg(tmp_path)
    with open(cfg, "a") as f:
        f.write('default_privacy = "private"\n')
    called = _patch_run(monkeypatch)
    assert runner.invoke(app, ["run", "-c", cfg]).exit_code == 0
    assert called["args"][4] == "private"
    called.clear()
    assert runner.invoke(app, ["run", "--privacy", "public", "-c", cfg]).exit_code == 0
    assert called["args"][4] == "public"


def test_status_and_review(tmp_path):
    cfg, store = _cfg(tmp_path)
    store.init_schema()
    store.upsert_catalog(_entry("A"))
    store.add_review("A", "licensed music")
    r = runner.invoke(app, ["status", "-c", cfg])
    assert r.exit_code == 0 and "discovered" in r.output
    r = runner.invoke(app, ["review", "-c", cfg])
    assert r.exit_code == 0 and "licensed" in r.output
    r = runner.invoke(app, ["review", "--approve", "A", "-c", cfg])
    assert r.exit_code == 0
    assert store.status_counts() == {ItemStatus.ready.value: 1}


def test_publish_calls_set_privacy(monkeypatch, tmp_path):
    cfg, store = _cfg(tmp_path)
    store.init_schema()
    store.mark_uploaded("fp", "A", "yt1")
    calls = []
    monkeypatch.setattr(cli.youtube_upload, "set_privacy", lambda v, p: calls.append((v, p)))
    r = runner.invoke(app, ["publish", "A", "-c", cfg])
    assert r.exit_code == 0 and calls == [("yt1", "public")]
    r = runner.invoke(app, ["publish", "nope", "-c", cfg])
    assert r.exit_code == 1


def test_publish_rejects_invalid_privacy(monkeypatch, tmp_path):
    cfg, store = _cfg(tmp_path)
    store.init_schema()
    store.mark_uploaded("fp", "A", "yt1")
    calls = []
    monkeypatch.setattr(cli.youtube_upload, "set_privacy", lambda v, p: calls.append((v, p)))
    r = runner.invoke(app, ["publish", "A", "--privacy", "publc", "-c", cfg])
    assert r.exit_code != 0 and calls == []


def test_publish_all_continues_after_failure(monkeypatch, tmp_path):
    cfg, store = _cfg(tmp_path)
    store.init_schema()
    store.mark_uploaded("f1", "A", "yt1")
    store.mark_uploaded("f2", "B", "yt2")
    calls = []

    def fake(v, p):
        calls.append(v)
        if v == "yt1":
            raise RuntimeError("boom")

    monkeypatch.setattr(cli.youtube_upload, "set_privacy", fake)
    r = runner.invoke(app, ["publish", "all", "-c", cfg])
    assert calls == ["yt1", "yt2"] and r.exit_code == 1 and "Succeeded: 1, failed: 1" in r.output


def test_review_approve_unknown_exits_1(tmp_path):
    cfg, store = _cfg(tmp_path)
    store.init_schema()
    r = runner.invoke(app, ["review", "--approve", "nope", "-c", cfg])
    assert r.exit_code == 1


def test_review_approve_removes_row(tmp_path):
    cfg, store = _cfg(tmp_path)
    store.init_schema()
    store.upsert_catalog(_entry("A"))
    store.add_review("A", "x")
    r = runner.invoke(app, ["review", "--approve", "A", "-c", cfg])
    assert r.exit_code == 0 and store.reviews() == []


def test_ingest_limit_bounds_copy(monkeypatch, tmp_path):
    from social2shorts.ingest import FakePhoneSource, PhoneVideo
    cfg, _ = _cfg(tmp_path)
    copied = []

    class P:
        def list_videos(self, folders):
            return [PhoneVideo.__new__(PhoneVideo) for _ in range(3)]

        def copy(self, name, dest):
            copied.append(name)
            return name

    seen = {}

    def fake_ingest(phone, folders, dest):
        seen["n"] = len(phone.list_videos(folders))
        return []

    monkeypatch.setattr(cli, "MtpPhoneSource", lambda *a, **k: P())
    monkeypatch.setattr(cli, "ingest_originals", fake_ingest)
    for lim, n in (("2", 2), ("0", 0)):
        r = runner.invoke(app, ["ingest", "--limit", lim, "-c", cfg])
        assert r.exit_code == 0 and seen["n"] == n
