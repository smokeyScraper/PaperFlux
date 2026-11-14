"""Small time helpers for friendly, timezone-aware status text."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def humanize_delta(delta: timedelta) -> str:
    """'in 3h 20m', '5 min ago', 'just now', 'in 2 days'."""
    seconds = int(delta.total_seconds())
    future = seconds > 0
    magnitude = abs(seconds)
    if magnitude < 45:
        return "in a moment" if future else "just now"
    minutes = round(magnitude / 60)
    if minutes < 60:
        text = f"{minutes} min"
    elif magnitude < 36 * 3600:
        hours, rem = divmod(magnitude // 60, 60)
        text = f"{hours}h {rem}m" if rem else f"{hours}h"
    else:
        days = round(magnitude / 86400)
        text = f"{days} day{'s' if days != 1 else ''}"
    return f"in {text}" if future else f"{text} ago"


def relative(dt: datetime, now: Optional[datetime] = None) -> str:
    now = _aware(now or datetime.now(timezone.utc))
    return humanize_delta(_aware(dt) - now)


def to_local(dt: datetime, tz_name: Optional[str]) -> datetime:
    dt = _aware(dt)
    if not tz_name:
        return dt
    try:
        from zoneinfo import ZoneInfo

        return dt.astimezone(ZoneInfo(tz_name))
    except Exception:
        return dt


def format_local(dt: datetime, tz_name: Optional[str], fmt: str = "%a %d %b, %H:%M") -> str:
    local = to_local(dt, tz_name)
    label = tz_name if tz_name and local.tzinfo and str(local.tzinfo) == tz_name else "UTC"
    return f"{local.strftime(fmt)} ({label})"
