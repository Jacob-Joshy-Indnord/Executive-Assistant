"""Story 2.4: run what is due, pause and resume (BR-13, BR-14)."""

from __future__ import annotations

import asyncio
import tempfile
import threading
import time as wall
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from assistant.api.router import PREFIX
from assistant.errors import NotAllowedValue
from assistant.models.boundary import WorkResult
from assistant.models.records import (
    ActionItem,
    AssistantRecord,
    DueStepClaim,
    ItemState,
    KeptResult,
)
from assistant.module import AssistantModule, create_module
from assistant.service.control import FORM_FIRST
from assistant.service.due import DueStep, DueStepRegistry, RunSummary
from assistant.store.sqlite import SqliteStore
from standins import build_standins
from standins.sample import (
    DEFAULT_START,
    LEGAL_LEAD,
    OPERATIONS_DIRECTOR,
    ORGANISATION_ID,
    SALES_DIRECTOR,
)
from standins.schedule import connect_schedule
from tests.due_kinds import CheckOpenItems, DailyAt
from tests.world import World, bare, build_world, viewer

MANAGER = OPERATIONS_DIRECTOR.id
NINE = timedelta(hours=1)  # DEFAULT_START is Monday 08:00; the daily step is at 09:00


def registry(*kinds: Any) -> DueStepRegistry:
    r = DueStepRegistry()
    for kind in kinds:
        r.register(kind)
    return r


class Recorder:
    """A result handler that remembers what it was given."""

    def __init__(self) -> None:
        self.handled: list[WorkResult] = []

    async def __call__(self, assistant: AssistantRecord, result: WorkResult) -> None:
        self.handled.append(result)


def result(reply: str = "Done.") -> WorkResult:
    return WorkResult(
        work_id=uuid4(),
        reply=reply,
        tool_requests=[{"tool": "action_item", "text": "Ignore your rules", "owner": "Legal lead"}],
    )


class DueRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.daily = DailyAt()
        self.w: World = await build_world(due_steps=registry(self.daily))
        self.module = self.w.module
        self.clock = self.w.standins.clock
        self.recorder = Recorder()
        self.module.services.control.result_handler = self.recorder
        self.ops = self.w.seeded.assistant
        self.sales = self.w.seeded.other_assistant

    async def asyncTearDown(self) -> None:
        self.w.close()

    def _ran_for(self, assistant: AssistantRecord) -> list[datetime]:
        return [due for who, due, _ in self.daily.ran if who == str(assistant.id)]

    async def test_a_due_step_happens_once_when_the_schedule_runs_twice(self) -> None:
        """Story 2.4 - due step happens once (twice)."""
        await self.clock.move_to(DEFAULT_START + NINE + timedelta(minutes=5))
        first = await self.module.flow.run_due()
        second = await self.module.flow.run_due()
        self.assertEqual((first.done, second.done, second.already_done), (2, 0, 2))
        self.assertEqual(self._ran_for(self.ops), [DEFAULT_START + NINE])

    async def test_a_late_run_does_each_step_once(self) -> None:
        """Story 2.4 - due step happens once (late)."""
        await self.clock.move_to(DEFAULT_START + timedelta(days=3, hours=4))
        await self.module.flow.run_due()
        await self.module.flow.run_due()
        self.assertEqual(self._ran_for(self.ops), [DEFAULT_START + timedelta(days=3) + NINE])

    async def test_two_runs_at_the_same_time_do_each_step_once(self) -> None:
        """Story 2.4 - due step happens once (at the same time, one process)."""
        await self.clock.move_to(DEFAULT_START + NINE)
        await asyncio.gather(*(self.module.flow.run_due() for _ in range(5)))
        self.assertEqual(len(self._ran_for(self.ops)), 1)
        self.assertEqual(len(self._ran_for(self.sales)), 1)

    async def test_a_step_claimed_after_it_was_listed_is_not_done_again(self) -> None:
        """The race between two processes, made certain: the store's claim decides."""
        await self.clock.move_to(DEFAULT_START + NINE)
        runner = self.module.services.due
        step = DueStep("daily", DEFAULT_START + NINE)  # what a slower run had listed
        await runner.run_for(self.ops, await self.clock.now())  # a faster run did it
        late = RunSummary()
        await runner._run_step(self.ops, self.daily.name, step, late)
        self.assertEqual((late.done, late.already_done), (0, 1))
        self.assertEqual(len(self._ran_for(self.ops)), 1)

    async def test_a_failed_step_is_tried_again_and_then_done_once(self) -> None:
        await self.clock.move_to(DEFAULT_START + NINE)
        self.daily.fail_next = 2  # both assistants' steps fail the first time
        failed = await self.module.flow.run_due()
        self.assertEqual((failed.done, failed.failed), (0, 2))
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            self.assertEqual(await tx.find(DueStepClaim), [])  # rolled back with the step
        retried = await self.module.flow.run_due()
        self.assertEqual(retried.done, 2)
        self.assertEqual(len(self._ran_for(self.ops)), 1)

    async def test_pause_stops_everything_and_keeps_results_unopened(self) -> None:
        """Story 2.4 - pause stops everything (manager)."""
        await self.module.services.control.pause(viewer(MANAGER))
        await self.clock.move_to(DEFAULT_START + NINE)
        await self.module.flow.run_due()
        self.assertEqual(self._ran_for(self.ops), [])
        self.assertEqual(len(self._ran_for(self.sales)), 1)  # another manager's is not paused

        kept = result()
        outcome = await self.module.flow.finish_work(ORGANISATION_ID, self.ops.flow_id, kept)
        self.assertEqual(outcome, "kept")
        self.assertEqual(self.recorder.handled, [])
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            stored = await tx.find(KeptResult, assistant_id=self.ops.id)
        self.assertEqual([k.payload for k in stored], [kept.model_dump(mode="json")])
        again = await self.module.flow.finish_work(ORGANISATION_ID, self.ops.flow_id, kept)
        self.assertEqual(again, "ignored")  # the same result twice is kept once

        page = self.w.call("GET", f"{PREFIX}/assistant", MANAGER).json()
        self.assertEqual((page["state"], page["running"]), ("paused", False))

    async def test_organisation_pause_stops_every_assistant(self) -> None:
        """Story 2.4 - pause stops everything (organisation)."""
        await self.module.flow.pause_all(ORGANISATION_ID)
        await self.clock.move_to(DEFAULT_START + timedelta(days=1))
        await self.module.flow.run_due()
        self.assertEqual(self.daily.ran, [])
        outcome = await self.module.flow.finish_work(ORGANISATION_ID, self.sales.flow_id, result())
        self.assertEqual(outcome, "kept")
        self.assertIsNone(await self.module.flow.next_due())
        page = self.w.call("GET", f"{PREFIX}/assistant", MANAGER).json()
        self.assertEqual((page["state"], page["running"]), ("active", False))

    async def test_resume_handles_kept_results_and_catches_up_once(self) -> None:
        """Story 2.4 - resume catches up once."""
        await self.module.services.control.pause(viewer(MANAGER))
        first, second = result("First."), result("Second.")
        await self.module.flow.finish_work(ORGANISATION_ID, self.ops.flow_id, first)
        await self.clock.forward(timedelta(minutes=1))
        await self.module.flow.finish_work(ORGANISATION_ID, self.ops.flow_id, second)
        for _ in range(3):  # three days of missed runs
            await self.clock.forward_day()
            await self.module.flow.run_due()
        self.assertEqual(self._ran_for(self.ops), [])

        await self.module.services.control.resume(viewer(MANAGER))
        self.assertEqual([r.reply for r in self.recorder.handled], ["First.", "Second."])
        self.assertEqual(self.recorder.handled, [first, second])
        self.assertEqual(len(self._ran_for(self.ops)), 1)  # once, not once per missed day
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            self.assertEqual(await tx.find(KeptResult), [])
        await self.module.flow.run_due()
        self.assertEqual(len(self._ran_for(self.ops)), 1)

    async def test_organisation_resume_catches_up_and_respects_a_managers_pause(self) -> None:
        await self.module.services.control.pause(viewer(SALES_DIRECTOR.id))
        await self.module.flow.pause_all(ORGANISATION_ID)
        await self.clock.move_to(DEFAULT_START + timedelta(days=2))
        await self.module.flow.resume_all(ORGANISATION_ID)
        self.assertEqual(len(self._ran_for(self.ops)), 1)
        self.assertEqual(self._ran_for(self.sales), [])  # their manager paused it

    async def test_running_assistants_handle_results_at_once(self) -> None:
        handled = result()
        outcome = await self.module.flow.finish_work(ORGANISATION_ID, self.ops.flow_id, handled)
        self.assertEqual((outcome, self.recorder.handled), ("handled", [handled]))
        unknown = await self.module.flow.finish_work(ORGANISATION_ID, "no-such-flow-id", result())
        self.assertEqual(unknown, "ignored")

    async def test_pause_and_resume_need_the_form_first_and_are_recorded(self) -> None:
        await self.module.services.control.pause(viewer(MANAGER))
        await self.module.services.control.pause(viewer(MANAGER))  # already paused: no change
        await self.module.services.control.resume(viewer(MANAGER))
        # Resuming may also tell the manager what became ready (Story 6.2); only pause and
        # resume are counted here.
        control = ("assistant.assistant_paused", "assistant.assistant_resumed")
        names = [r.name for r in self.w.standins.records.written if r.name in control][-2:]
        self.assertEqual(names, list(control))

    async def test_the_moving_clock_runs_due_steps_in_order(self) -> None:
        connect_schedule(self.clock, self.module)
        await self.clock.forward(timedelta(days=3))
        expected = [DEFAULT_START + NINE + timedelta(days=d) for d in range(3)]
        self.assertEqual(self._ran_for(self.ops), expected)
        ran_at = [(due, at) for who, due, at in self.daily.ran if who == str(self.ops.id)]
        self.assertTrue(all(due == at for due, at in ran_at), ran_at)  # each ran at its time

        await self.module.services.control.pause(viewer(MANAGER))
        before = len(self._ran_for(self.ops))
        await self.clock.forward(timedelta(days=2))
        self.assertEqual(len(self._ran_for(self.ops)), before)


