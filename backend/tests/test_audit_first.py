"""Story 1.3: audit records before changes (BR-9, BR-10) and ids-only logs."""

from __future__ import annotations

import ast
import json
import unittest
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from assistant.errors import NotSaved
from assistant.models.boundary import AuditRecord, RecordResult
from assistant.models.records import ActionItem, ItemState
from assistant.service.actions import Action, perform
from assistant.service.audit import (
    InvalidAuditRecord,
    audit_payload,
    is_token,
    record_then_change,
)
from assistant.service.guard import guard
from assistant.service.log import log_event
from assistant.store.sqlite import SqliteStore
from standins import build_standins
from standins.sample import LEGAL_LEAD, OPERATIONS_DIRECTOR, ORGANISATION_ID

MODULE_ROOT = Path(__file__).resolve().parents[1] / "assistant"
MANAGER = OPERATIONS_DIRECTOR.id
PROSE = "Please finish the review of the vendor contract before Friday"


class AuditFirstTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.standins = build_standins()
        self.boundary = guard(self.standins.boundary)
        self.store = SqliteStore()
        self.now = await self.standins.clock.now()

    async def asyncTearDown(self) -> None:
        await self.store.close()

    def _item(self, **changes: Any) -> ActionItem:
        base: dict[str, Any] = {
            "organisation_id": ORGANISATION_ID,
            "created_at": self.now,
            "text": PROSE,
            "owner_id": LEGAL_LEAD.id,
            "due_date": date(2026, 11, 27),
            "origin": "manager",
        }
        return ActionItem(**{**base, **changes})

    async def _perform(self, work: Any) -> Any:
        return await perform(self.boundary, self.store, ORGANISATION_ID, MANAGER, work)

    async def _items(self) -> list[ActionItem]:
        async with self.store.transaction(ORGANISATION_ID) as tx:
            return await tx.find(ActionItem)

    async def test_record_is_written_before_the_change(self) -> None:
        """Story 1.3 - record is written before the change."""
        seen_at_write: list[ActionItem | None] = []
        records = self.standins.records
        original_write = records.write
        action_box: list[Action] = []

        async def spying_write(record: AuditRecord) -> RecordResult:
            seen_at_write.append(await action_box[0].tx.get(ActionItem, item.id))
            return await original_write(record)

        records.write = spying_write  # type: ignore[method-assign]
        item = self._item()

        async def work(action: Action) -> None:
            action_box.append(action)
            added = await action.add("assistant.item_added", item)
            await action.change(
                "assistant.item_accepted", added, added.evolve(state=ItemState.OPEN)
            )

        await self._perform(work)
        # At the first write the item did not exist; at the second it was still waiting.
        self.assertIsNone(seen_at_write[0])
        assert seen_at_write[1] is not None
        self.assertEqual(seen_at_write[1].state, ItemState.WAITING_FOR_OWNER)
        self.assertEqual(
            [(r.name, r.state_before, r.state_after) for r in records.written],
            [
                ("assistant.item_added", "none", "waiting_for_owner"),
                ("assistant.item_accepted", "waiting_for_owner", "open"),
            ],
        )
        self.assertEqual((await self._items())[0].state, ItemState.OPEN)

    async def test_record_then_change_calls_change_only_after_writing(self) -> None:
        order: list[str] = []
        records = self.standins.records

        class OrderedRecords:
            async def write(self, record: AuditRecord) -> RecordResult:
                order.append("record")
                return await records.write(record)

        async def change() -> str:
            order.append("change")
            return "changed"

        record = AuditRecord(
            name="assistant.item_added",
            organisation_id=ORGANISATION_ID,
            who=MANAGER,
            item_kind="action_item",
            item_id="i",
            state_before="none",
            state_after="open",
            at=self.now,
        )
        self.assertEqual(await record_then_change(OrderedRecords(), record, change), "changed")
        self.assertEqual(order, ["record", "change"])

    async def test_failed_record_blocks_the_change(self) -> None:
        """Story 1.3 - failed record blocks the change."""
        kept = self._item(text="Kept")

        async def add_first(action: Action) -> None:
            await action.add("assistant.item_added", kept)

        await self._perform(add_first)
        self.standins.records.failing = True

        async def work(action: Action) -> None:
            current = await action.get(ActionItem, kept.id)
            assert current is not None
            await action.change(
                "assistant.item_extended",
                current,
                current.evolve(due_date=current.due_date + timedelta(days=7)),
            )

        with self.assertRaises(NotSaved) as caught:
            await self._perform(work)
        self.assertEqual(caught.exception.message, "Not saved. Try again.")
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(await self._items(), [kept.model_copy()])

    async def test_failed_record_rolls_back_earlier_changes_of_the_action(self) -> None:
        calls = {"n": 0}
        records = self.standins.records
        original_write = records.write

        async def second_write_fails(record: AuditRecord) -> RecordResult:
            calls["n"] += 1
            if calls["n"] == 2:
                return RecordResult(written=False, reason="The audit trail is not available.")
            return await original_write(record)

        records.write = second_write_fails  # type: ignore[method-assign]

        async def work(action: Action) -> None:
            await action.add("assistant.item_added", self._item(text="One"))
            await action.add("assistant.item_added", self._item(text="Two"))

        with self.assertRaises(NotSaved):
            await self._perform(work)
        self.assertEqual(await self._items(), [])

    async def test_unreachable_records_is_not_saved_too(self) -> None:
        async def broken(record: AuditRecord) -> RecordResult:
            raise ConnectionResetError("peer reset")

        self.standins.records.write = broken  # type: ignore[method-assign]

        async def work(action: Action) -> None:
            await action.add("assistant.item_added", self._item())

        with self.assertRaises(NotSaved) as caught:
            await self._perform(work)
        self.assertEqual(caught.exception.message, "Not saved. Try again.")
        self.assertEqual(await self._items(), [])

    async def test_remove_is_recorded_first(self) -> None:
        item = self._item()

        async def add(action: Action) -> ActionItem:
            return await action.add("assistant.item_added", item)

        added = await self._perform(add)

        async def remove(action: Action) -> None:
            await action.remove("assistant.item_removed", added)

        await self._perform(remove)
        last = self.standins.records.written[-1]
        self.assertEqual((last.name, last.state_after), ("assistant.item_removed", "removed"))
        self.assertEqual(await self._items(), [])

    async def test_records_written_by_actions_follow_br10(self) -> None:
        """Story 1.3 - records follow BR-10 and naming, even for an item full of prose."""

        async def work(action: Action) -> None:
            await action.add("assistant.item_added", self._item())

        await self._perform(work)
        record = self.standins.records.written[0]
        payload = json.dumps(audit_payload(record))
        self.assertNotIn(PROSE, payload)
        self.assertNotIn("review", payload)
        for key in ("who", "item_kind", "item_id", "state_before", "state_after", "at"):
            self.assertIn(key, audit_payload(record))

    async def test_an_action_cannot_change_from_a_record_it_did_not_read(self) -> None:
        async def work(action: Action) -> None:
            item = await action.add("assistant.item_added", self._item())
            await action.change("assistant.item_accepted", item, self._item())

        with self.assertRaises(ValueError):
            await self._perform(work)


