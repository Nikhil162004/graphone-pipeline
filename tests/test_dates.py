from datetime import datetime, timedelta, timezone

from src.dates import is_fresh, normalize_datetime

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


def test_relative_hours():
    assert normalize_datetime("2 hours ago", NOW) == NOW - timedelta(hours=2)


def test_relative_minutes_and_yesterday():
    assert normalize_datetime("45 mins ago", NOW) == NOW - timedelta(minutes=45)
    assert normalize_datetime("yesterday", NOW) == NOW - timedelta(days=1)


def test_iso_z_and_offset():
    assert normalize_datetime("2026-11-22T09:30:00Z") == datetime(2026, 11, 22, 9, 30, tzinfo=timezone.utc)
    assert normalize_datetime("2026-11-22T15:00:00+05:30") == datetime(2026, 11, 22, 9, 30, tzinfo=timezone.utc)


def test_rfc2822_and_epoch():
    assert normalize_datetime("Sun, 22 Nov 2026 09:30:00 GMT") == datetime(2026, 11, 22, 9, 30, tzinfo=timezone.utc)
    assert normalize_datetime(1795312800) is not None
    assert normalize_datetime("1795312800") is not None


def test_garbage_is_none():
    assert normalize_datetime("sometime last spring") is None
    assert normalize_datetime("") is None
    assert normalize_datetime(None) is None


def test_freshness_window():
    assert is_fresh(NOW - timedelta(hours=23), NOW)
    assert not is_fresh(NOW - timedelta(hours=25), NOW)
    assert not is_fresh(None, NOW)
    assert not is_fresh(NOW + timedelta(hours=3), NOW)  # future beyond skew
