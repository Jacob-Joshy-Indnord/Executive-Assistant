"""Story 4.1: the manager's controls on an item (task-QX3KZ7y0XiU)."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from assistant.models.records import PersonRequestState
from assistant.rules import items as rules
from standins.sample import FINANCE_LEAD
from tests.item_helpers import MANAGER, OWNER, EpicFour
from tests.world import World, build_world

MONDAY_10 = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)


class ItemControlsTest(EpicFour, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        await self.at(MONDAY_10)
        self.item = self.give()
        self.accept(self.item)
        self.w.standins.notices.sent.clear()

    async def asyncTearDown(self) -> None:
        self.w.close()

    def path(self, action: str, item: dict[str, str] | None = None) -> str:
        return f"/action-items/{(item or self.item)['id']}/{action}"

    async def test_manager_can_manage_an_item(self) -> None:
        """Story 4.1 - when the manager opens an item, they can change its follow-up times,
        extend, reassign or close it, and mark their own items as done."""
        self.assertEqual(self.view(self.item)["can"], ["follow_ups", "extend", "reassign", "close"])
        own = self.give(owner=MANAGER, text="Read the summary")
        self.assertEqual(self.view(own)["can"], ["update", "extend", "reassign", "close", "done"])
        self.assertEqual(self.view(self.item, OWNER)["can"], ["update"])

    async def test_follow_up_times_up_to_five_or_none(self) -> None:
        times = ["2026-11-24T14:00:00+01:00", "2026-11-26T09:30:00+00:00"]
        changed = self.ok("PUT", self.path("follow-ups"), MANAGER, {"times": times})
        self.assertEqual(
            changed["follow_up_times"],
            ["2026-11-24T13:00:00+00:00", "2026-11-26T09:30:00+00:00"],
        )
        self.assertTrue(changed["follow_ups_set"])
        none = self.ok("PUT", self.path("follow-ups"), MANAGER, {"times": []})
        self.assertEqual(none["follow_up_times"], [])
        six = [f"2026-11-2{d}T09:00:00+00:00" for d in range(4, 10)]
        self.assertEqual(
            self.refused("PUT", self.path("follow-ups"), MANAGER, {"times": six}),
            rules.TOO_MANY_FOLLOW_UPS,
        )
        past = ["2026-11-23T09:00:00+00:00"]
        self.assertEqual(
            self.refused("PUT", self.path("follow-ups"), MANAGER, {"times": past}),
            rules.FOLLOW_UP_PAST,
        )
        own = self.give(owner=MANAGER, text="Read the summary")
        self.assertEqual(
            self.refused("PUT", self.path("follow-ups", own), MANAGER, {"times": []}),
            rules.NO_FOLLOW_UPS,
        )
        self.assertEqual(len(self.audited("assistant.item_follow_ups_set")), 2)

    async def test_extend_sets_date_follow_ups_and_tells_the_owner(self) -> None:
        extended = self.ok("POST", self.path("extend"), MANAGER, {"due_date": "2026-12-04"})
        self.assertEqual(extended["due_date"], "2026-12-04")
        # Working days Tue 24 .. Fri 4 December: nine, so half-way is the fifth (Monday 30).
        self.assertEqual(
            extended["follow_up_times"],
            ["2026-11-30T09:00:00+00:00", "2026-12-04T09:00:00+00:00"],
        )
        self.assertEqual(
            self.notices_to(OWNER),
            ["The due date of an action item you own is now Friday 04 December."],
        )
        self.assertEqual(
            self.refused("POST", self.path("extend"), MANAGER, {"due_date": "2026-11-20"}),
            rules.DUE_IN_PAST,
        )
        (record,) = self.audited("assistant.item_extended")
        self.assertEqual((record.who, record.state_after), (MANAGER, "open"))

    async def test_reassign_asks_the_new_owner_and_tells_the_previous(self) -> None:
        reassigned = self.ok("POST", self.path("reassign"), MANAGER, {"owner_id": FINANCE_LEAD.id})
        self.assertEqual(reassigned["owner"]["id"], FINANCE_LEAD.id)
        self.assertEqual(reassigned["state"], "waiting_for_owner")
        self.assertEqual(reassigned["follow_up_times"], [])
        asks = await self.asks(self.item)
        new = [a for a in asks if a.person_id == FINANCE_LEAD.id]
        self.assertEqual([(a.asks, a.state) for a in new], [("accept", PersonRequestState.SENT)])
        self.assertEqual(len(self.notices_to(FINANCE_LEAD.id)), 1)
        self.assertEqual(
            self.notices_to(OWNER),
            ["An action item you owned has been given to someone else, "
             "so nothing more is needed from you."],  # fmt: skip
        )
        response = self.call("GET", f"/action-items/{self.item['id']}", OWNER)
        self.assertEqual(response.status_code, 404)  # no longer theirs
        self.assertEqual(
            self.refused("POST", self.path("reassign"), MANAGER, {"owner_id": FINANCE_LEAD.id}),
            rules.SAME_OWNER,
        )

    async def test_close_with_or_without_reason_tells_the_owner(self) -> None:
        other = self.give(text="Sign the renewal")
        self.w.standins.notices.sent.clear()
        closed = self.ok("POST", self.path("close"), MANAGER, {"reason": "The deal fell through."})
        self.assertEqual((closed["state"], closed["close_reason"]),
                         ("closed", "The deal fell through."))  # fmt: skip
        self.ok("POST", self.path("close", other), MANAGER)
        self.assertEqual(
            self.notices_to(OWNER),
            [
                "An action item you own has been closed, so nothing more is needed from you. "
                "The reason given: The deal fell through.",
                "An action item you own has been closed, so nothing more is needed from you.",
            ],
        )
        self.assertTrue(
            all(a.state is PersonRequestState.WITHDRAWN for a in await self.asks(other))
        )
        self.assertEqual(self.refused("POST", self.path("close"), MANAGER, {}), rules.ENDED)

    async def test_mark_own_items_done(self) -> None:
        own = self.give(owner=MANAGER, text="Read the summary")
        self.assertEqual(self.ok("POST", self.path("done", own), MANAGER)["state"], "done")
        self.assertEqual(self.refused("POST", self.path("done"), MANAGER), rules.ONLY_OWN_DONE)

    async def test_owners_cannot_use_the_managers_controls(self) -> None:
        for action, body in [
            ("extend", {"due_date": "2026-12-04"}),
            ("reassign", {"owner_id": FINANCE_LEAD.id}),
            ("close", {}),
            ("done", None),
        ]:
            with self.subTest(action):
                response = self.call("POST", self.path(action), OWNER, body)
                self.assertEqual(response.status_code, 404)
        self.assertEqual(self.view(self.item)["state"], "open")


if __name__ == "__main__":
    unittest.main()
