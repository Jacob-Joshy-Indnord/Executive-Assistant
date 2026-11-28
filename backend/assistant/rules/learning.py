"""Learning (part 2, Epic 3): signals, lessons, suggested levels and improvement. Pure rules.

The assistant learns from what happens and shows what it learned, and it never widens its
own authority: no lesson, signal or suggestion changes a level by itself. Here are the
limits, the sentences shown, how 'the same words' are compared, the one-line evidence of a
lesson, how the track record of a suggestion is judged, and the calendar months of the
improvement figures. Part 2's two learning tools are registered here.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from assistant.models.boundary import ApprovalLevel
from assistant.models.records import (
    AREA_LIMIT,
    LESSON_LIMIT,
    MAX_TRACK_RECORD,
    REASON_LIMIT,
    RequestRecord,
    RequestState,
    SignalKind,
)
from assistant.rules.tools import FieldSpec, ToolSpec, register

MAX_SIGNALS_IN_PACK = 50
MAX_KEPT_LESSONS = 200
PACK_SIGNAL_TEXT = 500
"""Characters of each wording of a signal given in a pack."""
EVIDENCE_LINE_LIMIT = 200
MAX_EVIDENCE_SIGNALS = 50
DECLINED_FOR_DAYS = 30
SIGNALS_LABEL = "Signals since the last piece of work"
LESSONS_LABEL = "What I've learned"

PREFERENCE_AREA = "preference"
MANAGER_AREA = "general"

# Sentences shown to the manager ----------------------------------------------------------

LIMIT_REACHED = (
    f"{MAX_KEPT_LESSONS} lessons are kept already, so remove one before keeping another."
)
LESSON_EMPTY = "Write the lesson first."
LESSON_TOO_LONG = f"A lesson can be at most {LESSON_LIMIT} characters."
AREA_TOO_LONG = f"An area can be at most {AREA_LIMIT} characters."
NOT_PROPOSED = "Only a proposed lesson can be kept."
FORM_LIMIT_REACHED = (
    f"{MAX_KEPT_LESSONS} lessons are kept already, so remove one before adding an answer "
    "to the form."
)
SUGGESTION_DECIDED = "This suggestion has already been decided."
LEVEL_LOCKED = "This kind of action keeps its level; no one can change it."

# Why a tool request is refused (the end of "Tool request N was refused because ...") -------

NO_EVIDENCE = (
    "its evidence names no signals kept for this assistant and is not a word-for-word quote "
    "of a message the manager sent to it"
)
REMOVED_BEFORE = "a lesson in the same words was removed"
ALREADY_IN_RECORD = "the same lesson is already in the record"
RECORD_FULL = f"{MAX_KEPT_LESSONS} lessons are kept already"
NOT_A_KIND = "it names no kind of action with an approval level"
NOT_A_LEVEL = "it names no approval level"
KIND_LOCKED = "that kind of action keeps its level"
NOT_WIDER = "the level it proposes is not wider than the level now"
TRACK_RECORD_SHORT = (
    f"the last {MAX_TRACK_RECORD} requests of that kind were not all approved unchanged"
)
ALREADY_OPEN = "the same suggestion is already waiting for the manager"
DECLINED_RECENTLY = (
    f"the manager declined the same suggestion less than {DECLINED_FOR_DAYS} days ago"
)


# The same words ------------------------------------------------------------------------------

_SPACE = re.compile(r"\s+")


def same_words(text: str) -> str:
    """The words of ``text``, ignoring capitals and spacing."""
    return _SPACE.sub(" ", text).strip().casefold()


def words_digest(text: str) -> str:
    """A one-way digest of the words, so a removed lesson's words need not be kept."""
    return hashlib.sha256(same_words(text).encode("utf-8")).hexdigest()


def lesson_refusal(text: str | None) -> str | None:
    """Why the manager's lesson cannot be kept as written, or ``None``."""
    if text is None or not text.strip():
        return LESSON_EMPTY
    if len(text.strip()) > LESSON_LIMIT:
        return LESSON_TOO_LONG
    return None


# Stated lessons: the manager's own words ------------------------------------------------------


def quotes(message: str, quote: str) -> bool:
    """Whether ``quote`` is, word for word, the manager's message or a run of whole words of it.

    Capitals, punctuation and spacing inside the quote must match exactly; only spaces around
    it are ignored.
    """
    wanted = quote.strip()
    if not wanted:
        return False
    start = message.find(wanted)
    while start != -1:
        end = start + len(wanted)
        before = message[start - 1] if start > 0 else " "
        after = message[end] if end < len(message) else " "
        if not (before.isalnum() and wanted[0].isalnum()) and not (
            after.isalnum() and wanted[-1].isalnum()
        ):
            return True
        start = message.find(wanted, start + 1)
    return False


# Shown evidence ------------------------------------------------------------------------------

SIGNAL_WORDS: dict[SignalKind, tuple[str, str]] = {
    SignalKind.DRAFT_EDITED: ("draft you edited", "drafts you edited"),
    SignalKind.DRAFT_APPROVED: ("draft you approved", "drafts you approved"),
    SignalKind.DRAFT_DECLINED: ("draft you declined", "drafts you declined"),
    SignalKind.BRIEFING_SENT_BACK: ("briefing you sent back", "briefings you sent back"),
    SignalKind.PAPER_SENT_BACK: ("decision paper you sent back", "decision papers you sent back"),
    SignalKind.NOTES_CORRECTED: ("correction to notes", "corrections to notes"),
    SignalKind.STATEMENT_CORRECTED: ("statement an owner corrected", "statements owners corrected"),
    SignalKind.ITEM_ON_TIME: ("item done on time", "items done on time"),
    SignalKind.ITEM_LATE: ("item done late", "items done late"),
}