class RecordRulesTest(unittest.TestCase):
    """Story 1.3 - records follow BR-10 and naming."""

    def _record(self, **changes: Any) -> AuditRecord:
        from tests.fakes import FIXED_NOW

        base: dict[str, Any] = {
            "name": "assistant.statement_confirmed",
            "organisation_id": "org",
            "assistant_id": "6f1c7a7e-0d1b-4b6e-9a43-1a2b3c4d5e6f",
            "who": "legal-lead",
            "item_kind": "confirmed_statement",
            "item_id": "s-1",
            "state_before": "with_owner",
            "state_after": "confirmed",
            "at": FIXED_NOW,
        }
        return AuditRecord(**{**base, **changes})

    def test_valid_record(self) -> None:
        payload = audit_payload(
            self._record(details={"version": 2, "corrected": False, "points": ["reason"]})
        )
        self.assertEqual(payload["name"], "assistant.statement_confirmed")
        self.assertEqual(payload["at"], "2026-11-23T09:00:00Z")

    def test_names_are_assistant_and_lower_snake_case(self) -> None:
        for good in ("assistant.statement_confirmed", "assistant.item_added", "assistant.paused"):
            audit_payload(self._record(name=good))
        for bad in (
            "statement_confirmed",
            "assistant.StatementConfirmed",
            "assistant.statement-confirmed",
            "assistant.statement confirmed",
            "assistant._x",
            "assistant.x__y",
            "assistant.",
            "flow.statement_confirmed",
            "assistant.item.added",
        ):
            with self.subTest(bad), self.assertRaises(InvalidAuditRecord):
                audit_payload(self._record(name=bad))

    def test_only_text_whole_numbers_booleans_lists_and_objects(self) -> None:
        for good in ({"n": 3}, {"ok": True}, {"ids": ["a", "b"]}, {"nested": {"count": 1}}):
            audit_payload(self._record(details=good))
        for bad in ({"amount": 1.5}, {"missing": None}, {"raw": b"x"}, {"items": [1.0]}):
            with self.subTest(bad), self.assertRaises(InvalidAuditRecord):
                audit_payload(self._record(details=bad))

    def test_never_message_statement_note_or_draft_text(self) -> None:
        for key in (
            "message",
            "statement",
            "note",
            "draft",
            "text",
            "edited_text",
            "update_note",
            "reason",
        ):
            with self.subTest(key), self.assertRaises(InvalidAuditRecord):
                audit_payload(self._record(details={key: "short"}))
        # a flag about text is fine; the text itself is not
        audit_payload(self._record(details={"note_given": True, "has_note": True}))
        with self.assertRaises(InvalidAuditRecord):
            audit_payload(self._record(details={"anything": PROSE}))
        with self.assertRaises(InvalidAuditRecord):
            audit_payload(self._record(state_after="on track"))

    def test_who_what_which_item_states_and_time_are_required(self) -> None:
        for key in ("who", "item_kind", "item_id", "state_before", "state_after"):
            with self.subTest(key), self.assertRaises((InvalidAuditRecord, ValidationError)):
                audit_payload(self._record(**{key: ""}))

    def test_tokens(self) -> None:
        self.assertTrue(is_token("waiting_for_owner"))
        self.assertTrue(is_token("2026-11-23T09:00:00Z"))
        self.assertFalse(is_token("two words"))
        self.assertFalse(is_token("x" * 201))


