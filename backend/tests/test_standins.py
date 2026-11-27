"""Story 1.1: the stand-ins of BRD section 9.3."""

from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from assistant.models.boundary import (
    ActionKind,
    ApprovalLevel,
    ApprovalLimits,
    ApprovalRequest,
    AuditRecord,
    InputPack,
    Notice,
    PackInput,
    WorkKind,
    WorkOrder,
)
from standins import build_standins
from standins.assistant import (
    ACTION_ITEM_TEXT_LIMIT,
    MESSAGE_KIND,
    MISSING_SOURCE,
    PROJECT_NEWS_KIND,
    UNKNOWN_NAME,
    next_weekday,
)
from standins.clock import MovableClock
from standins.people import PERSON_HEADER
from standins.sample import (
    BACKEND_ENGINEER,
    DEFAULT_START,
    FINANCE_LEAD,
    LEGAL_LEAD,
    OPERATIONS_DIRECTOR,
    ORGANISATION_ADMINISTRATOR,
    ORGANISATION_ID,
    PROGRAMME_LEAD,
    SALES_DIRECTOR,
    TEST_ENGINEER,
)

MESSAGE_LABEL = "Message from the manager"
KNOWN_ACTION_ITEM_FIELDS = {"tool", "text", "owner", "due_date", "source"}


def _order(kind: WorkKind, inputs: list[PackInput]) -> WorkOrder:
    return WorkOrder(
        work_id=uuid4(),
        organisation_id=ORGANISATION_ID,
        assistant_flow_id="flow-assistant-1",
        kind=kind,
        input_pack=InputPack(inputs=inputs),
    )


def _message(text: str) -> PackInput:
    return PackInput(label=MESSAGE_LABEL, read_at=DEFAULT_START, content=text, kind=MESSAGE_KIND)


class SampleOrganisationTest(unittest.IsolatedAsyncioTestCase):
    """Story 1.1 - the sample organisation, projects and assignments."""

    async def asyncSetUp(self) -> None:
        self.s = build_standins()

    async def test_people_of_the_sample_organisation(self) -> None:
        names = {}
        for p in (
            OPERATIONS_DIRECTOR,
            LEGAL_LEAD,
            FINANCE_LEAD,
            PROGRAMME_LEAD,
            ORGANISATION_ADMINISTRATOR,
            SALES_DIRECTOR,
            BACKEND_ENGINEER,
            TEST_ENGINEER,
        ):
            person = await self.s.people.person(p.id)
            assert person is not None
            self.assertEqual(person.organisation_id, ORGANISATION_ID)
            names[person.display_name] = person.kind
        self.assertEqual(
            names,
            {
                "Operations director": "person",
                "Legal lead": "person",
                "Finance lead": "person",
                "Programme lead": "person",
                "Organisation administrator": "person",
                "Sales director": "person",
                "Backend Engineer": "virtual_employee",
                "Test Engineer": "virtual_employee",
            },
        )
        self.assertTrue(await self.s.people.is_administrator(ORGANISATION_ADMINISTRATOR.id))

    async def test_the_demonstration_bar_names_the_person_with_each_call(self) -> None:
        asking = await self.s.people.person_asking({"X-Demo-Person": LEGAL_LEAD.id})
        assert asking is not None
        self.assertEqual(asking.display_name, "Legal lead")
        self.assertIsNone(await self.s.people.person_asking({PERSON_HEADER: "nobody"}))

    async def test_name_in_another_organisation_does_not_match(self) -> None:
        found = await self.s.people.find_by_name(ORGANISATION_ID, "legal LEAD")
        self.assertEqual([p.id for p in found], [LEGAL_LEAD.id])

    async def test_three_projects_with_budgets_spend_and_owners(self) -> None:
        projects = await self.s.work_data.projects(OPERATIONS_DIRECTOR.id)
        self.assertEqual(
            {p.name: p.owner_id for p in projects},
            {
                "CRM rollout": PROGRAMME_LEAD.id,
                "Vendor consolidation": LEGAL_LEAD.id,
                "Hiring plan": FINANCE_LEAD.id,
            },
        )
        for p in projects:
            self.assertEqual(p.read_at, DEFAULT_START)
            self.assertRegex(p.budget, r"^\d+\.\d{2}$")
        sales = await self.s.work_data.projects(SALES_DIRECTOR.id)
        self.assertEqual([p.name for p in sales], ["Hiring plan"])

    async def test_four_assignments(self) -> None:
        assignments = await self.s.work_data.assignments(OPERATIONS_DIRECTOR.id)
        self.assertEqual(len(assignments), 4)
        self.assertEqual(
            {a.virtual_employee_id for a in assignments}, {BACKEND_ENGINEER.id, TEST_ENGINEER.id}
        )
        self.assertEqual(await self.s.work_data.assignments(SALES_DIRECTOR.id), [])

    async def test_demonstration_controls_change_work_data(self) -> None:
        crm = next(iter(self.s.work_data.projects_by_id))
        first_assignment = next(iter(self.s.work_data.assignments_by_id))
        self.s.work_data.set_spend(crm, "1.00")
        self.s.work_data.set_assignment_state(first_assignment, "approved")
        self.s.work_data.hide_project_from(crm, OPERATIONS_DIRECTOR.id)
        self.s.work_data.remove_assignment(first_assignment)
        self.assertEqual(len(await self.s.work_data.projects(OPERATIONS_DIRECTOR.id)), 2)
        self.assertEqual(len(await self.s.work_data.assignments(OPERATIONS_DIRECTOR.id)), 3)


class ScriptedAssistantTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.s = build_standins()

    async def test_proposes_an_action_item_from_a_message(self) -> None:
        """Story 1.1 - scripted assistant proposes an item from a message."""
        order = _order(
            WorkKind.REQUEST, [_message("Remind the Legal lead to finish the review by Friday")]
        )
        result = await self.s.assistant.answer(order)
        self.assertEqual(
            result.tool_requests,
            [
                {
                    "tool": "action_item",
                    "text": "Finish the review",
                    "owner": "Legal lead",
                    "due_date": "2026-11-27",
                    "source": MESSAGE_LABEL,
                }
            ],
        )
        self.assertEqual(date.fromisoformat("2026-11-27").strftime("%A"), "Friday")
        self.assertTrue(result.reply)

    def test_next_weekday_is_on_or_after_today(self) -> None:
        friday = date(2026, 11, 27)
        self.assertEqual(next_weekday(friday, "Friday"), friday)
        self.assertEqual(next_weekday(friday, "monday"), date(2026, 11, 30))

    async def test_one_prepared_answer_for_each_kind_of_work(self) -> None:
        for kind in WorkKind:
            with self.subTest(kind):
                result = await self.s.assistant.answer(_order(kind, []))
                self.assertTrue(result.reply)
                self.assertEqual(result.tool_requests, [])

    async def test_drafts_one_statement_for_each_project_with_news(self) -> None:
        news = [
            PackInput(
                label=f"Project news: {name}",
                read_at=DEFAULT_START,
                content=content,
                kind=PROJECT_NEWS_KIND,
                subject_id=f"project-{name}",
            )
            for name, content in (("A", "Spend rose to 40%."), ("B", ""), ("C", "Item done."))
        ]
        result = await self.s.assistant.answer(_order(WorkKind.STATUS_DRAFTS, news))
        drafts = result.tool_requests
        self.assertEqual([d["project"] for d in drafts], ["project-A", "project-C"])
        for d in drafts:
            self.assertEqual(d["tool"], "status_statement")
            self.assertNotIn("colour", d)  # the owner chooses it
            self.assertTrue(d["source"].startswith("Project news: "))

    async def test_faults_switch_produces_faulty_results(self) -> None:
        """Story 1.1 - faults switch produces faulty results."""
        self.s.assistant.faults = True
        message = _message("Remind the Legal lead to finish the review by Friday")
        result = await self.s.assistant.answer(_order(WorkKind.REQUEST, [message]))
        faulty = result.tool_requests[1:]
        self.assertEqual(result.tool_requests[0]["owner"], "Legal lead")
        self.assertEqual(len(faulty), 4)
        self.assertTrue(any(r["owner"] == UNKNOWN_NAME for r in faulty), "unknown name")
        self.assertTrue(any(r["source"] == MISSING_SOURCE for r in faulty), "missing source")
        self.assertTrue(
            any(len(r["text"]) > ACTION_ITEM_TEXT_LIMIT for r in faulty), "over-limit text"
        )
        self.assertTrue(any(set(r) - KNOWN_ACTION_ITEM_FIELDS for r in faulty), "unknown field")
        # each faulty request has exactly one fault
        for r in faulty:
            faults = [
                r["owner"] == UNKNOWN_NAME,
                r["source"] != MESSAGE_LABEL,
                len(r["text"]) > ACTION_ITEM_TEXT_LIMIT,
                bool(set(r) - KNOWN_ACTION_ITEM_FIELDS),
            ]
            self.assertEqual(sum(faults), 1, r)

    async def test_start_work_hands_back_the_result_later(self) -> None:
        order = _order(WorkKind.REQUEST, [_message("Hello")])
        started = await self.s.assistant.start_work(order)
        self.assertTrue(started.accepted)
        finished = self.s.assistant.take_finished()
        self.assertEqual([r.work_id for r in finished], [order.work_id])
        self.assertEqual(self.s.assistant.take_finished(), [])

    async def test_refusing_switch(self) -> None:
        self.s.assistant.refusing = True
        started = await self.s.assistant.start_work(_order(WorkKind.REQUEST, []))
        self.assertFalse(started.accepted)
        self.assertEqual(self.s.assistant.take_finished(), [])


