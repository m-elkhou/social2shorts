from datetime import UTC, datetime

from social2shorts.db import Store
from social2shorts.publish.quota import QuotaBudget, quota_day


def test_quota_day_resets_at_pacific_midnight():
    # 06:59 UTC on Oct 23 is still Oct 22 in California (PDT, UTC-7)
    assert quota_day(datetime(2026, 10, 23, 6, 59, tzinfo=UTC)) == "2026-10-22"
    assert quota_day(datetime(2026, 10, 23, 7, 0, tzinfo=UTC)) == "2026-10-23"


def test_budget_units_and_upload_cap(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init_schema()
    now = [datetime(2026, 10, 23, 12, tzinfo=UTC)]
    budget = QuotaBudget(store, 10_000, lambda: now[0], max_uploads=5)
    assert budget.uploads_left(1600) == 5  # upload cap hits before the 6 the units allow
    for _ in range(4):
        budget.spend(1600, uploads=1)
    assert budget.remaining() == 3600 and budget.uploads_left(1600) == 1
    assert budget.can_afford(3600) and not budget.can_afford(3601)
    budget.exhaust()
    assert budget.remaining() == 0 and budget.uploads_left(1600) == 0
    now[0] = datetime(2026, 10, 24, 12, tzinfo=UTC)
    assert budget.remaining() == 10_000
