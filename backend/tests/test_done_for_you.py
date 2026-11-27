"""Story 3.4: what was done, "Try again" and Undo (task-vyG8VcQAxHo)."""

from __future__ import annotations

import unittest
from datetime import timedelta
from typing import Any
from uuid import UUID

from assistant.api.router import PREFIX
from assistant.models.boundary import ApprovalRequest, CarryOutResult, UndoResult
from assistant.models.records import (
    ActionItem,
    ItemState,
    PersonRequest,
    PersonRequestState,
    RequestRecord,
)
from assistant.rules.requests import CANNOT_UNDO, NOT_REACHED, NOT_RETRYABLE
from assistant.service.approval_levels import LevelChange
from standins.sample import ORGANISATION_ID
from tests.work_helpers import MANAGER, EpicThree, draft, item
from tests.world import World, build_world, viewer


class DoneForYouTest(EpicThree, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        await self.forget_seeded_request()
        self.approval = self.w.standins.approval
        self.clock = self.w.standins.clock

    async def asyncTearDown(self) -> None:
        self.w.close()

    def _post(self, path: str, body: Any = None) -> Any:
        return self.w.call("POST", f"{PREFIX}{path}", MANAGER, body)

    def _done(self) -> Any:
        response = self.w.call("GET", f"{PREFIX}/done", MANAGER)
        assert response.status_code == 200, response.text
        return response.json()

    async def _items(self) -> list[ActionItem]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            found = await tx.find(ActionItem, assistant_id=self.w.seeded.assistant.id)
        return [i for i in found if i.id != self.w.seeded.item.id]

    async def _proposed_item(self) -> str:
        await self.run_tools([item()])
        (request,) = [r for r in await self.requests() if r.request_kind == "action_item"]
        return str(request.id)

    async def test_done_items_listed_newest_first(self) -> None:
        """Story 3.4 - a request that was carried out is listed under Done for you with its
        effect, time, and whether the manager approved it or it needed no approval, newest
        first."""
        await self.w.module.services.levels.change(
            viewer(MANAGER),
            "send_message",
            LevelChange.model_validate({"level": "without_approval"}),
        )
        await self.run_tools([draft()])  # carried out at once
        first_at = await self.clock.now()
        await self.clock.forward(timedelta(minutes=30))
        request_id = await self._proposed_item()
        self._post(f"/requests/{request_id}/approve")
        second_at = await self.clock.now()

        listed = self._done()
        self.assertEqual(listed["count"], 2)
        newest, oldest = listed["requests"]
        self.assertEqual(
            (newest["kind"], newest["approved_by"], newest["state"]),
            ("action_item", "manager", "done"),
        )
        self.assertEqual(
            (oldest["kind"], oldest["approved_by"], oldest["state"]),
            ("message", "no_approval_needed", "done"),
        )
        self.assertEqual(newest["done_at"], second_at.isoformat())
        self.assertEqual(oldest["done_at"], first_at.isoformat())
        self.assertEqual(
            newest["effect"], "Add an action item for the Legal lead, due Friday 27 November."
        )
        self.assertEqual(oldest["effect"], "Send a message to the Finance lead.")

    async def test_waiting_and_declined_requests_are_not_done(self) -> None:
        await self.run_tools([draft(), item()])
        message, _ = await self.requests()
        self._post(f"/requests/{message.id}/decline")
        self.assertEqual(self._done(), {"count": 0, "requests": []})

    async def test_failure_offers_retry(self) -> None:
        """Story 3.4 - when carrying out fails the reason is shown with "Try again"."""
        request_id = await self._proposed_item()
        self.approval.failing = True
        failed = self._post(f"/requests/{request_id}/approve").json()
        self.assertEqual(
            (failed["state"], failed["reason"], failed["can_retry"]),
            ("failed", "Flow could not carry this out.", True),
        )
        self.assertEqual(await self._items(), [])  # the module's own part was rolled back
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            self.assertEqual(
                [a for a in await tx.find(PersonRequest) if a.asks == "accept"
                 and a.id != self.w.seeded.person_request.id], []
            )  # fmt: skip
        (listed,) = self._done()["requests"]
        self.assertEqual((listed["state"], listed["can_retry"]), ("failed", True))
        self.assertEqual(listed["reason"], "Flow could not carry this out.")

        self.approval.failing = False
        retried = self._post(f"/requests/{request_id}/retry").json()
        self.assertEqual((retried["state"], retried["can_retry"], retried["reason"]),
                         ("done", False, None))  # fmt: skip
        self.assertEqual(len(await self._items()), 1)
        again = self._post(f"/requests/{request_id}/retry")
        self.assertEqual(again.status_code, 422)
        self.assertEqual(again.json()["detail"]["message"], NOT_RETRYABLE)

    async def test_flow_out_of_reach_offers_retry(self) -> None:
        request_id = await self._proposed_item()

        async def down(request: ApprovalRequest) -> CarryOutResult:
            raise ConnectionError("down")

        real = self.approval.carry_out
        self.approval.carry_out = down  # type: ignore[method-assign]
        failed = self._post(f"/requests/{request_id}/approve").json()
        self.assertEqual((failed["state"], failed["reason"]), ("failed", NOT_REACHED))
        self.approval.carry_out = real  # type: ignore[method-assign]
        self.assertEqual(self._post(f"/requests/{request_id}/retry").json()["state"], "done")

    async def test_undo_where_possible(self) -> None:
        """Story 3.4 - when something can be undone, Undo is offered and its result is
        listed."""
        request_id = await self._proposed_item()
        done = self._post(f"/requests/{request_id}/approve").json()
        self.assertTrue(done["can_undo"])
        await self.clock.forward(timedelta(minutes=5))
        undone = self._post(f"/requests/{request_id}/undo").json()
        self.assertEqual((undone["state"], undone["can_undo"]), ("undone", False))
        self.assertEqual(undone["undone_at"], (await self.clock.now()).isoformat())
        (listed,) = self._done()["requests"]
        self.assertEqual((listed["state"], listed["undone_at"]), ("undone", undone["undone_at"]))
        (added,) = await self._items()
        self.assertEqual(added.state, ItemState.CLOSED)
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            (ask,) = await tx.find(PersonRequest, subject_id=str(added.id))
        self.assertEqual(ask.state, PersonRequestState.WITHDRAWN)
        self.assertEqual(len(self.approval.undone), 1)
        twice = self._post(f"/requests/{request_id}/undo").json()
        self.assertEqual(twice["state"], "undone")
        self.assertEqual(len(self.approval.undone), 1)

    async def test_what_cannot_be_undone_offers_no_undo(self) -> None:
        await self.run_tools([draft()])
        (message,) = await self.requests()
        done = self._post(f"/requests/{message.id}/approve").json()
        self.assertFalse(done["can_undo"])  # Flow says a message cannot be undone
        response = self._post(f"/requests/{message.id}/undo")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["message"], CANNOT_UNDO)

    async def test_an_undo_flow_refuses_is_listed_with_its_reason(self) -> None:
        request_id = await self._proposed_item()
        self._post(f"/requests/{request_id}/approve")

        async def refuse(request: ApprovalRequest) -> UndoResult:
            return UndoResult(done=False, reason="The notice has already been read.")

        self.approval.undo = refuse  # type: ignore[method-assign]
        result = self._post(f"/requests/{request_id}/undo").json()
        self.assertEqual(
            (result["state"], result["undo_reason"]), ("done", "The notice has already been read.")
        )
        (added,) = await self._items()
        self.assertEqual(added.state, ItemState.WAITING_FOR_OWNER)  # nothing was undone

    async def test_item_undo_window(self) -> None:
        """Story 3.4 - once an action item's owner has answered, Undo is no longer offered."""
        request_id = await self._proposed_item()
        self.assertTrue(self._post(f"/requests/{request_id}/approve").json()["can_undo"])
        (added,) = await self._items()
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            (ask,) = await tx.find(PersonRequest, subject_id=str(added.id))
            await tx.change(ask.evolve(state=PersonRequestState.ANSWERED))
            current = await tx.get(ActionItem, added.id)
            assert current is not None
            await tx.change(current.evolve(state=ItemState.OPEN))  # the owner accepted
        (listed,) = self._done()["requests"]
        self.assertFalse(listed["can_undo"])
        response = self._post(f"/requests/{request_id}/undo")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["message"], CANNOT_UNDO)
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            kept = await tx.get(ActionItem, added.id)
        assert kept is not None
        self.assertEqual(kept.state, ItemState.OPEN)

    async def test_undoing_a_confirm_request_takes_the_draft_back(self) -> None:
        from assistant.models.records import DraftState, StatementDraft
        from standins.sample import sample_id

        await self.run_tools(
            [{"tool": "status_statement", "project": sample_id("project/vendor_consolidation"),
              "reason": "Contracts are late.", "source": "Message from the manager"}]  # fmt: skip
        )
        (request,) = await self.requests()
        self._post(f"/requests/{request.id}/approve")
        undone = self._post(f"/requests/{request.id}/undo").json()
        self.assertEqual(undone["state"], "undone")
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            (statement,) = await tx.find(StatementDraft, assistant_id=self.w.seeded.assistant.id)
            (ask,) = await tx.find(PersonRequest, subject_id=str(statement.id))
        self.assertEqual(statement.state, DraftState.DRAFT)
        self.assertEqual(ask.state, PersonRequestState.WITHDRAWN)

    async def test_an_answered_ask_for_an_update_cannot_be_undone(self) -> None:
        await self.run_tools(
            [{"tool": "ask_for_update", "item": str(self.w.seeded.item.id),
              "owner": "Legal lead", "question": "On track?"}]  # fmt: skip
        )
        (request,) = await self.requests()
        self.assertTrue(self._post(f"/requests/{request.id}/approve").json()["can_undo"])
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            stored = await tx.get(RequestRecord, request.id)
            assert stored is not None
            ask = await tx.get(PersonRequest, UUID(stored.payload["ask_id"]))
            assert ask is not None
            await tx.change(ask.evolve(state=PersonRequestState.ANSWERED))
        self.assertFalse(self._done()["requests"][0]["can_undo"])


if __name__ == "__main__":
    unittest.main()