class ApprovalStandInTest(unittest.IsolatedAsyncioTestCase):
    """Story 1.1 - the approval stand-in applies section 8.2."""

    async def asyncSetUp(self) -> None:
        self.s = build_standins()

    def _request(self, **changes: object) -> ApprovalRequest:
        base: dict[str, object] = {
            "request_id": uuid4(),
            "organisation_id": ORGANISATION_ID,
            "manager_id": OPERATIONS_DIRECTOR.id,
            "assistant_flow_id": "a",
            "kind": ActionKind.SEND_MESSAGE,
            "target_id": LEGAL_LEAD.id,
            "effect": "A message is sent to the Legal lead.",
            "level": ApprovalLevel.WITHOUT_APPROVAL,
        }
        return ApprovalRequest(**{**base, **changes})  # type: ignore[arg-type]

    async def _decision(self, **changes: object) -> str:
        return (await self.s.approval.check(self._request(**changes))).outcome

    async def test_levels(self) -> None:
        self.assertEqual(await self._decision(level=ApprovalLevel.NOT_ALLOWED), "declined")
        self.assertEqual(await self._decision(level=ApprovalLevel.WITH_APPROVAL), "ask_manager")
        self.assertEqual(
            await self._decision(level=ApprovalLevel.WITH_APPROVAL, approved_by_manager=True),
            "carry_out",
        )
        self.assertEqual(await self._decision(), "carry_out")

    async def test_without_approval_outside_limits_waits_for_the_manager(self) -> None:
        limits = ApprovalLimits(person_ids=[FINANCE_LEAD.id], per_day=5)
        self.assertEqual(await self._decision(limits=limits), "ask_manager")
        self.assertEqual(
            await self._decision(limits=ApprovalLimits(per_day=5), count_today=5), "ask_manager"
        )
        self.assertEqual(
            await self._decision(limits=ApprovalLimits(per_day=5), count_today=4), "carry_out"
        )

    async def test_purchases_cannot_be_allowed(self) -> None:
        self.assertEqual(
            await self._decision(kind=ActionKind.PURCHASE, approved_by_manager=True), "declined"
        )

    async def test_carry_out_once_and_undo(self) -> None:
        item = self._request(kind=ActionKind.ADD_ACTION_ITEM, effect="The item is added.")
        first = await self.s.approval.carry_out(item)
        again = await self.s.approval.carry_out(item)
        self.assertEqual((first.effect, first.undoable), ("The item is added.", True))
        self.assertIs(first, again)
        self.assertTrue((await self.s.approval.undo(item)).done)
        self.assertFalse((await self.s.approval.undo(item)).done)

    async def test_messages_cannot_be_undone(self) -> None:
        message = self._request()
        self.assertFalse((await self.s.approval.carry_out(message)).undoable)
        self.assertEqual((await self.s.approval.undo(message)).reason, "This cannot be undone.")

    async def test_failing_carry_out(self) -> None:
        self.s.approval.failing = True
        result = await self.s.approval.carry_out(self._request())
        self.assertFalse(result.done)
        self.assertTrue(result.reason)


class ListsStandInTest(unittest.IsolatedAsyncioTestCase):
    """Story 1.1 - notices and records are kept in lists and can be told to fail."""

    async def asyncSetUp(self) -> None:
        self.s = build_standins()
        self.notice = Notice(
            organisation_id=ORGANISATION_ID,
            person_id=LEGAL_LEAD.id,
            subject_kind="action_item",
            subject_id="i",
            text="Something waits for you.",
        )
        self.record = AuditRecord(
            name="assistant.item_added",
            organisation_id=ORGANISATION_ID,
            who=OPERATIONS_DIRECTOR.id,
            item_kind="action_item",
            item_id="i",
            state_before="none",
            state_after="waiting_for_owner",
            at=DEFAULT_START,
        )

    async def test_notices(self) -> None:
        self.assertTrue((await self.s.notices.notify(self.notice)).delivered)
        self.s.notices.failing = True
        self.assertFalse((await self.s.notices.notify(self.notice)).delivered)
        self.assertEqual(self.s.notices.sent, [self.notice])

    async def test_records(self) -> None:
        self.assertTrue((await self.s.records.write(self.record)).written)
        self.s.records.failing = True
        self.assertFalse((await self.s.records.write(self.record)).written)
        self.assertEqual(self.s.records.written, [self.record])


