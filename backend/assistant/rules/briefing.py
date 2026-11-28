"""The briefing: when it is due, what it holds, and its figures. Pure rules.

Section 8 of part 2: the briefing is weekly on Mondays at 08:00 in the manager's
time zone by default; the manager sets it to weekly, every two weeks, monthly or
off, with the day and time. Every two weeks counts from the first briefing after
the schedule was set; a monthly briefing falls on the first chosen weekday of the
month. Every time is worked out on the manager's calendar, so the clocks changing
does not move it.

Each figure is counted from part 1's records and Flow's boundary data as part 1
defines them (BR-6): an item is open in state open; it is due this week when it
is still going (waiting for its owner or open) and its due date falls in the
manager's week, Monday to Sunday; it is late once it is still going after the
time part 1's overdue rule gives. 'Decisions waiting' counts decision papers in
state ready only, so a paper sent back is not waiting for the manager.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from assistant.models.boundary import AssignmentRecord, ProjectRecord
from assistant.models.preferences import Weekday
from assistant.models.records import (
    BRIEFING_NOTE_LIMIT,
    BRIEFING_SECTIONS,
    COMMENT_LIMIT,
    ActionItem,
    ActionItemFigures,
    AssignmentFigures,
    BriefingFrequency,
    BriefingNote,
    BriefingSectionKey,
    BriefingSettings,
    DecisionPaper,
    DecisionPaperState,
    ItemState,
    ProjectSpend,
)
from assistant.rules.follow_ups import overdue_at
from assistant.rules.settings import Settings

DEFAULT_FREQUENCY: BriefingFrequency = "weekly"
DEFAULT_DAY = Weekday.MONDAY
DEFAULT_AT = time(8, 0)
LOOK_AROUND_DAYS = 70
"""How far around a moment the schedule is searched: more than any briefing interval."""

SECTION_TITLES: dict[BriefingSectionKey, str] = {
    "action_items": "Action items",
    "assignments": "Assignments",
    "spend": "Spend against budget",
    "decisions": "Decisions waiting",
    "project_status": "Confirmed project status",
    "meetings": "Meetings since the last briefing",
}
FIGURE_SECTIONS: tuple[BriefingSectionKey, ...] = BRIEFING_SECTIONS[:5]
"""The five groups of figures."""
MEETINGS: BriefingSectionKey = "meetings"
STILL_GOING = frozenset({ItemState.WAITING_FOR_OWNER, ItemState.OPEN})

UNKNOWN_SECTION = "Choose sections from those the briefing has."
FORM_FIRST = "Save or skip the first-time form before asking for a briefing."
NOTES_NOT_PREPARED = "The notes could not be prepared."
NO_BRIEFING_RETURNED = "The briefing was refused because no briefing came back."
COMMENT_NEEDED = "Give a comment to send the briefing back."
COMMENT_TOO_LONG = f"A comment can be at most {COMMENT_LIMIT} characters."
BEING_PREPARED = "A new version of this briefing is being prepared."
REFUSALS_BEFORE_FIGURES_ALONE = 2
"""A briefing refused this many times is shown with its figures alone."""


@dataclass(frozen=True)
class Schedule:
    """The briefing's schedule as it applies: the manager's setting or the default."""

    frequency: BriefingFrequency
    day: Weekday
    at: time
    time_zone: str
    set_at: datetime
    """No briefing falls at or before this moment."""


def schedule_of(setting: BriefingSettings | None, time_zone: str, since: datetime) -> Schedule:
    """The manager's schedule, or the default counted from ``since`` (when they took the
    assistant on)."""
    if setting is None:
        return Schedule(DEFAULT_FREQUENCY, DEFAULT_DAY, DEFAULT_AT, time_zone, since)
    return Schedule(setting.frequency, setting.day, setting.at, time_zone, setting.set_at)


def _tz(schedule: Schedule) -> ZoneInfo:
    return ZoneInfo(schedule.time_zone)


def _at(day: date, schedule: Schedule) -> datetime:
    """The schedule's time of day on ``day`` in the manager's time zone, in UTC."""
    return datetime.combine(day, schedule.at, tzinfo=_tz(schedule)).astimezone(UTC)


def _on_weekday(day: date, schedule: Schedule) -> bool:
    if schedule.day.number != day.weekday():
        return False
    return schedule.frequency != "monthly" or day.day <= 7  # the first such weekday


def first_after_set(schedule: Schedule) -> datetime | None:
    """The first briefing after the schedule was set: the next such day and time."""
    if schedule.frequency == "off":
        return None
    day = schedule.set_at.astimezone(_tz(schedule)).date()
    for _ in range(LOOK_AROUND_DAYS):
        if _on_weekday(day, schedule):
            at = _at(day, schedule)
            if at > schedule.set_at:
                return at
        day += timedelta(days=1)
    return None


def briefing_times(around: datetime, schedule: Schedule) -> list[datetime]:
    """Briefing times within ``LOOK_AROUND_DAYS`` of ``around``, earliest first; none before
    the first briefing after the schedule was set."""
    first = first_after_set(schedule)
    if first is None:
        return []
    first_day = first.astimezone(_tz(schedule)).date()
    middle = around.astimezone(_tz(schedule)).date()
    times: list[datetime] = []
    for n in range(-LOOK_AROUND_DAYS, LOOK_AROUND_DAYS + 1):
        day = middle + timedelta(days=n)
        if day < first_day or not _on_weekday(day, schedule):
            continue
        if schedule.frequency == "every_two_weeks" and (day - first_day).days % 14:
            continue
        times.append(_at(day, schedule))
    return times


def latest_due(until: datetime, schedule: Schedule) -> datetime | None:
    """The latest briefing due at or before ``until``: one missed while paused is done once."""
    due = [t for t in briefing_times(until, schedule) if schedule.set_at < t <= until]
    return max(due) if due else None


def next_after(after: datetime, schedule: Schedule) -> datetime | None:
    upcoming = [
        t for t in briefing_times(after, schedule) if t > after and t > schedule.set_at
    ]
    if upcoming:
        return min(upcoming)
    first = first_after_set(schedule)
    return first if first is not None and first > after else None


# What the briefing holds --------------------------------------------------------------------


def ticked_sections(setting: BriefingSettings | None) -> list[BriefingSectionKey]:
    """The ticked sections, in the order shown; all when nothing was set."""
    ticked = set(setting.sections) if setting is not None else set(BRIEFING_SECTIONS)
    return [key for key in BRIEFING_SECTIONS if key in ticked]


def held_sections(
    ticked: Sequence[BriefingSectionKey], notes_kept: bool
) -> list[BriefingSectionKey]:
    """The five groups of figures that are ticked, and Meetings since the last briefing only
    when it is ticked and notes were kept in that time."""
    return [key for key in ticked if key != MEETINGS or notes_kept]


def sections_refusal(chosen: dict[str, bool]) -> str | None:
    if any(key not in BRIEFING_SECTIONS for key in chosen):
        return UNKNOWN_SECTION
    return None


def apply_ticks(
    current: Sequence[BriefingSectionKey], chosen: dict[str, bool]
) -> list[BriefingSectionKey]:
    """The sections after ticking or unticking those named; the others stay as they were."""
    ticked = {key for key in current}
    for key in BRIEFING_SECTIONS:
        if key in chosen:
            if chosen[key]:
                ticked.add(key)
            else:
                ticked.discard(key)
    return [key for key in BRIEFING_SECTIONS if key in ticked]


# The figures --------------------------------------------------------------------------------


def week_of(now: datetime, settings: Settings) -> tuple[date, date]:
    """Monday and Sunday of the manager's week, in their time zone."""
    today = now.astimezone(ZoneInfo(settings.time_zone)).date()
    monday = today - timedelta(days=today.weekday())
    return monday, monday + timedelta(days=6)


