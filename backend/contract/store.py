"""Reusable storage contract, run against any store implementation.

Usage::

    class FlowStoreContractTest(StoreContract, unittest.IsolatedAsyncioTestCase):
        async def make_store(self) -> Store:
            return FlowStore(...)

Covers organisation scoping, all-or-nothing transactions, version conflicts
and the immutability of confirmed statements and reports.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from assistant.errors import AlreadyChanged
from assistant.models.records import (
    ActionItem,
    ConfirmedStatement,
    MutableRecord,
    Record,
    Report,
    ReportLine,
    StatementWording,
)
from assistant.store.interface import Store, Transaction

if TYPE_CHECKING:
    import unittest

    _Base = unittest.IsolatedAsyncioTestCase
else:
    _Base = object

ORG_A = "contract-organisation-a"
ORG_B = "contract-organisation-b"
T0 = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
TRANSACTION_METHODS = {"organisation_id", "add", "get", "find", "change", "remove"}


class _Boom(Exception):
    pass


def item(org: str = ORG_A, **changes: Any) -> ActionItem:
    base: dict[str, Any] = {
        "organisation_id": org,
        "created_at": T0,
        "text": "Finish the review",
        "owner_id": "legal-lead",
        "due_date": date(2026, 11, 27),
        "origin": "manager",
    }
    return ActionItem(**{**base, **changes})


def statement(org: str = ORG_A, **changes: Any) -> ConfirmedStatement:
    base: dict[str, Any] = {
        "organisation_id": org,
        "created_at": T0,
        "project_id": "vendor-consolidation",
        "owner_id": "legal-lead",
        "colour": "amber",
        "wording": StatementWording(reason="Two contracts are still open."),
        "source": "Legal lead",
        "author_id": "legal-lead",
        "mark": "confirmed",
    }
    return ConfirmedStatement(**{**base, **changes})


def report(org: str = ORG_A) -> Report:
    return Report(
        organisation_id=org,
        created_at=T0,
        lines=[
            ReportLine(
                project_id="vendor-consolidation",
                project_name="Vendor consolidation",
                mark="no_confirmed_status",
            )
        ],
    )


class StoreContract(_Base):
    store: Store

    async def make_store(self) -> Store:
        raise NotImplementedError

    async def asyncSetUp(self) -> None:
        self.store = await self.make_store()

    async def asyncTearDown(self) -> None:
        await self.store.close()

    async def _add(self, record: Record, org: str = ORG_A) -> Any:
        async with self.store.transaction(org) as tx:
            return await tx.add(record)

    async def _get(self, record: Record, org: str = ORG_A) -> Any:
        async with self.store.transaction(org) as tx:
            return await tx.get(type(record), record.id)

    # Scoping ----------------------------------------------------------------

    async def test_scope_transaction_is_for_one_organisation(self) -> None:
        async with self.store.transaction(ORG_A) as tx:
            self.assertEqual(tx.organisation_id, ORG_A)

    async def test_scope_another_organisations_record_is_never_returned(self) -> None:
        theirs = await self._add(item(ORG_B), ORG_B)
        async with self.store.transaction(ORG_A) as tx:
            self.assertIsNone(await tx.get(ActionItem, theirs.id))
            self.assertEqual(await tx.find(ActionItem), [])
            self.assertEqual(await tx.find(ActionItem, owner_id="legal-lead"), [])
        self.assertEqual(await self._get(theirs, ORG_B), theirs)

    async def test_scope_cannot_write_another_organisations_record(self) -> None:
        theirs = await self._add(item(ORG_B), ORG_B)
        with self.assertRaises(ValueError):
            async with self.store.transaction(ORG_A) as tx:
                await tx.add(item(ORG_B))
        for attempt in ("change", "remove"):
            with self.subTest(attempt), self.assertRaises((ValueError, AlreadyChanged)):
                async with self.store.transaction(ORG_A) as tx:
                    if attempt == "change":
                        await tx.change(theirs.evolve(text="Taken over"))
                    else:
                        await tx.remove(theirs)
        self.assertEqual(await self._get(theirs, ORG_B), theirs)

    async def test_scope_append_only_records_are_scoped_too(self) -> None:
        theirs = await self._add(statement(ORG_B), ORG_B)
        async with self.store.transaction(ORG_A) as tx:
            self.assertIsNone(await tx.get(ConfirmedStatement, theirs.id))
            self.assertEqual(await tx.find(ConfirmedStatement), [])

    # All or nothing ----------------------------------------------------------

    async def test_rollback_failed_transaction_keeps_nothing(self) -> None:
        kept = await self._add(item(text="Kept"))
        gone = await self._add(item(text="To remove"))
        with self.assertRaises(_Boom):
            async with self.store.transaction(ORG_A) as tx:
                await tx.add(item(text="New"))
                await tx.add(statement())
                await tx.change(kept.evolve(text="Changed"))
                await tx.remove(gone)
                raise _Boom()
        async with self.store.transaction(ORG_A) as tx:
            self.assertEqual(
                sorted(i.text for i in await tx.find(ActionItem)), ["Kept", "To remove"]
            )
            self.assertEqual(await tx.get(ActionItem, kept.id), kept)
            self.assertEqual(await tx.find(ConfirmedStatement), [])

    async def test_rollback_conflict_undoes_earlier_changes_in_the_same_transaction(self) -> None:
        first = await self._add(item(text="First"))
        second = await self._add(item(text="Second"))
        async with self.store.transaction(ORG_A) as tx:
            await tx.change(second.evolve(text="Moved on"))
        with self.assertRaises(AlreadyChanged):
            async with self.store.transaction(ORG_A) as tx:
                await tx.change(first.evolve(text="Changed"))
                await tx.change(second.evolve(text="Stale"))
        self.assertEqual(await self._get(first), first)

    async def test_rollback_successful_transaction_keeps_everything(self) -> None:
        async with self.store.transaction(ORG_A) as tx:
            a = await tx.add(item(text="A"))
            b = await tx.add(item(text="B"))
            b2 = await tx.change(b.evolve(text="B2"))
        self.assertEqual(await self._get(a), a)
        self.assertEqual(await self._get(b), b2)

    # Version conflicts --------------------------------------------------------

    async def test_conflict_second_of_two_changes_is_refused(self) -> None:
        added = await self._add(item())
        first_read = await self._get(added)
        second_read = await self._get(added)
        async with self.store.transaction(ORG_A) as tx:
            saved = await tx.change(first_read.evolve(text="First person's change"))
        self.assertEqual(saved.version, added.version + 1)
        with self.assertRaises(AlreadyChanged) as caught:
            async with self.store.transaction(ORG_A) as tx:
                await tx.change(second_read.evolve(text="Second person's change"))
        self.assertEqual(caught.exception.message, "This has already changed.")
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(await self._get(added), saved)

    async def test_conflict_at_the_same_time_only_one_wins(self) -> None:
        added = await self._add(item())

        async def change(text: str) -> str:
            try:
                async with self.store.transaction(ORG_A) as tx:
                    await tx.change(added.evolve(text=text))
                return "saved"
            except AlreadyChanged:
                return "refused"

        results = await asyncio.gather(change("One"), change("Two"))
        self.assertEqual(sorted(results), ["refused", "saved"])
        stored = await self._get(added)
        self.assertEqual(stored.version, 2)
        self.assertEqual(stored.text, "One" if results[0] == "saved" else "Two")

    async def test_conflict_stale_remove_is_refused(self) -> None:
        added = await self._add(item())
        async with self.store.transaction(ORG_A) as tx:
            changed = await tx.change(added.evolve(text="Changed"))
        with self.assertRaises(AlreadyChanged):
            async with self.store.transaction(ORG_A) as tx:
                await tx.remove(added)
        self.assertEqual(await self._get(added), changed)
        async with self.store.transaction(ORG_A) as tx:
            await tx.remove(changed)
        self.assertIsNone(await self._get(added))

    async def test_conflict_change_after_remove_is_refused(self) -> None:
        added = await self._add(item())
        async with self.store.transaction(ORG_A) as tx:
            await tx.remove(added)
        with self.assertRaises(AlreadyChanged):
            async with self.store.transaction(ORG_A) as tx:
                await tx.change(added.evolve(text="Too late"))

    async def test_conflict_adding_the_same_id_twice_is_refused(self) -> None:
        added = await self._add(item())
        with self.assertRaises(AlreadyChanged):
            await self._add(added)

    # Immutability -------------------------------------------------------------

    async def test_immutable_confirmed_statement_and_report_cannot_change(self) -> None:
        for record in (statement(), report()):
            with self.subTest(record.kind):
                stored = await self._add(record)
                for attempt in ("change", "remove"):
                    with self.assertRaises(TypeError):
                        async with self.store.transaction(ORG_A) as tx:
                            if attempt == "change":
                                await tx.change(stored)
                            else:
                                await tx.remove(stored)
                self.assertEqual(await self._get(stored), stored)

    async def test_immutable_interface_offers_no_other_way(self) -> None:
        async with self.store.transaction(ORG_A) as tx:
            public = {
                name
                for name, _ in inspect.getmembers(type(tx))
                if not name.startswith("_") and name != "closed"
            }
            self.assertLessEqual(public, TRANSACTION_METHODS)
        hints = inspect.signature(Transaction.change).parameters["record"].annotation
        self.assertIn("M", str(hints))

    async def test_immutable_correction_is_a_new_version_and_both_are_kept(self) -> None:
        original = await self._add(statement())
        correction = await self._add(
            statement(
                mark="corrected",
                created_at=T0 + timedelta(days=1),
                wording=StatementWording(reason="One contract is still open."),
                previous_version_id=original.id,
            )
        )
        async with self.store.transaction(ORG_A) as tx:
            versions = await tx.find(ConfirmedStatement, project_id="vendor-consolidation")
        self.assertEqual(versions, [original, correction])

    # Reading ------------------------------------------------------------------

    async def test_find_filters_and_orders_oldest_first(self) -> None:
        later = await self._add(item(text="Later", created_at=T0 + timedelta(hours=1)))
        earlier = await self._add(item(text="Earlier"))
        other_owner = await self._add(item(owner_id="finance-lead"))
        async with self.store.transaction(ORG_A) as tx:
            mine = await tx.find(ActionItem, owner_id="legal-lead")
            self.assertEqual([r.id for r in mine], [earlier.id, later.id])
            self.assertEqual(await tx.find(ActionItem, owner_id="finance-lead"), [other_owner])
            with self.assertRaises(ValueError):
                await tx.find(ActionItem, no_such_field=1)

    async def test_get_of_unknown_id_or_wrong_type_is_none(self) -> None:
        added = await self._add(item())
        async with self.store.transaction(ORG_A) as tx:
            self.assertIsNone(await tx.get(ActionItem, uuid4()))
            self.assertIsNone(await tx.get(ConfirmedStatement, added.id))

    async def test_add_stores_version_one(self) -> None:
        stored = await self._add(item())
        self.assertEqual(stored.version, 1)
        self.assertIsInstance(stored, MutableRecord)
