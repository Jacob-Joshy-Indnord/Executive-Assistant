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

from datetime import date, datetime
from enum import StrEnum
from typing import Any, ClassVar, Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from assistant.models.boundary import (
    ActionKind,
    ApprovalLevel,
    ApprovalLimits,
    PackInput,
    WorkKind,
)
from assistant.models.common import Amount, FlowId, UtcDateTime
from assistant.models.preferences import PreferencesForm

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
    """When this manager took over (information only)."""
    handed_over_to: FlowId | None = None
    """Set when this record ended because the assistant was handed over. The new
    manager gets a new record, so nothing from before is theirs (Story 2.4)."""


class Preferences(MutableRecord):
    """The answers of the first-time form (Story 2.1); every part is optional."""

    kind: ClassVar[str] = "preferences"

    form: PreferencesForm = Field(default_factory=PreferencesForm)
    saved: bool = False
    skipped: bool = False
    first_open_time_zone: str | None = None
    """The time zone of the manager's browser when they first opened the page."""


class ApprovalLevelRecord(MutableRecord):
    kind: ClassVar[str] = "approval_level"

    action_kind: ActionKind
    level: ApprovalLevel = ApprovalLevel.WITH_APPROVAL
    limits: ApprovalLimits = Field(default_factory=ApprovalLimits)

    @property
    def state(self) -> str:
        """What audit records name as the state before and after: the level."""
        return self.level.value


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
    message_id: UUID | None = None
    """The manager's chat message this piece answers, for work asked in the chat."""
    given_inputs: list[PackInput] = Field(default_factory=list)
    """Inputs the asker gave, e.g. the manager's choice for follow-on work."""
    input_labels: list[str] = Field(default_factory=list)
    reply: str | None = Field(default=None, max_length=CHAT_LIMIT)
    refused: list[str] = Field(default_factory=list)
    """The reason for each tool request that failed a check."""
    reason: str | None = Field(default=None, max_length=REASON_LIMIT)
    """Why Flow refused the work or it failed: one sentence to show."""
    retry_of: UUID | None = None
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None
    state: WorkState = WorkState.WAITING


class ChatMessage(MutableRecord):
    kind: ClassVar[str] = "chat_message"

    author: Literal["manager", "assistant"]
    text: str = Field(min_length=1, max_length=CHAT_LIMIT)
    work_id: UUID | None = None
    """The piece of work the message asked for, or that the reply came from."""


class WorkSlot(MutableRecord):
    """Which piece of work is running for one assistant. Its id is derived from the
    assistant and every start or finish changes it with a version check, so at most one
    piece runs per assistant whatever the store."""

    kind: ClassVar[str] = "work_slot"

    running_work_id: UUID | None = None

    @property
    def state(self) -> str:
        return "running" if self.running_work_id else "free"


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
    """Something to be done on the manager's behalf; goes through the approval check.

    ``request_kind`` names the handler that carries it out (see
    :mod:`assistant.service.request_kinds`); ``subject_kind`` and ``subject_id`` name what
    it is about, and ``subject_version`` the version of that subject when it was asked, so
    a request whose subject has since changed is withdrawn.
    """

    kind: ClassVar[str] = "request"

    action_kind: ActionKind
    request_kind: str = Field(default="", max_length=50)
    subject_kind: str = Field(max_length=50)
    subject_id: str | None = None
    subject_version: int | None = None
    target_id: FlowId | None = None
    work_id: UUID | None = None
    text: str | None = Field(default=None, max_length=MESSAGE_LIMIT)
    edited_text: str | None = Field(default=None, max_length=MESSAGE_LIMIT)
    payload: dict[str, Any] = Field(default_factory=dict)
    """What carrying it out needs besides the text, e.g. a due date or a project."""
    reason: str | None = Field(default=None, max_length=REASON_LIMIT)
    declined_by: Literal["flow", "manager"] | None = None
    effect: str | None = Field(default=None, max_length=REASON_LIMIT)
    allowed_by: Literal["manager", "without_approval"] | None = None
    undoable: bool = False
    decided_at: UtcDateTime | None = None
    done_at: UtcDateTime | None = None
    undone_at: UtcDateTime | None = None
    undo_reason: str | None = Field(default=None, max_length=REASON_LIMIT)
    """Why the last undo did not work, to show beside the request."""
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
    follow_ups_set: bool = False
    """The times were chosen (by the manager, or in an approved proposal), so acceptance
    keeps them instead of working out the section 8.1 schedule."""
    follow_up_waiting_until: UtcDateTime | None = None
    """A follow-up that fell outside working hours or over the day's notices waits until then."""
    follow_up_note: str | None = Field(default=None, max_length=REASON_LIMIT)
    """Why follow-ups are not being sent, e.g. asking owners is not allowed."""
    origin: Literal["manager", "chat"]
    project_id: FlowId | None = None
    assignment_id: FlowId | None = None
    assignment_state: str | None = Field(default=None, max_length=50)
    """The linked assignment's state as last shown on the item, or ``missing``."""
    accepted_at: UtcDateTime | None = None
    answer_reason: str | None = Field(default=None, max_length=REASON_LIMIT)
    """The owner's own reason for proposing another date or declining (BR-16)."""
    date_asked_for: date | None = None
    close_reason: str | None = Field(default=None, max_length=REASON_LIMIT)
    history: list[ItemEvent] = Field(default_factory=list)
    state: ItemState = ItemState.WAITING_FOR_OWNER


