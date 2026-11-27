"""Story 3.3: "Approve all" and withdrawal of stale requests (task-4RHEHf-dsf8)."""

from __future__ import annotations

import unittest
from typing import Any

from assistant.api.router import PREFIX
from assistant.models.records import ActionItem, ItemState, RequestRecord, RequestState
from assistant.rules.requests import RESUME_FIRST, WITHDRAWN
from assistant.service.actions import perform
from standins.sample import ORGANISATION_ID, sample_id
from tests.work_helpers import MANAGER, SOURCE, EpicThree, draft, item
from tests.world import World, build_world

VENDOR = sample_id("project/vendor_consolidation")
CRM = sample_id("project/crm_rollout")


def ask_about_item(item_id: str) -> dict[str, Any]:
    return {"tool": "ask_for_update", "item": item_id, "owner": "Legal lead",
            "question": "Is the review on track?"}  # fmt: skip


def statement(project: str, reason: str) -> dict[str, Any]:
    return {"tool": "status_statement", "project": project, "reason": reason, "source": SOURCE}


class ApproveAllAndWithdrawalTest(EpicThree, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        await self.forget_seeded_request()
        self.item_id = str(self.w.seeded.item.id)

    async def asyncTearDown(self) -> None:
        self.w.close()

    def _post(self, path: str, body: Any = None) -> Any:
        return self.w.call("POST", f"{PREFIX}{path}", MANAGER, body)

    async def _states(self) -> dict[str, RequestState]:
        return {r.request_kind: r.state for r in await self.requests()}

    async def _change_item(self, **changes: Any) -> None:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            current = await tx.get(ActionItem, self.w.seeded.item.id)
            assert current is not None
            await tx.change(current.evolve(**changes))

    async def test_approve_all_covers_owner_requests_only(self) -> None:
        """Story 3.3 - "Approve all" approves the requests to owners and nothing else."""
        await self.run_tools(
            [
                ask_about_item(self.item_id),
                statement(VENDOR, "Contracts are late."),
                draft(),
                item(),
            ]
        )
        self.assertEqual(self.waiting()["count"], 4)
        response = self._post("/requests/approve-all")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["approved"], 2)
        self.assertEqual(sorted(r["kind"] for r in body["requests"]), ["action_item", "message"])
        self.assertEqual(
            await self._states(),
            {
                "update_ask": RequestState.DONE,
                "statement_confirm": RequestState.DONE,
                "message": RequestState.WAITING,
                "action_item": RequestState.WAITING,
            },
        )
        for request in await self.requests():
            if request.state is RequestState.DONE:
                self.assertEqual(request.allowed_by, "manager")
        self.assertEqual(len(self.w.standins.approval.carried_out), 2)

    async def test_approve_all_with_nothing_to_owners_changes_nothing(self) -> None:
        await self.run_tools([draft()])
        body = self._post("/requests/approve-all").json()
        self.assertEqual((body["approved"], body["count"]), (0, 1))
        self.assertEqual(self.w.standins.approval.carried_out, {})

    async def test_approve_all_while_paused_is_refused(self) -> None:
        await self.run_tools([ask_about_item(self.item_id)])
        self._post("/assistant/pause")
        response = self._post("/requests/approve-all")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["message"], RESUME_FIRST)

    async def _assert_withdrawn_after(self, **item_changes: Any) -> None:
        await self.run_tools(
            [ask_about_item(self.item_id), item(item=self.item_id, due_date="2026-11-27")]
        )
        await self._change_item(**item_changes)
        self.assertEqual(self.waiting()["count"], 0)  # no longer offered
        await self.w.module.flow.run_due()
        self.assertEqual(
            await self._states(),
            {"update_ask": RequestState.WITHDRAWN, "action_item_change": RequestState.WITHDRAWN},
        )
        names = [r.name for r in self.w.standins.records.written]
        self.assertEqual(names.count("assistant.request_withdrawn"), 2)

    async def test_stale_requests_withdrawn_when_the_item_is_done(self) -> None:
        """Story 3.3 - when the subject of a waiting request is done, it is withdrawn."""
        await self._assert_withdrawn_after(state=ItemState.DONE)

    async def test_stale_requests_withdrawn_when_the_item_is_closed(self) -> None:
        """Story 3.3 - when the subject of a waiting request is closed, it is withdrawn."""
        await self._assert_withdrawn_after(state=ItemState.CLOSED)

    async def test_stale_requests_withdrawn_when_the_item_changed(self) -> None:
        """Story 3.3 - when the subject of a waiting request is changed, it is withdrawn."""
        await self._assert_withdrawn_after(text="Finish the review and sign it")

    async def test_approving_a_stale_request_withdraws_it(self) -> None:
        await self.run_tools([ask_about_item(self.item_id)])
        (request,) = await self.requests()
        await self._change_item(state=ItemState.CLOSED)
        response = self._post(f"/requests/{request.id}/approve")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["message"], WITHDRAWN)
        self.assertEqual(await self._states(), {"update_ask": RequestState.WITHDRAWN})
        self.assertEqual(self.w.standins.approval.carried_out, {})

    async def test_a_newer_draft_withdraws_the_older_confirm_request(self) -> None:
        await self.run_tools([statement(VENDOR, "Contracts are late.")])
        await self.run_tools([statement(VENDOR, "Contracts are signed.")])
        await self.w.module.flow.run_due()
        states = [(r.request_kind, r.state) for r in await self.requests()]
        self.assertEqual(
            states,
            [("statement_confirm", RequestState.WITHDRAWN),
             ("statement_confirm", RequestState.WAITING)],  # fmt: skip
        )

    async def test_services_withdraw_requests_about_a_subject_at_once(self) -> None:
        await self.run_tools([ask_about_item(self.item_id), draft()])
        services = self.w.module.services.requests

        async def owner_answered(action: Any) -> int:
            return await services.withdraw_for_subject(action, "action_item", self.item_id)

        withdrawn = await perform(
            self.w.module.boundary, self.w.store, ORGANISATION_ID, MANAGER, owner_answered
        )
        self.assertEqual(withdrawn, 1)
        self.assertEqual(
            await self._states(),
            {"update_ask": RequestState.WITHDRAWN, "message": RequestState.WAITING},
        )

    async def test_requests_about_unchanged_subjects_stay(self) -> None:
        await self.run_tools([ask_about_item(self.item_id), statement(CRM, "On plan.")])
        await self.w.module.flow.run_due()
        self.assertTrue(all(r.state is RequestState.WAITING for r in await self.requests()))
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            self.assertEqual(len(await tx.find(RequestRecord, state=RequestState.WAITING)), 2)


if __name__ == "__main__":
    unittest.main()
