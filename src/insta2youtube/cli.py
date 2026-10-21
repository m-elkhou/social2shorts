from __future__ import annotations

from enum import Enum
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from insta2youtube import curate as curate_mod
from insta2youtube import youtube_upload
from insta2youtube.config import Settings, load_config
from insta2youtube.db import Store
from insta2youtube.discover import discover_tiktok
from insta2youtube.ingest import MtpPhoneSource, ingest_originals
from insta2youtube.models import ItemStatus
from insta2youtube.pipeline import PipelineDeps, run_backlog, run_continuous

app = typer.Typer(help="Republish curated TikTok videos as YouTube Shorts.", no_args_is_help=True)
console = Console()

CLIENT_SECRETS = "secrets/client_secret.json"
TOKEN_PATH = "secrets/token.json"

class Privacy(str, Enum):
    public = "public"
    unlisted = "unlisted"
    private = "private"


class _LimitedPhone:
    """Wraps a PhoneSource so only the first N videos are listed (and copied)."""

    def __init__(self, inner, limit: int):
        self.inner = inner
        self.limit = limit

    def list_videos(self, folders):
        return self.inner.list_videos(folders)[: self.limit]

    def copy(self, name, dest_dir):
        return self.inner.copy(name, dest_dir)


ConfigOpt = typer.Option("config.toml", "-c", "--config", help="Path to config TOML.")


def _store(settings: Settings) -> Store:
    store = Store(settings.db_path)
    store.init_schema()
    return store


@app.command()
def auth() -> None:
    """Run the OAuth consent flow once and store the token."""
    youtube_upload.get_credentials(CLIENT_SECRETS, TOKEN_PATH)
    console.print("[green]YouTube authorization OK; token stored.[/green]")


@app.command()
def discover(limit: Optional[int] = typer.Option(None, "--limit"), config: str = ConfigOpt) -> None:
    """Discover TikTok videos and upsert them into the catalog."""
    settings = load_config(config)
    store = _store(settings)
    entries = discover_tiktok(settings.tiktok_profile_url, limit)
    for e in entries:
        store.upsert_catalog(e)
    console.print(f"[green]Discovered {len(entries)} video(s); catalog updated.[/green]")


@app.command()
def curate(config: str = ConfigOpt) -> None:
    """Show the curated shortlist."""
    settings = load_config(config)
    shortlist = curate_mod.curate(_store(settings).all_catalog(), settings)
    table = Table(title=f"Shortlist ({len(shortlist)})")
    for col in ("source_id", "duration", "views", "caption"):
        table.add_column(col)
    for e in shortlist:
        table.add_row(e.source_id, f"{e.duration:.0f}s", str(e.view_count), e.caption[:60])
    console.print(table)


@app.command()
def ingest(limit: Optional[int] = typer.Option(None, "--limit"), config: str = ConfigOpt) -> None:
    """Copy originals off the phone into originals_dir."""
    settings = load_config(config)
    phone = MtpPhoneSource(settings.phone_name_match, folders=settings.phone_video_folders)
    if limit is not None:
        phone = _LimitedPhone(phone, max(limit, 0))
    originals = ingest_originals(phone, settings.phone_video_folders, settings.originals_dir)
    table = Table(title=f"Ingested ({len(originals)})")
    table.add_column("path")
    table.add_column("duration")
    for o in originals:
        table.add_row(o.path, f"{o.duration:.1f}s")
    console.print(table)


@app.command()
def run_continuous(
    limit: int = typer.Option(3, "--limit"),
    privacy: Optional[Privacy] = typer.Option(
        None, "--privacy", help="Default: default_privacy from config."
    ),
    config: str = ConfigOpt,
) -> None:
    """Run the continuous pipeline (discover only unseen)."""
    settings = load_config(config)
    phone = MtpPhoneSource(settings.phone_name_match, folders=settings.phone_video_folders)
    deps = PipelineDeps.default(settings)
    report = run_continuous(
        settings, phone, deps, limit, privacy.value if privacy else settings.default_privacy
    )
    table = Table(title="Continuous run report")
    table.add_column("bucket")
    table.add_column("count")
    table.add_column("items")
    for name in ("uploaded", "review", "pending", "skipped"):
        items = getattr(report, name)
        shown = ", ".join(str(i["source_id"] if isinstance(i, dict) else i) for i in items)
        table.add_row(name, str(len(items)), shown)
    console.print(table)
    if report.alert:
        console.print(f"[bold red]ALERT:[/bold red] {report.alert}")


