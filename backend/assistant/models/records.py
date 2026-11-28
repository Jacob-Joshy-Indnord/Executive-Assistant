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

from datetime import date, datetime, time
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
from assistant.models.preferences import PreferencesForm, Weekday

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
    meeting_notes_allowed: bool = False
    """Whether transcripts may be used for notes in the organisation (part 2, section 8)."""


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
    origin: Literal["manager", "chat", "meeting"]
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
    asks: Literal["accept", "update", "status", "confirm", "attend"]
    """``attend``: an organiser is asked whether the assistant may attend their meeting."""
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


class ManagerNoticeMark(MutableRecord):
    """Up to when the manager has been told what is ready for them: reports and attention
    entries made after ``told_until`` are in the next notice. Its id is derived from the
    assistant and every notice changes it with a version check, so a notice goes once."""

    kind: ClassVar[str] = "manager_notice_mark"

    told_until: UtcDateTime
    told_at_that_time: list[str] = Field(default_factory=list, max_length=1000)
    """Ids of what was told that was made at ``told_until`` itself, so that something made
    at the same moment but after the notice is told next time."""

    @property
    def state(self) -> str:
        return "told"


class OrganisationControl(MutableRecord):
    """Whether the organisation has paused all its virtual employees."""

    kind: ClassVar[str] = "organisation_control"

    all_paused: bool = False

    @property
    def state(self) -> str:
        return "paused" if self.all_paused else "running"


# --- Meetings and their notes (part 2, Epic 1) -------------------------------------------

TITLE_LIMIT = 200
NOTE_LINE_LIMIT = 300
SUMMARY_LIMIT = 600
PART_TEXT_LIMIT = 100_000


class MeetingState(StrEnum):
    SHARED = "shared"
    WRITING = "writing"
    READY = "ready"
    FAILED = "failed"
    WITHDRAWN = "withdrawn"
    REMOVED = "removed"


class TranscriptPart(BaseModel):
    """One numbered part of a transcript. The text (and the speaker's name) is kept only
    until it is removed (BR-3); the number and the times or line numbers stay."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    number: int = Field(ge=1)
    starts: str | None = Field(default=None, max_length=20)
    """For a .vtt or an attended meeting: when the part starts, e.g. ``00:01:02.000``."""
    ends: str | None = Field(default=None, max_length=20)
    first_line: int | None = Field(default=None, ge=1)
    """For a text transcript: the line numbers the part covers."""
    last_line: int | None = Field(default=None, ge=1)
    speaker: str | None = Field(default=None, max_length=200)
    text: str | None = Field(default=None, max_length=PART_TEXT_LIMIT)

    def without_text(self) -> TranscriptPart:
        return self.model_copy(update={"text": None, "speaker": None})


class Meeting(MutableRecord):
    """A transcript shared by the person who ran a meeting, or delivered by the meeting
    service for a meeting the assistant attended, and what became of it."""

    kind: ClassVar[str] = "meeting"

    title: str = Field(min_length=1, max_length=TITLE_LIMIT)
    meeting_date: date
    organiser_id: FlowId
    manager_id: FlowId
    project_ids: list[FlowId] = Field(default_factory=list, max_length=50)
    origin: Literal["shared", "attended"] = "shared"
    ran_it: bool = False
    """The organiser's tick: "I ran this meeting"."""
    people_knew: bool = False
    """The organiser's tick: "People in it knew it was recorded and used for notes"."""
    organiser_agreed: bool = False
    """For an attended meeting: the organiser's agreement, in place of the two ticks."""
    source_format: Literal["vtt", "text", "attended"] = "text"
    parts: list[TranscriptPart] = Field(default_factory=list)
    text_arrived_at: UtcDateTime
    """When the transcript's text arrived: shared, or delivered by the meeting service."""
    text_removed_at: UtcDateTime | None = None
    work_id: UUID | None = None
    """The piece of work writing the notes now."""
    attendance_id: UUID | None = None
    notes_id: UUID | None = None
    manager_attended: bool | None = None
    reason: str | None = Field(default=None, max_length=REASON_LIMIT)
    """Why the notes could not be written: one sentence to show the manager."""
    state: MeetingState = MeetingState.SHARED

    @property
    def text_kept(self) -> bool:
        return any(part.text is not None or part.speaker is not None for part in self.parts)


