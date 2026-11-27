"""A minimal spy boundary for tests that must not depend on the stand-ins."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from assistant.boundary.interfaces import Boundary
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

FIXED_NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)


class Spy:
    """Records every call; raises ``raise_with`` when it is set."""

    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.raise_with: Exception | None = None

    def _hit(self, name: str) -> None:
        self.calls.append(name)
        if self.raise_with is not None:
            raise self.raise_with


class SpyPeople(Spy):
    async def person_asking(self, headers: Mapping[str, str]) -> Person | None:
        self._hit("people.person_asking")
        return None

    async def person(self, person_id: str) -> Person | None:
        self._hit("people.person")
        return None

    async def find_by_name(self, organisation_id: str, name: str) -> list[Person]:
        self._hit("people.find_by_name")
        return []

    async def is_administrator(self, person_id: str) -> bool:
        self._hit("people.is_administrator")
        return False


class SpyWorkData(Spy):
    async def projects(self, manager_id: str) -> list[ProjectRecord]:
        self._hit("work_data.projects")
        return []

    async def assignments(self, manager_id: str) -> list[AssignmentRecord]:
        self._hit("work_data.assignments")
        return []


class SpyAssistant(Spy):
    async def start_work(self, order: WorkOrder) -> StartResult:
        self._hit("assistant.start_work")
        return StartResult(accepted=True)


class SpyApproval(Spy):
    async def check(self, request: ApprovalRequest) -> CheckOutcome:
        self._hit("approval.check")
        return CheckOutcome(outcome="ask_manager")

    async def carry_out(self, request: ApprovalRequest) -> CarryOutResult:
        self._hit("approval.carry_out")
        return CarryOutResult(done=True, effect=request.effect)

    async def undo(self, request: ApprovalRequest) -> UndoResult:
        self._hit("approval.undo")
        return UndoResult(done=True)


class SpyNotices(Spy):
    async def notify(self, notice: Notice) -> NoticeResult:
        self._hit("notices.notify")
        return NoticeResult(delivered=True)


class SpyRecords(Spy):
    def __init__(self, calls: list[str]) -> None:
        super().__init__(calls)
        self.written: list[AuditRecord] = []

    async def write(self, record: AuditRecord) -> RecordResult:
        self._hit("records.write")
        self.written.append(record)
        return RecordResult(written=True)


class SpyClock(Spy):
    def __init__(self, calls: list[str]) -> None:
        super().__init__(calls)
        self.value: Any = FIXED_NOW

    async def now(self) -> datetime:
        self._hit("clock.now")
        return self.value  # type: ignore[no-any-return]


def spy_boundary() -> tuple[Boundary, list[str]]:
    calls: list[str] = []
    boundary = Boundary(
        people=SpyPeople(calls),
        work_data=SpyWorkData(calls),
        assistant=SpyAssistant(calls),
        approval=SpyApproval(calls),
        notices=SpyNotices(calls),
        records=SpyRecords(calls),
        clock=SpyClock(calls),
    )
    return boundary, calls
