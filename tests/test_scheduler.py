from datetime import UTC, datetime

import pytest

from social2shorts.config import ScheduleSettings
from social2shorts.publish.scheduler import ScheduleFull, next_slot, upcoming_slots

PARIS = ScheduleSettings(timezone="Europe/Paris", publish_slots=["12:00", "18:00"], max_per_day=2)


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_first_slot_today_in_local_time():
    # 09:00 UTC = 11:00 Paris (CEST, UTC+2) -> 12:00 local = 10:00 UTC
    assert next_slot(utc(2026, 10, 23, 9), [], PARIS) == utc(2026, 10, 23, 10)


def test_min_lead_time_skips_imminent_slot():
    # 09:30 UTC: 12:00 local (10:00 UTC) is only 30 min away -> 18:00 local
    assert next_slot(utc(2026, 10, 23, 9, 30), [], PARIS) == utc(2026, 10, 23, 16)


def test_skips_taken_slots_and_full_days():
    taken = [utc(2026, 10, 23, 10)]
    assert next_slot(utc(2026, 10, 23, 9), taken, PARIS) == utc(2026, 10, 23, 16)
    one_a_day = PARIS.model_copy(update={"max_per_day": 1})
    assert next_slot(utc(2026, 10, 23, 9), taken, one_a_day) == utc(2026, 10, 24, 10)


def test_dst_change_keeps_local_wall_clock():
    # Paris leaves summer time on 2026-10-25: 12:00 local is 10:00 UTC before, 11:00 UTC after
    slots = upcoming_slots(utc(2026, 10, 24, 17), [], PARIS, 3)
    assert slots == [utc(2026, 10, 25, 11), utc(2026, 10, 25, 17), utc(2026, 10, 26, 11)]


def test_slot_in_spring_forward_gap_moves_forward():
    cfg = ScheduleSettings(timezone="Europe/Paris", publish_slots=["02:30"], max_per_day=1)
    # 2026-03-29 02:30 does not exist in Paris; it is published at 03:30 CEST = 01:30 UTC
    assert next_slot(utc(2026, 3, 28, 12), [], cfg) == utc(2026, 3, 29, 1, 30)


def test_casablanca_default_is_valid():
    slot = next_slot(utc(2026, 10, 23, 9), [], ScheduleSettings())
    assert slot > utc(2026, 10, 23, 10)


def test_full_schedule_raises(monkeypatch):
    monkeypatch.setattr("social2shorts.publish.scheduler.HORIZON_DAYS", 1)
    taken = [utc(2026, 10, 23, 10), utc(2026, 10, 23, 16)]
    with pytest.raises(ScheduleFull):
        next_slot(utc(2026, 10, 23, 9), taken, PARIS)