class LogsHoldIdsAndStatesOnlyTest(unittest.IsolatedAsyncioTestCase):
    """Story 1.3 - logs hold ids and states only."""

    async def test_actions_log_ids_and_states_only(self) -> None:
        standins = build_standins()
        store = SqliteStore()
        now = await standins.clock.now()
        item = ActionItem(
            organisation_id=ORGANISATION_ID,
            created_at=now,
            text=PROSE,
            owner_id=LEGAL_LEAD.id,
            due_date=date(2026, 11, 27),
            origin="manager",
        )

        async def work(action: Action) -> None:
            await action.add("assistant.item_added", item)

        with self.assertLogs("assistant", level="INFO") as logs:
            await perform(guard(standins.boundary), store, ORGANISATION_ID, MANAGER, work)
            standins.records.failing = True
            with self.assertRaises(NotSaved):
                await perform(guard(standins.boundary), store, ORGANISATION_ID, MANAGER, work)
        await store.close()
        joined = "\n".join(logs.output)
        self.assertNotIn("review", joined)
        self.assertIn(str(item.id), joined)
        for line in logs.output:
            fields = json.loads(line.split(" ", 1)[1])
            for value in fields.values():
                self.assertTrue(isinstance(value, int) or is_token(value), value)

    def test_log_event_refuses_text(self) -> None:
        with self.assertRaises(InvalidAuditRecord):
            log_event("assistant.item_added", note=PROSE)
        with self.assertRaises(InvalidAuditRecord):
            log_event("assistant.item_added", item_id="a b")
        with self.assertRaises(ValueError):
            log_event("Item added")

    def test_module_logs_and_writes_records_only_through_one_place(self) -> None:
        offenders: list[str] = []
        for path in MODULE_ROOT.rglob("*.py"):
            relative = path.relative_to(MODULE_ROOT).as_posix()
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id == "print":
                    offenders.append(f"{relative}: print")
                if isinstance(node, ast.Import | ast.ImportFrom):
                    names = (
                        [a.name for a in node.names]
                        if isinstance(node, ast.Import)
                        else [node.module or ""]
                    )
                    if "logging" in names and relative != "service/log.py":
                        offenders.append(f"{relative}: imports logging")
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr == "write"
                    and isinstance(node.value, ast.Attribute)
                    and node.value.attr == "records"
                    and relative != "service/audit.py"
                ):
                    offenders.append(f"{relative}: writes a record directly")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
