"""Operator actions behind the CLI: review decisions, privacy changes and status summaries."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from social2shorts.config import Settings
from social2shorts.db import Store
from social2shorts.models import ItemStatus, MatchResult
from social2shorts.ports import VideoPlatform
from social2shorts.publish.quota import QuotaBudget
from social2shorts.publish.scheduler import upcoming_slots
from social2shorts.publish.youtube import COST_UPDATE, AuthRefreshError

log = logging.getLogger(__name__)


class ActionError(Exception):
    """An operator action cannot be done; the message says what to do instead."""


def approve(store: Store, key: str, original: Path | None = None) -> str:
    """Approve a reviewed item so the next run processes it; return a human message.

    Args:
        store: The repository.
        key: Item key from the review queue.
        original: For ambiguous matches, the phone original to use.

    Raises:
        ActionError: Unknown item, missing ``--original`` for an ambiguous match, bad path.
    """
    item = store.item(key)
    if item is None:
        raise ActionError(f"Unknown item {key!r}. Run `social2shorts review` to list the queue.")
    if item.status in (ItemStatus.uploaded, ItemStatus.claimed):
        store.resolve_review(key)
        return (
            f"{key} is already on YouTube; review cleared. "
            f"Use `publish {key}` to change its visibility."
        )
    if original is not None:
        if not original.is_file():
            raise ActionError(f"{original} does not exist. Pass the full path of the original.")
        store.set_source(key, str(original))
        store.set_match(MatchResult(source_id=key, original_path=str(original), confidence=1.0,
                                    status=ItemStatus.matched))  # fmt: skip
    elif item.status is ItemStatus.ambiguous:
        options = "\n  ".join(store.match_candidates(key)) or "(none recorded)"
        raise ActionError(
            f"{key} matches several originals. Choose one with --original <path>:\n  {options}"
        )
    store.set_status(key, ItemStatus.ready)
    store.reset_attempts(key)
    store.resolve_review(key)
    return f"{key} approved: it will be processed first on the next run."


def reject(store: Store, key: str) -> str:
    """Never process this item again; return a human message.

    Raises:
        ActionError: If the item is unknown.
    """
    if store.item(key) is None:
        raise ActionError(f"Unknown item {key!r}. Run `social2shorts review` to list the queue.")
    store.set_status(key, ItemStatus.rejected)
    store.resolve_review(key)
    return f"{key} rejected: it will be skipped from now on."


@dataclass
class PublishResult:
    """Outcome of a privacy change over several uploads."""

    done: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


def publish(
    store: Store, platform: VideoPlatform, budget: QuotaBudget, target: str, privacy: str
) -> PublishResult:
    """Set the visibility of one item's upload (or ``all`` uploads), within today's quota.

    Raises:
        ActionError: If nothing matches ``target``.
        AuthRefreshError: If YouTube authorization fails.
    """
    uploads = [u for u in store.uploads() if target == "all" or u.item_key == target]
    if not uploads:
        raise ActionError(f"No upload found for {target!r}. `social2shorts status` lists uploads.")
    result = PublishResult()
    for u in uploads:
        if not budget.can_afford(COST_UPDATE):
            result.failed.append((u.item_key, "daily quota spent, retry tomorrow"))
            continue
        try:
            platform.set_privacy(u.youtube_id, privacy)
        except AuthRefreshError:
            raise
        except Exception as exc:
            budget.spend(COST_UPDATE)
            result.failed.append((u.item_key, f"{type(exc).__name__}: {exc}"))
            continue
        budget.spend(COST_UPDATE)
        store.set_upload_privacy(u.youtube_id, privacy)
        if store.item(u.item_key) and privacy != "private":
            store.set_status(u.item_key, ItemStatus.uploaded)
            store.resolve_review(u.item_key)
        result.done.append(u.item_key)
    return result


@dataclass
class Summary:
    """Everything ``status`` shows."""

    counts: dict[tuple[str, str], int]
    quota_used: int
    quota_daily: int
    uploads_left_today: int
    scheduled: list[tuple[datetime, str]]
    next_slots: list[datetime]
    reviews: int
    alerts: list[str]


def summary(settings: Settings, store: Store, now: datetime, slots: int = 5) -> Summary:
    """Counts, today's quota, upcoming publications and open alerts."""
    yt = settings.youtube
    budget = QuotaBudget(store, yt.daily_quota_units, lambda: now, yt.max_uploads_per_day)
    scheduled = sorted(
        (u.publish_at, u.item_key) for u in store.uploads() if u.publish_at and u.publish_at > now
    )
    return Summary(
        counts=store.status_counts(),
        quota_used=budget.used(),
        quota_daily=budget.daily_units,
        uploads_left_today=budget.uploads_left(yt.upload_cost_units),
        scheduled=scheduled,
        next_slots=upcoming_slots(now, store.publish_times(), settings.schedule, slots),
        reviews=len(store.reviews()),
        alerts=[a["message"] for a in store.open_alerts()],
    )
