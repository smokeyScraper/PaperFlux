"""Pure scheduling rules, shared by the scheduler and the UI.

The scheduler processes papers on weekdays, at or after SCHEDULE_HOUR_UTC, once
per UTC day. The UI used to print "tomorrow" regardless, which was wrong on
Fridays and weekends.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

SCHEDULE_HOUR_UTC = 8
STALE_PROCESSING_AFTER = timedelta(hours=3)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def is_due(now: datetime, last_processed: Optional[datetime]) -> bool:
    """True when the scheduler would process papers on its next tick."""
    now = _aware(now)
    if now.weekday() >= 5 or now.hour < SCHEDULE_HOUR_UTC:
        return False
    return last_processed is None or _aware(last_processed).date() < now.date()


def next_scheduled_run(now: datetime, last_processed: Optional[datetime] = None) -> datetime:
    """When the next automatic run happens (``now`` itself when it is already due)."""
    now = _aware(now)
    if is_due(now, last_processed):
        return now
    candidate = now.replace(hour=SCHEDULE_HOUR_UTC, minute=0, second=0, microsecond=0)
    if now >= candidate or (last_processed and _aware(last_processed).date() >= now.date() and candidate.date() == now.date()):
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def is_stale(started_at: Optional[datetime], now: datetime) -> bool:
    """A processing flag older than this belongs to a run that died (Space restart)."""
    if started_at is None:
        return False
    return _aware(now) - _aware(started_at) > STALE_PROCESSING_AFTER
