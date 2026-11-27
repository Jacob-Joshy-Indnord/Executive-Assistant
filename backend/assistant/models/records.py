"""Record shapes of BRD section 10.

Every record has an id (UUID), its organisation, its assistant where it has
one, the time it was made (UTC) and a version number for optimistic checks.

Records are either *mutable* (changed and removed through the store with a
version check) or *append-only*: confirmed statements and reports are never
changed or removed (BR-3, Story 5.3), so the store offers no way to.

Each record type registers itself under its ``kind`` in :data:`RECORD_TYPES`,
so that new kinds are added in one place.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any, ClassVar, Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from assistant.models.boundary import ActionKind, ApprovalLevel, ApprovalLimits, WorkKind
from assistant.models.common import FlowId, UtcDateTime

# Text limits of section 8.1, in characters.
ACTION_ITEM_LIMIT = 300
STATEMENT_FIELD_LIMIT = 300
REASON_LIMIT = 500
UPDATE_NOTE_LIMIT = 1000
MESSAGE_LIMIT = 2000
CHAT_LIMIT = 4000

RECORD_TYPES: dict[str, type[Record]] = {}


class Record(BaseModel):
    """Fields every record has."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ClassVar[str] = ""
    append_only: ClassVar[bool] = False

    id: UUID = Field(default_factory=uuid4)
    organisation_id: FlowId
    assistant_id: UUID | None = None
    created_at: UtcDateTime
    version: int = Field(default=1, ge=1)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.kind:
            if cls.kind in RECORD_TYPES:
                raise TypeError(f"record kind {cls.kind!r} is registered twice")
            RECORD_TYPES[cls.kind] = cls

    def evolve(self, **changes: Any) -> Self:
        """A validated copy with ``changes``; identity and version stay as read."""
        for fixed in ("id", "organisation_id", "created_at", "version"):
            if fixed in changes:
                raise ValueError(f"{fixed} cannot be changed")
        return self.model_validate({**self.model_dump(), **changes})


class MutableRecord(Record):
    """A record the store can change or remove, with a version check."""


class AppendOnlyRecord(Record):
    """A record that, once stored, is kept exactly as it is."""

    append_only: ClassVar[bool] = True


# --- Assistant and settings -------------------------------------------------


class AssistantState(StrEnum):
    NEW = "new"
    ACTIVE = "active"
    PAUSED = "paused"
    ENDED = "ended"


class AssistantRecord(MutableRecord):
    kind: ClassVar[str] = "assistant"

    manager_id: FlowId
    display_name: str = Field(min_length=1, max_length=200)
    flow_id: FlowId
    state: AssistantState = AssistantState.NEW
    manager_since: UtcDateTime | None = None
    """When the current manager took over; they see nothing made before it (Story 2.4)."""


class Preferences(MutableRecord):
    """The answers of the first-time form (Story 2.1); every part is optional."""

    kind: ClassVar[str] = "preferences"

    answers: dict[str, str] = Field(default_factory=dict)
    skipped: bool = False


class ApprovalLevelRecord(MutableRecord):
    kind: ClassVar[str] = "approval_level"

    action_kind: ActionKind
    level: ApprovalLevel = ApprovalLevel.WITH_APPROVAL
    limits: ApprovalLimits = Field(default_factory=ApprovalLimits)


Colour = Literal["green", "amber", "red"]


class OrganisationSettings(MutableRecord):
    kind: ClassVar[str] = "organisation_settings"

    colour_meanings: dict[Colour, str] = Field(default_factory=dict)


# --- Work, chat and requests ------------------------------------------------


class WorkState(StrEnum):
    WAITING = "waiting"
    RUNNING = "running"
    DONE = "done"
    REFUSED = "refused"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PieceOfWork(MutableRecord):
    kind: ClassVar[str] = "piece_of_work"

    work_kind: WorkKind
    asked_by: FlowId | None = None
    """The person who asked; ``None`` when it came from a schedule."""
    input_labels: list[str] = Field(default_factory=list)
    reply: str | None = Field(default=None, max_length=CHAT_LIMIT)
    refused: list[str] = Field(default_factory=list)
    """The reason for each tool request that failed a check."""
    state: WorkState = WorkState.WAITING


class ChatMessage(MutableRecord):
    kind: ClassVar[str] = "chat_message"

    author: Literal["manager", "assistant"]
    text: str = Field(min_length=1, max_length=CHAT_LIMIT)


class RequestState(StrEnum):
    WAITING = "waiting"
    APPROVED = "approved"
    DONE = "done"
    DECLINED = "declined"
    REFUSED = "refused"
    FAILED = "failed"
    WITHDRAWN = "withdrawn"
    UNDONE = "undone"


class RequestRecord(MutableRecord):
    """Something to be done on the manager's behalf; goes through the approval check."""

    kind: ClassVar[str] = "request"

    action_kind: ActionKind
    subject_kind: str = Field(max_length=50)
    subject_id: str | None = None
    target_id: FlowId | None = None
    text: str | None = Field(default=None, max_length=MESSAGE_LIMIT)
    edited_text: str | None = Field(default=None, max_length=MESSAGE_LIMIT)
    reason: str | None = Field(default=None, max_length=REASON_LIMIT)
    effect: str | None = Field(default=None, max_length=REASON_LIMIT)
    allowed_by: Literal["manager", "without_approval"] | None = None
    state: RequestState = RequestState.WAITING