def is_late(item: ActionItem, now: datetime, settings: Settings) -> bool:
    """Still going after part 1's overdue time: the end of the working day of its due date,
    or that many working days later when the manager said so."""
    return item.state in STILL_GOING and overdue_at(item.due_date, settings) <= now


def action_item_figures(
    items: Iterable[ActionItem], now: datetime, settings: Settings
) -> ActionItemFigures:
    monday, sunday = week_of(now, settings)
    found = list(items)
    return ActionItemFigures(
        open=sum(1 for i in found if i.state is ItemState.OPEN),
        due_this_week=sum(
            1 for i in found if i.state in STILL_GOING and monday <= i.due_date <= sunday
        ),
        late=sum(1 for i in found if is_late(i, now, settings)),
    )


def assignment_figures(assignments: Iterable[AssignmentRecord]) -> AssignmentFigures:
    """In progress, and waiting for the manager: waiting for their review."""
    found = list(assignments)
    return AssignmentFigures(
        in_progress=sum(1 for a in found if a.state == "in_progress"),
        waiting_for_manager=sum(1 for a in found if a.state == "waiting_for_review"),
    )


def spend_figures(projects: Iterable[ProjectRecord]) -> list[ProjectSpend]:
    """Spend against budget for each project the manager can see, as Flow gives it."""
    return [
        ProjectSpend(
            project_id=p.id,
            project_name=p.name,
            spend=p.spend,
            budget=p.budget,
            source_label=p.source_label,
            read_at=p.read_at,
        )
        for p in sorted(projects, key=lambda p: p.name.casefold())
    ]


