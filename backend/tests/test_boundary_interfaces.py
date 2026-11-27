"""Story 1.1: the boundary interfaces, the module factory and boundary failures."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from assistant.boundary.interfaces import Boundary
from assistant.errors import BoundaryUnavailable, NotFound
from assistant.models.boundary import (
    ActionKind,
    ApprovalLevel,
    ApprovalRequest,
    AuditRecord,
    CarryOutResult,
    CheckOutcome,
    InputPack,
    Notice,
    ProjectRecord,
    StartResult,
    WorkKind,
    WorkOrder,
)
from assistant.module import create_module
from assistant.rules.sentences import is_plain_sentence
from assistant.service.guard import PART_SENTENCES
from assistant.store.sqlite import SqliteStore
from tests.fakes import FIXED_NOW, spy_boundary

ORG = "org-1"


def _approval_request() -> ApprovalRequest:
    return ApprovalRequest(
        request_id=uuid4(),
        organisation_id=ORG,
        manager_id="m",
        assistant_flow_id="a",
        kind=ActionKind.SEND_MESSAGE,
        effect="A message is sent.",
        level=ApprovalLevel.WITH_APPROVAL,
    )


class ModuleFactoryTest(unittest.IsolatedAsyncioTestCase):
    """Story 1.1 - all outside needs go through the injected boundary."""

    async def test_every_part_is_reached_through_the_injected_bundle(self) -> None:
        injected, calls = spy_boundary()
        module = create_module(injected, SqliteStore())
        b = module.boundary

        await b.people.person_asking({})
        await b.people.person("p")
        await b.people.find_by_name(ORG, "x")
        await b.people.is_administrator("p")
        await b.work_data.projects("m")
        await b.work_data.assignments("m")
        await b.assistant.start_work(
            WorkOrder(
                work_id=uuid4(),
                organisation_id=ORG,
                assistant_flow_id="a",
                kind=WorkKind.REQUEST,
                input_pack=InputPack(),
            )
        )
        await b.approval.check(_approval_request())
        await b.approval.carry_out(_approval_request())
        await b.approval.undo(_approval_request())
        await b.notices.notify(
            Notice(organisation_id=ORG, person_id="p", subject_kind="k", subject_id="i", text="t")
        )
        await b.records.write(
            AuditRecord(
                name="assistant.thing_done",
                organisation_id=ORG,
                who="p",
                item_kind="k",
                item_id="i",
                state_before="none",
                state_after="open",
                at=FIXED_NOW,
            )
        )
        self.assertEqual(await b.clock.now(), FIXED_NOW)

        self.assertEqual(
            calls,
            [
                "people.person_asking",
                "people.person",
                "people.find_by_name",
                "people.is_administrator",
                "work_data.projects",
                "work_data.assignments",
                "assistant.start_work",
                "approval.check",
                "approval.carry_out",
                "approval.undo",
                "notices.notify",
                "records.write",
                "clock.now",
            ],
        )

    def test_factory_takes_one_boundary_bundle(self) -> None:
        with self.assertRaises(TypeError):
            create_module(object(), SqliteStore())  # type: ignore[arg-type]

    def test_bundle_refuses_a_part_that_is_not_its_interface(self) -> None:
        injected, _ = spy_boundary()
        with self.assertRaises(TypeError):
            Boundary(
                people=injected.people,
                work_data=injected.work_data,
                assistant=injected.assistant,
                approval=injected.approval,
                notices=injected.notices,
                records=object(),  # type: ignore[arg-type]
                clock=injected.clock,
            )


class BoundaryFailureTest(unittest.IsolatedAsyncioTestCase):
    """Story 1.1 - a boundary failure is safe and plain."""

    async def test_any_failure_becomes_one_plain_sentence(self) -> None:
        injected, _ = spy_boundary()
        module = create_module(injected, SqliteStore())
        for part_name in PART_SENTENCES:
            getattr(injected, part_name).raise_with = ConnectionError("socket closed\ntrace")
        nothing: Any = None
        attempts = {
            "people": lambda: module.boundary.people.person("p"),
            "work_data": lambda: module.boundary.work_data.projects("m"),
            "assistant": lambda: module.boundary.assistant.start_work(nothing),
            "approval": lambda: module.boundary.approval.check(_approval_request()),
            "notices": lambda: module.boundary.notices.notify(nothing),
            "records": lambda: module.boundary.records.write(nothing),
            "clock": lambda: module.boundary.clock.now(),
        }
        for part_name, attempt in attempts.items():
            with self.subTest(part_name):
                with self.assertRaises(BoundaryUnavailable) as caught:
                    await attempt()
                self.assertTrue(is_plain_sentence(caught.exception.message))
                self.assertNotIn("socket", caught.exception.message)
                self.assertEqual(caught.exception.status, 503)

    async def test_module_errors_pass_through_unchanged(self) -> None:
        injected, _ = spy_boundary()
        injected.people.raise_with = NotFound()  # type: ignore[attr-defined]
        with self.assertRaises(NotFound):
            await create_module(injected, SqliteStore()).boundary.people.person("p")

    async def test_a_clock_that_is_not_utc_is_a_failure(self) -> None:
        injected, _ = spy_boundary()
        clock: Any = injected.clock
        clock.value = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1)))
        with self.assertRaises(BoundaryUnavailable):
            await create_module(injected, SqliteStore()).boundary.clock.now()

    def test_every_part_sentence_is_plain(self) -> None:
        for sentence in PART_SENTENCES.values():
            self.assertTrue(is_plain_sentence(sentence), sentence)


class ReturnShapesTest(unittest.TestCase):
    """Story 1.1 - the return shapes of section 9.1."""

    def test_amounts_are_text_with_two_decimals(self) -> None:
        base = {
            "id": "p1",
            "name": "CRM rollout",
            "owner_id": "o",
            "spend": "10.00",
            "source_label": "Flow projects",
            "read_at": FIXED_NOW,
        }
        self.assertEqual(ProjectRecord(**base, budget="125000.00").budget, "125000.00")
        for bad in ("125000", "1.5", "1,000.00", 12.5):
            with self.subTest(bad), self.assertRaises(ValidationError):
                ProjectRecord(**base, budget=bad)

    def test_read_time_must_be_utc(self) -> None:
        with self.assertRaises(ValidationError):
            ProjectRecord(
                id="p",
                name="n",
                owner_id="o",
                budget="1.00",
                spend="0.00",
                source_label="s",
                read_at=datetime(2026, 1, 1),
            )

    def test_check_outcomes(self) -> None:
        for outcome in ("carry_out", "ask_manager"):
            self.assertEqual(CheckOutcome(outcome=outcome).outcome, outcome)
        self.assertEqual(CheckOutcome(outcome="declined", reason="No.").reason, "No.")
        with self.assertRaises(ValidationError):
            CheckOutcome(outcome="declined")
        with self.assertRaises(ValidationError):
            CheckOutcome(outcome="maybe")  # type: ignore[arg-type]

    def test_carry_out_has_effect_and_undoable_flag_or_reason(self) -> None:
        done = CarryOutResult(done=True, effect="The item is added.", undoable=True)
        self.assertTrue(done.undoable)
        with self.assertRaises(ValidationError):
            CarryOutResult(done=True)
        with self.assertRaises(ValidationError):
            CarryOutResult(done=False)

    def test_refused_work_needs_one_sentence(self) -> None:
        with self.assertRaises(ValidationError):
            StartResult(accepted=False)


if __name__ == "__main__":
    unittest.main()
