"""Story 4.4: items that need the manager, the entries (task-Go90WuLqUj0)."""

from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from typing import Any

from assistant.errors import AlreadyChanged
from assistant.models.records import AttentionEntry, AttentionState, PersonRequestState
from assistant.rules import items as rules
from assistant.service.actions import perform
from assistant.service.attention import entry_id, raise_for_item
from standins.sample import ORGANISATION_ID
from tests.item_helpers import MANAGER, OWNER, EpicFour
from tests.world import World, build_world

MONDAY_10 = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)
FRIDAY_18 = datetime(2026, 11, 27, 18, 0, tzinfo=UTC)


class AttentionEntriesTest(EpicFour, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        await self.at(MONDAY_10)
        self.item = self.give()

    async def asyncTearDown(self) -> None:
        self.w.close()

    def shown(self) -> list[Any]:
        """Needs your attention, as the manager sees it: entries about this test's items."""
        listed = self.ok("GET", "/attention", MANAGER)["entries"]
        seeded = str(self.w.seeded.item.id)
        return [e for e in listed if e["item"] is not None and e["item"]["id"] != seeded]

    async def test_overdue_entry_shows_item_reason_owner_last_update_and_choices(self) -> None:
        """Story 4.4 - an item not done at the end of its due date raises one entry with the
        item, the reason, its owner, the last update and the choices."""
        self.accept(self.item)
        self.update(self.item, "on_track", note="Waiting on one signature.")
        await self.at(datetime(2026, 11, 27, 17, 59, tzinfo=UTC))
        await self.run_due()
        self.assertEqual(self.shown(), [])
        await self.at(FRIDAY_18)
        await self.run_due()
        (entry,) = self.shown()
        self.assertEqual(entry["item"]["text"], "Review the vendor contracts")
        self.assertEqual((entry["reason_code"], entry["label"]), ("overdue", "Overdue"))
        self.assertEqual(entry["reason"], rules.REASON_SENTENCES[rules.Reason.OVERDUE])
        self.assertEqual(entry["owner"]["display_name"], "Legal lead")
        self.assertEqual(entry["last_update"]["note"], "Waiting on one signature.")
        self.assertEqual(entry["choices"], ["Extend", "Reassign", "Take it up", "Close"])
        self.assertEqual(self.view(self.item)["attention"]["id"], entry["id"])

    async def test_unanswered_request_raises_entry(self) -> None:
        """Story 4.4 - a request not answered in time (the end of the next working day)."""
        await self.at(datetime(2026, 11, 24, 17, 0, tzinfo=UTC))
        await self.run_due()
        self.assertEqual(await self.open_entries(self.item), [])
        await self.at(datetime(2026, 11, 24, 18, 0, tzinfo=UTC))
        await self.run_due()
        (entry,) = await self.open_entries(self.item)
        self.assertEqual(entry.reason_code, "unanswered")

    async def test_blocked_declined_other_date_and_owner_left_raise_entries(self) -> None:
        """Story 4.4 - the owner says Blocked, declines, asks for another date or leaves."""
        blocked, declined, other_date, left = (
            self.item,
            self.give(text="Sign the renewal"),
            self.give(text="Call the supplier"),
            self.give(text="File the papers"),
        )
        self.accept(blocked)
        self.update(blocked, "blocked", note="The supplier has not replied.")
        self.ok("POST", f"/action-items/{declined['id']}/decline", OWNER, {"reason": "Not mine."})
        self.accept(other_date)
        self.update(other_date, "on_track", date_asked_for="2026-12-04")

        async def reasons() -> list[str | None]:
            found = []
            for item in (blocked, declined, other_date, left):
                (entry,) = await self.open_entries(item) or [None]
                found.append(entry.reason_code if entry else None)
            return found

        self.assertEqual(await reasons(), ["blocked", "declined", "other_date", None])
        self.w.standins.people.leave(OWNER)
        await self.run_due()
        self.assertEqual(await reasons(), ["owner_left"] * 4)
        (entry,) = self.ok("GET", "/attention", MANAGER)["entries"][-1:]
        self.assertEqual(entry["choices"], [rules.REASSIGN, rules.CLOSE])
        self.assertEqual([a.state for a in await self.asks(left)], [PersonRequestState.WITHDRAWN])
        await self.run_due()  # once for each owner
        self.assertEqual(len(await self.entries(left)), 1)

    async def test_each_owner_trigger_raises_its_reason(self) -> None:
        self.accept(self.item)
        self.update(self.item, "blocked")
        self.assertEqual([e.reason_code for e in await self.open_entries(self.item)], ["blocked"])
        other = self.give(text="Call the supplier")
        self.accept(other)
        self.update(other, "on_track", date_asked_for="2026-12-04")
        (entry,) = await self.open_entries(other)
        self.assertEqual((entry.reason_code, str(entry.asked_date)), ("other_date", "2026-12-04"))

    async def test_entry_updated_not_duplicated(self) -> None:
        """Story 4.4 - when the reason changes, the same entry is updated; an item never has
        two open entries."""
        self.accept(self.item)
        self.update(self.item, "blocked")
        (first,) = await self.open_entries(self.item)
        await self.at(FRIDAY_18)
        await self.run_due()
        (entry,) = await self.open_entries(self.item)
        self.assertEqual((entry.id, entry.reason_code), (first.id, "overdue"))
        self.assertEqual(len(await self.entries(self.item)), 1)
        self.assertEqual(len(self.audited("assistant.attention_updated")), 1)

    async def test_two_triggers_at_once_make_one_entry(self) -> None:
        self.accept(self.item)
        item = await self.stored(self.item)

        async def raise_one(reason: rules.Reason) -> None:
            async def work(action: Any) -> None:
                await raise_for_item(action, item, reason)

            await perform(self.w.module.boundary, self.w.store, ORGANISATION_ID, "flow", work)

        await asyncio.gather(raise_one(rules.Reason.BLOCKED), raise_one(rules.Reason.OVERDUE))
        (entry,) = await self.entries(self.item)
        self.assertEqual((entry.state, entry.reason_code), (AttentionState.OPEN, "overdue"))

    async def test_a_second_entry_with_the_same_id_is_refused(self) -> None:
        """Two stores' transactions that both saw no entry derive the same id, so the store
        keeps one and refuses the other (it is tried again)."""
        self.accept(self.item)
        item = await self.stored(self.item)
        entry = AttentionEntry(
            id=entry_id(item, 0),
            organisation_id=ORGANISATION_ID,
            assistant_id=item.assistant_id,
            created_at=MONDAY_10,
            subject_kind="action_item",
            subject_id=str(item.id),
            reason="x",
            choices=["a", "b"],
        )
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            await tx.add(entry)
        with self.assertRaises(AlreadyChanged):
            async with self.w.store.transaction(ORGANISATION_ID) as tx:
                await tx.add(entry)

    async def test_settled_entries_cleared(self) -> None:
        """Story 4.4 - when an update settles the reason, the entry is cleared."""
        self.accept(self.item)
        self.update(self.item, "blocked")
        self.update(self.item, "on_track", note="Unblocked.")
        (entry,) = await self.entries(self.item)
        self.assertEqual(entry.state, AttentionState.CLEARED)

        late = self.give(text="Sign the renewal")
        self.accept(late)
        await self.at(FRIDAY_18)
        await self.run_due()
        self.update(late, "on_track")  # still late: not settled
        self.assertEqual([e.reason_code for e in await self.open_entries(late)], ["overdue"])
        self.update(late, "done")
        self.assertEqual(await self.open_entries(late), [])


if __name__ == "__main__":
    unittest.main()
