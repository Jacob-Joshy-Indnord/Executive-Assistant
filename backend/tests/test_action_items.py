"""Story 4.1: give an action item (task-UuEtNpZ2n74)."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from assistant.models.records import (
    ItemState,
    PersonRequestState,
    RequestState,
)
from assistant.rules import items as rules
from standins.sample import FINANCE_LEAD, ORGANISATION_ID, sample_id
from tests.item_helpers import FRIDAY, MANAGER, OWNER, EpicFour
from tests.work_helpers import EpicThree, item
from tests.world import World, build_world

MONDAY_10 = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)


class ActionItemsTest(EpicFour, EpicThree, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        await self.forget_seeded_request()
        await self.at(MONDAY_10)

    async def asyncTearDown(self) -> None:
        self.w.close()

    async def test_manager_item_asks_owner_without_approval(self) -> None:
        """Story 4.1 - the manager adds an item with text, owner and a due date from today
        on: the owner is asked to accept it without an approval step."""
        given = self.give()
        self.assertEqual(given["state"], "waiting_for_owner")
        self.assertEqual(given["origin"], "manager")
        (ask,) = await self.asks(given)
        self.assertEqual((ask.asks, ask.person_id, ask.state), ("accept", OWNER, "sent"))
        self.assertEqual(ask.answer_by, datetime(2026, 11, 24, 18, 0, tzinfo=UTC))
        self.assertEqual(len(self.notices_to(OWNER)), 1)
        self.assertEqual(self.w.standins.approval.checked, [])  # no approval step
        self.assertEqual(await self.requests_about(given), [])
        self.assertEqual(len(self.audited("assistant.item_added")), 1)

    async def test_ask_outside_working_hours_waits_for_them(self) -> None:
        await self.at(datetime(2026, 11, 23, 20, 0, tzinfo=UTC))
        given = self.give()
        (ask,) = await self.asks(given)
        self.assertEqual(ask.state, PersonRequestState.TO_BE_SENT)
        self.assertEqual(ask.send_after, datetime(2026, 11, 24, 9, 0, tzinfo=UTC))
        self.assertEqual(self.notices_to(OWNER), [])
        await self.at(datetime(2026, 11, 24, 9, 0, tzinfo=UTC))
        await self.run_due()
        (ask,) = await self.asks(given)
        self.assertEqual(ask.state, PersonRequestState.SENT)
        self.assertEqual(len(self.notices_to(OWNER)), 1)

    async def test_an_item_needs_a_text_an_owner_and_a_date_from_today(self) -> None:
        cases = {
            rules.TEXT_EMPTY: {"text": "  "},
            rules.TEXT_TOO_LONG: {"text": "x" * 301},
            rules.DUE_IN_PAST: {"due_date": "2026-11-22"},
            rules.NOT_AN_OWNER: {"owner_id": "nobody"},
        }
        for sentence, change in cases.items():
            with self.subTest(sentence):
                body = {"text": "Review", "owner_id": OWNER, "due_date": FRIDAY, **change}
                self.assertEqual(self.refused("POST", "/action-items", MANAGER, body), sentence)
        today = self.give(due="2026-11-23", text="x" * 300)
        self.assertEqual(today["due_date"], "2026-11-23")

    async def test_assistant_item_needs_approval_check(self) -> None:
        """Story 4.1 - an item the assistant proposes goes through the approval check
        before its owner is asked."""
        await self.run_tools([item()])
        (request,) = await self.requests()
        self.assertEqual(request.state, RequestState.WAITING)
        self.assertEqual(self.w.standins.approval.checked[-1][0].kind.value, "add_action_item")
        self.assertEqual(self.notices_to(OWNER), [])
        self.ok("POST", f"/requests/{request.id}/approve", MANAGER)
        items = self.ok("GET", "/action-items", MANAGER)["items"]
        added = next(i for i in items if i["origin"] == "chat")
        self.assertEqual(added["state"], "waiting_for_owner")
        (ask,) = await self.asks(added)
        self.assertEqual((ask.asks, ask.state), ("accept", PersonRequestState.SENT))

    async def test_acceptance_opens_item_and_sets_follow_ups(self) -> None:
        """Story 4.1 - when the owner accepts, the item is open and its follow-ups are set."""
        given = self.give()
        accepted = self.accept(given)
        self.assertEqual(accepted["state"], "open")
        self.assertEqual(
            accepted["follow_up_times"],
            ["2026-11-25T09:00:00+00:00", "2026-11-27T09:00:00+00:00"],
        )
        (ask,) = await self.asks(given)
        self.assertEqual(ask.state, PersonRequestState.ANSWERED)
        self.assertEqual(self.accept(given)["state"], "open")  # pressed twice

    async def test_proposed_date_raises_attention_either_way_owner_told(self) -> None:
        """Story 4.1 - the owner proposes another date with a reason: an attention entry
        offers the manager that date or the first one; either way the owner is told and the
        item is open."""
        path = "/action-items/{}/propose-date"
        first, second = self.give(), self.give(text="Sign the renewal")
        self.assertEqual(
            self.refused("POST", path.format(first["id"]), OWNER, {"due_date": "2026-12-04"}),
            rules.REASON_NEEDED,
        )
        for given in (first, second):
            answered = self.ok(
                "POST",
                path.format(given["id"]),
                OWNER,
                {"due_date": "2026-12-04", "reason": "Legal is away until then."},
            )
            self.assertEqual(answered["state"], "open")
            self.assertEqual(answered["due_date"], FRIDAY)
            self.assertEqual(answered["answer_reason"], "Legal is away until then.")
            self.assertTrue(answered["follow_up_times"])
            (entry,) = await self.open_entries(given)
            self.assertEqual(entry.reason_code, "other_date")
            self.assertEqual(entry.choices, [rules.USE_NEW_DATE, rules.KEEP_DATE])
            self.assertEqual(entry.asked_date.isoformat() if entry.asked_date else None,
                             "2026-12-04")  # fmt: skip
        told_before = len(self.notices_to(OWNER))
        (entry,) = await self.open_entries(first)
        self.ok("POST", f"/attention/{entry.id}/decide", MANAGER, {"choice": "Use the new date"})
        self.assertEqual(self.view(first)["due_date"], "2026-12-04")
        (entry,) = await self.open_entries(second)
        self.ok("POST", f"/attention/{entry.id}/decide", MANAGER, {"choice": "keep the first date"})
        self.assertEqual(self.view(second)["due_date"], FRIDAY)
        told = self.notices_to(OWNER)[told_before:]
        self.assertEqual(
            told,
            [
                "The due date of an action item you own is now Friday 04 December.",
                "The due date of an action item you own stays Friday 27 November.",
            ],
        )
        for given in (first, second):
            self.assertEqual(self.view(given)["state"], "open")
            self.assertEqual(await self.open_entries(given), [])

    async def test_decline_raises_attention(self) -> None:
        """Story 4.1 - when the owner declines with a reason, an attention entry is made."""
        given = self.give()
        path = f"/action-items/{given['id']}/decline"
        self.assertEqual(self.refused("POST", path, OWNER, {}), rules.REASON_NEEDED)
        declined = self.ok("POST", path, OWNER, {"reason": "Finance owns the contracts."})
        self.assertEqual(declined["state"], "declined")
        self.assertEqual(declined["answer_reason"], "Finance owns the contracts.")
        (entry,) = await self.open_entries(given)
        self.assertEqual(entry.reason_code, "declined")
        self.assertEqual(entry.choices, [rules.REASSIGN, rules.TAKE_IT_UP, rules.CLOSE])
        (ask,) = await self.asks(given)
        self.assertEqual(ask.state, PersonRequestState.ANSWERED)

    async def test_self_assigned_item_is_open_at_once(self) -> None:
        """Story 4.1 - an item the manager gives themselves is open at once, no one is asked,
        and it has no follow-ups."""
        own = self.give(owner=MANAGER, follow_up_times=["2026-11-25T09:00:00+00:00"])
        self.assertEqual(own["state"], "open")
        self.assertEqual(own["follow_up_times"], [])
        self.assertEqual(await self.asks(own), [])
        self.assertEqual(self.w.standins.notices.sent, [])
        await self.at(datetime(2026, 11, 27, 9, 0, tzinfo=UTC))
        await self.run_due()
        self.assertEqual(await self.requests_about(own), [])

    async def test_assistant_item_for_the_manager_is_open_at_once(self) -> None:
        await self.run_tools([item(owner="Operations director")])
        (request,) = await self.requests()
        self.ok("POST", f"/requests/{request.id}/approve", MANAGER)
        items = self.ok("GET", "/action-items", MANAGER)["items"]
        own = next(i for i in items if i["origin"] == "chat")
        self.assertEqual((own["state"], own["follow_up_times"]), ("open", []))
        self.assertEqual(await self.asks(own), [])

    async def test_hand_over_or_end_closes_open_items(self) -> None:
        """Story 4.1 - when the assistant is handed over or ended, its open items are closed,
        what owners were asked is withdrawn, and each owner is told once."""
        for closing in ("end", "hand_over"):
            with self.subTest(closing):
                self.w.close()
                self.w = await build_world()
                await self.at(MONDAY_10)
                opened = self.give()
                self.accept(opened)
                waiting = self.give(text="Sign the renewal")
                finance = self.give(owner=FINANCE_LEAD.id, text="Send the figures")
                own = self.give(owner=MANAGER, text="Read the summary")
                self.w.standins.notices.sent.clear()
                flow_id = self.w.seeded.assistant.flow_id
                if closing == "end":
                    await self.w.module.flow.end(ORGANISATION_ID, flow_id)
                else:
                    await self.w.module.flow.hand_over(
                        ORGANISATION_ID, flow_id, sample_id("person/programme_lead")
                    )
                for given in (opened, waiting, finance, own):
                    self.assertEqual((await self.stored(given)).state, ItemState.CLOSED)
                for given in (waiting, finance):
                    self.assertTrue(
                        all(a.state is PersonRequestState.WITHDRAWN for a in await self.asks(given))
                    )
                told = sorted(n.person_id for n in self.w.standins.notices.sent)
                self.assertEqual(told, sorted([OWNER, FINANCE_LEAD.id]))


if __name__ == "__main__":
    unittest.main()
