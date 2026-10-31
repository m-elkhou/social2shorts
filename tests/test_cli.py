from datetime import UTC, datetime
from types import SimpleNamespace

from fakes import entry
from typer.testing import CliRunner

from social2shorts import cli
from social2shorts.cli import app
from social2shorts.db import Store
from social2shorts.models import ItemStatus, Privacy, ReviewKind, Upload
from social2shorts.pipeline import RunReport, SourceChoice
from social2shorts.publish.quota import quota_day

runner = CliRunner()


def _cfg(tmp_path, extra: str = ""):
    p = tmp_path / "config.toml"
    p.write_text(f'db_path = "{(tmp_path / "db.sqlite").as_posix()}"\n'
                 f'work_dir = "{(tmp_path / "w").as_posix()}"\n{extra}')  # fmt: skip
    store = Store(tmp_path / "db.sqlite")
    store.init_schema()
    return str(p), store


def _patch_run(monkeypatch, report: RunReport | None = None) -> dict:
    called = {}

    def fake(settings, deps, options):
        called["options"] = options
        return report or RunReport()

    monkeypatch.setattr(cli, "run_pipeline", fake)
    monkeypatch.setattr(cli.PipelineDeps, "default", classmethod(lambda cls, s: object()))
    return called


def test_run_passes_options(monkeypatch, tmp_path):
    cfg, _ = _cfg(tmp_path, '[youtube]\ndefault_privacy = "private"\n')
    called = _patch_run(monkeypatch)
    assert runner.invoke(app, ["run", "-c", cfg]).exit_code == 0
    o = called["options"]
    assert o.limit == 3 and o.privacy is Privacy.private and o.source is SourceChoice.all
    args = ["run", "--source", "raw", "--privacy", "scheduled", "--continuous", "--dry-run"]
    assert runner.invoke(app, [*args, "-c", cfg]).exit_code == 0
    o = called["options"]
    assert o.limit is None and o.privacy is Privacy.scheduled and o.dry_run
    assert o.source is SourceChoice.raw


def test_run_rejects_invalid_privacy(monkeypatch, tmp_path):
    cfg, _ = _cfg(tmp_path)
    called = _patch_run(monkeypatch)
    assert runner.invoke(app, ["run", "--privacy", "bogus", "-c", cfg]).exit_code != 0
    assert "options" not in called


def test_run_alert_exits_2_and_explains(monkeypatch, tmp_path):
    cfg, _ = _cfg(tmp_path)
    _patch_run(monkeypatch, RunReport(alert="YouTube authorization failed", review=["B"]))
    r = runner.invoke(app, ["run", "-c", cfg])
    assert r.exit_code == 2 and "ALERT" in r.output and "social2shorts review" in r.output


def test_missing_config_is_actionable(tmp_path):
    r = runner.invoke(app, ["status", "-c", str(tmp_path / "nope.toml")])
    assert r.exit_code == 1 and "config.example.toml" in r.output


def test_review_list_approve_reject(tmp_path):
    cfg, store = _cfg(tmp_path)
    store.upsert_catalog(entry("A"))
    store.upsert_catalog(entry("B"))
    store.add_review("A", ReviewKind.audio, "licensed music")
    store.add_review("B", ReviewKind.audio, "licensed music")
    r = runner.invoke(app, ["review", "-c", cfg])
    assert r.exit_code == 0 and "licensed" in r.output
    assert runner.invoke(app, ["review", "--approve", "A", "-c", cfg]).exit_code == 0
    assert runner.invoke(app, ["review", "--reject", "B", "-c", cfg]).exit_code == 0
    assert store.item("A").status is ItemStatus.ready
    assert store.item("B").status is ItemStatus.rejected and store.reviews() == []
    r = runner.invoke(app, ["review", "--approve", "nope", "-c", cfg])
    assert r.exit_code == 1 and "Unknown item" in r.output


def test_review_ambiguous_needs_original(tmp_path):
    cfg, store = _cfg(tmp_path)
    store.upsert_catalog(entry("A"))
    store.set_status("A", ItemStatus.ambiguous)
    r = runner.invoke(app, ["review", "--approve", "A", "-c", cfg])
    assert r.exit_code == 1 and "--original" in r.output
    original = tmp_path / "A.mp4"
    original.write_bytes(b"x")
    r = runner.invoke(app, ["review", "--approve", "A", "--original", str(original), "-c", cfg])
    assert r.exit_code == 0
    assert store.item("A").source_path == str(original)


def _uploaded(store, key, vid):
    store.record_upload(Upload(youtube_id=vid, item_key=key, fingerprint=f"fp{key}",
                               privacy="unlisted", uploaded_at=datetime.now(UTC)))  # fmt: skip


class Platform:
    def __init__(self, fail_on=None):
        self.calls, self.fail_on = [], fail_on

    def set_privacy(self, vid, privacy):
        self.calls.append((vid, privacy))
        if vid == self.fail_on:
            raise RuntimeError("boom")


def _patch_platform(monkeypatch, platform):
    deps = SimpleNamespace(platform=platform, clock=lambda: datetime.now(UTC))
    monkeypatch.setattr(cli.PipelineDeps, "default", classmethod(lambda cls, s: deps))


def test_publish_one_and_all(monkeypatch, tmp_path):
    cfg, store = _cfg(tmp_path)
    _uploaded(store, "A", "yt1")
    _uploaded(store, "B", "yt2")
    platform = Platform(fail_on="yt1")
    _patch_platform(monkeypatch, platform)
    assert runner.invoke(app, ["publish", "all", "-c", cfg]).exit_code != 0  # --privacy required
    r = runner.invoke(app, ["publish", "all", "--privacy", "public", "-c", cfg])
    assert platform.calls == [("yt1", "public"), ("yt2", "public")]
    assert r.exit_code == 1 and "Succeeded: 1, failed: 1" in r.output
    assert [u.privacy for u in store.uploads()] == ["unlisted", "public"]
    assert runner.invoke(app, ["publish", "nope", "--privacy", "public", "-c", cfg]).exit_code == 1
    assert runner.invoke(app, ["publish", "A", "--privacy", "publc", "-c", cfg]).exit_code != 0


def test_status_and_schedule(tmp_path):
    cfg, store = _cfg(tmp_path)
    store.upsert_catalog(entry("A"))
    store.spend_quota(quota_day(datetime.now(UTC)), 3200, uploads=2)
    r = runner.invoke(app, ["status", "-c", cfg])
    assert r.exit_code == 0 and "discovered" in r.output and "3200/10000" in r.output
    assert "room for 3 more upload" in r.output  # 6800 left, 1600 + 400 (subtitles) each
    r = runner.invoke(app, ["schedule", "--count", "2", "-c", cfg])
    assert r.exit_code == 0 and r.output.count("free slot") == 2
