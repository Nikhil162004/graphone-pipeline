"""Publication-date extraction and normalisation.

Feeds and job boards publish dates as ISO strings, RFC-2822, epoch seconds,
and (on HTML pages) relative strings like "2 hours ago". Everything the
pipeline stores is an aware UTC datetime; anything that cannot be parsed
returns None and the caller decides whether a freshness heuristic may
rescue the record (Phase II rules).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

_RELATIVE = re.compile(
    r"(\d+)\s*(minutes?|mins?|hours?|hrs?|days?|weeks?)\s+ago", re.IGNORECASE
)
_FORMATS = (
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y",
    "%d %b %Y",
    "%m/%d/%Y",
)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_datetime(value, now: datetime | None = None) -> datetime | None:
    """Best-effort parse of any date representation into aware UTC."""
    if value is None or value == "":
        return None
    now = now or datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, (int, float)):
        return _epoch(value)
    if isinstance(value, str) and value.strip().isdigit():
        return _epoch(int(value.strip()))
    if not isinstance(value, str):
        return None

    text = value.strip()
    lowered = text.lower()
    if lowered in ("just now", "now", "today"):
        return now
    if lowered == "yesterday":
        return now - timedelta(days=1)
    match = _RELATIVE.search(text)
    if match:
        amount, unit = int(match.group(1)), match.group(2).lower()
        delta = {
            "m": timedelta(minutes=amount),
            "h": timedelta(hours=amount),
            "d": timedelta(days=amount),
            "w": timedelta(weeks=amount),
        }[unit[0]]
        return now - delta

    iso = text.replace("Z", "+00:00") if text.endswith("Z") else text
    try:
        return _as_utc(datetime.fromisoformat(iso))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
        if parsed is not None:
            return _as_utc(parsed)
    except (TypeError, ValueError):
        pass
    for fmt in _FORMATS:
        try:
            return _as_utc(datetime.strptime(text, fmt))
        except ValueError:
            continue
    return None


def _epoch(value: float) -> datetime | None:
    try:
        if value > 1e12:  # milliseconds
            value = value / 1000
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def iso_z(dt: datetime | None) -> str:
    """ISO-8601 with Z suffix, seconds precision; '' for missing."""
    if dt is None:
        return ""
    return _as_utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_fresh(published: datetime | None, now: datetime, hours: int = 24) -> bool:
    """The Phase II guarantee: known date, not in the future by more than a
    small clock-skew allowance, and no older than the window."""
    if published is None:
        return False
    if published > now + timedelta(minutes=30):
        return False
    return published >= now - timedelta(hours=hours)
