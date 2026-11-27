"""The demonstration store: SQLite from the standard library.

SQL appears only in this file. Records are kept as validated JSON in one
table, with the columns the store needs for scoping and version checks.
Transactions are serialised with a lock, which is ample for a demonstration
and keeps every transaction all-or-nothing. Triggers refuse any UPDATE or
DELETE of append-only records, as a second guard behind the interface.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, TypeVar
from uuid import UUID

from assistant.errors import AlreadyChanged
from assistant.models.records import MutableRecord, Record, append_only_kinds
from assistant.store.interface import Transaction, check_scope, matches, refuse_append_only

_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

R = TypeVar("R", bound=Record)
M = TypeVar("M", bound=MutableRecord)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    organisation_id TEXT NOT NULL,
    assistant_id TEXT,
    created_at TEXT NOT NULL,
    version INTEGER NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS records_scope ON records (organisation_id, kind, assistant_id);
"""


def _append_only_triggers() -> str:
    kinds = sorted(append_only_kinds())
    for kind in kinds:
        if not kind.replace("_", "").isalnum():
            raise ValueError(f"unexpected record kind {kind!r}")
    listed = ", ".join(f"'{kind}'" for kind in kinds) or "''"
    return f"""
DROP TRIGGER IF EXISTS append_only_no_update;
DROP TRIGGER IF EXISTS append_only_no_delete;
CREATE TRIGGER append_only_no_update BEFORE UPDATE ON records
    WHEN OLD.kind IN ({listed})
    BEGIN SELECT RAISE(ABORT, 'append-only record'); END;
CREATE TRIGGER append_only_no_delete BEFORE DELETE ON records
    WHEN OLD.kind IN ({listed})
    BEGIN SELECT RAISE(ABORT, 'append-only record'); END;
"""


class SqliteStore:
    """Demonstration store. ``path`` is a file, or ``":memory:"`` for tests."""

    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._conn.executescript(_SCHEMA + _append_only_triggers())
        self._lock = asyncio.Lock()
        self._holder: asyncio.Task[Any] | None = None

    @asynccontextmanager
    async def transaction(self, organisation_id: str) -> AsyncIterator[Transaction]:
        if not organisation_id:
            raise ValueError("a transaction needs an organisation")
        if self._holder is not None and self._holder is asyncio.current_task():
            raise RuntimeError("transactions cannot be nested")
        async with self._lock:
            self._holder = asyncio.current_task()
            self._conn.execute("BEGIN IMMEDIATE")
            tx = _SqliteTransaction(self._conn, organisation_id)
            try:
                yield tx
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")
            finally:
                tx.closed = True
                self._holder = None

    async def close(self) -> None:
        self._conn.close()


class _SqliteTransaction:
    def __init__(self, conn: sqlite3.Connection, organisation_id: str) -> None:
        self._conn = conn
        self._organisation_id = organisation_id
        self.closed = False

    @property
    def organisation_id(self) -> str:
        return self._organisation_id

    def _open(self) -> sqlite3.Connection:
        if self.closed:
            raise RuntimeError("this transaction has ended")
        return self._conn

    async def add(self, record: R) -> R:
        check_scope(self._organisation_id, record)
        stored = record.model_validate({**record.model_dump(), "version": 1})
        try:
            self._open().execute(
                "INSERT INTO records (id, kind, organisation_id, assistant_id, created_at,"
                " version, data) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(stored.id),
                    stored.kind,
                    stored.organisation_id,
                    str(stored.assistant_id) if stored.assistant_id else None,
                    stored.created_at.strftime(_TIME_FORMAT),
                    stored.version,
                    stored.model_dump_json(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise AlreadyChanged() from exc
        return stored

    async def get(self, record_type: type[R], record_id: UUID) -> R | None:
        row = (
            self._open()
            .execute(
                "SELECT data FROM records WHERE id = ? AND kind = ? AND organisation_id = ?",
                (str(record_id), record_type.kind, self._organisation_id),
            )
            .fetchone()
        )
        return record_type.model_validate_json(row[0]) if row else None

    async def find(self, record_type: type[R], **equals: Any) -> list[R]:
        sql = "SELECT data FROM records WHERE kind = ? AND organisation_id = ?"
        params: list[Any] = [record_type.kind, self._organisation_id]
        if "assistant_id" in equals:
            assistant_id = equals["assistant_id"]
            if assistant_id is None:
                sql += " AND assistant_id IS NULL"
            else:
                sql += " AND assistant_id = ?"
                params.append(str(assistant_id))
        sql += " ORDER BY created_at, seq"
        rows = self._open().execute(sql, params).fetchall()
        records = [record_type.model_validate_json(row[0]) for row in rows]
        return [r for r in records if matches(r, equals)]

    async def change(self, record: M) -> M:
        refuse_append_only(record)
        check_scope(self._organisation_id, record)
        changed = record.model_validate({**record.model_dump(), "version": record.version + 1})
        cursor = self._open().execute(
            "UPDATE records SET data = ?, version = ?, assistant_id = ?"
            " WHERE id = ? AND kind = ? AND organisation_id = ? AND version = ?",
            (
                changed.model_dump_json(),
                changed.version,
                str(changed.assistant_id) if changed.assistant_id else None,
                str(record.id),
                record.kind,
                self._organisation_id,
                record.version,
            ),
        )
        if cursor.rowcount != 1:
            raise AlreadyChanged()
        return changed

    async def remove(self, record: MutableRecord) -> None:
        refuse_append_only(record)
        check_scope(self._organisation_id, record)
        cursor = self._open().execute(
            "DELETE FROM records WHERE id = ? AND kind = ? AND organisation_id = ? AND version = ?",
            (str(record.id), record.kind, self._organisation_id, record.version),
        )
        if cursor.rowcount != 1:
            raise AlreadyChanged()
