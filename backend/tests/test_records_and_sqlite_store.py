"""Story 1.2: record shapes of section 10 and what is specific to the SQLite store.

The behavioural scenarios (scoping, rollback, conflict, immutability) are
covered by the reusable storage contract in ``contract/store.py``, which runs
against this store in ``test_store_contract.py``.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from assistant.models.records import (
    RECORD_TYPES,
    ActionItem,
    AppendOnlyRecord,
    AssistantRecord,
    ConfirmedStatement,
    MutableRecord,
    Report,
    StatementWording,
    append_only_kinds,
)
from assistant.store.interface import Store, Transaction
from assistant.store.sqlite import SqliteStore

ORG = "org-a"
NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)


def _item(**changes: object) -> ActionItem:
    base: dict[str, object] = {
        "organisation_id": ORG,
        "created_at": NOW,
        "text": "Finish the review",
        "owner_id": "legal",
        "due_date": date(2026, 11, 27),
        "origin": "manager",
    }
    return ActionItem(**{**base, **changes})  # type: ignore[arg-type]


def _statement() -> ConfirmedStatement:
    return ConfirmedStatement(
        organisation_id=ORG,
        created_at=NOW,
        project_id="p",
        owner_id="legal",
        colour="green",
        wording=StatementWording(reason="On plan."),
        source="Owner",
        author_id="legal",
        mark="confirmed",
    )


class RecordShapesTest(unittest.TestCase):
    """Story 1.2 - the record shapes of section 10."""

    def test_every_record_has_id_organisation_assistant_time_and_version(self) -> None:
        self.assertEqual(
            set(RECORD_TYPES),
            {
                "assistant",
                "preferences",
                "approval_level",
                "organisation_settings",
                "piece_of_work",
                "work_slot",
                "chat_message",
                "request",
                "action_item",
                "update",
                "person_request",
                "manager_notice_mark",
                "followed_project",
                "statement_draft",
                "confirmed_statement",
                "report",
                "attention_entry",
                "due_step_claim",
                "kept_result",
                "organisation_control",
                "meeting",
                "meeting_notes",
                "meeting_attendance",
            },
        )
        for kind, cls in RECORD_TYPES.items():
            with self.subTest(kind):
                for name in ("id", "organisation_id", "assistant_id", "created_at", "version"):
                    self.assertIn(name, cls.model_fields)

    def test_ids_are_uuids_and_times_utc(self) -> None:
        item = _item()
        self.assertEqual(item.id.version, 4)
        self.assertEqual(item.version, 1)
        with self.assertRaises(ValidationError):
            _item(created_at=datetime(2026, 11, 23, 9, 0))

    def test_text_limits(self) -> None:
        _item(text="x" * 300)
        with self.assertRaises(ValidationError):
            _item(text="x" * 301)

    def test_only_confirmed_statements_and_reports_are_append_only(self) -> None:
        self.assertEqual(append_only_kinds(), {"confirmed_statement", "report"})
        self.assertTrue(issubclass(ConfirmedStatement, AppendOnlyRecord))
        self.assertTrue(issubclass(Report, AppendOnlyRecord))
        self.assertFalse(issubclass(ConfirmedStatement, MutableRecord))

    def test_evolve_validates_and_keeps_identity(self) -> None:
        item = _item()
        changed = item.evolve(text="Send the signed copy")
        self.assertEqual((changed.id, changed.version), (item.id, item.version))
        with self.assertRaises(ValidationError):
            item.evolve(text="x" * 301)
        with self.assertRaises(ValueError):
            item.evolve(version=7)

    def test_records_are_frozen(self) -> None:
        with self.assertRaises(ValidationError):
            _item().text = "changed"  # type: ignore[misc]


class SqliteStoreSpecificsTest(unittest.IsolatedAsyncioTestCase):
    """Story 1.2 - the demonstration store."""

    async def asyncSetUp(self) -> None:
        self.store = SqliteStore()

    async def asyncTearDown(self) -> None:
        await self.store.close()

    def test_implements_the_storage_interface(self) -> None:
        store: Store = self.store
        self.assertTrue(callable(store.transaction))

    async def test_sql_refuses_to_update_or_delete_append_only_rows(self) -> None:
        async with self.store.transaction(ORG) as tx:
            await tx.add(_statement())
            await tx.add(Report(organisation_id=ORG, created_at=NOW))
        conn = self.store._conn  # the second guard, behind the interface
        for sql in ("UPDATE records SET data = '{}'", "DELETE FROM records"):
            with self.subTest(sql), self.assertRaises(sqlite3.IntegrityError):
                conn.execute(sql)

    async def test_transactions_cannot_be_nested(self) -> None:
        async with self.store.transaction(ORG):
            with self.assertRaises(RuntimeError):
                async with self.store.transaction(ORG):
                    pass

    async def test_an_ended_transaction_cannot_be_used(self) -> None:
        async with self.store.transaction(ORG) as tx:
            pass
        kept: Transaction = tx
        with self.assertRaises(RuntimeError):
            await kept.add(_item())

    async def test_a_transaction_needs_an_organisation(self) -> None:
        with self.assertRaises(ValueError):
            async with self.store.transaction(""):
                pass

    async def test_find_by_assistant_uses_the_column(self) -> None:
        assistant_id = uuid4()
        async with self.store.transaction(ORG) as tx:
            await tx.add(_item(assistant_id=assistant_id))
            await tx.add(_item())
            self.assertEqual(len(await tx.find(ActionItem, assistant_id=assistant_id)), 1)
            self.assertEqual(len(await tx.find(ActionItem, assistant_id=None)), 1)

    async def test_records_survive_in_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "demo.sqlite3")
            first = SqliteStore(path)
            async with first.transaction(ORG) as tx:
                added = await tx.add(
                    AssistantRecord(
                        organisation_id=ORG,
                        created_at=NOW,
                        manager_id="m",
                        display_name="Executive Assistant",
                        flow_id="f",
                    )
                )
            await first.close()
            second = SqliteStore(path)
            async with second.transaction(ORG) as tx:
                self.assertEqual(await tx.get(AssistantRecord, added.id), added)
            await second.close()


if __name__ == "__main__":
    unittest.main()
