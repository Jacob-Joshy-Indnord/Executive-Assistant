"""Project status: when reports, drafts and asks are due, and what a report says. Pure rules.

Section 8.1: the report is weekly on a chosen day and time (Mondays 08:00 by
default), every two weeks, monthly or off. A monthly report is on the first
chosen weekday of the month. Owners are asked a number of working days before
the report, at 09:00; statements are drafted one working day before that.
Every time is worked out on the manager's calendar, so the clocks changing
does not move it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from assistant.models.records import ConfirmedStatement, ReportLine, StatementWording
from assistant.rules.follow_ups import at_local
from assistant.rules.settings import Settings

ASK_AT = time(9, 0)
DRAFT_WORKING_DAYS_BEFORE_ASK = 1
LOOK_AROUND_DAYS = 70
"""How far around a moment the schedule is searched: more than any report interval."""

COLOUR_WORDS = {"green": "Green", "amber": "Amber", "red": "Red"}
MARK_WORDS = {
    "confirmed": "Confirmed",
    "corrected": "Corrected by its owner",
    "awaiting_confirmation": "Awaiting confirmation",
    "no_confirmed_status": "No confirmed status yet",
}
POINTS = ("reason", "what_changed", "what_comes_next")


def _working(day: date, settings: Settings) -> bool:
    return day.weekday() in {d.number for d in settings.working_days}


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _is_report_day(day: date, settings: Settings, anchor: date) -> bool:
    if settings.report_frequency == "off" or day.weekday() != settings.report_day.number:
        return False
    if settings.report_frequency == "monthly":
        return day.day <= 7  # the first such weekday of the month
    if settings.report_frequency == "every_two_weeks":
        return ((_monday(day) - _monday(anchor)).days // 7) % 2 == 0
    return True


def report_times(around: datetime, settings: Settings, anchor: date) -> list[datetime]:
    """Report times within ``LOOK_AROUND_DAYS`` of ``around``, earliest first. ``anchor`` is
    a date in a week that has a report, for reports every two weeks."""
    tz = ZoneInfo(settings.time_zone)
    middle = around.astimezone(tz).date()
    days = (middle + timedelta(days=n) for n in range(-LOOK_AROUND_DAYS, LOOK_AROUND_DAYS + 1))
    return [
        at_local(day, settings.report_time, settings)
        for day in days
        if _is_report_day(day, settings, anchor)
    ]


def working_days_before(day: date, count: int, settings: Settings) -> date:
    while count > 0:
        day -= timedelta(days=1)
        if _working(day, settings):
            count -= 1
    return day


def ask_time(report_at: datetime, settings: Settings) -> datetime:
    """Owners are asked at 09:00, the chosen number of working days before the report."""
    day = report_at.astimezone(ZoneInfo(settings.time_zone)).date()
    ask_day = working_days_before(day, settings.ask_owners_working_days_before, settings)
    return at_local(ask_day, ASK_AT, settings)


def draft_time(report_at: datetime, settings: Settings) -> datetime:
    """Statements are drafted one working day before owners are asked, at the same time."""
    ask_day = ask_time(report_at, settings).astimezone(ZoneInfo(settings.time_zone)).date()
    day = working_days_before(ask_day, DRAFT_WORKING_DAYS_BEFORE_ASK, settings)
    return at_local(day, ASK_AT, settings)


def latest_due(
    until: datetime, since: datetime, settings: Settings, anchor: date, step: str
) -> datetime | None:
    """The report whose ``step`` ("draft", "ask" or "report") is the latest due at or before
    ``until`` and after ``since``: missed steps are done once, for the latest report."""
    best: datetime | None = None
    for report_at in report_times(until, settings, anchor):
        at = step_time(report_at, settings, step)
        if since < at <= until and (best is None or report_at > best):
            best = report_at
    return best


def next_step_after(
    after: datetime, settings: Settings, anchor: date, step: str
) -> datetime | None:
    upcoming = [
        step_time(r, settings, step)
        for r in report_times(after, settings, anchor)
        if step_time(r, settings, step) > after
    ]
    return min(upcoming) if upcoming else None


def step_time(report_at: datetime, settings: Settings, step: str) -> datetime:
    if step == "draft":
        return draft_time(report_at, settings)
    if step == "ask":
        return ask_time(report_at, settings)
    return report_at


def next_report_after(after: datetime, settings: Settings, anchor: date) -> datetime | None:
    return next_step_after(after, settings, anchor, "report")


# What a report says ---------------------------------------------------------------------


def line_for(
    project_id: str,
    project_name: str,
    latest: ConfirmedStatement | None,
    awaiting: bool,
    settings: Settings,
) -> ReportLine:
    """One line: the latest confirmed status, marked; never an unconfirmed wording (BR-4)."""
    if latest is None:
        return ReportLine(
            project_id=project_id, project_name=project_name, mark="no_confirmed_status"
        )
    return ReportLine(
        project_id=project_id,
        project_name=project_name,
        colour=latest.colour,
        wording=latest.wording,
        source=latest.source,
        statement_date=latest.created_at.astimezone(ZoneInfo(settings.time_zone)).date(),
        statement_id=latest.id,
        mark="awaiting_confirmation" if awaiting else latest.mark,
    )


def changed_since(lines: Sequence[ReportLine], previous: Sequence[ReportLine] | None) -> list[str]:
    """Projects whose colour differs from the previous report's line for them."""
    if previous is None:
        return []
    before = {line.project_id: line.colour for line in previous}
    return [
        line.project_id
        for line in lines
        if line.project_id in before and before[line.project_id] != line.colour
    ]


def changed_points(
    wording: StatementWording,
    colour: str,
    earlier: StatementWording,
    earlier_colour: str | None,
) -> list[str]:
    """Which points of a correction differ from what it corrected (no wording is kept)."""
    points = ["colour"] if earlier_colour is not None and earlier_colour != colour else []
    return points + [p for p in POINTS if getattr(wording, p) != getattr(earlier, p)]


def line_text(line: ReportLine) -> str:
    """A line in words: "Vendor consolidation. Amber. Legal review is late. Corrected by its
    owner." (section 13.1)."""
    parts = [line.project_name]
    if line.colour is not None and line.wording is not None:
        parts += [COLOUR_WORDS[line.colour], line.wording.reason.rstrip(".")]
    parts.append(MARK_WORDS[line.mark])
    return ". ".join(parts) + "."
