"""Part 2, Story 1.3 Review the notes, and "Followed to the end" (task-eGhAoNEt8fc,
task-ZXl5E3mXUSQ)."""

from __future__ import annotations

import unittest
from typing import Any

from assistant.models.boundary import ActionKind
from assistant.models.records import (
    ActionItem,
    ConfirmedStatement,
    MeetingNotes,
    PersonRequest,
    StatementDraft,
)
from assistant.service.meeting_notes import CHOOSE_OWNER, DUE_NEEDED, NOT_PROPOSED
from standins.sample import FINANCE_LEAD, ORGANISATION_ID
from tests.meeting_helpers import MANAGER, OWNER, VENDORS, Meetings
from tests.world import build_world

FINANCE = FINANCE_LEAD.id


class ReviewNotesTest(Meetings, unittest.IsolatedAsyncioTestCase):
    """Story 1.3 Review the notes (task-eGhAoNEt8fc)."""

    async def asyncSetUp(self) -> None:
        self.w = await build_world()
        await self.follow(VENDORS)
        meeting = await self.write_notes()
        assert meeting.notes_id is not None
        self.path = f"/meeting-notes/{meeting.notes_id}"

    async def asyncTearDown(self) -> None:
        self.w.close()

    def view(self) -> Any:
        return self.ok("GET", self.path, MANAGER)

    def action(self, index: int) -> Any:
        return self.view()["actions"][index]

    def keep(self, index: int, body: Any = None) -> Any:
        return self.call(
            "POST",
            f"{self.path}/actions/{self.action(index)['id']}/keep",
            MANAGER,
            body,
        )

    def edit(self, index: int, body: Any) -> Any:
        return self.call("PUT", f"{self.path}/actions/{self.action(index)['id']}", MANAGER, body)

    async def items(self) -> list[ActionItem]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return [
                i
                for i in await tx.find(ActionItem, assistant_id=self.w.seeded.assistant.id)
                if i.origin == "meeting"
            ]

    async def asks_to(self, person: str) -> list[PersonRequest]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return [
                a
                for a in await tx.find(PersonRequest, person_id=person)
                if a.subject_kind == "action_item"
                and a.subject_id != str(self.w.seeded.item.id)
            ]

    async def test_the_manager_sees_decisions_actions_and_awaiting_statements(self) -> None:
        """Story 1.3 - the manager sees the decisions, the actions with Keep, Edit and Remove,
        and each project's statement as 'Awaiting its owner' until it is confirmed."""
        notes = self.view()
        self.assertEqual(
            [d["text"] for d in notes["decisions"]], ["We keep two vendors for office supplies."]
        )
        for action in notes["actions"]:
            self.assertEqual(
                (action["state"], action["can_keep"], action["can_edit"], action["can_remove"]),
                ("proposed", True, True, True),
            )
        (statement,) = notes["statements"]
        self.assertEqual(statement["state"], "awaiting_owner")
        self.assertIsNone(statement["confirmed"])
        self.assertNotIn("Two contracts are signed", str(notes))  # not before it is confirmed

    async def test_a_kept_action_becomes_an_action_item_from_a_meeting(self) -> None:
        """Story 1.3 - a kept action becomes an action item with the origin 'meeting', and its
        owner is asked to accept it."""
        response = self.keep(0)
        self.assertEqual(response.status_code, 200, response.text)
        kept = response.json()["actions"][0]
        self.assertEqual(kept["state"], "kept")
        (item,) = await self.items()
        self.assertEqual(str(item.id), kept["item_id"])
        self.assertEqual(
            (item.origin, item.owner_id, item.text, item.due_date.isoformat(), item.state.value),
            ("meeting", OWNER, "Send the signed contracts", "2026-12-04", "waiting_for_owner"),
        )
        (ask,) = await self.asks_to(OWNER)
        self.assertEqual((ask.asks, ask.subject_id), ("accept", str(item.id)))
        mine = self.ok("GET", "/my-action-items", OWNER)["items"]
        self.assertIn("Send the signed contracts", [i["text"] for i in mine])
        again = self.keep(0)  # pressed twice: kept once
        self.assertEqual(again.status_code, 200)
        self.assertEqual(len(await self.items()), 1)

    async def test_an_action_that_is_not_kept_never_reaches_its_owner(self) -> None:
        """Story 1.3 - an action that is removed or not yet kept: its owner is not asked and
        no action item exists for it."""
        removed = self.call("POST", f"{self.path}/actions/{self.action(1)['id']}/remove", MANAGER)
        self.assertEqual(removed.json()["actions"][1]["state"], "removed")
        self.assertEqual(await self.items(), [])
        self.assertEqual(await self.asks_to(OWNER), [])
        self.assertEqual(await self.asks_to(FINANCE), [])
        self.assertEqual(self.ok("GET", "/my-action-items", FINANCE)["items"], [])
        response = self.keep(1)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["message"], NOT_PROPOSED)
        self.assertFalse(self.action(1)["can_keep"])

    async def test_edit_changes_an_action_before_it_is_kept(self) -> None:
        """Story 1.3 - Edit changes the text, owner and due date of an action not yet kept;
        an owner chosen this way replaces 'Owner not found'."""
        edited = self.edit(0, {"owner": "Nobody Of That Name"}).json()["actions"][0]
        self.assertEqual(edited["owner_label"], "Owner not found")
        edited = self.edit(
            0, {"text": "Send the countersigned contracts", "owner": "finance LEAD",
                "due_date": "2026-12-11"},
        ).json()["actions"][0]  # fmt: skip
        self.assertEqual(
            (edited["text"], edited["owner"]["id"], edited["owner_label"], edited["due_date"]),
            ("Send the countersigned contracts", FINANCE, "Finance lead", "2026-12-11"),
        )
        self.assertTrue(edited["can_keep"])
        self.keep(0)
        (item,) = await self.items()
        self.assertEqual(
            (item.text, item.owner_id, item.due_date.isoformat()),
            ("Send the countersigned contracts", FINANCE, "2026-12-11"),
        )
        response = self.edit(0, {"text": "Too late"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["message"], NOT_PROPOSED)

    async def test_an_edited_action_meets_the_checks_of_a_hand_added_item(self) -> None:
        """Story 1.3 - while the text is over 300 characters, or the owner does not match
        exactly one person or virtual employee, Keep stays unavailable."""
        long = self.edit(0, {"text": "x" * 301}).json()["actions"][0]
        self.assertFalse(long["can_keep"])
        self.assertEqual(long["keep_blocked"], "An item can be at most 300 characters.")
        refused = self.keep(0)
        self.assertEqual(refused.status_code, 422)
        self.assertEqual(
            refused.json()["detail"]["message"],
            "An item can be at most 300 characters.",
        )
        self.edit(0, {"text": "Send the contracts", "owner": "Nobody Of That Name"})
        unmatched = self.action(0)
        self.assertEqual((unmatched["can_keep"], unmatched["keep_blocked"]), (False, CHOOSE_OWNER))
        self.assertEqual(self.keep(0).json()["detail"]["message"], CHOOSE_OWNER)
        self.assertEqual(await self.items(), [])
        self.edit(0, {"owner": "Legal lead"})
        self.assertTrue(self.action(0)["can_keep"])
        self.assertEqual(self.keep(0).status_code, 200)
        self.assertEqual(len(await self.items()), 1)

    async def test_an_action_without_a_due_date_waits_for_one(self) -> None:
        self.edit(0, {"owner": "Legal lead"})
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            (notes,) = [
                n for n in await tx.find(MeetingNotes) if f"/meeting-notes/{n.id}" == self.path
            ]
            line = notes.actions[0].model_copy(update={"due_date": None})
            await tx.change(notes.evolve(actions=[line, *notes.actions[1:]]))
        self.assertEqual(self.action(0)["keep_blocked"], DUE_NEEDED)
        self.edit(0, {"due_date": "2026-12-04"})
        self.assertTrue(self.action(0)["can_keep"])

    async def test_editing_and_keeping_need_no_approval(self) -> None:
        """Story 1.3 - editing and keeping take effect as the manager's own decisions, without
        the approval check."""
        checked = len(self.w.standins.approval.checked)
        self.edit(1, {"due_date": "2026-12-03"})
        self.assertEqual(self.keep(1).status_code, 200)
        self.assertEqual(len(self.w.standins.approval.checked), checked)
        requests = self.ok("GET", "/requests", MANAGER)["requests"]
        self.assertNotIn("Item", [r["label"] for r in requests])

    async def test_a_statement_is_sent_for_confirmation_at_once_without_a_colour(self) -> None:
        """Story 1.3 - the person who confirms the project's status is asked at once, through
        the approval check, and confirms or corrects it as in part 1; no colour is chosen."""
        checked = [r for r, _ in self.w.standins.approval.checked if r.kind is ActionKind.ASK_OWNER]
        self.assertEqual(len(checked), 1)
        confirm = next(r for r in self.ok("GET", "/requests", MANAGER)["requests"]
                       if r["kind"] == "statement_confirm")  # fmt: skip
        self.assertEqual(confirm["target"]["id"], OWNER)
        self.assertEqual(confirm["project"]["name"], "Vendor consolidation")
        self.assertIsNone(confirm["text"])
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            (draft,) = await tx.find(StatementDraft, project_id=VENDORS)
        self.assertNotIn("colour", StatementDraft.model_fields)
        self.ok("POST", f"/requests/{confirm['id']}/approve", MANAGER)
        (ask,) = [
            a
            for a in self.ok("GET", "/status-requests", OWNER)["requests"]
            if a["asks"] == "confirm"
        ]
        request = self.ok("GET", f"/status-requests/{ask['id']}", OWNER)
        self.assertEqual((request["start"], request["colour"]), ("draft", None))
        self.assertEqual(request["wording"]["reason"], draft.wording.reason)
        self.ok(
            "POST",
            f"/status-requests/{ask['id']}/answer",
            OWNER,
            {"colour": "amber", "reason": draft.wording.reason},
        )
        (statement,) = self.view()["statements"]
        self.assertEqual(statement["state"], "confirmed")
        self.assertEqual(
            (statement["confirmed"]["colour"], statement["confirmed"]["reason"]),
            ("amber", draft.wording.reason),
        )

    async def test_deleting_the_notes_keeps_what_was_kept_or_confirmed(self) -> None:
        """Story 1.3 - deleting the notes removes them; items already kept and confirmed
        statements stay."""
        self.keep(0)
        confirm = next(r for r in self.ok("GET", "/requests", MANAGER)["requests"]
                       if r["kind"] == "statement_confirm")  # fmt: skip
        self.ok("POST", f"/requests/{confirm['id']}/approve", MANAGER)
        (ask,) = [
            a
            for a in self.ok("GET", "/status-requests", OWNER)["requests"]
            if a["asks"] == "confirm"
        ]
        self.ok("POST", f"/status-requests/{ask['id']}/answer", OWNER,
                {"colour": "green", "reason": "All contracts are signed."})  # fmt: skip
        deleted = self.ok("DELETE", self.path, MANAGER)
        self.assertTrue(deleted["deleted"])
        self.assertEqual(self.call("GET", self.path, MANAGER).status_code, 404)
        self.assertEqual(len(await self.items()), 1)
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            confirmed = await tx.find(ConfirmedStatement, project_id=VENDORS)
        self.assertEqual([c.wording.reason for c in confirmed], ["All contracts are signed."])

    async def test_nobody_else_reviews_the_notes(self) -> None:
        for person in (OWNER, FINANCE, self.w.seeded.other_assistant.manager_id):
            with self.subTest(person):
                self.assertEqual(self.call("GET", self.path, person).status_code, 404)
                action = self.action(0)["id"]
                response = self.call("POST", f"{self.path}/actions/{action}/keep", person)
                self.assertEqual(response.status_code, 404)
        self.assertEqual(await self.items(), [])


class OutcomeInTheNotesTest(Meetings, unittest.IsolatedAsyncioTestCase):
    """Followed to the end: the outcome of kept actions in the notes (task-ZXl5E3mXUSQ)."""

    async def asyncSetUp(self) -> None:
        self.w = await build_world()
        meeting = await self.write_notes()
        self.path = f"/meeting-notes/{meeting.notes_id}"

    async def asyncTearDown(self) -> None:
        self.w.close()

    def actions(self) -> Any:
        return self.ok("GET", self.path, MANAGER)["actions"]

    def kept(self, index: int) -> Any:
        action = self.actions()[index]
        self.ok("POST", f"{self.path}/actions/{action['id']}/keep", MANAGER)
        return self.actions()[index]

    async def test_a_kept_action_is_followed_up_like_every_other_item(self) -> None:
        """Followed to the end - a kept action is an action item with the origin 'meeting',
        its owner is asked to accept it, and it is followed up like every other item."""
        line = self.kept(0)
        item = self.ok("GET", f"/action-items/{line['item_id']}", MANAGER)
        self.assertEqual((item["origin"], item["state"]), ("meeting", "waiting_for_owner"))
        accepted = self.ok("POST", f"/action-items/{line['item_id']}/accept", OWNER)
        self.assertEqual(accepted["state"], "open")
        self.assertTrue(accepted["follow_up_times"])
        self.assertIsNone(self.actions()[0]["outcome"])

    async def test_the_notes_show_the_outcome_and_the_date(self) -> None:
        """Followed to the end - when the item is done, closed or raised to the manager, its
        line in the notes shows the outcome and the date."""
        done = self.kept(0)
        self.ok("POST", f"/action-items/{done['item_id']}/accept", OWNER)
        self.ok("POST", f"/action-items/{done['item_id']}/updates", OWNER, {"word": "done"})
        closed = self.kept(1)
        self.ok(
            "POST",
            f"/action-items/{closed['item_id']}/close",
            MANAGER,
            {"reason": "Not needed."},
        )
        actions = self.actions()
        self.assertEqual(actions[0]["outcome"], {"outcome": "done", "date": "2026-11-23"})
        self.assertEqual(actions[1]["outcome"], {"outcome": "closed", "date": "2026-11-23"})

    async def test_an_item_raised_to_the_manager_shows_on_its_line(self) -> None:
        line = self.kept(0)
        self.ok("POST", f"/action-items/{line['item_id']}/decline", OWNER, {"reason": "Not mine."})
        self.assertEqual(self.actions()[0]["outcome"], {"outcome": "raised", "date": "2026-11-23"})


if __name__ == "__main__":
    unittest.main()