class Update(MutableRecord):
    kind: ClassVar[str] = "update"

    item_id: UUID
    author_id: FlowId
    word: Literal["on_track", "done", "blocked"]
    note: str | None = Field(default=None, max_length=UPDATE_NOTE_LIMIT)
    date_asked_for: date | None = None
    source: Literal["owner", "assignment"] = "owner"
    """``assignment``: mirrored from the linked assignment in Flow, without confirmation."""


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
    send_after: UtcDateTime | None = None
    """For a request still to be sent: the earliest time it may go out."""
    sent_at: UtcDateTime | None = None
    state: PersonRequestState = PersonRequestState.TO_BE_SENT


# --- Project status ---------------------------------------------------------


class AssignmentChange(BaseModel):
    """A change seen to an assignment on a followed project: ids and states only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assignment_id: FlowId
    title: str = Field(max_length=300)
    before: str | None = Field(default=None, max_length=50)
    """``None`` when the assignment was not on the project before."""
    after: str = Field(max_length=50)
    """Its state now, or ``missing``."""
    at: UtcDateTime


MAX_ASSIGNMENT_CHANGES = 100


class FollowedProject(MutableRecord):
    kind: ClassVar[str] = "followed_project"

    project_id: FlowId
    confirmer_id: FlowId
    project_name: str = Field(default="", max_length=200)
    """As last read, to name the project when the manager can no longer see it."""
    window_start: UtcDateTime | None = None
    """When "since the last report" starts: the last report, or when it was followed."""
    spend_at_start: Amount | None = None
    assignment_states: dict[str, str] = Field(default_factory=dict)
    """The state last seen of each assignment on the project."""
    assignment_changes: list[AssignmentChange] = Field(
        default_factory=list, max_length=MAX_ASSIGNMENT_CHANGES
    )
    """Changes seen since ``window_start``: news for the next drafts."""
    asked_at: UtcDateTime | None = None
    """When its confirmer was last asked for its status."""
    missed_reports: int = Field(default=0, ge=0)
    """Reports in a row without a confirmation for the project."""

    def since(self) -> datetime:
        return self.window_start or self.created_at


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
    ask_id: UUID | None = None
    """The request to the owner it answered, if any."""
    changed_points: list[Literal["colour", "reason", "what_changed", "what_comes_next"]] = Field(
        default_factory=list
    )
    """For a correction: which points differ from what it corrected."""
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
    made_by: Literal["schedule", "manager"] = "schedule"
    """``manager``: made at once with "Report now"."""
    due_at: UtcDateTime | None = None
    """For a scheduled report: the time it was due."""


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
    reason_code: str | None = Field(default=None, max_length=50)
    """For an item's entry, which trigger raised it (see :mod:`assistant.rules.items`)."""
    asked_date: date | None = None
    """The date the owner asked for, offered to the manager."""
    decision: str | None = Field(default=None, max_length=100)
    decision_target_id: FlowId | None = None
    """Whom the decision concerned, e.g. the new owner or the person a message went to."""
    decided_by: FlowId | None = None
    decided_at: UtcDateTime | None = None
    state: AttentionState = AttentionState.OPEN


# --- Running what is due, pause ------------------------------------------------


class DueStepClaim(MutableRecord):
    """One due step, claimed once (BR-13). Its id is derived from what is due, so a
    second claim of the same step, from any process, is refused by the store."""

    kind: ClassVar[str] = "due_step_claim"

    step_kind: str = Field(max_length=100)
    subject: str = Field(max_length=200)
    due_at: UtcDateTime


class KeptResult(MutableRecord):
    """A result that arrived while the assistant was paused, kept aside unopened.

    ``payload`` is exactly what Flow sent; nothing reads it until the assistant
    is resumed. Its id is derived from the piece of work, so a repeat is ignored.
    """

    kind: ClassVar[str] = "kept_result"

    work_id: UUID
    payload: dict[str, Any]


class OrganisationControl(MutableRecord):
    """Whether the organisation has paused all its virtual employees."""

    kind: ClassVar[str] = "organisation_control"

    all_paused: bool = False

    @property
    def state(self) -> str:
        return "paused" if self.all_paused else "running"


def append_only_kinds() -> frozenset[str]:
    """Kinds that are never changed or removed once stored."""
    return frozenset(kind for kind, cls in RECORD_TYPES.items() if cls.append_only)