class MovableClockTest(unittest.IsolatedAsyncioTestCase):
    """Story 1.1 - moving the clock runs due steps in order."""

    async def asyncSetUp(self) -> None:
        self.start = datetime(2026, 11, 23, 8, 0, tzinfo=UTC)
        self.clock = MovableClock(self.start)
        self.seen: list[tuple[str, datetime]] = []

    def _schedule(self, name: str, offset: timedelta) -> None:
        async def step() -> None:
            self.seen.append((name, await self.clock.now()))

        self.clock.at(self.start + offset, step, name)

    async def test_forward_an_hour_runs_steps_on_the_way_in_time_order(self) -> None:
        self._schedule("c", timedelta(minutes=50))
        self._schedule("a", timedelta(minutes=10))
        self._schedule("b", timedelta(minutes=30))
        self._schedule("later", timedelta(minutes=61))
        ran = await self.clock.forward_hour()
        self.assertEqual([r.name for r in ran], ["a", "b", "c"])
        self.assertEqual(
            self.seen,
            [
                ("a", self.start + timedelta(minutes=10)),
                ("b", self.start + timedelta(minutes=30)),
                ("c", self.start + timedelta(minutes=50)),
            ],
        )
        self.assertEqual(await self.clock.now(), self.start + timedelta(hours=1))

    async def test_forward_a_day_includes_steps_added_on_the_way(self) -> None:
        async def first() -> None:
            self.seen.append(("first", await self.clock.now()))
            self._schedule("added", timedelta(hours=5))

        self.clock.at(self.start + timedelta(hours=2), first, "first")
        self._schedule("middle", timedelta(hours=3))
        await self.clock.forward_day()
        self.assertEqual([name for name, _ in self.seen], ["first", "middle", "added"])
        self.assertEqual(await self.clock.now(), self.start + timedelta(days=1))

    async def test_to_next_due_step(self) -> None:
        self._schedule("x", timedelta(hours=3))
        self._schedule("x2", timedelta(hours=3))
        self._schedule("y", timedelta(hours=4))
        ran = await self.clock.to_next_due()
        self.assertEqual([r.name for r in ran], ["x", "x2"])
        self.assertEqual(await self.clock.now(), self.start + timedelta(hours=3))
        ran = await self.clock.to_next_due()
        self.assertEqual([r.name for r in ran], ["y"])
        self.assertEqual(await self.clock.now(), self.start + timedelta(hours=4))

    async def test_to_next_due_with_nothing_due_stays(self) -> None:
        self.assertEqual(await self.clock.to_next_due(), [])
        self.assertEqual(await self.clock.now(), self.start)

    async def test_sources_run_in_time_order_with_scheduled_steps(self) -> None:
        dues = [self.start + timedelta(hours=h) for h in (1, 4)]

        async def next_due() -> datetime | None:
            return dues[0] if dues else None

        async def run_due(at: datetime) -> None:
            self.seen.append(("source", at))
            dues.pop(0)

        self.clock.add_source("run what is due", next_due, run_due)
        self._schedule("step", timedelta(hours=2))
        await self.clock.forward_day()
        self.assertEqual(
            self.seen,
            [
                ("source", self.start + timedelta(hours=1)),
                ("step", self.start + timedelta(hours=2)),
                ("source", self.start + timedelta(hours=4)),
            ],
        )

    async def test_never_moves_back(self) -> None:
        with self.assertRaises(ValueError):
            await self.clock.forward(timedelta(seconds=-1))
        with self.assertRaises(ValueError):
            await self.clock.move_to(self.start - timedelta(seconds=1))

    async def test_a_source_that_never_settles_is_stopped(self) -> None:
        async def next_due() -> datetime | None:
            return self.start

        async def run_due(at: datetime) -> None:
            return None

        clock = MovableClock(self.start, max_steps_per_move=50)
        clock.add_source("stuck", next_due, run_due)
        with self.assertRaises(RuntimeError):
            await clock.forward_hour()


if __name__ == "__main__":
    unittest.main()
