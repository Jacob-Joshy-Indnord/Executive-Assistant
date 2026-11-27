"""The one storage interface every record goes through.

Every read and write happens inside a transaction opened for one
organisation. A transaction keeps all of its changes or none of them: any
exception raised inside it rolls it back.

Optimistic versioning: :meth:`Transaction.change` and :meth:`Transaction.remove`
take the record as it was read. If its stored version has moved on, they raise
:class:`~assistant.errors.AlreadyChanged` ("This has already changed.") and
nothing is overwritten.

Append-only records (confirmed statements and reports) can be added and read,
but ``change`` and ``remove`` accept only :class:`MutableRecord`, so the
interface offers no way to change or remove them.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, TypeVar
from uuid import UUID

from assistant.models.records import MutableRecord, Record

R = TypeVar("R", bound=Record)
M = TypeVar("M", bound=MutableRecord)


class Transaction(Protocol):
    @property
    def organisation_id(self) -> str:
        """The one organisation this transaction is scoped to."""
        ...

    async def add(self, record: R) -> R:
        """Store a new record of this organisation. Returns it as stored (version 1).

        Adding an id that already exists raises ``AlreadyChanged``.
        """
        ...

    async def get(self, record_type: type[R], record_id: UUID) -> R | None:
        """The record of this organisation with this id, or ``None``."""
        ...

    async def find(self, record_type: type[R], **equals: Any) -> list[R]:
        """Records of this organisation whose fields equal ``equals``, oldest first."""
        ...

    async def change(self, record: M) -> M:
        """Store ``record`` over the version it was read at. Returns it with the next version."""
        ...

    async def remove(self, record: MutableRecord) -> None:
        """Remove ``record`` if its stored version is still the one it was read at."""
        ...


class Store(Protocol):
    def transaction(self, organisation_id: str) -> AbstractAsyncContextManager[Transaction]:
        """Open a transaction for one organisation."""
        ...

    async def close(self) -> None: ...


def refuse_append_only(record: Record) -> None:
    """Shared guard: stores call this before any change or removal."""
    if not isinstance(record, MutableRecord) or record.append_only:
        raise TypeError(f"{type(record).__name__} records cannot be changed or removed")


def check_scope(organisation_id: str, record: Record) -> None:
    """Shared guard: a record may only be written in its own organisation's transaction."""
    if record.organisation_id != organisation_id:
        raise ValueError("record belongs to another organisation")


def matches(record: Record, equals: dict[str, Any]) -> bool:
    """Shared filter for ``find``: every named field equals its value."""
    for name, value in equals.items():
        if name not in type(record).model_fields:
            raise ValueError(f"{type(record).__name__} has no field {name!r}")
        if getattr(record, name) != value:
            return False
    return True
