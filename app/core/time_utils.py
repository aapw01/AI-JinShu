"""Datetime serialization helpers for API responses."""
from __future__ import annotations

from datetime import datetime, timezone


def to_utc_iso_z(dt: datetime | None) -> str:
    """Serialize datetime as UTC RFC3339 string ending with 'Z'.

    Naive datetimes are treated as UTC to preserve backward compatibility with
    existing DB rows and model defaults.
    """
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")

