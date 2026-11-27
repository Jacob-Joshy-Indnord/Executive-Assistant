"""Effective settings: the manager's answers, or the defaults of section 8.1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import TypeVar

from assistant.models.preferences import (
    FollowUps,
    PreferencesForm,
    ReportFrequency,
    ReportPreferences,
    Weekday,
    WorkingTime,
)

T = TypeVar("T")

DEFAULT_TIME_ZONE = "UTC"
"""Used only when the manager has neither answered nor opened the page in a browser."""


@dataclass(frozen=True)
class Settings:
    working_days: tuple[Weekday, ...]
    starts: time
    ends: time
    time_zone: str
    follow_ups: int | None
    """``None``: half-way and on the due date (section 8.1)."""
    answer_within_working_days: int
    late_after_working_days: int
    notices_per_owner_per_day: int
    report_frequency: ReportFrequency
    report_day: Weekday
    report_time: time
    ask_owners_working_days_before: int


DEFAULTS = Settings(
    working_days=(
        Weekday.MONDAY,
        Weekday.TUESDAY,
        Weekday.WEDNESDAY,
        Weekday.THURSDAY,
        Weekday.FRIDAY,
    ),
    starts=time(9, 0),
    ends=time(18, 0),
    time_zone=DEFAULT_TIME_ZONE,
    follow_ups=None,
    answer_within_working_days=1,
    late_after_working_days=0,
    notices_per_owner_per_day=3,
    report_frequency="weekly",
    report_day=Weekday.MONDAY,
    report_time=time(8, 0),
    ask_owners_working_days_before=2,
)


def _or(value: T | None, default: T) -> T:
    return default if value is None else value


def effective_settings(
    form: PreferencesForm | None, first_open_time_zone: str | None = None
) -> Settings:
    """Each answer the manager gave, and the section 8.1 default for the rest.

    The default time zone is the one of the manager's browser when they first
    opened the page.
    """
    form = form or PreferencesForm()
    wt = form.working_time or WorkingTime()
    fu = form.follow_ups or FollowUps()
    rp = form.report or ReportPreferences()
    d = DEFAULTS
    starts, ends = _or(wt.starts, d.starts), _or(wt.ends, d.ends)
    if starts >= ends:  # one end given, against the other's default
        starts, ends = d.starts, d.ends
    return Settings(
        working_days=tuple(wt.days) if wt.days else d.working_days,
        starts=starts,
        ends=ends,
        time_zone=wt.time_zone or first_open_time_zone or d.time_zone,
        follow_ups=fu.follow_ups,
        answer_within_working_days=_or(
            fu.answer_within_working_days, d.answer_within_working_days
        ),
        late_after_working_days=_or(fu.late_after_working_days, d.late_after_working_days),
        notices_per_owner_per_day=_or(
            fu.notices_per_owner_per_day, d.notices_per_owner_per_day
        ),
        report_frequency=_or(rp.frequency, d.report_frequency),
        report_day=_or(rp.day, d.report_day),
        report_time=_or(rp.time, d.report_time),
        ask_owners_working_days_before=_or(
            rp.ask_owners_working_days_before, d.ask_owners_working_days_before
        ),
    )


def form_answers(form: PreferencesForm | None) -> dict[str, str]:
    """The manager's answers as plain text for an input pack; unanswered fields are left out."""
    if form is None:
        return {}
    answers: dict[str, str] = {}

    def put(key: str, value: object) -> None:
        if value is None or value == [] or value == "":
            return
        if isinstance(value, list):
            answers[key] = "; ".join(str(v) for v in value)
        elif isinstance(value, time):
            answers[key] = value.strftime("%H:%M")
        else:
            answers[key] = str(value)

    if wt := form.working_time:
        put("working_days", [d.value.capitalize() for d in wt.days] if wt.days else None)
        put("working_day_starts", wt.starts)
        put("working_day_ends", wt.ends)
        put("time_zone", wt.time_zone)
    if tone := form.tone:
        put("tone", tone.tone)
        put("sign_off", tone.sign_off)
    if pr := form.priorities:
        put("priorities", pr.priorities)
        put("key_people", pr.key_people)
    if fu := form.follow_ups:
        put("follow_ups_per_item", fu.follow_ups)
        put("answer_within_working_days", fu.answer_within_working_days)
        put("late_after_working_days", fu.late_after_working_days)
        put("notices_per_owner_per_day", fu.notices_per_owner_per_day)
    if rp := form.report:
        put("report_frequency", rp.frequency)
        put("report_day", rp.day.value.capitalize() if rp.day else None)
        put("report_time", rp.time)
        put("ask_owners_working_days_before", rp.ask_owners_working_days_before)
        put("report_includes", rp.includes)
        put("report_leaves_out", rp.leaves_out)
    return answers
