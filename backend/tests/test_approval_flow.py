"""Story 3.3: approve, edit or decline (task-lLXeNPHg-QA)."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any

from assistant.api.router import PREFIX
from assistant.models.boundary import ApprovalRequest, CarryOutResult, CheckOutcome
from assistant.models.records import (
    ActionItem,
    DraftState,
    PersonRequest,
    PersonRequestState,
    RequestRecord,
    RequestState,
    StatementDraft,
)
from assistant.rules.requests import CANNOT_EDIT, RESUME_FIRST
from assistant.service.approval_levels import LevelChange
from standins.sample import FINANCE_LEAD, LEGAL_LEAD, ORGANISATION_ID, PROGRAMME_LEAD, sample_id
from tests.work_helpers import MANAGER, SOURCE, EpicThree, draft, item
from tests.world import World, build_world, viewer

VENDOR = sample_id("project/vendor_consolidation")
WORDING = "Two contracts are late and legal has not replied."


class ApprovalFlowTest(EpicThree, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        await self.forget_seeded_request()
        self.approval = self.w.standins.approval
        self.carried: list[ApprovalRequest] = []
        real = self.approval.carry_out

        async def counting(request: ApprovalRequest) -> CarryOutResult:
            self.carried.append(request)
            return await real(request)

        self.approval.carry_out = counting  # type: ignore[method-assign]

    async def asyncTearDown(self) -> None:
        self.w.close()

    def _post(self, path: str, body: Any = None) -> Any:
        return self.w.call("POST", f"{PREFIX}{path}", MANAGER, body)

    async def _items(self) -> list[ActionItem]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            found = await tx.find(ActionItem, assistant_id=self.w.seeded.assistant.id)
        return [i for i in found if i.id != self.w.seeded.item.id]

    async def _set_level(self, kind: str, level: str) -> None:
        await self.w.module.services.levels.change(
            viewer(MANAGER), kind, LevelChange.model_validate({"level": level})
        )

    async def test_flow_check_routes_the_request(self) -> None:
        """Story 3.3 - Flow's check routes each request: carried out, listed under Needs
        your approval, or declined with Flow's reason."""
        await self._set_level("send_message", "without_approval")
        await self._set_level("ask_owner", "not_allowed")
        await self.run_tools(
            [
                item(),  # with approval: waits for the manager
                draft(),  # without approval, inside the limits: carried out
                {"tool": "ask_for_update", "project": VENDOR, "owner": "Legal lead",
                 "question": "Where are we?"},  # not allowed: declined  # fmt: skip
            ]
        )
        waiting, sent, asked = await self.requests()
        self.assertEqual(waiting.state, RequestState.WAITING)
        self.assertEqual([r["id"] for r in self.waiting()["requests"]], [str(waiting.id)])
        self.assertEqual((sent.state, sent.allowed_by), (RequestState.DONE, "without_approval"))
        self.assertEqual([c.request_id for c in self.carried], [sent.id])
        self.assertEqual(self.carried[0].text, "Please send the figures.")
        self.assertEqual(
            (asked.state, asked.declined_by, asked.reason),
            (RequestState.DECLINED, "flow", "This kind of action is not allowed."),
        )
        checked = [r for r, _ in self.approval.checked]
        self.assertEqual([c.level.value for c in checked],
                         ["with_approval", "without_approval", "not_allowed"])  # fmt: skip
        self.assertTrue(all(c.limits is not None and c.count_today >= 0 for c in checked))

    async def test_flow_may_be_stricter_than_the_levels(self) -> None:
        async def stricter(request: ApprovalRequest) -> CheckOutcome:
            return CheckOutcome(outcome="declined", reason="The budget for messages is spent.")

        self.approval.check = stricter  # type: ignore[method-assign]
        await self._set_level("send_message", "without_approval")
        await self.run_tools([draft()])
        (request,) = await self.requests()
        self.assertEqual(
            (request.state, request.reason),
            (RequestState.DECLINED, "The budget for messages is spent."),
        )
        self.assertEqual(self.carried, [])

    async def test_todays_count_is_sent_with_each_check(self) -> None:
        await self._set_level("send_message", "without_approval")
        await self.run_tools([draft(text="One"), draft(text="Two")])
        counts = [r.count_today for r, _ in self.approval.checked]
        self.assertEqual(counts, [0, 1])

    async def test_approve_carries_out_once(self) -> None:
        """Story 3.3 - approving carries the request out exactly once, even when pressed
        twice or in two windows."""
        await self.run_tools([item()])
        (request,) = await self.requests()
        first = self._post(f"/requests/{request.id}/approve")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual((first.json()["state"], first.json()["approved_by"]), ("done", "manager"))
        second = self._post(f"/requests/{request.id}/approve")
        self.assertEqual((second.status_code, second.json()["state"]), (200, "done"))
        self.assertEqual(len(self.carried), 1)
        (added,) = await self._items()
        self.assertEqual((added.owner_id, added.origin), (LEGAL_LEAD.id, "chat"))
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            asks = await tx.find(PersonRequest, subject_id=str(added.id))
        self.assertEqual([(a.asks, a.state) for a in asks], [("accept", PersonRequestState.SENT)])

    async def test_approve_in_two_windows_at_once_carries_out_once(self) -> None:
        await self.run_tools([item()])
        (request,) = await self.requests()
        services = self.w.module.services.requests
        outcomes = await asyncio.gather(
            *(services.approve(viewer(MANAGER), str(request.id)) for _ in range(4)),
            return_exceptions=True,
        )
        self.assertTrue(any(isinstance(o, dict) and o["state"] == "done" for o in outcomes))
        self.assertEqual(len(self.carried), 1)
        self.assertEqual(len(await self._items()), 1)

    async def test_edited_text_is_used(self) -> None:
        """Story 3.3 - when the manager edits a request and approves it, the edited text is
        carried out and both texts are kept."""
        await self.run_tools([draft(text="Send the figures."), item()])
        message, proposed = await self.requests()
        edited = self._post(
            f"/requests/{message.id}/approve", {"text": "Please send the Q3 figures today."}
        ).json()
        self.assertEqual(
            (edited["original_text"], edited["edited_text"], edited["text"]),
            ("Send the figures.", "Please send the Q3 figures today.",
             "Please send the Q3 figures today."),
        )  # fmt: skip
        self.assertEqual(self.carried[-1].text, "Please send the Q3 figures today.")
        self._post(f"/requests/{proposed.id}/approve", {"text": "Finish the review by noon"})
        (added,) = await self._items()
        self.assertEqual(added.text, "Finish the review by noon")
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            kept = await tx.get(RequestRecord, proposed.id)
        assert kept is not None
        self.assertEqual(
            (kept.text, kept.edited_text),
            ("Finish the contract review", "Finish the review by noon"),
        )

    async def test_approved_item_change_touches_text_and_follow_ups_only(self) -> None:
        seeded = self.w.seeded.item
        times = ["2026-11-25T09:00:00+00:00", "2026-11-26T09:00:00+00:00"]
        await self.run_tools(
            [item(item=str(seeded.id), text="Finish the review and sign it",
                  due_date=seeded.due_date.isoformat(), follow_up_times=times)]  # fmt: skip
        )
        (change,) = await self.requests()
        done = self._post(f"/requests/{change.id}/approve").json()
        self.assertEqual((done["kind"], done["state"]), ("action_item_change", "done"))
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            changed = await tx.get(ActionItem, seeded.id)
        assert changed is not None
        self.assertEqual(changed.text, "Finish the review and sign it")
        self.assertEqual([t.isoformat() for t in changed.follow_up_times], times)
        self.assertEqual((changed.due_date, changed.owner_id), (seeded.due_date, seeded.owner_id))
        self.assertEqual(changed.history[-1].event, "changed")
        self.assertFalse(done["can_undo"])

    async def test_an_ask_about_a_project_is_not_withdrawn_by_itself(self) -> None:
        await self.run_tools(
            [{"tool": "ask_for_update", "project": VENDOR, "owner": "Legal lead",
              "question": "Where are we?"}]  # fmt: skip
        )
        await self.w.module.flow.run_due()
        (ask,) = self.waiting()["requests"]
        self.assertEqual(ask["project"], {"id": VENDOR, "name": "Vendor consolidation"})
        done = self._post(f"/requests/{ask['id']}/approve").json()
        self.assertEqual(done["state"], "done")
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            (asked,) = await tx.find(PersonRequest, subject_id=VENDOR)
        self.assertEqual((asked.asks, asked.person_id), ("status", LEGAL_LEAD.id))

    async def test_edited_text_over_its_limit_is_refused(self) -> None:
        await self.run_tools([item()])
        (request,) = await self.requests()
        response = self._post(f"/requests/{request.id}/approve", {"text": "x" * 301})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"]["message"], "The text can be at most 300 characters."
        )
        self.assertEqual(self.carried, [])

    async def test_decline_carries_out_nothing(self) -> None:
        """Story 3.3 - when the manager declines nothing is carried out, and the reason is
        kept if given."""
        await self.run_tools([item(), draft()])
        with_reason, without = await self.requests()
        one = self._post(f"/requests/{with_reason.id}/decline", {"reason": "Not this week."})
        two = self._post(f"/requests/{without.id}/decline")
        self.assertEqual(
            (one.json()["state"], one.json()["reason"], one.json()["declined_by"]),
            ("declined", "Not this week.", "manager"),
        )
        self.assertEqual((two.json()["state"], two.json()["reason"]), ("declined", None))
        self.assertEqual(self.carried, [])
        self.assertEqual(await self._items(), [])
        self.assertEqual(self.waiting()["count"], 0)
        late = self._post(f"/requests/{with_reason.id}/approve")
        self.assertEqual(late.status_code, 409)
        self.assertEqual(self.carried, [])
        names = [r.name for r in self.w.standins.records.written]
        self.assertIn("assistant.request_declined", names)
        for record in self.w.standins.records.written:
            self.assertNotIn("Not this week.", str(record.model_dump()))  # BR-10

    async def test_decline_reason_over_500_characters_refused(self) -> None:
        await self.run_tools([item()])
        (request,) = await self.requests()
        response = self._post(f"/requests/{request.id}/decline", {"reason": "r" * 501})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"]["message"], "A reason can be at most 500 characters."
        )

    async def test_confirm_statement_requests_hide_wording(self) -> None:
        """Story 3.3 - a request asking an owner to confirm a statement shows the owner and
        project without the wording, and can be approved or declined but not edited."""
        await self.run_tools(
            [{"tool": "status_statement", "project": VENDOR, "reason": WORDING,
              "what_changed": "Legal is behind.", "what_comes_next": "Chase legal.",
              "source": SOURCE}],  # fmt: skip
            text="Ask the Legal lead to confirm the vendor status",
        )
        listing = self.w.call("GET", f"{PREFIX}/requests", MANAGER)
        (shown,) = listing.json()["requests"]
        self.assertEqual(shown["kind"], "statement_confirm")
        self.assertEqual(shown["target"], {"id": LEGAL_LEAD.id, "display_name": "Legal lead"})
        self.assertEqual(shown["project"], {"id": VENDOR, "name": "Vendor consolidation"})
        self.assertIsNone(shown["text"])
        self.assertFalse(shown["editable"])
        for hidden in (WORDING, "Legal is behind.", "Chase legal."):
            self.assertNotIn(hidden, listing.text)
            self.assertNotIn(hidden, self.w.call("GET", f"{PREFIX}/requests/{shown['id']}",
                                                 MANAGER).text)  # fmt: skip
        refused = self._post(f"/requests/{shown['id']}/approve", {"text": "Other words"})
        self.assertEqual(refused.status_code, 422)
        self.assertEqual(refused.json()["detail"]["message"], CANNOT_EDIT)

        approved = self._post(f"/requests/{shown['id']}/approve")
        self.assertEqual(approved.json()["state"], "done")
        self.assertNotIn(WORDING, approved.text)
        self.assertIsNone(self.carried[-1].text)  # the wording never goes to Flow's check
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            (statement,) = await tx.find(StatementDraft, assistant_id=self.w.seeded.assistant.id)
            asks = await tx.find(PersonRequest, subject_id=str(statement.id))
        self.assertEqual(statement.state, DraftState.WITH_OWNER)
        self.assertEqual([(a.asks, a.person_id) for a in asks], [("confirm", LEGAL_LEAD.id)])

    async def test_confirm_statement_request_can_be_declined(self) -> None:
        await self.run_tools(
            [{"tool": "status_statement", "project": VENDOR, "reason": WORDING,
              "source": SOURCE}]  # fmt: skip
        )
        (request,) = await self.requests()
        declined = self._post(f"/requests/{request.id}/decline").json()
        self.assertEqual(declined["state"], "declined")
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            (statement,) = await tx.find(StatementDraft, assistant_id=self.w.seeded.assistant.id)
        self.assertEqual(statement.state, DraftState.DRAFT)  # kept for when they are next asked

    async def test_scheduled_drafts_wait_for_the_next_ask(self) -> None:
        """Section 11: a statement drafted on a schedule is kept for its confirmer and not
        asked of them until they are next asked."""
        from assistant.models.boundary import WorkKind, WorkResult
        from assistant.models.records import ConfirmedStatement, FollowedProject, StatementWording
        from assistant.service.actions import perform

        services = self.w.module.services
        ops = self.w.seeded.assistant
        mine = {"organisation_id": ORGANISATION_ID, "assistant_id": ops.id,
                "created_at": await self.w.standins.clock.now()}  # fmt: skip
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            await tx.add(FollowedProject(**mine, project_id=VENDOR, confirmer_id=LEGAL_LEAD.id))
            await tx.add(
                ConfirmedStatement(
                    **mine, project_id=VENDOR, owner_id=LEGAL_LEAD.id, colour="green",
                    wording=StatementWording(reason="On plan."), source="Legal lead",
                    author_id=LEGAL_LEAD.id, mark="confirmed",
                )
            )  # fmt: skip

        async def ask(action: Any) -> Any:
            return await services.work.enqueue(action, ops, WorkKind.STATUS_DRAFTS)

        piece = await perform(self.w.module.boundary, self.w.store, ORGANISATION_ID, "flow", ask)
        await services.work.advance(ops)
        self.w.standins.assistant.take_finished()
        await self.w.module.flow.finish_work(
            ORGANISATION_ID,
            ops.flow_id,
            WorkResult(
                work_id=piece.id,
                tool_requests=[{"tool": "status_statement", "project": VENDOR,
                                "reason": WORDING,
                                "source": "Confirmed status: Vendor consolidation"}],  # fmt: skip
            ),
        )
        self.assertEqual(self.work_view(piece.id)["refused"], [])
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            (statement,) = await tx.find(StatementDraft, assistant_id=ops.id)
        self.assertEqual(statement.state, DraftState.DRAFT)
        self.assertEqual(await self.requests(), [])

    async def test_daily_limit_holds_within_one_result(self) -> None:
        await self.w.module.services.levels.change(
            viewer(MANAGER),
            "send_message",
            LevelChange.model_validate({"level": "without_approval", "limits": {"per_day": 1}}),
        )
        await self.run_tools([draft(text="One"), draft(text="Two")])
        first, second = await self.requests()
        self.assertEqual((first.state, second.state), (RequestState.DONE, RequestState.WAITING))
        self.assertEqual(len(self.carried), 1)

    async def test_approving_while_paused_is_refused(self) -> None:
        await self.run_tools([item()])
        (request,) = await self.requests()
        self._post("/assistant/pause")
        response = self._post(f"/requests/{request.id}/approve")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["message"], RESUME_FIRST)
        self.assertEqual(self.carried, [])

    async def test_requests_allowed_while_paused_wait_until_resume(self) -> None:
        await self.run_tools([item()])
        (request,) = await self.requests()
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            stored = await tx.get(RequestRecord, request.id)
            assert stored is not None
            await tx.change(stored.evolve(state=RequestState.APPROVED, allowed_by="manager"))
        self._post("/assistant/pause")
        await self.w.module.services.requests.carry_out_approved(self.w.seeded.assistant)
        self.assertEqual(self.carried, [])
        self._post("/assistant/resume")
        self.assertEqual(len(self.carried), 1)

    async def _assert_waiting_requests_removed(self) -> None:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            left = await tx.find(RequestRecord, assistant_id=self.w.seeded.assistant.id)
        self.assertEqual([r.state for r in left], [RequestState.DONE])

    async def _requests_in_every_open_state(self) -> None:
        await self._set_level("send_message", "without_approval")
        await self.run_tools([item(), draft(), item(owner="Finance lead")])
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            (third,) = await tx.find(
                RequestRecord, target_id=FINANCE_LEAD.id, request_kind="action_item"
            )
            await tx.change(third.evolve(state=RequestState.APPROVED, allowed_by="manager"))

    async def test_end_removes_waiting_requests(self) -> None:
        """Story 3.3 - when the assistant is ended its waiting requests are removed."""
        await self._requests_in_every_open_state()
        await self.w.module.flow.end(ORGANISATION_ID, self.w.seeded.assistant.flow_id)
        await self._assert_waiting_requests_removed()

    async def test_hand_over_removes_waiting_requests(self) -> None:
        """Story 3.3 - when the assistant is handed over its waiting requests are removed."""
        await self._requests_in_every_open_state()
        await self.w.module.flow.hand_over(
            ORGANISATION_ID, self.w.seeded.assistant.flow_id, PROGRAMME_LEAD.id
        )
        await self._assert_waiting_requests_removed()
        self.w.call("POST", f"{PREFIX}/preferences/skip", PROGRAMME_LEAD.id)
        listing = self.w.call("GET", f"{PREFIX}/requests", PROGRAMME_LEAD.id).json()
        self.assertEqual(listing, {"count": 0, "requests": []})


if __name__ == "__main__":
    unittest.main()
