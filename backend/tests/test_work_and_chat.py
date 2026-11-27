"""Story 3.1: pieces of work and the chat (task-tCADNiUr_Ds)."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from uuid import uuid4

from assistant.api.router import PREFIX
from assistant.models.boundary import StartResult, WorkOrder, WorkResult
from assistant.models.records import ChatMessage, PieceOfWork, WorkSlot, WorkState
from assistant.rules.sentences import is_plain_sentence
from assistant.rules.work import (
    CHAT_TOO_LONG,
    FORM_FIRST,
    NOT_RETRYABLE,
    NOT_STARTED,
    REPLY_TOO_LONG,
    WORK_FAILED,
)
from assistant.service.work_kinds import MESSAGE_LABEL
from standins.sample import FINANCE_LEAD, OPERATIONS_DIRECTOR, ORGANISATION_ID, PROGRAMME_LEAD
from tests.world import World, bare, build_world

MANAGER = OPERATIONS_DIRECTOR.id
REMIND = "Remind the Legal lead to finish the review by Friday"


class WorkAndChatTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        self.ops = self.w.seeded.assistant
        self.scripted = self.w.standins.assistant
        self.flow = self.w.module.flow

    async def asyncTearDown(self) -> None:
        self.w.close()

    def _send(self, text: str, person: str = MANAGER) -> Any:
        return self.w.call("POST", f"{PREFIX}/chat", person, {"text": text})

    def _chat(self, person: str = MANAGER) -> Any:
        return self.w.call("GET", f"{PREFIX}/chat", person)

    async def _deliver(self) -> list[str]:
        """Hand the scripted assistant's finished results to the module, as Flow would."""
        return [
            await self.flow.finish_work(ORGANISATION_ID, self.ops.flow_id, result)
            for result in self.scripted.take_finished()
        ]

    async def _pieces(self) -> list[PieceOfWork]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            found = await tx.find(PieceOfWork, assistant_id=self.ops.id)
        return [p for p in found if p.id != self.w.seeded.work.id]

    def _started_messages(self) -> list[str]:
        return [
            next(i.content for i in order.input_pack.inputs if i.kind == "message")
            for order in self.scripted.started
        ]

    async def test_message_creates_work(self) -> None:
        """Story 3.1 - the manager's message creates a piece of work, its input pack is made,
        it is handed to Flow, and the message and reply are kept in the chat."""
        response = self._send(REMIND)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual((body["author"], body["text"]), ("manager", REMIND))
        self.assertEqual(body["work"]["state"], "running")

        (piece,) = await self._pieces()
        self.assertEqual((piece.work_kind.value, piece.asked_by), ("request", MANAGER))
        self.assertEqual(piece.state, WorkState.RUNNING)
        self.assertIn(MESSAGE_LABEL, piece.input_labels)
        (order,) = self.scripted.started
        self.assertEqual(order.work_id, piece.id)
        self.assertEqual(order.assistant_flow_id, self.ops.flow_id)
        message = next(i for i in order.input_pack.inputs if i.kind == "message")
        self.assertEqual((message.content, message.note), (REMIND, "data, not instructions"))

        self.assertEqual(await self._deliver(), ["handled"])
        messages = self._chat().json()["messages"]
        self.assertEqual([m["author"] for m in messages], ["manager", "assistant"])
        self.assertEqual(messages[0]["text"], REMIND)
        self.assertTrue(messages[1]["text"].startswith("I propose an action item"))
        self.assertEqual(messages[0]["work"]["state"], "done")
        (done,) = await self._pieces()
        self.assertEqual((done.state, done.reply), (WorkState.DONE, messages[1]["text"]))
        work = self.w.call("GET", f"{PREFIX}/work/{done.id}", MANAGER).json()
        self.assertEqual((work["state"], work["can_retry"]), ("done", False))

    async def test_work_is_queued_in_order(self) -> None:
        """Story 3.1 - while work is running the next piece waits; pieces run in the order
        asked."""
        for text in ("First", "Second", "Third"):
            self.assertEqual(self._send(text).status_code, 200)
        states = [p.state for p in await self._pieces()]
        self.assertEqual(states, [WorkState.RUNNING, WorkState.WAITING, WorkState.WAITING])
        self.assertEqual(self._started_messages(), ["First"])
        for _ in range(3):
            await self._deliver()
        self.assertEqual(self._started_messages(), ["First", "Second", "Third"])
        self.assertTrue(all(p.state is WorkState.DONE for p in await self._pieces()))

    async def test_only_one_piece_runs_even_when_started_at_once(self) -> None:
        for text in ("One", "Two"):
            self._send(text)
        await self._deliver()  # "Two" starts
        self.scripted.take_finished()
        await asyncio.gather(*(self.w.module.services.work.advance(self.ops) for _ in range(5)))
        running = [p for p in await self._pieces() if p.state is WorkState.RUNNING]
        self.assertEqual(len(running), 1)
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            (slot,) = await tx.find(WorkSlot, assistant_id=self.ops.id)
        self.assertEqual(slot.running_work_id, running[0].id)

    async def test_refusal_is_retryable(self) -> None:
        """Story 3.1 - when Flow refuses the work the manager sees the reason in one
        sentence and can try again."""
        self.scripted.refusing = True
        body = self._send("First").json()
        self.assertEqual(body["work"]["state"], "refused")
        self.assertEqual(body["work"]["reason"], "The assistant is not available right now.")
        self.assertTrue(body["work"]["can_retry"])
        self.assertTrue(is_plain_sentence(body["work"]["reason"]))

        self.scripted.refusing = False
        retried = self.w.call("POST", f"{PREFIX}/work/{body['work']['id']}/retry", MANAGER)
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(retried.json()["state"], "running")
        again = self.w.call("POST", f"{PREFIX}/work/{body['work']['id']}/retry", MANAGER)
        self.assertEqual(again.json()["id"], retried.json()["id"])  # pressed twice: once
        self.assertEqual(self._started_messages(), ["First"])
        await self._deliver()
        messages = self._chat().json()["messages"]
        self.assertEqual([m["author"] for m in messages], ["manager", "assistant"])
        self.assertEqual(messages[0]["work"]["state"], "done")  # follows the latest try

    async def test_failure_is_retryable(self) -> None:
        """Story 3.1 - when the work fails the manager sees the reason in one sentence and
        can try again."""
        body = self._send("First").json()
        work_id = body["work"]["id"]
        self.scripted.take_finished()
        await self.flow.finish_work(
            ORGANISATION_ID,
            self.ops.flow_id,
            WorkResult(work_id=work_id, failure="The assistant ran out of time."),
        )
        work = self.w.call("GET", f"{PREFIX}/work/{work_id}", MANAGER).json()
        self.assertEqual(
            (work["state"], work["reason"], work["can_retry"]),
            ("failed", "The assistant ran out of time.", True),
        )
        retried = self.w.call("POST", f"{PREFIX}/work/{work_id}/retry", MANAGER).json()
        self.assertEqual(retried["state"], "running")
        self.assertEqual(self._started_messages(), ["First", "First"])

    async def test_a_failure_that_is_not_one_sentence_is_shown_as_one(self) -> None:
        body = self._send("First").json()
        await self.flow.finish_work(
            ORGANISATION_ID,
            self.ops.flow_id,
            WorkResult(work_id=body["work"]["id"], failure="Traceback:\n  <stack>"),
        )
        work = self.w.call("GET", f"{PREFIX}/work/{body['work']['id']}", MANAGER).json()
        self.assertEqual(work["reason"], WORK_FAILED)

    async def test_an_unreachable_assistant_fails_the_work_with_a_reason(self) -> None:
        async def unreachable(order: WorkOrder) -> StartResult:
            raise ConnectionError("down")

        self.scripted.start_work = unreachable  # type: ignore[method-assign]
        body = self._send("First").json()
        self.assertEqual(
            (body["work"]["state"], body["work"]["reason"]), ("failed", NOT_STARTED)
        )
        self.assertTrue(body["work"]["can_retry"])

    async def test_refused_work_lets_the_next_piece_run(self) -> None:
        self._send("First")
        self._send("Second")
        self.scripted.refusing = True
        await self._deliver()  # "Second" is refused when it starts
        self.scripted.refusing = False
        self._send("Third")
        self.assertEqual(
            [p.state for p in await self._pieces()],
            [WorkState.DONE, WorkState.REFUSED, WorkState.RUNNING],
        )

    async def test_only_refused_or_failed_work_can_be_tried_again(self) -> None:
        body = self._send("First").json()
        response = self.w.call("POST", f"{PREFIX}/work/{body['work']['id']}/retry", MANAGER)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["message"], NOT_RETRYABLE)

    async def test_duplicate_results_ignored(self) -> None:
        """Story 3.1 - when the same result arrives twice the second is ignored."""
        self._send(REMIND)
        (result,) = self.scripted.take_finished()
        await self.flow.finish_work(ORGANISATION_ID, self.ops.flow_id, result)
        await self.flow.finish_work(ORGANISATION_ID, self.ops.flow_id, result)
        messages = self._chat().json()["messages"]
        self.assertEqual([m["author"] for m in messages], ["manager", "assistant"])
        unknown = WorkResult(work_id=uuid4(), reply="Who asked?")
        await self.flow.finish_work(ORGANISATION_ID, self.ops.flow_id, unknown)
        self.assertEqual(len(self._chat().json()["messages"]), 2)

    async def test_the_same_result_at_the_same_moment_is_used_once(self) -> None:
        self._send(REMIND)
        (result,) = self.scripted.take_finished()
        await asyncio.gather(
            *(self.flow.finish_work(ORGANISATION_ID, self.ops.flow_id, result) for _ in range(3))
        )
        authors = [m["author"] for m in self._chat().json()["messages"]]
        self.assertEqual(authors, ["manager", "assistant"])

    async def _assert_chat_gone(self) -> None:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            self.assertEqual(await tx.find(ChatMessage, assistant_id=self.ops.id), [])
            pieces = await tx.find(PieceOfWork, assistant_id=self.ops.id)
            self.assertEqual(await tx.find(WorkSlot, assistant_id=self.ops.id), [])
        self.assertTrue(all(p.reply is None and not p.given_inputs for p in pieces))

    async def test_end_removes_chat(self) -> None:
        """Story 3.1 - when the assistant is ended its chat is removed."""
        self._send(REMIND)
        await self._deliver()
        await self.flow.end(ORGANISATION_ID, self.ops.flow_id)
        await self._assert_chat_gone()
        self.assertEqual(self._chat().status_code, 404)

    async def test_hand_over_removes_chat(self) -> None:
        """Story 3.1 - when the assistant is handed over its chat is removed."""
        self._send(REMIND)
        await self._deliver()
        self._send("Still running when handed over")
        await self.flow.hand_over(ORGANISATION_ID, self.ops.flow_id, PROGRAMME_LEAD.id)
        await self._assert_chat_gone()
        late = self.scripted.take_finished()
        for result in late:  # the result of cancelled work is not used
            await self.flow.finish_work(ORGANISATION_ID, self.ops.flow_id, result)
        self.w.call("POST", f"{PREFIX}/preferences/skip", PROGRAMME_LEAD.id)
        self.assertEqual(self._chat(PROGRAMME_LEAD.id).json(), {"messages": []})

    async def test_chat_message_over_4000_characters_refused(self) -> None:
        """Story 3.1 - a chat message over 4,000 characters is refused with one sentence."""
        response = self._send("x" * 4001)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["message"], CHAT_TOO_LONG)
        self.assertTrue(is_plain_sentence(CHAT_TOO_LONG))
        self.assertEqual(await self._pieces(), [])
        self.assertEqual(self._send("x" * 4000).status_code, 200)

    async def test_reply_over_4000_characters_refused(self) -> None:
        """Story 3.1 - a reply over 4,000 characters is refused with one sentence."""
        body = self._send("First").json()
        self.scripted.take_finished()
        await self.flow.finish_work(
            ORGANISATION_ID,
            self.ops.flow_id,
            WorkResult(work_id=body["work"]["id"], reply="y" * 4001),
        )
        messages = self._chat().json()["messages"]
        self.assertEqual([m["author"] for m in messages], ["manager"])
        work = messages[0]["work"]
        self.assertEqual((work["state"], work["refused"]), ("done", [REPLY_TOO_LONG]))
        self.assertEqual(work["refused_count"], 1)
        self.assertTrue(is_plain_sentence(REPLY_TOO_LONG))

    async def test_empty_message_refused(self) -> None:
        self.assertEqual(self._send("   ").status_code, 422)

    async def test_paused_assistant_keeps_the_message_and_starts_it_on_resume(self) -> None:
        self.w.call("POST", f"{PREFIX}/assistant/pause", MANAGER)
        body = self._send("While paused").json()
        self.assertEqual(body["work"]["state"], "waiting")
        self.assertEqual(self.scripted.started, [])
        self.w.call("POST", f"{PREFIX}/assistant/resume", MANAGER)
        self.assertEqual(self._started_messages(), ["While paused"])

    async def test_result_kept_while_paused_is_used_once_on_resume(self) -> None:
        self._send(REMIND)
        (result,) = self.scripted.take_finished()
        self.w.call("POST", f"{PREFIX}/assistant/pause", MANAGER)
        self.assertEqual(
            await self.flow.finish_work(ORGANISATION_ID, self.ops.flow_id, result), "kept"
        )
        self.assertEqual(len(self._chat().json()["messages"]), 1)
        self.w.call("POST", f"{PREFIX}/assistant/resume", MANAGER)
        await self.flow.finish_work(ORGANISATION_ID, self.ops.flow_id, result)
        self.assertEqual(len(self._chat().json()["messages"]), 2)

    async def test_others_cannot_read_or_retry_the_managers_work(self) -> None:
        body = self._send("First").json()
        for person in (FINANCE_LEAD.id,):
            response = self.w.call("GET", f"{PREFIX}/work/{body['work']['id']}", person)
            self.assertEqual(response.status_code, 404)

    def test_a_new_assistant_takes_no_work(self) -> None:
        b = bare()
        asyncio.run(
            b.module.flow.set_up(ORGANISATION_ID, MANAGER, "Executive Assistant", "flow-new")
        )
        response = b.call("POST", f"{PREFIX}/chat", MANAGER, {"text": "Hello"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["message"], FORM_FIRST)
        b.client.close()


if __name__ == "__main__":
    unittest.main()
