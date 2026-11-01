"""Daily YouTube Data API budget, tracked in the DB per Pacific-time day (when Google resets it).

Two limits apply and whichever is hit first wins: the units budget (``videos.insert`` ~1600 of
10,000 units) and a per-project cap on the number of ``videos.insert`` calls per day.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from social2shorts.db import Store

PACIFIC = ZoneInfo("America/Los_Angeles")
UPLOAD_WINDOW = timedelta(hours=24)


def quota_day(now: datetime) -> str:
    """The quota day (``YYYY-MM-DD`` in Pacific time) that ``now`` belongs to."""
    return now.astimezone(PACIFIC).date().isoformat()


class QuotaBudget:
    """Units and uploads spent today vs. the daily allowances."""

    def __init__(
        self,
        store: Store,
        daily_units: int,
        clock: Callable[[], datetime],
        max_uploads: int = 100,
    ):
        self.store = store
        self.daily_units = daily_units
        self.max_uploads = max_uploads
        self.clock = clock

    @property
    def day(self) -> str:
        """Current quota day."""
        return quota_day(self.clock())

    def used(self) -> int:
        """Units spent today."""
        return self.store.quota_used(self.day)[0]

    def uploads(self) -> int:
        """``videos.insert`` calls made today."""
        return self.store.quota_used(self.day)[1]

    def remaining(self) -> int:
        """Units left today (never negative)."""
        return max(self.daily_units - self.used(), 0)

    def can_afford(self, units: int) -> bool:
        """True if ``units`` fit in what is left today."""
        return self.remaining() >= units

    def uploads_left(self, upload_cost: int) -> int:
        """How many more uploads both limits allow today."""
        return max(min(self.remaining() // upload_cost, self.max_uploads - self.uploads()), 0)

    def spend(self, units: int, uploads: int = 0) -> None:
        """Record ``units`` (and ``uploads`` insert calls) spent now."""
        self.store.spend_quota(self.day, units, uploads)

    def exhaust(self) -> None:
        """Mark today as fully spent (YouTube said quotaExceeded before our count did)."""
        self.spend(self.remaining())


def upload_limit_until(store: Store) -> datetime | None:
    """When the channel may upload again after YouTube's ``uploadLimitExceeded``, or None.

    That limit (about 10 uploads) counts the uploads of the last 24 hours, not a Pacific day
    (refused again 4 and 8.5 hours after the 2026-10-31 uploads, across a quota reset). After a
    refusal, wait until every upload of the 24 hours before it is 24 hours old: the next run
    then gets the whole allowance, not one slot and another refusal.
    """
    refused = store.upload_limit_refused_at()
    if refused is None:
        return None
    recent = [t for t in store.upload_times() if refused - UPLOAD_WINDOW < t <= refused]
    return max(recent, default=refused) + UPLOAD_WINDOW