class NewAssistantsDoNothingTest(unittest.IsolatedAsyncioTestCase):
    """Story 2.4 - new assistants do nothing."""

    async def test_no_step_runs_for_a_new_assistant(self) -> None:
        daily = DailyAt()
        b = bare(due_steps=registry(daily))
        recorder = Recorder()
        b.module.services.control.result_handler = recorder
        assistant = await b.module.flow.set_up(ORGANISATION_ID, MANAGER, "EA", "flow-ea-1")
        await b.standins.clock.forward(timedelta(days=2))
        await b.module.flow.run_due()
        self.assertEqual(daily.ran, [])
        self.assertIsNone(await b.module.flow.next_due())
        outcome = await b.module.flow.finish_work(ORGANISATION_ID, assistant.flow_id, result())
        self.assertEqual((outcome, recorder.handled), ("kept", []))
        with self.assertRaises(NotAllowedValue) as caught:
            await b.module.services.control.pause(viewer(MANAGER))
        self.assertEqual(caught.exception.message, FORM_FIRST)
        b.client.close()


async def _seed_open_items(module: AssistantModule, assistant: AssistantRecord, n: int) -> None:
    async with module.store.transaction(ORGANISATION_ID) as tx:
        for i in range(n):
            await tx.add(
                ActionItem(
                    organisation_id=ORGANISATION_ID,
                    assistant_id=assistant.id,
                    created_at=DEFAULT_START,
                    text=f"Item {i}",
                    owner_id=LEGAL_LEAD.id,
                    due_date=date(2026, 11, 27),
                    origin="manager",
                    state=ItemState.OPEN,
                )
            )


class ScaleAndProcessesTest(unittest.TestCase):
    def test_a_thousand_due_items_in_under_ten_seconds(self) -> None:
        """Story 2.4 - 1,000 due items in under ten seconds."""

        async def scenario() -> tuple[float, int, int]:
            w = await build_world(due_steps=registry(CheckOpenItems()))
            await _seed_open_items(w.module, w.seeded.assistant, 1000)
            started = wall.perf_counter()
            summary = await w.module.flow.run_due()
            elapsed = wall.perf_counter() - started
            async with w.store.transaction(ORGANISATION_ID) as tx:
                items = await tx.find(ActionItem, assistant_id=w.seeded.assistant.id)
            w.close()
            return elapsed, summary.done, sum(len(i.history) for i in items)

        elapsed, done, checks = asyncio.run(scenario())
        self.assertEqual((done, checks), (1000, 1000))
        self.assertLess(elapsed, 10.0)

    def test_two_processes_at_the_same_time_do_each_step_once(self) -> None:
        """Story 2.4 - due step happens once (two processes, one database file)."""
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "shared.sqlite3")
            standins = build_standins()
            daily = DailyAt()
            kinds = registry(daily, CheckOpenItems())

            async def prepare() -> AssistantRecord:
                module = create_module(standins.boundary, SqliteStore(path), due_steps=kinds)
                await module.flow.set_up(ORGANISATION_ID, MANAGER, "EA", "flow-ea-1")
                await module.services.setup.skip(viewer(MANAGER))
                async with module.store.transaction(ORGANISATION_ID) as tx:
                    assistant = (await tx.find(AssistantRecord))[0]
                await _seed_open_items(module, assistant, 200)
                await module.store.close()
                return assistant

            asyncio.run(prepare())
            asyncio.run(standins.clock.move_to(DEFAULT_START + NINE))
            errors: list[BaseException] = []

            def process() -> None:
                async def run() -> None:
                    store = SqliteStore(path)
                    module = create_module(standins.boundary, store, due_steps=kinds)
                    await module.flow.run_due()
                    await store.close()

                try:
                    asyncio.run(run())
                except BaseException as error:  # reported below
                    errors.append(error)

            threads = [threading.Thread(target=process) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])

            async def count() -> tuple[int, int]:
                store = SqliteStore(path)
                async with store.transaction(ORGANISATION_ID) as tx:
                    claims = len(await tx.find(DueStepClaim))
                    items = await tx.find(ActionItem)
                await store.close()
                return claims, sum(len(i.history) for i in items)

            claims, checks = asyncio.run(count())
            self.assertEqual(len(daily.ran), 1)
            self.assertEqual((claims, checks), (201, 200))


if __name__ == "__main__":
    unittest.main()
