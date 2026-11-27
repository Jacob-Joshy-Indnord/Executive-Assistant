"""Async interfaces for each part of the Flow boundary.

Flow, or the stand-ins, implement these. The module receives one
:class:`Boundary` bundle when it is created and reaches nothing outside itself
any other way.

Any operation may raise. The module treats every exception as "Flow could not
be reached": the person sees one plain sentence and the surrounding
transaction is rolled back (see :mod:`assistant.service.guard`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from assistant.models.boundary import (
    ApprovalRequest,
    AssignmentRecord,
    AuditRecord,
    CarryOutResult,
    CheckOutcome,
    JoinRequest,
    JoinResult,
    KnowledgeNote,
    KnowledgeResult,
    LeaveResult,
    MeetingInfo,
    MeetingTranscript,
    Notice,
    NoticeResult,
    Person,
    ProjectRecord,
    RecordResult,
    StartResult,
    UndoResult,
    WorkOrder,
)


@runtime_checkable
class People(Protocol):
    async def person_asking(self, headers: Mapping[str, str]) -> Person | None:
        """The person making this API call, named by Flow from the request."""
        ...

    async def person(self, person_id: str) -> Person | None:
        """A person or virtual employee by id."""
        ...

    async def find_by_name(self, organisation_id: str, name: str) -> list[Person]:
        """People and virtual employees whose display name equals ``name``, ignoring case."""
        ...

    async def is_administrator(self, person_id: str) -> bool:
        """Whether the person is an administrator of their organisation."""
        ...


@runtime_checkable
class WorkData(Protocol):
    async def projects(self, manager_id: str) -> list[ProjectRecord]:
        """The projects the manager can see, with owner, budget and spend."""
        ...

    async def assignments(self, manager_id: str) -> list[AssignmentRecord]:
        """The assignments of the manager's virtual employees."""
        ...


@runtime_checkable
class Assistant(Protocol):
    async def start_work(self, order: WorkOrder) -> StartResult:
        """Start a piece of work. The result comes back through "Finish a piece of work"."""
        ...


@runtime_checkable
class Approval(Protocol):
    async def check(self, request: ApprovalRequest) -> CheckOutcome:
        """Carry out, ask the manager, or declined with a reason. Flow's answer is final."""
        ...

    async def carry_out(self, request: ApprovalRequest) -> CarryOutResult:
        """Carry out an allowed request.

        Idempotent per ``request_id``: once a request has been carried out, asking again
        carries out nothing more and returns the same result. The module relies on this to
        try again safely when its own transaction is lost after Flow has answered.
        """
        ...

    async def undo(self, request: ApprovalRequest) -> UndoResult:
        """Undo a request that was carried out. Idempotent per ``request_id``: undoing it
        again undoes nothing more and answers done."""
        ...


@runtime_checkable
class Notices(Protocol):
    async def notify(self, notice: Notice) -> NoticeResult:
        """Tell a person that something waits for them."""
        ...


@runtime_checkable
class Records(Protocol):
    async def write(self, record: AuditRecord) -> RecordResult:
        """Write one audit record."""
        ...


@runtime_checkable
class Meetings(Protocol):
    """The meeting service, reached only through Flow. The module asks; it never connects
    to a meeting, records sound or joins anything itself."""

    async def upcoming(self, manager_id: str) -> list[MeetingInfo]:
        """The manager's meetings that have not ended. A cancelled meeting is not listed."""
        ...

    async def join(self, request: JoinRequest) -> JoinResult:
        """Let the assistant in as a silent note-taker under ``request.display_name``.

        Asking again for the same meeting and assistant joins nothing more and answers how
        it stands now: joined, still waiting to be let in, or refused.
        """
        ...

    async def leave(self, meeting_id: str, assistant_flow_id: str) -> LeaveResult:
        """Take the assistant out of the meeting. Idempotent: leaving again is harmless."""
        ...

    async def transcript(
        self, meeting_id: str, assistant_flow_id: str
    ) -> MeetingTranscript | None:
        """The transcript of a meeting the assistant attended, or ``None`` while there is none."""
        ...


@runtime_checkable
class Knowledge(Protocol):
    """The organisation's knowledge store, told only of notes the manager chose to share."""

    async def add(self, note: KnowledgeNote) -> KnowledgeResult:
        """Add or replace a shared note. Idempotent per ``note.note_id``."""
        ...

    async def remove(self, organisation_id: str, note_id: str) -> KnowledgeResult:
        """Take a note back. Idempotent: removing a note that is not there answers done."""
        ...


@runtime_checkable
class Clock(Protocol):
    async def now(self) -> datetime:
        """The time now, in UTC."""
        ...


@dataclass(frozen=True)
class Boundary:
    """The one bundle passed in when the module is created."""

    people: People
    work_data: WorkData
    assistant: Assistant
    approval: Approval
    notices: Notices
    records: Records
    clock: Clock
    meetings: Meetings
    knowledge: Knowledge

    def __post_init__(self) -> None:
        expected: dict[str, type] = {
            "people": People,
            "work_data": WorkData,
            "assistant": Assistant,
            "approval": Approval,
            "notices": Notices,
            "records": Records,
            "clock": Clock,
            "meetings": Meetings,
            "knowledge": Knowledge,
        }
        for field_name, protocol in expected.items():
            if not isinstance(getattr(self, field_name), protocol):
                raise TypeError(
                    f"boundary part {field_name!r} does not implement {protocol.__name__}"
                )
