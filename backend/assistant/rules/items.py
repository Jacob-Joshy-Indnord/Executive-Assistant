"""Action items, updates and their attention entries (Epic 4). Pure rules.

What the manager and owners may do with an item and the sentence shown when
they may not; BR-16 (who wrote the words decides whether they need
confirming); which trigger raises an attention entry, with which choices, and
which update settles it; and how an assignment's state shows on its item.
"""

from __future__ import annotations

from datetime import date, timedelta
from enum import StrEnum
from typing import Literal

from assistant.models.records import (
    ACTION_ITEM_LIMIT,
    MESSAGE_LIMIT,
    REASON_LIMIT,
    UPDATE_NOTE_LIMIT,
    ItemState,
)

Word = Literal["on_track", "done", "blocked"]
WORDS: tuple[Word, ...] = ("on_track", "done", "blocked")
WORD_LABELS: dict[str, str] = {"on_track": "On track", "done": "Done", "blocked": "Blocked"}
MAX_FOLLOW_UPS = 5

FORM_FIRST = "Save or skip the first-time form before giving action items."
TEXT_EMPTY = "The item needs a text."
TEXT_TOO_LONG = f"An item can be at most {ACTION_ITEM_LIMIT} characters."
DUE_IN_PAST = "The due date must be today or later."
DUE_TOO_FAR = "The due date must be within two years."
NOT_AN_OWNER = "The owner must be a person or virtual employee in the organisation."
ASSIGNMENT_NEEDED = "Choose one of the virtual employee's assignments."
ASSIGNMENT_NOT_THEIRS = "That assignment is not one of this virtual employee's."
ASSIGNMENT_ONLY_FOR_VIRTUAL = "Only an item for a virtual employee is linked to an assignment."
UNKNOWN_PROJECT = "That project is not one you can see."
ONLY_THE_OWNER = "Only the owner of this item can answer it."
NOT_WAITING = "This item is no longer waiting for its owner."
ACCEPT_FIRST = "Accept the item before giving an update."
ENDED = "This item is already done or closed."
NOT_OPEN = "Only an open item can be changed this way."
LINKED = "This item follows its assignment, so its owner is not asked."
ONLY_OWN_DONE = "Only an item you gave yourself can be marked done here."
SAME_OWNER = "The item already has this owner."
SAME_DATE = "Choose a date other than the current due date."
NO_FOLLOW_UPS = "This item has no follow-ups because no one is asked about it."
REASON_NEEDED = "Give a reason."
REASON_TOO_LONG = f"A reason can be at most {REASON_LIMIT} characters."
NOTE_TOO_LONG = f"A note can be at most {UPDATE_NOTE_LIMIT:,} characters."
UNKNOWN_WORD = "Choose On track, Done or Blocked."
TOO_MANY_FOLLOW_UPS = f"An item can have at most {MAX_FOLLOW_UPS} follow-ups."
FOLLOW_UP_PAST = "Follow-up times must be in the future."
FOLLOW_UP_NO_ZONE = "Follow-up times need a time zone."
ASKING_NOT_ALLOWED = "Follow-ups are not sent because asking owners is not allowed."

UNFINISHED = frozenset({ItemState.WAITING_FOR_OWNER, ItemState.OPEN, ItemState.DECLINED})
"""Items the manager can still decide about."""


def text_refusal(text: str) -> str | None:
    if not text.strip():
        return TEXT_EMPTY
    if len(text) > ACTION_ITEM_LIMIT:
        return TEXT_TOO_LONG
    return None


def due_date_refusal(due: date, today: date) -> str | None:
    """A due date is from today on, and not more than two years away."""
    if due < today:
        return DUE_IN_PAST
    if due > today + timedelta(days=366 * 2):
        return DUE_TOO_FAR
    return None


def reason_refusal(reason: str | None, required: bool) -> str | None:
    if reason is None or not reason.strip():
        return REASON_NEEDED if required else None
    if len(reason) > REASON_LIMIT:
        return REASON_TOO_LONG
    return None


def note_refusal(note: str | None) -> str | None:
    if note is not None and len(note) > UPDATE_NOTE_LIMIT:
        return NOTE_TOO_LONG
    return None


def message_refusal(text: str | None) -> str | None:
    if text is None or not text.strip():
        return "Write the message before sending it."
    if len(text) > MESSAGE_LIMIT:
        return f"A message can be at most {MESSAGE_LIMIT:,} characters."
    return None


# BR-16 ------------------------------------------------------------------------------