class NoteLine(BaseModel):
    """A summary, decision or open point, with the transcript parts it came from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1, max_length=SUMMARY_LIMIT)
    parts: list[int] = Field(min_length=1)


class NoteAction(BaseModel):
    """An action from a meeting. It reaches its owner only once the manager keeps it (BR-4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    text: str = Field(min_length=1, max_length=MESSAGE_LIMIT)
    owner_name: str | None = Field(default=None, max_length=200)
    owner_id: FlowId | None = None
    """``None``: "Owner not found", for the manager to choose with Edit."""
    due_date: date | None = None
    parts: list[int] = Field(min_length=1)
    state: Literal["proposed", "kept", "removed"] = "proposed"
    item_id: UUID | None = None
    """The action item it became when the manager kept it."""
    edited: bool = False


class NoteStatement(BaseModel):
    """A project's status as said in the meeting. Never shown to the manager until its
    owner confirms it, and it never carries a colour (BR-4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: FlowId
    project_name: str = Field(max_length=200)
    wording: StatementWording
    parts: list[int] = Field(min_length=1)
    draft_id: UUID | None = None


class MeetingNotes(MutableRecord):
    """The notes of one meeting: a summary, decisions, actions, open points and status,
    each line with its transcript parts, and nothing else that was said (BR-2)."""

    kind: ClassVar[str] = "meeting_notes"

    meeting_id: UUID
    title: str = Field(min_length=1, max_length=TITLE_LIMIT)
    meeting_date: date
    organiser_id: FlowId
    project_ids: list[FlowId] = Field(default_factory=list)
    origin: Literal["shared", "attended"] = "shared"
    summary: NoteLine | None = None
    decisions: list[NoteLine] = Field(default_factory=list)
    actions: list[NoteAction] = Field(default_factory=list)
    open_points: list[NoteLine] = Field(default_factory=list)
    statements: list[NoteStatement] = Field(default_factory=list)
    parts: list[TranscriptPart] = Field(default_factory=list)
    """The part numbers and times or line numbers the lines cite; never their text."""
    manager_attended: bool | None = None
    dropped_statements: int = Field(default=0, ge=0)
    """Statements about projects the manager does not follow, left out."""
    shared: bool = False
    """Whether the manager shared the note with the organisation's knowledge store."""
    shared_at: UtcDateTime | None = None

    @property
    def state(self) -> str:
        return "shared" if self.shared else "kept"


class AttendanceState(StrEnum):
    BOOKED = "booked"
    WAITING_FOR_ORGANISER = "waiting_for_organiser"
    DECLINED = "declined"
    JOINING = "joining"
    ATTENDING = "attending"
    LEFT = "left"
    WRITING_NOTES = "writing_notes"
    READY = "ready"
    FAILED = "failed"
    WITHDRAWN = "withdrawn"


AttendanceBecause = Literal[
    "cancelled",
    "ended_before_joining",
    "no_answer",
    "organiser_declined",
    "organiser_removed",
    "join_refused",
    "not_let_in",
    "no_transcript",
    "notes_failed",
    "handed_over",
    "ended",
]


class MeetingAttendance(MutableRecord):
    """The assistant booked to attend one meeting. Ids, times and states only: no
    transcript, no notes and no other text; the title is read from the meeting service
    when it is shown."""

    kind: ClassVar[str] = "meeting_attendance"

    manager_id: FlowId
    meeting_ref: FlowId
    """The meeting as the meeting service identifies it."""
    organiser_id: FlowId
    starts_at: UtcDateTime
    ends_at: UtcDateTime
    series_id: FlowId | None = None
    booked_by: Literal["manager", "assistant"] = "manager"
    request_id: UUID | None = None
    """The assistant's proposal through the approval check, if it came from one."""
    ask_id: UUID | None = None
    """What the organiser was asked on Requests for you."""
    join_asked_at: UtcDateTime | None = None
    left_at: UtcDateTime | None = None
    transcript_wanted: bool = True
    meeting_id: UUID | None = None
    """The Meeting kept from its transcript."""
    because: AttendanceBecause | None = None
    state: AttendanceState = AttendanceState.BOOKED


# --- The briefing and decision papers (part 2, Epic 2) ----------------------------------

BRIEFING_NOTE_LIMIT = 500
DECISION_PAPER_LIMIT = 6000
COMMENT_LIMIT = 500
MIN_OPTIONS = 2
MAX_OPTIONS = 5

BriefingFrequency = Literal["weekly", "every_two_weeks", "monthly", "off"]
BriefingSectionKey = Literal[
    "action_items", "assignments", "spend", "decisions", "project_status", "meetings"
]
BRIEFING_SECTIONS: tuple[BriefingSectionKey, ...] = (
    "action_items",
    "assignments",
    "spend",
    "decisions",
    "project_status",
    "meetings",
)
"""The five groups of figures, then Meetings since the last briefing, in the order shown."""


class BriefingSettings(MutableRecord):
    """The briefing's schedule and the sections the manager ticked in Settings next to it.
    Without one, the briefing is weekly on Mondays at 08:00 with every section."""

    kind: ClassVar[str] = "briefing_settings"

    frequency: BriefingFrequency = "weekly"
    day: Weekday = Weekday.MONDAY
    at: time = time(8, 0)
    """The time of day in the manager's time zone."""
    sections: list[BriefingSectionKey] = Field(default_factory=lambda: list(BRIEFING_SECTIONS))
    """The ticked sections; all are on by default."""
    set_at: UtcDateTime
    """When the schedule was set: every two weeks counts from the first briefing after it."""

    @property
    def state(self) -> str:
        return self.frequency


class _Figures(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ActionItemFigures(_Figures):
    open: int = Field(ge=0)
    due_this_week: int = Field(ge=0)
    late: int = Field(ge=0)


class AssignmentFigures(_Figures):
    in_progress: int = Field(ge=0)
    waiting_for_manager: int = Field(ge=0)


class ProjectSpend(_Figures):
    project_id: FlowId
    project_name: str = Field(max_length=200)
    spend: Amount
    budget: Amount
    source_label: str = Field(max_length=200)
    read_at: UtcDateTime


class BriefingFigures(_Figures):
    """What the module worked out (BR-6). A group that is not in the briefing is ``None``."""

    action_items: ActionItemFigures | None = None
    assignments: AssignmentFigures | None = None
    spend: list[ProjectSpend] | None = None
    decisions_waiting: int | None = Field(default=None, ge=0)
    project_status: list[ReportLine] | None = None


class BriefingNote(_Figures):
    """One of the assistant's notes, with the inputs of the pack it cites."""

    text: str = Field(min_length=1, max_length=BRIEFING_NOTE_LIMIT)
    sources: list[str] = Field(min_length=1)


class BriefingMeeting(_Figures):
    """A meeting whose notes were kept since the last briefing, citing its notes."""

    notes_id: UUID
    title: str = Field(min_length=1, max_length=TITLE_LIMIT)
    meeting_date: date
    decisions: list[str] = Field(default_factory=list)
    open_points: list[str] = Field(default_factory=list)
    source: str = Field(max_length=200)
    """The label its notes have in the briefing's input pack."""


class BriefingVersion(_Figures):
    number: int = Field(ge=1)
    notes: dict[str, list[BriefingNote]] = Field(default_factory=dict)
    """The assistant's notes for each section."""
    notes_prepared: bool = True
    """False when the notes were refused twice: the figures are shown alone."""
    arrived_at: UtcDateTime
    comment: str | None = Field(default=None, max_length=COMMENT_LIMIT)
    """The manager's comment when they sent this version back."""
    sent_back_at: UtcDateTime | None = None


class BriefingState(StrEnum):
    PREPARING = "preparing"
    READY = "ready"
    ACCEPTED = "accepted"
    SENT_BACK = "sent_back"


class Briefing(MutableRecord):
    """One briefing: the figures the module worked out, the meetings kept since the last
    briefing, and each version of the assistant's notes. Every version is kept."""

    kind: ClassVar[str] = "briefing"

    made_by: Literal["schedule", "manager"] = "schedule"
    due_at: UtcDateTime | None = None
    """For a scheduled briefing: the time it was due."""
    since: UtcDateTime | None = None
    """When the last briefing was made; ``None`` for the first."""
    sections: list[BriefingSectionKey] = Field(default_factory=list)
    figures: BriefingFigures = Field(default_factory=BriefingFigures)
    meetings: list[BriefingMeeting] = Field(default_factory=list)
    versions: list[BriefingVersion] = Field(default_factory=list)
    work_id: UUID | None = None
    """The piece of work preparing the next version."""
    refusals: int = Field(default=0, ge=0)
    """How many times the version being prepared was refused."""
    state: BriefingState = BriefingState.PREPARING


class PaperVersion(_Figures):
    number: int = Field(ge=1)
    decision: str = Field(min_length=1, max_length=DECISION_PAPER_LIMIT)
    background: str = Field(default="", max_length=DECISION_PAPER_LIMIT)
    options: list[str] = Field(min_length=MIN_OPTIONS, max_length=MAX_OPTIONS)
    recommendation: str = Field(min_length=1, max_length=DECISION_PAPER_LIMIT)
    due_date: date
    sources: list[str] = Field(min_length=1)
    arrived_at: UtcDateTime
    comment: str | None = Field(default=None, max_length=COMMENT_LIMIT)
    """The manager's comment when they sent this version back."""
    sent_back_at: UtcDateTime | None = None


class DecisionPaperState(StrEnum):
    PREPARING = "preparing"
    """Being prepared or asked for once more; never shown with content."""
    READY = "ready"
    SENT_BACK = "sent_back"
    DECIDED = "decided"
    NOT_PREPARED = "not_prepared"
    """Refused twice: the manager sees that it could not be prepared, with Try again."""


class DecisionPaper(MutableRecord):
    """A decision put to the manager with options and a recommendation. Only versions that
    passed the checks are kept; a refused paper is never kept or shown."""

    kind: ClassVar[str] = "decision_paper"

    message_id: UUID | None = None
    """The manager's message that asked for it."""
    work_id: UUID | None = None
    """The piece of work preparing the next version."""
    versions: list[PaperVersion] = Field(default_factory=list)
    refusals: int = Field(default=0, ge=0)
    decision_id: UUID | None = None
    follow_on_work_id: UUID | None = None
    state: DecisionPaperState = DecisionPaperState.PREPARING


class TakenDecision(AppendOnlyRecord):
    """A decision the manager took on a paper. Recorded before it takes effect, and never
    changed or removed (BR-7): a later decision is a new one."""

    kind: ClassVar[str] = "taken_decision"

    paper_id: UUID
    version: int = Field(ge=1)
    option: int | None = Field(default=None, ge=1, le=MAX_OPTIONS)
    """The option picked, counted from 1; ``None`` when the manager wrote their own."""
    text: str = Field(min_length=1, max_length=DECISION_PAPER_LIMIT)
    decided_by: FlowId


# --- Learning (part 2, Epic 3) ----------------------------------------------------------

SIGNAL_TEXT_LIMIT = 6000
LESSON_LIMIT = 500
AREA_LIMIT = 50
MAX_TRACK_RECORD = 20


class SignalKind(StrEnum):
    DRAFT_EDITED = "draft_edited"
    DRAFT_APPROVED = "draft_approved"
    DRAFT_DECLINED = "draft_declined"
    BRIEFING_SENT_BACK = "briefing_sent_back"
    PAPER_SENT_BACK = "paper_sent_back"
    NOTES_CORRECTED = "notes_corrected"
    STATEMENT_CORRECTED = "statement_corrected"
    ITEM_ON_TIME = "item_on_time"
    ITEM_LATE = "item_late"


class Signal(MutableRecord):
    """Something that happened that the assistant can learn from. It may hold the before
    and after wording, so it is its manager's only and goes at hand-over or end; its change
    record and logs hold ids and kinds only. It never holds transcript text."""

    kind: ClassVar[str] = "signal"

    signal_kind: SignalKind
    subject_kind: str = Field(max_length=50)
    subject_id: str | None = Field(default=None, max_length=200)
    action_kind: ActionKind | None = None
    """For a draft: the kind of action it was for."""
    before: str | None = Field(default=None, max_length=SIGNAL_TEXT_LIMIT)
    after: str | None = Field(default=None, max_length=SIGNAL_TEXT_LIMIT)
    said: str | None = Field(default=None, max_length=SIGNAL_TEXT_LIMIT)
    """The manager's own comment or reason, e.g. when a deliverable is sent back."""


class LessonState(StrEnum):
    PROPOSED = "proposed"
    KEPT = "kept"


LessonOrigin = Literal["assistant", "your_words", "typed", "form"]
"""Proposed by the assistant from signals; stated from the manager's own message; typed by
the manager on the page; or an answer of the preferences form."""


class Lesson(MutableRecord):
    """One entry of the learning record. Only kept lessons reach an input pack."""

    kind: ClassVar[str] = "lesson"

    area: str = Field(min_length=1, max_length=AREA_LIMIT)
    text: str = Field(min_length=1, max_length=LESSON_LIMIT)
    origin: LessonOrigin
    signal_ids: list[UUID] = Field(default_factory=list, max_length=50)
    """The signals it rests on, when the assistant proposed it from signals."""
    signal_kinds: list[SignalKind] = Field(default_factory=list, max_length=50)
    message_id: UUID | None = None
    """The manager's message it quotes, word for word, when it is stated."""
    form_key: str | None = Field(default=None, max_length=50)
    """The answer of the preferences form it comes from."""
    edited: bool = False
    """The manager edited it: it is kept in their wording."""
    kept_at: UtcDateTime | None = None
    state: LessonState = LessonState.PROPOSED


class RemovedLesson(MutableRecord):
    """That a lesson was removed: a one-way digest of its words (ignoring capitals and
    spacing), so the same words are not proposed again. The words themselves are gone."""

    kind: ClassVar[str] = "removed_lesson"

    digest: str = Field(min_length=64, max_length=64)


class SuggestionState(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"
    DECLINED = "declined"


class ApprovalSuggestion(MutableRecord):
    """The assistant's suggestion of a wider approval level, with the track record the
    module counted itself. Only the manager's acceptance changes a level."""

    kind: ClassVar[str] = "approval_suggestion"

    action_kind: ActionKind
    level_before: ApprovalLevel
    proposed_level: ApprovalLevel
    considered: int = Field(ge=0, le=MAX_TRACK_RECORD)
    approved_unchanged: int = Field(ge=0, le=MAX_TRACK_RECORD)
    request_ids: list[UUID] = Field(default_factory=list, max_length=MAX_TRACK_RECORD)
    """The requests the track record was counted from, newest last."""
    decided_by: FlowId | None = None
    decided_at: UtcDateTime | None = None
    state: SuggestionState = SuggestionState.OPEN


def append_only_kinds() -> frozenset[str]:
    """Kinds that are never changed or removed once stored."""
    return frozenset(kind for kind, cls in RECORD_TYPES.items() if cls.append_only)
