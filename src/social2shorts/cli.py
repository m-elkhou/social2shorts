"""Command-line interface (typer + rich): thin wrappers over the pipeline and operator actions."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated, NoReturn
from zoneinfo import ZoneInfo

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from social2shorts import operations
from social2shorts.config import Settings, load_config
from social2shorts.curate import curate as curate_entries
from social2shorts.curate import score
from social2shorts.db import Store
from social2shorts.match import index_originals
from social2shorts.media import LocalMedia
from social2shorts.models import Privacy
from social2shorts.pipeline import (
    PipelineDeps,
    RunOptions,
    RunReport,
    SourceChoice,
    run_pipeline,
    utcnow,
)
from social2shorts.publish.quota import QuotaBudget
from social2shorts.publish.youtube import AuthRefreshError, get_credentials
from social2shorts.sources.phone import MtpPhoneSource, PhoneOriginals
from social2shorts.sources.tiktok_instagram import SocialCatalog

app = typer.Typer(help="Turn moto videos into YouTube Shorts, safely.", no_args_is_help=True)
console = Console()

ConfigOpt = Annotated[str, typer.Option("-c", "--config", help="Path to config TOML.")]
DEFAULT_CONFIG = "config.toml"


@app.callback()
def _setup(verbose: Annotated[bool, typer.Option("-v", "--verbose")] = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False)],
        force=True,
    )


def _settings(config: str) -> Settings:
    try:
        return load_config(config)
    except FileNotFoundError:
        _fail(f"Config file {config} not found. Copy config.example.toml to {config} and edit it.")
    except ValidationError as exc:
        _fail(f"{config} has invalid values:\n{exc}")


def _store(settings: Settings) -> Store:
    store = Store(settings.db_path)
    store.init_schema()
    return store


def _fail(message: str, code: int = 1) -> NoReturn:
    console.print(f"[red]{message}[/red]")
    raise typer.Exit(code)


def _ok(message: str) -> None:
    console.print(f"[green]{message}[/green]")


@app.command()
def auth(config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Link the YouTube channel (opens a browser once) and clear auth alerts."""
    settings = _settings(config)
    yt = settings.youtube
    if not Path(yt.client_secrets).is_file():
        _fail(
            f"OAuth client file {yt.client_secrets} is missing. Download it from Google Cloud "
            "Console (APIs & Services > Credentials) and save it there."
        )
    get_credentials(yt.client_secrets, yt.token_path, interactive=True)
    _store(settings).resolve_alerts()
    _ok(f"YouTube authorization OK; token saved to {yt.token_path}.")


@app.command()
def discover(
    limit: Annotated[int | None, typer.Option(help="Max posts per platform.")] = None,
    config: ConfigOpt = DEFAULT_CONFIG,
) -> None:
    """List posts from the TikTok/Instagram profiles into the catalog."""
    settings = _settings(config)
    src = settings.sources
    entries = SocialCatalog(
        src.tiktok_profile_url, src.instagram_profile_url, src.instagram_cookies, limit
    ).discover()
    if not entries:
        _fail("No posts found. Check [sources] profile URLs (and instagram_cookies), then retry.")
    store = _store(settings)
    for e in entries:
        store.upsert_catalog(e)
    by_platform = {p: sum(e.platform == p for e in entries) for p in {e.platform for e in entries}}
    detail = ", ".join(f"{p.value}: {n}" for p, n in sorted(by_platform.items()))
    _ok(f"Catalog updated with {len(entries)} posts ({detail}). Next: `social2shorts curate`.")


@app.command()
def curate(
    top: Annotated[int, typer.Option(help="How many to show.")] = 30,
    config: ConfigOpt = DEFAULT_CONFIG,
) -> None:
    """Show the best catalog posts (deduped across platforms), in processing order."""
    settings = _settings(config)
    entries = _store(settings).catalog_entries()
    shortlist = curate_entries(entries, settings.min_duration_s, settings.sources.prefer_platform)
    table = Table(title=f"Shortlist: {len(shortlist)} of {len(entries)} posts")
    for col in ("id", "platform", "duration", "views", "score", "caption"):
        table.add_column(col)
    for e in shortlist[:top]:
        table.add_row(e.source_id, e.platform.value, f"{e.duration:.0f}s", f"{e.view_count:,}",
                      f"{score(e):.1f}", e.caption[:50])  # fmt: skip
    console.print(table)