def decisions_waiting(papers: Iterable[DecisionPaper]) -> int:
    """Decision papers in state ready only: one sent back is not waiting for the manager."""
    return sum(1 for p in papers if p.state is DecisionPaperState.READY)


# Checking what the assistant returns (BR-6) ------------------------------------------------


def _refused(why: str) -> str:
    return f"The briefing was refused because {why}."


@dataclass
class CheckedBriefing:
    notes: dict[str, list[BriefingNote]] = field(default_factory=dict)
    refused: list[str] = field(default_factory=list)


def check_briefing(
    raw: dict[str, Any], held: Sequence[BriefingSectionKey], labels: set[str]
) -> CheckedBriefing:
    """The assistant's notes for each section held, or why the briefing is refused: a note
    that cites nothing, or an input that was not in the pack, refuses the whole briefing."""
    checked = CheckedBriefing()
    reasons: list[str] = []

    def refuse(why: str) -> None:
        reason = _refused(why)
        if reason not in reasons:
            reasons.append(reason)

    sections = raw.get("sections")
    if not isinstance(sections, list):
        refuse("its sections could not be read")
        checked.refused = reasons
        return checked
    for entry in sections:
        if not isinstance(entry, dict) or not isinstance(entry.get("notes", []), list):
            refuse("a section could not be read")
            continue
        key = entry.get("section")
        if key not in held:
            refuse("it has notes for a section the briefing does not hold")
            continue
        title = SECTION_TITLES[key]
        for note in entry.get("notes", []):
            if not isinstance(note, dict) or not isinstance(note.get("text"), str):
                refuse(f"a note in {title} could not be read")
                continue
            text = note["text"].strip()
            sources = note.get("sources")
            if not text:
                refuse(f"a note in {title} is empty")
            elif len(text) > BRIEFING_NOTE_LIMIT:
                refuse(f"a note in {title} is longer than {BRIEFING_NOTE_LIMIT} characters")
            elif not isinstance(sources, list) or not sources:
                refuse(f"a note in {title} cites nothing")
            elif not all(isinstance(s, str) and s in labels for s in sources):
                refuse(f"a note in {title} cites an input that was not in the pack")
            else:
                checked.notes.setdefault(key, []).append(
                    BriefingNote(text=text, sources=list(dict.fromkeys(sources)))
                )
    checked.refused = reasons
    if reasons:
        checked.notes = {}
    return checked


def comment_refusal(comment: str | None) -> str | None:
    if comment is None or not comment.strip():
        return COMMENT_NEEDED
    if len(comment) > COMMENT_LIMIT:
        return COMMENT_TOO_LONG
    return None
