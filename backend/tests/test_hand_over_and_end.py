"""Story 2.4: hand-over and end (and Story 2.1: no hand-over to a manager who has one)."""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

from assistant.api.router import PREFIX
from assistant.errors import ModuleError, NotAllowedValue
from assistant.models.boundary import ActionKind, WorkResult
from assistant.models.preferences import PreferencesForm
from assistant.models.records import (
    ActionItem,
    AssistantRecord,
    AssistantState,
    AttentionEntry,
    AttentionState,
    ChatMessage,
    ConfirmedStatement,
    FollowedProject,
    ItemState,
    KeptResult,
    PersonRequest,
    PersonRequestState,
    PieceOfWork,
    Preferences,
    Record,
    Report,
    RequestRecord,
    RequestState,
    StatementDraft,
    StatementWording,
    WorkKind,
    WorkState,
)
from assistant.service.approval_levels import LevelChange
from assistant.service.closing import CLOSING_HANDLERS, Closing, ClosingRegistry
from assistant.service.setup import ALREADY_HAS_ONE
from standins.sample import (
    BACKEND_ENGINEER,
    FINANCE_LEAD,
    LEGAL_LEAD,
    OPERATIONS_DIRECTOR,
    ORGANISATION_ID,
    PROGRAMME_LEAD,
    SALES_DIRECTOR,
)
from tests.world import World, build_world, viewer

MANAGER = OPERATIONS_DIRECTOR.id
NEW_MANAGER = PROGRAMME_LEAD.id
PERSON_WITHOUT_ASSISTANT = FINANCE_LEAD.id  # a person without an assistant of their own


class HandOverAndEndTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        self.flow = self.w.module.flow
        self.ops = self.w.seeded.assistant
        self.t = await self.w.standins.clock.now()
        await self._seed_more()

    async def asyncTearDown(self) -> None:
        self.w.close()

    async def _seed_more(self) -> None:
        """Everything an assistant collects: some to close, some to remove, some to keep."""
        await self.w.module.services.setup.save(
            viewer(MANAGER), PreferencesForm.model_validate({"tone": {"sign_off": "Thanks"}})
        )
        await self.w.module.services.levels.change(
            viewer(MANAGER),
            "send_message",
            LevelChange.model_validate({"level": "without_approval"}),
        )
        mine: dict[str, Any] = {
            "organisation_id": ORGANISATION_ID,
            "assistant_id": self.ops.id,
            "created_at": self.t,
        }

        def item(owner: str, state: ItemState) -> ActionItem:
            return ActionItem(
                **mine, text="t", owner_id=owner, due_date=date(2026, 12, 4), origin="manager",
                state=state,
            )  # fmt: skip

        wording = StatementWording(reason="On plan.")
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            self.open_finance = await tx.add(item(FINANCE_LEAD.id, ItemState.OPEN))
            self.done_item = await tx.add(item(LEGAL_LEAD.id, ItemState.DONE))
            self.declined = await tx.add(item(FINANCE_LEAD.id, ItemState.DECLINED))
            self.virtual = await tx.add(item(BACKEND_ENGINEER.id, ItemState.OPEN))
            seeded_ask = self.w.seeded.person_request
            self.sent_ask = await tx.change(seeded_ask.evolve(state=PersonRequestState.SENT))
            self.answered_ask = await tx.add(
                PersonRequest(
                    **mine, person_id=FINANCE_LEAD.id, asks="update", subject_kind="action_item",
                    subject_id=str(self.open_finance.id), answer_by=self.t + timedelta(days=1),
                    state=PersonRequestState.ANSWERED,
                )
            )  # fmt: skip
            self.followed = await tx.add(
                FollowedProject(**mine, project_id="crm", confirmer_id=PROGRAMME_LEAD.id)
            )
            self.chat = await tx.add(ChatMessage(**mine, author="manager", text="Hello"))
            self.waiting = await tx.add(
                RequestRecord(**mine, action_kind=ActionKind.SEND_MESSAGE, subject_kind="message")
            )
            self.done_request = await tx.add(
                RequestRecord(
                    **mine, action_kind=ActionKind.SEND_MESSAGE, subject_kind="message",
                    state=RequestState.DONE,
                )
            )  # fmt: skip
            self.draft = await tx.add(
                StatementDraft(
                    **mine, project_id="crm", owner_id=PROGRAMME_LEAD.id, wording=wording,
                    source="Flow project: CRM rollout", author_id="flow-assistant-ops",
                )
            )  # fmt: skip
            self.report = await tx.add(Report(**mine))
            self.attention = await tx.add(
                AttentionEntry(
                    **mine, subject_kind="action_item", reason="Late", choices=["a", "b"]
                )
            )
            self.running_work = await tx.add(
                PieceOfWork(**mine, work_kind=WorkKind.REQUEST, state=WorkState.RUNNING)
            )
        await self.w.module.services.control.pause(viewer(MANAGER))
        await self.flow.finish_work(
            ORGANISATION_ID, self.ops.flow_id, WorkResult(work_id=uuid4(), reply="Kept.")
        )

    async def _get(self, record: Record) -> Any:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return await tx.get(type(record), record.id)

    async def _all(self, record_type: type[Record]) -> list[Any]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return await tx.find(record_type, assistant_id=self.ops.id)

    def _told(self) -> list[str]:
        return [n.person_id for n in self.w.standins.notices.sent]

    async def _assert_open_work_closed_and_owners_told_once(self) -> None:
        for record in (self.w.seeded.item, self.open_finance, self.declined, self.virtual):
            self.assertEqual((await self._get(record)).state, ItemState.CLOSED)
        self.assertEqual((await self._get(self.done_item)).state, ItemState.DONE)
        self.assertEqual((await self._get(self.sent_ask)).state, PersonRequestState.WITHDRAWN)
        self.assertEqual((await self._get(self.answered_ask)).state, PersonRequestState.ANSWERED)
        # Legal lead: item and ask, told once; Finance lead: open item, told once;
        # the declined item's owner was not waited on; virtual employees get no notice.
        self.assertEqual(sorted(self._told()), sorted([LEGAL_LEAD.id, FINANCE_LEAD.id]))
        for notice in self.w.standins.notices.sent:
            self.assertIn("Nothing more is needed from you", notice.text)
            self.assertIn("Operations director", notice.text)

    async def _assert_working_data_removed_and_history_kept(self) -> None:
        for record_type in (Preferences, FollowedProject, ChatMessage, StatementDraft, KeptResult):
            with self.subTest(record_type.kind):
                self.assertEqual(await self._all(record_type), [])
        self.assertIsNone(await self._get(self.waiting))
        self.assertEqual(await self._get(self.done_request), self.done_request)
        self.assertEqual(await self._get(self.w.seeded.statement), self.w.seeded.statement)
        self.assertEqual(await self._get(self.report), self.report)
        self.assertEqual(len(await self._all(ConfirmedStatement)), 1)
        self.assertEqual((await self._get(self.attention)).state, AttentionState.CLEARED)
        self.assertEqual((await self._get(self.running_work)).state, WorkState.CANCELLED)

    async def test_end_closes_open_work_and_tells_owners_once_even_while_paused(self) -> None:
        """Story 2.4 - open work closed and owners told once (end, while paused)."""
        ended = await self.flow.end(ORGANISATION_ID, self.ops.flow_id)
        assert ended is not None
        self.assertEqual(ended.state, AssistantState.ENDED)
        await self._assert_open_work_closed_and_owners_told_once()
        self.assertIsNone(await self.flow.end(ORGANISATION_ID, self.ops.flow_id))  # again
        self.assertEqual(len(self._told()), 2)

    async def test_end_removes_working_data_and_keeps_history(self) -> None:
        """Story 2.4 - working data removed, history kept (end)."""
        await self.flow.end(ORGANISATION_ID, self.ops.flow_id)
        await self._assert_working_data_removed_and_history_kept()
        page = self.w.call("GET", f"{PREFIX}/assistant", MANAGER)
        self.assertEqual(page.status_code, 404)
        # the manager can be given a new assistant once the old one has ended
        await self.flow.set_up(ORGANISATION_ID, MANAGER, "Executive Assistant", "flow-ea-new")

    async def test_hand_over_closes_open_work_and_removes_working_data(self) -> None:
        """Story 2.4 - open work closed, owners told once; data removed (hand-over)."""
        await self.flow.hand_over(ORGANISATION_ID, self.ops.flow_id, NEW_MANAGER)
        await self._assert_open_work_closed_and_owners_told_once()
        await self._assert_working_data_removed_and_history_kept()

    async def test_hand_over_starts_fresh(self) -> None:
        """Story 2.4 - hand-over starts fresh."""
        handed = await self.flow.hand_over(ORGANISATION_ID, self.ops.flow_id, NEW_MANAGER)
        self.assertEqual(
            (handed.flow_id, handed.manager_id, handed.state),
            (self.ops.flow_id, NEW_MANAGER, AssistantState.NEW),
        )
        self.assertNotEqual(handed.id, self.ops.id)  # a new record for the new manager
        previous = await self._get(self.ops)
        self.assertEqual(
            (previous.state, previous.handed_over_to), (AssistantState.ENDED, NEW_MANAGER)
        )
        page = self.w.call("GET", f"{PREFIX}/assistant", NEW_MANAGER).json()
        self.assertTrue(page["form_offered"])
        form = self.w.call("GET", f"{PREFIX}/preferences", NEW_MANAGER).json()
        self.assertEqual(form["saved"], False)
        self.assertEqual(form["form"]["tone"], None)  # not the previous manager's answers
        levels = self.w.call("GET", f"{PREFIX}/approval-levels", NEW_MANAGER).json()
        self.assertEqual(
            {lvl["kind"]: lvl["level"] for lvl in levels},
            {
                "ask_owner": "with_approval",
                "add_action_item": "with_approval",
                "send_message": "with_approval",  # the old manager had set without approval
                "purchase": "not_allowed",
                "write_meeting_notes": "with_approval",
                "attend_meeting": "with_approval",
            },
        )
        # nothing from before is visible to the new manager
        for path in (
            f"/statements/{self.w.seeded.statement.id}",
            f"/work/{self.w.seeded.work.id}",
            f"/action-items/{self.open_finance.id}",
        ):
            with self.subTest(path):
                response = self.w.call("GET", f"{PREFIX}{path}", NEW_MANAGER)
                self.assertEqual(response.status_code, 404)
        # and the previous manager no longer has it
        self.assertEqual(self.w.call("GET", f"{PREFIX}/assistant", MANAGER).status_code, 404)
        names = [r.name for r in self.w.standins.records.written]
        self.assertIn("assistant.handed_over", names)

    async def test_no_hand_over_to_a_manager_who_has_an_assistant(self) -> None:
        """Story 2.1 - one assistant per manager (hand-over)."""
        with self.assertRaises(NotAllowedValue) as caught:
            await self.flow.hand_over(ORGANISATION_ID, self.ops.flow_id, SALES_DIRECTOR.id)
        self.assertEqual(caught.exception.message, ALREADY_HAS_ONE)
        current = await self._get(self.ops)
        self.assertEqual((current.manager_id, current.state), (MANAGER, AssistantState.PAUSED))
        self.assertEqual((await self._get(self.open_finance)).state, ItemState.OPEN)
        self.assertEqual(self._told(), [])

    async def test_hand_over_to_someone_who_is_not_a_person_is_refused(self) -> None:
        with self.assertRaises(NotAllowedValue):
            await self.flow.hand_over(ORGANISATION_ID, self.ops.flow_id, BACKEND_ENGINEER.id)

    async def test_nothing_changes_and_no_one_is_told_when_records_cannot_be_written(self) -> None:
        self.w.standins.records.failing = True
        with self.assertRaises(ModuleError):
            await self.flow.end(ORGANISATION_ID, self.ops.flow_id)
        self.w.standins.records.failing = False
        self.assertEqual((await self._get(self.ops)).state, AssistantState.PAUSED)
        self.assertEqual((await self._get(self.open_finance)).state, ItemState.OPEN)
        self.assertEqual(self._told(), [])

    async def test_a_notice_that_cannot_be_sent_does_not_undo_the_end(self) -> None:
        self.w.standins.notices.failing = True
        ended = await self.flow.end(ORGANISATION_ID, self.ops.flow_id)
        assert ended is not None
        self.assertEqual((await self._get(self.ops)).state, AssistantState.ENDED)
        self.assertEqual(self._told(), [])

    async def test_hand_over_again_to_the_same_manager_changes_nothing(self) -> None:
        await self.flow.hand_over(ORGANISATION_ID, self.ops.flow_id, NEW_MANAGER)
        told = len(self._told())
        again = await self.flow.hand_over(ORGANISATION_ID, self.ops.flow_id, NEW_MANAGER)
        self.assertEqual(again.state, AssistantState.NEW)
        self.assertEqual(len(self._told()), told)

    async def test_later_services_add_their_own_closing_handlers(self) -> None:
        seen: list[tuple[str, str]] = []

        async def later_service(action: Any, assistant: AssistantRecord, closing: Closing) -> None:
            seen.append((str(assistant.id), closing.reason))
            closing.tell.add(PROGRAMME_LEAD.id)

        closing_service = self.w.module.services.closing
        closing_service.registry = ClosingRegistry([*CLOSING_HANDLERS.handlers, later_service])
        await self.flow.hand_over(ORGANISATION_ID, self.ops.flow_id, PERSON_WITHOUT_ASSISTANT)
        self.assertEqual(seen, [(str(self.ops.id), "handed_over")])
        self.assertIn(PROGRAMME_LEAD.id, self._told())

    async def test_an_ended_assistant_does_nothing_more(self) -> None:
        await self.flow.end(ORGANISATION_ID, self.ops.flow_id)
        outcome = await self.flow.finish_work(
            ORGANISATION_ID, self.ops.flow_id, WorkResult(work_id=uuid4())
        )
        self.assertEqual(outcome, "ignored")
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            ended = await tx.get(AssistantRecord, self.ops.id)
        assert ended is not None
        self.assertEqual(ended.state, AssistantState.ENDED)


if __name__ == "__main__":
    unittest.main()
