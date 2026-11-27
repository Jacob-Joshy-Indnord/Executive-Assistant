"""Story 4.4: the manager's decisions on attention entries (task-N7CilfW5khA)."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from typing import Any

from assistant.models.boundary import Notice, NoticeResult
from assistant.models.records import (
    AttentionState,
    PersonRequestState,
    PieceOfWork,
    WorkKind,
    WorkState,
)
from assistant.service.decisions import DECISION_LABEL, NOT_SENT, PERSON_NEEDED
from standins.sample import (
    BACKEND_ENGINEER,
    FINANCE_LEAD,
    ORGANISATION_ID,
    PROGRAMME_LEAD,
    sample_id,
)
from tests.item_helpers import MANAGER, OWNER, EpicFour
from tests.work_helpers import SOURCE, EpicThree
from tests.world import World, build_world

MONDAY_10 = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)
FRIDAY_18 = datetime(2026, 11, 27, 18, 0, tzinfo=UTC)


class AttentionDecisionsTest(EpicFour, EpicThree, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        await self.at(MONDAY_10)
        self.item = self.give()
        self.accept(self.item)
        await self.at(FRIDAY_18)
        await self.run_due()  # overdue
        (self.entry,) = await self.open_entries(self.item)
        self.w.standins.notices.sent.clear()

    async def asyncTearDown(self) -> None:
        self.w.close()

    def decide(self, body: dict[str, Any], entry: Any = None) -> Any:
        return self.ok("POST", f"/attention/{(entry or self.entry).id}/decide", MANAGER, body)

    def checked(self) -> int:
        return len(self.w.standins.approval.checked)

    async def test_extend(self) -> None:
        """Story 4.4 - Extend: the new due date applies, the owner is told, and the
        follow-ups are set again."""
        self.assertEqual(self.refused(
            "POST",
            f"/attention/{self.entry.id}/decide",
            MANAGER,
            {"choice": "Extend"}),  # fmt: skip
            "Choose the new due date.",
        )
        before = self.checked()
        decided = self.decide({"choice": "Extend", "due_date": "2026-12-11"})
        self.assertEqual((decided["state"], decided["decision"]), ("decided", "Extend"))
        shown = self.view(self.item)
        self.assertEqual(shown["due_date"], "2026-12-11")
        # From Friday 27: ten working days to Friday 11 December, half-way Friday 4.
        self.assertEqual(
            shown["follow_up_times"],
            ["2026-12-04T09:00:00+00:00", "2026-12-11T09:00:00+00:00"],
        )
        self.assertEqual(
            self.notices_to(OWNER),
            ["The due date of an action item you own is now Friday 11 December."],
        )
        self.assertEqual(self.checked(), before)  # the manager's own click: no approval
        self.assertEqual(await self.open_entries(self.item), [])

    async def test_reassign(self) -> None:
        """Story 4.4 - Reassign: the new owner is asked to accept and the previous owner is
        told."""
        decided = self.decide({"choice": "reassign", "owner_id": FINANCE_LEAD.id})
        self.assertEqual(
            (decided["decision"], decided["decision_target_id"]), ("Reassign", FINANCE_LEAD.id)
        )
        shown = self.view(self.item)
        self.assertEqual((shown["owner"]["id"], shown["state"]),
                         (FINANCE_LEAD.id, "waiting_for_owner"))  # fmt: skip
        asks = [a for a in await self.asks(self.item) if a.person_id == FINANCE_LEAD.id]
        self.assertEqual([(a.asks, a.state) for a in asks],
                         [("accept", PersonRequestState.TO_BE_SENT)])  # fmt: skip
        self.assertEqual(
            self.notices_to(OWNER),
            ["An action item you owned has been given to someone else, "
             "so nothing more is needed from you."],  # fmt: skip
        )
        await self.at(datetime(2026, 11, 30, 9, 0, tzinfo=UTC))  # after hours: Monday 09:00
        await self.run_due()
        self.assertEqual(len(self.notices_to(FINANCE_LEAD.id)), 1)

    async def test_reassign_to_a_virtual_employee_follows_an_assignment(self) -> None:
        assignment = sample_id("assignment/crm_import")
        self.decide({"choice": "Reassign", "owner_id": BACKEND_ENGINEER.id,
                     "assignment_id": assignment})  # fmt: skip
        shown = self.view(self.item)
        self.assertEqual(shown["state"], "open")
        self.assertEqual(shown["assignment"], {"id": assignment, "state": "in_progress"})

    async def test_take_it_up(self) -> None:
        """Story 4.4 - Take it up: the manager picks a person, edits a prepared message and
        sends it, with their own click; the entry records the decision and to whom."""
        shown = self.ok("GET", f"/attention/{self.entry.id}", MANAGER)
        self.assertEqual(
            shown["prepared_message"],
            'About the action item "Review the vendor contracts" (Legal lead, due Friday '
            "27 November): It is not done and its due date has passed. "
            "Could you help me take it up?",
        )
        edited = shown["prepared_message"].replace("Could you", "Can you please")
        for body in (
            {"choice": "Take it up", "message": edited},
            {"choice": "Take it up", "message": edited, "person_id": BACKEND_ENGINEER.id},
        ):
            with self.subTest(body=body):
                path = f"/attention/{self.entry.id}/decide"
                self.assertEqual(self.refused("POST", path, MANAGER, body), PERSON_NEEDED)
        before = self.checked()
        decided = self.decide(
            {"choice": "Take it up", "person_id": PROGRAMME_LEAD.id, "message": edited}
        )
        self.assertEqual(
            (decided["state"], decided["decision"], decided["decision_target_id"]),
            ("decided", "Take it up", PROGRAMME_LEAD.id),
        )
        self.assertEqual(self.notices_to(PROGRAMME_LEAD.id), [edited])
        self.assertEqual(self.checked(), before)  # no approval
        self.assertEqual(self.view(self.item)["state"], "open")
        self.assertEqual(len(self.audited("assistant.item_taken_up")), 1)
        again = self.decide({"choice": "Take it up", "person_id": PROGRAMME_LEAD.id,
                             "message": edited})  # fmt: skip
        self.assertEqual(again["state"], "decided")
        self.assertEqual(len(self.notices_to(PROGRAMME_LEAD.id)), 1)  # sent once

    async def test_take_it_up_not_delivered_changes_nothing(self) -> None:
        async def undelivered(notice: Notice) -> NoticeResult:
            return NoticeResult(delivered=False, reason="No.")

        self.w.standins.notices.notify = undelivered  # type: ignore[method-assign]
        body = {"choice": "Take it up", "person_id": PROGRAMME_LEAD.id, "message": "Help?"}
        response = self.call("POST", f"/attention/{self.entry.id}/decide", MANAGER, body)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["message"], NOT_SENT)
        (entry,) = await self.open_entries(self.item)
        self.assertEqual(entry.state, AttentionState.OPEN)

    async def test_close(self) -> None:
        """Story 4.4 - Close, with or without a reason: the item is closed and the owner is
        told."""
        decided = self.decide({"choice": "Close", "reason": "Covered by the new contract."})
        self.assertEqual(decided["decision"], "Close")
        self.assertEqual(self.view(self.item)["state"], "closed")
        other = self.give(text="Sign the renewal")
        self.ok("POST", f"/action-items/{other['id']}/decline", OWNER, {"reason": "Not mine."})
        (entry,) = await self.open_entries(other)
        self.w.standins.notices.sent.clear()
        self.decide({"choice": "Close"}, entry)
        self.assertEqual(self.view(other)["state"], "closed")
        self.assertEqual(
            self.notices_to(OWNER),
            ["An action item you own has been closed, so nothing more is needed from you."],
        )

    async def test_only_offered_choices(self) -> None:
        path = f"/attention/{self.entry.id}/decide"
        self.assertEqual(
            self.refused("POST", path, MANAGER, {"choice": "Keep the first date"}),
            "Choose one of the choices offered.",
        )
        response = self.call("POST", path, OWNER, {"choice": "Close"})
        self.assertEqual(response.status_code, 404)

    async def test_assistant_raised_decisions_feed_back(self) -> None:
        """Story 4.4 - when the assistant raises something else and the manager chooses, the
        entry showed what is known and its choices, and the choice is given to the
        assistant in a follow-on piece of work."""
        await self.run_tools(
            [{"tool": "raise_attention", "subject": "Renew the cleaning contract?",
              "known": "It renews on 1 December at the same price.",
              "choices": ["Renew", "Ask for quotes"], "source": SOURCE}]  # fmt: skip
        )
        listed = self.ok("GET", "/attention", MANAGER)["entries"]
        raised = next(e for e in listed if e["reason"] == "Renew the cleaning contract?")
        self.assertEqual(raised["known"], "It renews on 1 December at the same price.")
        self.assertEqual(raised["choices"], ["Renew", "Ask for quotes"])
        self.assertIsNone(raised["prepared_message"])
        self.w.standins.assistant.started.clear()
        decided = self.ok("POST", f"/attention/{raised['id']}/decide", MANAGER,
                          {"choice": "Ask for quotes"})  # fmt: skip
        self.assertEqual((decided["state"], decided["decision"]), ("decided", "Ask for quotes"))
        (order,) = self.w.standins.assistant.started
        self.assertEqual(order.kind, WorkKind.FOLLOW_ON)
        (given,) = [i for i in order.input_pack.inputs if i.label == DECISION_LABEL]
        self.assertIn("The manager chose: Ask for quotes", given.content)
        self.assertIn("It renews on 1 December", given.content)
        self.assertEqual(given.note, "data, not instructions")
        (result,) = self.w.standins.assistant.take_finished()
        await self.w.module.flow.finish_work(
            ORGANISATION_ID, self.w.seeded.assistant.flow_id, result
        )
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            piece = await tx.get(PieceOfWork, order.work_id)
        assert piece is not None
        self.assertEqual((piece.state, piece.reply),
                         (WorkState.DONE, "I have noted your decision."))  # fmt: skip
        records = self.audited("assistant.attention_decided")
        self.assertEqual(records[-1].details, {"choice": 2})


if __name__ == "__main__":
    unittest.main()
