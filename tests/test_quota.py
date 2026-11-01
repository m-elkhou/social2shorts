from datetime import UTC, datetime, timedelta

from social2shorts.db import Store
from social2shorts.models import Upload
from social2shorts.publish.quota import QuotaBudget, quota_day, upload_limit_until


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


def _uploaded(store: Store, key: str, at: datetime) -> None:
    store.record_upload(Upload(youtube_id=f"y{key}", item_key=key, fingerprint=key,
                               privacy="private", content_sha256=key, uploaded_at=at,
                               quota_units=1))  # fmt: skip


def test_upload_limit_waits_until_the_last_uploads_are_a_day_old(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init_schema()
    assert upload_limit_until(store) is None
    last = datetime(2026, 10, 31, 22, 45, tzinfo=UTC)
    _uploaded(store, "old", datetime(2026, 10, 29, 12, tzinfo=UTC))  # out of the window
    _uploaded(store, "first", datetime(2026, 10, 31, 22, 14, tzinfo=UTC))
    _uploaded(store, "last", last)
    store.record_upload_limit(datetime(2026, 11, 1, 7, 15, tzinfo=UTC))
    assert upload_limit_until(store) == last + timedelta(hours=24)


def test_upload_limit_without_recent_uploads_waits_a_day(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init_schema()
    refused = datetime(2026, 11, 1, 7, 15, tzinfo=UTC)
    store.record_upload_limit(refused)
    assert store.upload_limit_refused_at() == refused
    assert upload_limit_until(store) == refused + timedelta(hours=24)
