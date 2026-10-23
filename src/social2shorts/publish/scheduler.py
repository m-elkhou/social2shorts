"""Publication cadence: picks the next free ``publishAt`` slot (local timezone, DST-safe)."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from social2shorts.config import ScheduleSettings

HORIZON_DAYS = 400


class ScheduleFull(RuntimeError):
    """No free slot within the planning horizon."""


def _slot_times(cfg: ScheduleSettings) -> list[time]:
    return [time.fromisoformat(s) for s in cfg.publish_slots]


def next_slot(now: datetime, taken: list[datetime], cfg: ScheduleSettings) -> datetime:
    """Earliest free slot (UTC) at least ``min_lead_minutes`` after ``now``.

    Slots are wall-clock times in ``cfg.timezone``; a day already holding ``max_per_day``
    publications is skipped. A slot falling in a DST gap moves forward by the gap (e.g. 02:30
    on a spring-forward night is published at 03:30 local).

    Raises:
        ScheduleFull: If nothing is free within ``HORIZON_DAYS``.
    """
    tz = ZoneInfo(cfg.timezone)
    earliest = now + timedelta(minutes=cfg.min_lead_minutes)
    taken_utc = {t.astimezone(UTC) for t in taken}
    per_day = Counter(t.astimezone(tz).date() for t in taken)
    first_day = now.astimezone(tz).date()
    for offset in range(HORIZON_DAYS):
        day = first_day + timedelta(days=offset)
        if per_day[day] >= cfg.max_per_day:
            continue
        for slot in _slot_times(cfg):
            instant = datetime.combine(day, slot, tzinfo=tz).astimezone(UTC)
            if instant >= earliest and instant not in taken_utc:
                return instant
    raise ScheduleFull(f"no free publish slot in the next {HORIZON_DAYS} days")


def upcoming_slots(
    now: datetime, taken: list[datetime], cfg: ScheduleSettings, count: int
) -> list[datetime]:
    """The next ``count`` free slots, as if each were taken in turn."""
    taken = list(taken)
    slots = []
    for _ in range(count):
        slot = next_slot(now, taken, cfg)
        slots.append(slot)
        taken.append(slot)
    return slots