@app.command()
def run(
    limit: int = typer.Option(3, "--limit"),
    privacy: Optional[Privacy] = typer.Option(
        None, "--privacy", help="Default: default_privacy from config."
    ),
    config: str = ConfigOpt,
) -> None:
    """Run the backlog pipeline end to end."""
    settings = load_config(config)
    phone = MtpPhoneSource(settings.phone_name_match, folders=settings.phone_video_folders)
    deps = PipelineDeps.default(settings)
    report = run_backlog(
        settings, phone, deps, limit, privacy.value if privacy else settings.default_privacy
    )
    table = Table(title="Run report")
    table.add_column("bucket")
    table.add_column("count")
    table.add_column("items")
    for name in ("uploaded", "review", "pending", "skipped"):
        items = getattr(report, name)
        shown = ", ".join(str(i["source_id"] if isinstance(i, dict) else i) for i in items)
        table.add_row(name, str(len(items)), shown)
    console.print(table)
    if report.alert:
        console.print(f"[bold red]ALERT:[/bold red] {report.alert}")


@app.command()
def review(
    approve: Optional[str] = typer.Option(None, "--approve", help="source_id to mark ready."),
    config: str = ConfigOpt,
) -> None:
    """List the review queue; optionally approve an item."""
    store = _store(load_config(config))
    if approve:
        known = {e.source_id for e in store.all_catalog()} | {r["source_id"] for r in store.reviews()}
        if approve not in known:
            console.print(f"[red]Unknown source_id: {approve}[/red]")
            raise typer.Exit(1)
        store.set_status(approve, ItemStatus.ready)
        store.resolve_review(approve)
        console.print(f"[green]{approve} approved -> ready.[/green]")
        return
    rows = store.reviews()
    table = Table(title=f"Review queue ({len(rows)})")
    for col in ("source_id", "reason", "created"):
        table.add_column(col)
    for r in rows:
        table.add_row(r["source_id"], r["reason"], str(r["created"]))
    console.print(table)


@app.command()
def publish(
    target: str = typer.Argument(..., help="source_id or the word all"),
    privacy: Privacy = typer.Option(Privacy.public, "--privacy"),
    config: str = ConfigOpt,
) -> None:
    """Flip uploaded video(s) to the given privacy."""
    store = _store(load_config(config))
    rows = store.ledger()
    if target != "all":
        rows = [r for r in rows if r["source_id"] == target]
    if not rows:
        console.print(f"[red]No uploaded video found for {target}.[/red]")
        raise typer.Exit(1)
    ok, failed = [], []
    for r in rows:
        try:
            youtube_upload.set_privacy(r["youtube_id"], privacy.value)
            ok.append(r["source_id"])
            console.print(f"{r['source_id']} ({r['youtube_id']}) -> {privacy.value}")
        except Exception as exc:
            failed.append(r["source_id"])
            console.print(f"[red]{r['source_id']} failed: {type(exc).__name__}: {exc}[/red]")
    console.print(f"Succeeded: {len(ok)}, failed: {len(failed)}")
    if failed:
        raise typer.Exit(1)


@app.command()
def status(config: str = ConfigOpt) -> None:
    """Show catalog counts by status."""
    counts = _store(load_config(config)).status_counts()
    table = Table(title="Catalog status")
    table.add_column("status")
    table.add_column("count")
    for s in ItemStatus:
        table.add_row(s.value, str(counts.get(s.value, 0)))
    console.print(table)


if __name__ == "__main__":
    app()
