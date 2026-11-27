"""Story 4.5: items given to virtual employees (task-csIYWYia8yA)."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from typing import Any

from assistant.models.records import AttentionState, ItemState
from assistant.rules import items as rules
from standins.sample import BACKEND_ENGINEER, TEST_ENGINEER, sample_id
from tests.item_helpers import MANAGER, EpicFour
from tests.work_helpers import EpicThree, item
from tests.world import World, build_world

MONDAY_10 = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)
CRM_IMPORT = sample_id("assignment/crm_import")
CRM_TESTS = sample_id("assignment/crm_tests")


class AssignmentItemsTest(EpicFour, EpicThree, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        await self.forget_seeded_request()
        await self.at(MONDAY_10)
        self.w.standins.notices.sent.clear()

    async def asyncTearDown(self) -> None:
        self.w.close()

    def give_linked(self, assignment: str = CRM_IMPORT) -> Any:
        return self.give(
            owner=BACKEND_ENGINEER.id, assignment_id=assignment, text="Import the CRM data"
        )

    async def test_linked_item_opens_at_once(self) -> None:
        """Story 4.5 - when the owner is a virtual employee, the item is linked to the
        assignment the manager chooses, is open at once, and no request is sent."""
        chosen = self.ok("GET", "/assignments", MANAGER)["assignments"]
        self.assertIn(CRM_IMPORT, [a["id"] for a in chosen])
        given = self.give_linked()
        self.assertEqual(given["state"], "open")
        self.assertEqual(given["assignment"], {"id": CRM_IMPORT, "state": "in_progress"})
        self.assertEqual(given["follow_up_times"], [])
        self.assertEqual(await self.asks(given), [])
        self.assertEqual(await self.requests_about(given), [])
        self.assertEqual(self.w.standins.notices.sent, [])
        self.assertEqual(self.w.standins.approval.checked, [])
        await self.at(datetime(2026, 11, 27, 9, 0, tzinfo=UTC))
        await self.run_due()
        self.assertEqual(await self.requests_about(given), [])  # never followed up

    async def test_manager_chooses_one_of_its_assignments(self) -> None:
        body = {"text": "Import", "owner_id": BACKEND_ENGINEER.id, "due_date": "2026-11-27"}
        cases = {
            rules.ASSIGNMENT_NEEDED: {},
            rules.ASSIGNMENT_NOT_THEIRS: {"assignment_id": CRM_TESTS},
            rules.ASSIGNMENT_ONLY_FOR_VIRTUAL: {
                "owner_id": sample_id("person/legal_lead"),
                "assignment_id": CRM_IMPORT,
            },
        }
        for sentence, change in cases.items():
            with self.subTest(sentence):
                self.assertEqual(
                    self.refused("POST", "/action-items", MANAGER, {**body, **change}), sentence
                )
        tester = self.give(owner=TEST_ENGINEER.id, assignment_id=CRM_TESTS, text="Test")
        self.assertEqual(tester["assignment"]["id"], CRM_TESTS)

    async def test_assistant_cannot_give_an_item_to_a_virtual_employee(self) -> None:
        await self.run_tools([item(owner="Backend Engineer")])
        self.assertEqual(await self.requests(), [])
        work = self.w.standins.assistant.started[-1].work_id
        self.assertEqual(
            self.work_view(work)["refused"],
            ["Tool request 1 was refused because an item for a virtual employee follows one "
             "of its assignments, which the manager chooses."],  # fmt: skip
        )

    async def test_assignment_state_mirrored(self) -> None:
        """Story 4.5 - when the assignment changes, the item shows it without confirmation:
        in progress as On track, blocked as Blocked, approved as Done."""
        given = self.give_linked()
        self.assertEqual(self.view(given)["last_update"]["word"], "on_track")
        work_data = self.w.standins.work_data
        work_data.set_assignment_state(CRM_IMPORT, "blocked")
        await self.run_due()
        await self.run_due()  # read again, nothing new
        shown = self.view(given)
        self.assertEqual(
            [(u["word"], u["source"], u["confirmed"]) for u in shown["updates"]],
            [("on_track", "assignment", True), ("blocked", "assignment", True)],
        )
        self.assertEqual(shown["assignment"]["state"], "blocked")
        self.assertEqual([e.reason_code for e in await self.open_entries(given)], ["blocked"])
        work_data.set_assignment_state(CRM_IMPORT, "in_progress")
        await self.later(minutes=5)
        await self.run_due()
        self.assertEqual(self.view(given)["last_update"]["word"], "on_track")
        self.assertEqual(await self.open_entries(given), [])  # settled
        work_data.set_assignment_state(CRM_IMPORT, "approved")
        await self.run_due()
        done = self.view(given)
        self.assertEqual((done["state"], done["last_update"]["word"]), ("done", "done"))
        self.assertEqual(len(done["updates"]), 4)

    async def test_review_or_missing_raises_attention(self) -> None:
        """Story 4.5 - when the assignment waits for the manager's review, or can no longer be
        found, an attention entry is made."""
        review = self.give(owner=TEST_ENGINEER.id, assignment_id=CRM_TESTS, text="Test it")
        (entry,) = await self.open_entries(review)
        self.assertEqual(entry.reason_code, "review")
        self.assertEqual(self.view(review)["updates"], [])  # waiting for review shows no word
        given = self.give_linked()
        self.w.standins.work_data.remove_assignment(CRM_IMPORT)
        await self.run_due()
        (entry,) = await self.open_entries(given)
        self.assertEqual(entry.reason_code, "assignment_missing")
        self.assertEqual(self.view(given)["assignment"]["state"], "missing")
        self.assertEqual((await self.stored(given)).state, ItemState.OPEN)
        self.w.standins.work_data.set_assignment_state(CRM_TESTS, "approved")
        await self.run_due()
        (cleared,) = await self.entries(review)
        self.assertEqual(cleared.state, AttentionState.CLEARED)

    async def test_assignments_unreadable_waits_for_the_next_run(self) -> None:
        given = self.give_linked()
        work_data = self.w.standins.work_data
        real = work_data.assignments

        async def unreachable(manager_id: str) -> list[object]:
            raise ConnectionError("down")

        work_data.assignments = unreachable  # type: ignore[assignment,method-assign]
        work_data.set_assignment_state(CRM_IMPORT, "blocked")
        await self.run_due()
        self.assertEqual(self.view(given)["assignment"]["state"], "in_progress")
        work_data.assignments = real  # type: ignore[method-assign]
        await self.run_due()
        self.assertEqual(self.view(given)["assignment"]["state"], "blocked")


if __name__ == "__main__":
    unittest.main()