@app.command()
def ingest(
    limit: Annotated[int | None, typer.Option(help="Max phone videos to copy.")] = None,
    config: ConfigOpt = DEFAULT_CONFIG,
) -> None:
    """Copy new phone originals (skips files already copied) and cache their fingerprints."""
    settings = _settings(config)
    source = PhoneOriginals(
        lambda: MtpPhoneSource(settings.phone_name_match, settings.phone_video_folders),
        settings.phone_video_folders,
        limit,
    )
    paths = source.sync(Path(settings.originals_dir))
    originals = index_originals(paths, _store(settings), LocalMedia(), settings.phash_frames)
    _ok(f"{len(originals)} originals ready in {settings.originals_dir}.")


@app.command()
def run(
    source: Annotated[SourceChoice, typer.Option(help="Which videos to process.")] = (
        SourceChoice.all
    ),
    limit: Annotated[int, typer.Option(help="Max uploads (renders with --dry-run).")] = 3,
    privacy: Annotated[
        Privacy | None, typer.Option(help="Default: [youtube] default_privacy.")
    ] = None,
    continuous: Annotated[
        bool, typer.Option(help="Unattended: no --limit, upload until today's quota is spent.")
    ] = False,
    dry_run: Annotated[bool, typer.Option(help="Render only; never upload.")] = False,
    config: ConfigOpt = DEFAULT_CONFIG,
) -> None:
    """Process videos end to end (catalog posts and/or raw inbox footage)."""
    settings = _settings(config)
    options = RunOptions(
        source=source,
        limit=None if continuous else limit,
        privacy=privacy or settings.youtube.default_privacy,
        dry_run=dry_run,
    )
    report = run_pipeline(settings, PipelineDeps.default(settings), options)
    _print_report(report, dry_run)
    if report.alert:
        _fail(f"ALERT: {report.alert}", code=2)


def _print_report(report: RunReport, dry_run: bool) -> None:
    table = Table(title="Dry run (nothing uploaded)" if dry_run else "Run report")
    table.add_column("result")
    table.add_column("count", justify="right")
    table.add_column("items")
    buckets = {
        "uploaded": [f"{u.item_key} -> youtu.be/{u.youtube_id}" for u in report.uploaded],
        "rendered": report.rendered,
        "needs review": report.review,
        "waiting for original": report.pending,
        "already uploaded": report.skipped,
        "failed (will retry)": report.failed,
        "made private (claim)": report.claimed,
    }
    for name, items in buckets.items():
        if items:
            table.add_row(name, str(len(items)), ", ".join(items))
    console.print(table if table.row_count else "Nothing to do: no new videos to process.")
    if report.stop_reason:
        console.print(f"[yellow]Stopped early: {report.stop_reason}.[/yellow]")
    if report.review or report.claimed:
        console.print("Some videos need you: run `social2shorts review`.")
    if report.pending:
        console.print("Some posts have no phone original yet: plug the phone in and run again.")


@app.command()
def review(
    approve: Annotated[str | None, typer.Option(help="Item to approve.")] = None,
    original: Annotated[
        Path | None, typer.Option(help="With --approve: the original to use (ambiguous match).")
    ] = None,
    reject: Annotated[str | None, typer.Option(help="Item to skip for good.")] = None,
    config: ConfigOpt = DEFAULT_CONFIG,
) -> None:
    """List items waiting for a decision, or approve/reject one."""
    store = _store(_settings(config))
    try:
        if approve:
            _ok(operations.approve(store, approve, original))
            return
        if reject:
            _ok(operations.reject(store, reject))
            return
    except operations.ActionError as exc:
        _fail(str(exc))
    rows = store.reviews()
    if not rows:
        _ok("Review queue is empty.")
        return
    table = Table(title=f"Waiting for you ({len(rows)})")
    for col in ("item", "kind", "what to do", "since"):
        table.add_column(col)
    for r in rows:
        table.add_row(r["source_id"], r["kind"], r["reason"], str(r["created"]))
    console.print(table)


