from typer.testing import CliRunner

from insta2youtube import cli
from insta2youtube.cli import app
from insta2youtube.db import Store
from insta2youtube.models import CatalogEntry, ItemStatus, Platform
from insta2youtube.pipeline import PipelineDeps, RunReport

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