FORM_ANSWER_NAMES: dict[str, str] = {
    "working_days": "working days",
    "working_day_starts": "when the working day starts",
    "working_day_ends": "when the working day ends",
    "time_zone": "time zone",
    "tone": "tone",
    "sign_off": "sign-off",
    "priorities": "priorities",
    "key_people": "key people",
    "follow_ups_per_item": "follow-ups for each item",
    "answer_within_working_days": "working days to answer",
    "late_after_working_days": "working days before an item is late",
    "notices_per_owner_per_day": "notices to each owner a day",
    "report_frequency": "how often the report comes",
    "report_day": "the day of the report",
    "report_time": "the time of the report",
    "ask_owners_working_days_before": "working days before the report to ask owners",
    "report_includes": "what the report includes",
    "report_leaves_out": "what the report leaves out",
}

ORIGIN_WORDS = {
    "assistant": "Proposed by your assistant",
    "your_words": "Your own words",
    "typed": "Added by you",
    "form": "Your preferences form",
}


def one_line(text: str, limit: int = EVIDENCE_LINE_LIMIT) -> str:
    flat = _SPACE.sub(" ", text).strip()
    return flat if len(flat) <= limit else flat[: limit - 3].rstrip() + "..."


def signals_evidence(kinds: Sequence[SignalKind]) -> str:
    """'From 2 drafts you edited and 1 item done late.'"""
    counts: dict[SignalKind, int] = {}
    for kind in kinds:
        counts[kind] = counts.get(kind, 0) + 1
    parts = [f"{n} {SIGNAL_WORDS[k][0 if n == 1 else 1]}" for k, n in counts.items()]
    if not parts:
        return "From signals that are no longer kept."
    said = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return one_line(f"From {said}.")


def evidence_line(
    origin: str,
    *,
    signal_kinds: Sequence[SignalKind] = (),
    message_date: date | None = None,
    form_key: str | None = None,
    added_on: date | None = None,
) -> str:
    """The evidence of a lesson in one line."""
    if origin == "assistant":
        return signals_evidence(signal_kinds)
    if origin == "your_words":
        when = f" of {message_date:%d %B %Y}" if message_date else ""
        return f"Quoted word for word from your message{when}."
    if origin == "form":
        name = FORM_ANSWER_NAMES.get(form_key or "", (form_key or "an answer").replace("_", " "))
        return one_line(f"Your answer about {name} in the preferences form.")
    when = f" on {added_on:%d %B %Y}" if added_on else ""
    return f"Written by you{when}."


# Suggested levels --------------------------------------------------------------------------

LEVEL_ORDER = {
    ApprovalLevel.NOT_ALLOWED: 0,
    ApprovalLevel.WITH_APPROVAL: 1,
    ApprovalLevel.WITHOUT_APPROVAL: 2,
}


def wider(proposed: ApprovalLevel, now: ApprovalLevel) -> bool:
    return LEVEL_ORDER[proposed] > LEVEL_ORDER[now]


def put_to_the_manager(request: RequestRecord) -> bool:
    """A request the manager decided themselves: approved (as it was or edited) or declined."""
    if request.allowed_by == "manager":
        return request.state in (
            RequestState.APPROVED,
            RequestState.DONE,
            RequestState.FAILED,
            RequestState.UNDONE,
        )
    return request.state is RequestState.DECLINED and request.declined_by == "manager"


def approved_unchanged(request: RequestRecord) -> bool:
    return (
        request.allowed_by == "manager"
        and request.edited_text is None
        and put_to_the_manager(request)
    )


def track_record(requests: Iterable[RequestRecord]) -> list[RequestRecord]:
    """The last 20 requests of one kind the manager decided, oldest first."""
    decided = [r for r in requests if put_to_the_manager(r) and r.decided_at is not None]
    decided.sort(key=lambda r: (r.decided_at or r.created_at, r.created_at))
    return decided[-MAX_TRACK_RECORD:]


def supports(record: Sequence[RequestRecord]) -> bool:
    """Only a full record of 20, every one approved unchanged, supports a wider level."""
    return len(record) == MAX_TRACK_RECORD and all(approved_unchanged(r) for r in record)


# Calendar months in the manager's time zone ------------------------------------------------


def month_of(moment: datetime, time_zone: str) -> date:
    """The first day of the calendar month ``moment`` falls in, in ``time_zone``."""
    local = moment.astimezone(ZoneInfo(time_zone)).date()
    return local.replace(day=1)


def previous_month(first: date) -> date:
    return date(first.year - 1, 12, 1) if first.month == 1 else date(first.year, first.month - 1, 1)


def next_month(first: date) -> date:
    return date(first.year + 1, 1, 1) if first.month == 12 else date(first.year, first.month + 1, 1)


def month_bounds(first: date, time_zone: str) -> tuple[datetime, datetime]:
    """The month as UTC times: from its first moment, up to the first moment of the next."""
    zone = ZoneInfo(time_zone)
    start = datetime.combine(first, time(0), zone).astimezone(UTC)
    end = datetime.combine(next_month(first), time(0), zone).astimezone(UTC)
    return start, end


# The learning tools ----------------------------------------------------------------------

F = FieldSpec
PROPOSE_LEARNING = register(
    ToolSpec(
        "propose_learning",
        (
            F("area", "text", AREA_LIMIT),
            F("lesson", "text", LESSON_LIMIT),
            F("evidence", "evidence", REASON_LIMIT * 4),
        ),
    )
)
SUGGEST_APPROVAL_CHANGE = register(
    ToolSpec(
        "suggest_approval_change",
        (F("kind", "text", 50), F("level", "text", 50), F("source", "source", required=False)),
    )
)