# --- Action items and updates -----------------------------------------------


class ItemState(StrEnum):
    WAITING_FOR_OWNER = "waiting_for_owner"
    OPEN = "open"
    DECLINED = "declined"
    DONE = "done"
    CLOSED = "closed"


class ItemEvent(BaseModel):
    """One line of an action item's history: ids and states only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    at: UtcDateTime
    who: FlowId
    event: str = Field(max_length=50)


class ActionItem(MutableRecord):
    kind: ClassVar[str] = "action_item"

    text: str = Field(min_length=1, max_length=ACTION_ITEM_LIMIT)
    owner_id: FlowId
    due_date: date
    follow_up_times: list[UtcDateTime] = Field(default_factory=list, max_length=5)
    origin: Literal["manager", "chat"]
    project_id: FlowId | None = None
    assignment_id: FlowId | None = None
    history: list[ItemEvent] = Field(default_factory=list)
    state: ItemState = ItemState.WAITING_FOR_OWNER


class Update(MutableRecord):
    kind: ClassVar[str] = "update"

    item_id: UUID
    author_id: FlowId
    word: Literal["on_track", "done", "blocked"]
    note: str | None = Field(default=None, max_length=UPDATE_NOTE_LIMIT)
    date_asked_for: date | None = None


class PersonRequestState(StrEnum):
    TO_BE_SENT = "to_be_sent"
    SENT = "sent"
    ANSWERED = "answered"
    WITHDRAWN = "withdrawn"


class PersonRequest(MutableRecord):
    """Something asked of a person: accept, update, status or confirm."""

    kind: ClassVar[str] = "person_request"

    person_id: FlowId
    asks: Literal["accept", "update", "status", "confirm"]
    subject_kind: str = Field(max_length=50)
    subject_id: str
    answer_by: UtcDateTime
    state: PersonRequestState = PersonRequestState.TO_BE_SENT


# --- Project status ---------------------------------------------------------


class FollowedProject(MutableRecord):
    kind: ClassVar[str] = "followed_project"

    project_id: FlowId
    confirmer_id: FlowId


class StatementWording(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str = Field(max_length=REASON_LIMIT)
    what_changed: str = Field(default="", max_length=STATEMENT_FIELD_LIMIT)
    what_comes_next: str = Field(default="", max_length=STATEMENT_FIELD_LIMIT)


class DraftState(StrEnum):
    DRAFT = "draft"
    WITH_OWNER = "with_owner"
    REPLACED = "replaced"
    ANSWERED = "answered"


class StatementDraft(MutableRecord):
    """A statement not yet confirmed. Never shown as status (BR-4)."""

    kind: ClassVar[str] = "statement_draft"

    project_id: FlowId
    owner_id: FlowId
    wording: StatementWording
    source: str = Field(max_length=200)
    author_id: FlowId
    state: DraftState = DraftState.DRAFT


class ConfirmedStatement(AppendOnlyRecord):
    """A confirmed or corrected statement version: never changed or removed (BR-3).

    A later correction is a new record pointing at ``previous_version_id``.
    """

    kind: ClassVar[str] = "confirmed_statement"

    project_id: FlowId
    owner_id: FlowId
    colour: Colour
    wording: StatementWording
    source: str = Field(max_length=200)
    author_id: FlowId
    mark: Literal["confirmed", "corrected"]
    previous_version_id: UUID | None = None
    draft_id: UUID | None = None
    unconfirmed_wording: StatementWording | None = None
    """The assistant's wording the owner did not confirm (BR-5). Never shown to the manager."""


class ReportLine(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: FlowId
    project_name: str
    colour: Colour | None = None
    wording: StatementWording | None = None
    source: str | None = None
    statement_date: date | None = None
    statement_id: UUID | None = None
    mark: Literal["confirmed", "corrected", "awaiting_confirmation", "no_confirmed_status"]


class Report(AppendOnlyRecord):
    """The status of every followed project at one moment, kept as it was."""

    kind: ClassVar[str] = "report"

    lines: list[ReportLine] = Field(default_factory=list)
    changed_since_last: list[FlowId] = Field(default_factory=list)


# --- Attention ---------------------------------------------------------------


class AttentionState(StrEnum):
    OPEN = "open"
    DECIDED = "decided"
    CLEARED = "cleared"


class AttentionEntry(MutableRecord):
    kind: ClassVar[str] = "attention_entry"

    subject_kind: str = Field(max_length=50)
    subject_id: str | None = None
    reason: str = Field(max_length=REASON_LIMIT)
    known: str = Field(default="", max_length=MESSAGE_LIMIT)
    choices: list[str] = Field(min_length=2, max_length=4)
    decision: str | None = None
    state: AttentionState = AttentionState.OPEN


def append_only_kinds() -> frozenset[str]:
    """Kinds that are never changed or removed once stored."""
    return frozenset(kind for kind, cls in RECORD_TYPES.items() if cls.append_only)