@app.command()
def publish(
    target: Annotated[str, typer.Argument(help="Item key, or 'all'.")],
    privacy: Annotated[Privacy, typer.Option(help="Required: public, unlisted or private.")],
    include_claimed: Annotated[
        bool, typer.Option(help="Also change videos the Content-ID guard made private.")
    ] = False,
    include_scheduled: Annotated[
        bool, typer.Option(help="Also change videos still waiting for their publish time.")
    ] = False,
    config: ConfigOpt = DEFAULT_CONFIG,
) -> None:
    """Change the visibility of uploaded video(s), e.g. unlisted -> public after review."""
    if privacy is Privacy.scheduled:
        _fail("Use public, unlisted or private here; scheduling happens at upload time.")
    settings = _settings(config)
    store = _store(settings)
    deps = PipelineDeps.default(settings)
    yt = settings.youtube
    budget = QuotaBudget(store, yt.daily_quota_units, deps.clock, yt.max_uploads_per_day)
    try:
        result = operations.publish(
            store, deps.platform, budget, target, privacy.value,
            include_claimed=include_claimed, include_scheduled=include_scheduled,
        )  # fmt: skip
    except operations.ActionError as exc:
        _fail(str(exc))
    except AuthRefreshError as exc:
        _fail(f"YouTube authorization failed ({exc}). Run `social2shorts auth` and retry.", 2)
    for key in result.done:
        console.print(f"{key} -> {privacy.value}")
    for key, why in result.excluded:
        console.print(f"[yellow]{key} left unchanged: {why}[/yellow]")
    for key, why in result.failed:
        console.print(f"[red]{key} failed: {why}[/red]")
    console.print(
        f"Succeeded: {len(result.done)}, failed: {len(result.failed)}, "
        f"left unchanged: {len(result.excluded)}"
    )
    if result.failed:
        raise typer.Exit(1)


@app.command()
def status(config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Item counts, today's quota and what is scheduled next."""
    settings = _settings(config)
    s = operations.summary(settings, _store(settings), utcnow())
    for alert in s.alerts:
        console.print(f"[bold red]ALERT:[/bold red] {alert}")
    table = Table(title="Items")
    for col in ("source", "status", "count"):
        table.add_column(col)
    for (kind, st), n in sorted(s.counts.items()):
        table.add_row(kind, st, str(n))
    console.print(table)
    console.print(
        f"Quota today (Pacific): {s.quota_used}/{s.quota_daily} units used, "
        f"room for {s.uploads_left_today} more upload(s)."
    )
    tz = settings.schedule.timezone
    if s.scheduled:
        console.print(f"Next publications ({tz}):")
        for when, key in s.scheduled[:5]:
            console.print(f"  {_local(when, tz)}  {key}")
    if s.reviews:
        console.print(f"{s.reviews} item(s) need review: `social2shorts review`.")


@app.command()
def schedule(
    count: Annotated[int, typer.Option(help="Free slots to preview.")] = 6,
    config: ConfigOpt = DEFAULT_CONFIG,
) -> None:
    """List planned publication times and the next free slots."""
    settings = _settings(config)
    s = operations.summary(settings, _store(settings), utcnow(), slots=count)
    tz = settings.schedule.timezone
    table = Table(title=f"Publication schedule ({tz})")
    table.add_column("when")
    table.add_column("item")
    for when, key in s.scheduled:
        table.add_row(_local(when, tz), key)
    for when in s.next_slots:
        table.add_row(_local(when, tz), "[dim]free slot[/dim]")
    console.print(table)


def _local(when: datetime, tz: str) -> str:
    return when.astimezone(ZoneInfo(tz)).strftime("%a %d %b %H:%M")


if __name__ == "__main__":
    app()
