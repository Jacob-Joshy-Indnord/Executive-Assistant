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

    def __post_init__(self) -> None:
        expected: dict[str, type] = {
            "people": People,
            "work_data": WorkData,
            "assistant": Assistant,
            "approval": Approval,
            "notices": Notices,
            "records": Records,
            "clock": Clock,
        }
        for field_name, protocol in expected.items():
            if not isinstance(getattr(self, field_name), protocol):
                raise TypeError(
                    f"boundary part {field_name!r} does not implement {protocol.__name__}"
                )
