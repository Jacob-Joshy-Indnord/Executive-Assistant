"""A second, in-memory store used to show the storage contract is store-agnostic."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, TypeVar
from uuid import UUID

from assistant.errors import AlreadyChanged
from assistant.models.records import MutableRecord, Record
from assistant.store.interface import Transaction, check_scope, matches, refuse_append_only

R = TypeVar("R", bound=Record)
M = TypeVar("M", bound=MutableRecord)


class MemoryStore:
    def __init__(self) -> None:
        self._records: dict[UUID, Record] = {}
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self, organisation_id: str) -> AsyncIterator[Transaction]:
        async with self._lock:
            tx = _MemoryTransaction(dict(self._records), organisation_id)
            yield tx
            self._records = tx.working  # only on success

    async def close(self) -> None:
        self._records.clear()


class _MemoryTransaction:
    def __init__(self, working: dict[UUID, Record], organisation_id: str) -> None:
        self.working = working
        self._organisation_id = organisation_id

    @property
    def organisation_id(self) -> str:
        return self._organisation_id

    def _mine(self, record_id: UUID) -> Record | None:
        found = self.working.get(record_id)
        return found if found and found.organisation_id == self._organisation_id else None

    async def add(self, record: R) -> R:
        check_scope(self._organisation_id, record)
        if record.id in self.working:
            raise AlreadyChanged()
        stored = record.model_validate({**record.model_dump(), "version": 1})
        self.working[stored.id] = stored
        return stored

    async def get(self, record_type: type[R], record_id: UUID) -> R | None:
        found = self._mine(record_id)
        return found if isinstance(found, record_type) else None

    async def find(self, record_type: type[R], **equals: Any) -> list[R]:
        found = [
            r
            for r in self.working.values()
            if isinstance(r, record_type) and r.organisation_id == self._organisation_id
        ]
        return sorted((r for r in found if matches(r, equals)), key=lambda r: r.created_at)

    async def change(self, record: M) -> M:
        refuse_append_only(record)
        check_scope(self._organisation_id, record)
        current = self._mine(record.id)
        if current is None or current.version != record.version:
            raise AlreadyChanged()
        changed = record.model_validate({**record.model_dump(), "version": record.version + 1})
        self.working[record.id] = changed
        return changed

    async def remove(self, record: MutableRecord) -> None:
        refuse_append_only(record)
        check_scope(self._organisation_id, record)
        current = self._mine(record.id)
        if current is None or current.version != record.version:
            raise AlreadyChanged()
        del self.working[record.id]
