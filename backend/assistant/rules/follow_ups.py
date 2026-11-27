"""When follow-ups and asks may go out (section 8.1, Story 4.2). Pure rules.

Every time is worked out on the manager's calendar: a local date and a local
time of day, turned into UTC through the time zone, so 09:00 stays 09:00 when
the clocks change.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from math import ceil
from zoneinfo import ZoneInfo

from assistant.rules.settings import Settings

FOLLOW_UP_AT = time(9, 0)
"""Follow-ups of the section 8.1 schedule are at 09:00 local time."""
SHORT_NOTICE_WORKING_DAYS = 2
"""An item due within this many working days of acceptance gets one follow-up, on the due date."""
DEFAULT_FOLLOW_UPS = 2


def _tz(settings: Settings) -> ZoneInfo:
    return ZoneInfo(settings.time_zone)


def _working(day: date, settings: Settings) -> bool:
    return day.weekday() in {d.number for d in settings.working_days}


def at_local(day: date, when: time, settings: Settings) -> datetime:
    """``when`` on ``day`` in the manager's time zone, in UTC."""
    return datetime.combine(day, when, tzinfo=_tz(settings)).astimezone(UTC)


def working_days_after(start: date, end: date, settings: Settings) -> list[date]:
    """The working days after ``start``, up to and including ``end``."""
    days: list[date] = []
    day = start + timedelta(days=1)
    while day <= end:
        if _working(day, settings):
            days.append(day)
        day += timedelta(days=1)
    return days


def follow_up_times(accepted_at: datetime, due: date, settings: Settings) -> list[datetime]:
    """The follow-ups an item gets when it is accepted, in UTC, earliest first.

    Section 8.1: at 09:00 on the working day half-way between acceptance and the due
    date (counted in working days, rounded up) and at 09:00 on the due date. An item due
    within two working days gets the due-date follow-up only. When the manager chose a
    number of follow-ups for all items, they are spread the same way (the k-th of n at
    the working day ``ceil(k * days / n)``), the last always on the due date; none means
    none. Times that have already passed are left out.
    """
    count = DEFAULT_FOLLOW_UPS if settings.follow_ups is None else settings.follow_ups
    if count == 0:
        return []
    start = accepted_at.astimezone(_tz(settings)).date()
    days = working_days_after(start, due, settings)
    targets: list[date] = []
    if len(days) > SHORT_NOTICE_WORKING_DAYS:
        for k in range(1, count):
            targets.append(days[ceil(k * len(days) / count) - 1])
    targets.append(due)
    times = sorted({at_local(day, FOLLOW_UP_AT, settings) for day in targets})
    return [t for t in times if t > accepted_at]


def sendable_at(when: datetime, settings: Settings) -> datetime:
    """The earliest time at or after ``when`` inside the manager's working hours."""
    local = when.astimezone(_tz(settings))
    day = local.date()
    if _working(day, settings):
        if local.time() < settings.starts:
            return at_local(day, settings.starts, settings)
        if local.time() < settings.ends:
            return when
    return next_working_start(when, settings)


def next_working_start(when: datetime, settings: Settings) -> datetime:
    """The start of the next working day after the local date of ``when``."""
    day = when.astimezone(_tz(settings)).date() + timedelta(days=1)
    while not _working(day, settings):
        day += timedelta(days=1)
    return at_local(day, settings.starts, settings)


def in_working_hours(when: datetime, settings: Settings) -> bool:
    return sendable_at(when, settings) == when


def one_working_day_before(now: datetime, settings: Settings) -> datetime:
    """The same local time on the working day before today: "the last working day"."""
    local = now.astimezone(_tz(settings))
    day = local.date() - timedelta(days=1)
    while not _working(day, settings):
        day -= timedelta(days=1)
    return at_local(day, local.time().replace(tzinfo=None), settings)


def overdue_at(due: date, settings: Settings) -> datetime:
    """When an item that is not done counts as late: the end of the working day of its due
    date, or that many working days later when the manager said so (section 8.1)."""
    day = due
    late = settings.late_after_working_days
    while late > 0:
        day += timedelta(days=1)
        if _working(day, settings):
            late -= 1
    return at_local(day, settings.ends, settings)
