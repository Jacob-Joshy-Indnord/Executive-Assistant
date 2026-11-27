"""One person's action: a store transaction in which every write is audited first.

Services change records only through :class:`Action`, so every change has its
audit record written before it (BR-9). Anything that fails inside
:func:`perform` (a boundary failure, a record that is not written, a version
conflict) rolls back the whole action.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypeVar
from uuid import UUID

from assistant.boundary.interfaces import Boundary
from assistant.errors import ModuleError
from assistant.models.boundary import AuditRecord
from assistant.models.records import MutableRecord, Record
from assistant.service.audit import record_then_change
from assistant.service.log import log_event
from assistant.store.interface import Store, Transaction

R = TypeVar("R", bound=Record)
M = TypeVar("M", bound=MutableRecord)
T = TypeVar("T")

ABSENT = "none"
PRESENT = "present"
REMOVED = "removed"


def state_of(record: Record) -> str:
    """The state recorded for an item: its ``state`` field, or that it exists."""
    state = getattr(record, "state", None)
    return str(state) if state is not None else PRESENT


@dataclass
class Action:
    """Reads freely; writes only through :meth:`add`, :meth:`change` and :meth:`remove`."""

    tx: Transaction
    who: str
    now: datetime
    boundary: Boundary
    assistant_id: UUID | None = None

    async def get(self, record_type: type[R], record_id: UUID) -> R | None:
        return await self.tx.get(record_type, record_id)

    async def find(self, record_type: type[R], **equals: Any) -> list[R]:
        return await self.tx.find(record_type, **equals)

    def _record(
        self, name: str, item: Record, before: str, after: str, details: dict[str, Any] | None
    ) -> AuditRecord:
        assistant_id = item.assistant_id or self.assistant_id
        return AuditRecord(
            name=name,
            organisation_id=self.tx.organisation_id,
            assistant_id=str(assistant_id) if assistant_id else None,
            who=self.who,
            item_kind=item.kind,
            item_id=str(item.id),
            state_before=before,
            state_after=after,
            at=self.now,
            details=details or {},
        )

    async def _audited(
        self,
        name: str,
        item: Record,
        before: str,
        after: str,
        details: dict[str, Any] | None,
        change: Callable[[], Awaitable[T]],
    ) -> T:
        record = self._record(name, item, before, after, details)
        result = await record_then_change(self.boundary.records, record, change)
        log_event(
            "assistant.change_recorded",
            record=name,
            item_kind=item.kind,
            item_id=str(item.id),
            state_before=before,
            state_after=after,
        )
        return result

    async def add(self, name: str, record: R, details: dict[str, Any] | None = None) -> R:
        return await self._audited(
            name, record, ABSENT, state_of(record), details, lambda: self.tx.add(record)
        )

    async def change(
        self, name: str, before: M, after: M, details: dict[str, Any] | None = None
    ) -> M:
        if after.id != before.id or after.version != before.version:
            raise ValueError("change a record from the version that was read")
        return await self._audited(
            name, after, state_of(before), state_of(after), details, lambda: self.tx.change(after)
        )

    async def remove(
        self, name: str, record: MutableRecord, details: dict[str, Any] | None = None
    ) -> None:
        await self._audited(
            name, record, state_of(record), REMOVED, details, lambda: self.tx.remove(record)
        )


async def perform(
    boundary: Boundary,
    store: Store,
    organisation_id: str,
    who: str,
    work: Callable[[Action], Awaitable[T]],
    assistant_id: UUID | None = None,
) -> T:
    """Run ``work`` as one all-or-nothing action of ``who`` in one organisation."""
    now = await boundary.clock.now()
    try:
        async with store.transaction(organisation_id) as tx:
            result = await work(Action(tx, who, now, boundary, assistant_id))
    except ModuleError as exc:
        log_event("assistant.action_refused", who=who, code=exc.code, status=exc.status)
        raise
    return result
