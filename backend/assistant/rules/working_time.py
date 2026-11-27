"""Working days and hours in the manager's time zone (section 8.1). Pure rules."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from assistant.rules.settings import Settings


def local_date(now: datetime, settings: Settings) -> date:
    """Today, in the manager's time zone."""
    return now.astimezone(ZoneInfo(settings.time_zone)).date()


def end_of_working_day_after(now: datetime, settings: Settings, working_days: int) -> datetime:
    """The end of the ``working_days``-th working day after today, in UTC.

    With the default of one, a request asked today is answered by the end of the next
    working day. Clock changes are handled by the time zone, so 18:00 stays 18:00.
    """
    tz = ZoneInfo(settings.time_zone)
    day = now.astimezone(tz).date()
    allowed = {d.index for d in settings.working_days}
    counted = 0
    while counted < max(working_days, 1):
        day += timedelta(days=1)
        if day.weekday() in allowed:
            counted += 1
    return datetime.combine(day, settings.ends, tzinfo=tz).astimezone(UTC)


def answer_by(now: datetime, settings: Settings) -> datetime:
    """When a request to a person must be answered: the manager's "time to answer"."""
    return end_of_working_day_after(now, settings, settings.answer_within_working_days)