def needs_confirmation(author_id: str, owner_id: str) -> bool:
    """BR-16: words the owner wrote themselves are shown word for word and count as
    confirmed. Anyone else's words about the owner's work need the owner's confirmation."""
    return author_id != owner_id


# Attention entries ------------------------------------------------------------------


class Reason(StrEnum):
    OVERDUE = "overdue"
    UNANSWERED = "unanswered"
    BLOCKED = "blocked"
    DECLINED = "declined"
    OTHER_DATE = "other_date"
    OWNER_LEFT = "owner_left"
    REVIEW = "review"
    ASSIGNMENT_MISSING = "assignment_missing"


EXTEND = "Extend"
REASSIGN = "Reassign"
TAKE_IT_UP = "Take it up"
CLOSE = "Close"
USE_NEW_DATE = "Use the new date"
KEEP_DATE = "Keep the first date"

REASON_SENTENCES: dict[Reason, str] = {
    Reason.OVERDUE: "It is not done and its due date has passed.",
    Reason.UNANSWERED: "Its owner has not answered in time.",
    Reason.BLOCKED: "Its owner says it is blocked.",
    Reason.DECLINED: "Its owner declined it.",
    Reason.OTHER_DATE: "Its owner asked for another date.",
    Reason.OWNER_LEFT: "Its owner has left the organisation.",
    Reason.REVIEW: "Its assignment waits for your review.",
    Reason.ASSIGNMENT_MISSING: "Its assignment can no longer be found.",
}
REASON_LABELS: dict[Reason, str] = {
    Reason.OVERDUE: "Overdue",
    Reason.UNANSWERED: "No answer",
    Reason.BLOCKED: "Blocked",
    Reason.DECLINED: "Declined",
    Reason.OTHER_DATE: "Other date",
    Reason.OWNER_LEFT: "Owner left",
    Reason.REVIEW: "Review",
    Reason.ASSIGNMENT_MISSING: "Assignment gone",
}
_ALL = [EXTEND, REASSIGN, TAKE_IT_UP, CLOSE]
CHOICES: dict[Reason, list[str]] = {
    Reason.OVERDUE: _ALL,
    Reason.UNANSWERED: _ALL,
    Reason.BLOCKED: _ALL,
    Reason.DECLINED: [REASSIGN, TAKE_IT_UP, CLOSE],
    Reason.OTHER_DATE: [USE_NEW_DATE, KEEP_DATE],
    Reason.OWNER_LEFT: [REASSIGN, CLOSE],
    Reason.REVIEW: _ALL,
    Reason.ASSIGNMENT_MISSING: [REASSIGN, TAKE_IT_UP, CLOSE],
}

_SETTLED_BY: dict[Reason, frozenset[str]] = {
    Reason.UNANSWERED: frozenset(WORDS),
    Reason.BLOCKED: frozenset({"on_track", "done"}),
    Reason.REVIEW: frozenset({"on_track", "done"}),
    Reason.ASSIGNMENT_MISSING: frozenset(WORDS),
    Reason.OVERDUE: frozenset({"done"}),
    Reason.OTHER_DATE: frozenset({"done"}),
    Reason.DECLINED: frozenset(),
    Reason.OWNER_LEFT: frozenset(),
}


def settles(reason: str | None, word: str) -> bool:
    """Whether an update with ``word`` settles an entry raised for ``reason``: any answer
    settles "no answer", On track or Done settles Blocked, only Done settles a late item."""
    try:
        return word in _SETTLED_BY[Reason(reason or "")]
    except ValueError:
        return False


def choice_named(choices: list[str], wanted: str) -> str | None:
    """The entry's choice called ``wanted``, ignoring case and spaces at the ends."""
    key = wanted.strip().casefold()
    return next((c for c in choices if c.casefold() == key), None)


def prepared_message(item_text: str, owner_name: str, due: date, reason: Reason) -> str:
    """The message offered for Take it up, made by the module (not the assistant); the
    manager edits it before sending."""
    return (
        f'About the action item "{item_text}" ({owner_name}, due {due:%A %d %B}): '
        f"{REASON_SENTENCES[reason]} Could you help me take it up?"
    )


# Assignments -------------------------------------------------------------------------

MISSING = "missing"
ASSIGNMENT_WORDS: dict[str, Word] = {
    "in_progress": "on_track",
    "blocked": "blocked",
    "approved": "done",
}
"""How an assignment's state shows on its item; other states leave the item unchanged."""
ASSIGNMENT_REASONS: dict[str, Reason] = {
    "waiting_for_review": Reason.REVIEW,
    MISSING: Reason.ASSIGNMENT_MISSING,
}
