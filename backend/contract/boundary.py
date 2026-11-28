"""Reusable boundary contract (BRD section 9.1), run against any implementation.

Usage::

    class FlowBoundaryContractTest(BoundaryContract, unittest.IsolatedAsyncioTestCase):
        async def make(self) -> BoundaryUnderTest:
            return BoundaryUnderTest(boundary=..., organisation_id=..., ...)

The suite asserts only what section 9.1 promises, so Flow's own
implementation may be stricter (for example decline more) and still pass.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from assistant.boundary.interfaces import Boundary
from assistant.models.boundary import (
    ActionKind,
    ApprovalLevel,
    ApprovalLimits,
    ApprovalRequest,
    AssignmentRecord,
    AuditRecord,
    CarryOutResult,
    CheckOutcome,
    InputPack,
    JoinRequest,
    JoinResult,
    KnowledgeNote,
    KnowledgeResult,
    LeaveResult,
    MeetingInfo,
    Notice,
    NoticeResult,
    PackInput,
    Person,
    ProjectRecord,
    RecordResult,
    StartResult,
    UndoResult,
    WorkKind,
    WorkOrder,
    WorkResult,
)
from assistant.rules.sentences import is_plain_sentence

if TYPE_CHECKING:
    import unittest

    _Base = unittest.IsolatedAsyncioTestCase
else:
    _Base = object


@dataclass(frozen=True)
class BoundaryUnderTest:
    """An implementation, and the few facts about its data the contract relies on."""

    boundary: Boundary
    organisation_id: str
    asking_headers: Mapping[str, str]
    """Request headers that name ``asking_person_id`` as the person asking."""
    asking_person_id: str
    manager_id: str
    """A manager who can see at least one project and has at least one assignment."""
    administrator_id: str
    non_administrator_id: str
    unique_display_name: str
    """The display name of exactly one person or virtual employee of the organisation."""
    assistant_flow_id: str
    meeting_manager_id: str | None = None
    """A manager with at least one upcoming meeting; ``manager_id`` when left out."""


class BoundaryContract(_Base):
    subject: BoundaryUnderTest

    async def make(self) -> BoundaryUnderTest:
        raise NotImplementedError

    async def asyncSetUp(self) -> None:
        self.subject = await self.make()

    @property
    def b(self) -> Boundary:
        return self.subject.boundary

    async def _assert_utc_recent(self, when: Any) -> None:
        self.assertIsNotNone(when.tzinfo)
        self.assertEqual(when.utcoffset(), timedelta(0))

    def _request(self, **changes: Any) -> ApprovalRequest:
        base: dict[str, Any] = {
            "request_id": uuid4(),
            "organisation_id": self.subject.organisation_id,
            "manager_id": self.subject.manager_id,
            "assistant_flow_id": self.subject.assistant_flow_id,
            "kind": ActionKind.ADD_ACTION_ITEM,
            "target_id": self.subject.non_administrator_id,
            "effect": "An action item is added.",
            "level": ApprovalLevel.WITH_APPROVAL,
        }
        return ApprovalRequest(**{**base, **changes})

    # People -----------------------------------------------------------------

    async def test_people_names_the_person_asking(self) -> None:
        person = await self.b.people.person_asking(self.subject.asking_headers)
        self.assertIsInstance(person, Person)
        assert person is not None
        self.assertEqual(person.id, self.subject.asking_person_id)
        self.assertEqual(person.organisation_id, self.subject.organisation_id)
        self.assertIn(person.kind, ("person", "virtual_employee"))

    async def test_people_without_a_named_person_returns_none(self) -> None:
        self.assertIsNone(await self.b.people.person_asking({}))

    async def test_people_by_id(self) -> None:
        person = await self.b.people.person(self.subject.manager_id)
        assert person is not None
        self.assertEqual(person.id, self.subject.manager_id)
        self.assertTrue(person.display_name)
        self.assertIsNone(await self.b.people.person(f"no-such-person-{uuid4()}"))

    async def test_people_search_matches_whole_name_ignoring_case(self) -> None:
        name, org = self.subject.unique_display_name, self.subject.organisation_id
        for variant in (name, name.upper(), name.lower()):
            found = await self.b.people.find_by_name(org, variant)
            self.assertEqual(len(found), 1, variant)
            self.assertEqual(found[0].display_name.casefold(), name.casefold())
            self.assertEqual(found[0].organisation_id, org)
        self.assertEqual(await self.b.people.find_by_name(org, name[:-1]), [])
        self.assertEqual(await self.b.people.find_by_name(org, f"No one {uuid4()}"), [])

    async def test_people_search_stays_in_the_organisation(self) -> None:
        other = f"no-such-organisation-{uuid4()}"
        name = self.subject.unique_display_name
        self.assertEqual(await self.b.people.find_by_name(other, name), [])

    async def test_people_administrator(self) -> None:
        self.assertTrue(await self.b.people.is_administrator(self.subject.administrator_id))
        self.assertFalse(await self.b.people.is_administrator(self.subject.non_administrator_id))

    # Work data --------------------------------------------------------------

    async def test_work_data_projects_have_owner_budget_spend_source_and_time(self) -> None:
        projects = await self.b.work_data.projects(self.subject.manager_id)
        self.assertTrue(projects)
        for project in projects:
            self.assertIsInstance(project, ProjectRecord)
            self.assertRegex(project.budget, r"^-?\d+\.\d{2}$")
            self.assertRegex(project.spend, r"^-?\d+\.\d{2}$")
            self.assertTrue(project.source_label)
            await self._assert_utc_recent(project.read_at)
            owner = await self.b.people.person(project.owner_id)
            assert owner is not None
            self.assertEqual(owner.organisation_id, self.subject.organisation_id)

    async def test_work_data_assignments_belong_to_virtual_employees(self) -> None:
        assignments = await self.b.work_data.assignments(self.subject.manager_id)
        self.assertTrue(assignments)
        for assignment in assignments:
            self.assertIsInstance(assignment, AssignmentRecord)
            self.assertEqual(assignment.manager_id, self.subject.manager_id)
            self.assertTrue(assignment.source_label)
            await self._assert_utc_recent(assignment.read_at)
            employee = await self.b.people.person(assignment.virtual_employee_id)
            assert employee is not None
            self.assertEqual(employee.kind, "virtual_employee")

    async def test_work_data_for_someone_unknown_is_empty(self) -> None:
        nobody = f"no-such-person-{uuid4()}"
        self.assertEqual(await self.b.work_data.projects(nobody), [])
        self.assertEqual(await self.b.work_data.assignments(nobody), [])

    # Assistant --------------------------------------------------------------

    async def test_assistant_accepts_or_refuses_with_one_sentence(self) -> None:
        now = await self.b.clock.now()
        order = WorkOrder(
            work_id=uuid4(),
            organisation_id=self.subject.organisation_id,
            assistant_flow_id=self.subject.assistant_flow_id,
            kind=WorkKind.REQUEST,
            input_pack=InputPack(
                inputs=[PackInput(label="Message from the manager", read_at=now, content="Hello.")]
            ),
        )
        result = await self.b.assistant.start_work(order)
        self.assertIsInstance(result, StartResult)
        if not result.accepted:
            self.assertTrue(is_plain_sentence(result.reason), result.reason)

    def test_a_finished_piece_of_work_can_carry_one_deliverable(self) -> None:
        """Part 2: Flow's "Finish a piece of work" can carry one deliverable, a briefing or a
        decision paper; the rest of the result is as before."""
        deliverable = {"kind": "briefing", "sections": []}
        result = WorkResult(work_id=uuid4(), reply="Done.", deliverable=deliverable)
        self.assertEqual(result.deliverable, deliverable)
        self.assertEqual(WorkResult.model_validate(result.model_dump(mode="json")), result)
        self.assertIsNone(WorkResult(work_id=uuid4()).deliverable)

    # Approval ---------------------------------------------------------------

    async def _check(self, **changes: Any) -> CheckOutcome:
        outcome = await self.b.approval.check(self._request(**changes))
        self.assertIsInstance(outcome, CheckOutcome)
        if outcome.outcome == "declined":
            self.assertTrue(is_plain_sentence(outcome.reason), outcome.reason)
        return outcome

    async def test_approval_not_allowed_is_declined(self) -> None:
        outcome = await self._check(level=ApprovalLevel.NOT_ALLOWED)
        self.assertEqual(outcome.outcome, "declined")

    async def test_approval_purchases_are_always_declined(self) -> None:
        for level in ApprovalLevel:
            with self.subTest(level):
                outcome = await self._check(
                    kind=ActionKind.PURCHASE, level=level, approved_by_manager=True
                )
                self.assertEqual(outcome.outcome, "declined")

    async def test_approval_with_approval_never_carries_out_unapproved(self) -> None:
        outcome = await self._check(level=ApprovalLevel.WITH_APPROVAL)
        self.assertIn(outcome.outcome, ("ask_manager", "declined"))

    async def test_approval_manager_approved_is_carried_out_or_declined(self) -> None:
        outcome = await self._check(level=ApprovalLevel.WITH_APPROVAL, approved_by_manager=True)
        self.assertIn(outcome.outcome, ("carry_out", "declined"))

    async def test_approval_without_approval_outside_limits_is_not_carried_out(self) -> None:
        over_count = await self._check(
            level=ApprovalLevel.WITHOUT_APPROVAL,
            limits=ApprovalLimits(per_day=2),
            count_today=2,
        )
        self.assertNotEqual(over_count.outcome, "carry_out")
        other_person = await self._check(
            level=ApprovalLevel.WITHOUT_APPROVAL,
            limits=ApprovalLimits(person_ids=[self.subject.manager_id]),
            target_id=self.subject.non_administrator_id,
        )
        self.assertNotEqual(other_person.outcome, "carry_out")

    async def test_approval_without_approval_inside_limits_gets_an_answer(self) -> None:
        await self._check(
            level=ApprovalLevel.WITHOUT_APPROVAL,
            limits=ApprovalLimits(per_day=5),
            count_today=0,
        )

    async def test_approval_carry_out_gives_effect_and_undoable_or_reason(self) -> None:
        request = self._request(approved_by_manager=True)
        result = await self.b.approval.carry_out(request)
        self.assertIsInstance(result, CarryOutResult)
        sentence = result.effect if result.done else result.reason
        self.assertTrue(is_plain_sentence(sentence), sentence)
        self.assertIsInstance(result.undoable, bool)
        if result.done and result.undoable:
            undone = await self.b.approval.undo(request)
            self.assertIsInstance(undone, UndoResult)
            if not undone.done:
                self.assertTrue(is_plain_sentence(undone.reason), undone.reason)

    async def test_approval_carry_out_and_undo_are_idempotent_per_request(self) -> None:
        """The module may ask again after losing its own transaction (section 9.1)."""
        request = self._request(approved_by_manager=True)
        first = await self.b.approval.carry_out(request)
        again = await self.b.approval.carry_out(request)
        self.assertEqual((again.done, again.effect, again.undoable),
                         (first.done, first.effect, first.undoable))  # fmt: skip
        if first.done and first.undoable:
            undone = await self.b.approval.undo(request)
            if undone.done:
                self.assertTrue((await self.b.approval.undo(request)).done)

    async def test_approval_undo_of_something_never_carried_out_fails_with_reason(self) -> None:
        result = await self.b.approval.undo(self._request())
        self.assertFalse(result.done)
        self.assertTrue(is_plain_sentence(result.reason), result.reason)

    async def test_approval_without_approval_outside_a_project_limit_waits(self) -> None:
        """Part 2: outside the projects a kind is limited to, a request is never carried out
        without approval, and it is not declined for that either."""
        outcome = await self._check(
            kind=ActionKind.WRITE_MEETING_NOTES,
            level=ApprovalLevel.WITHOUT_APPROVAL,
            limits=ApprovalLimits(project_ids=["project-inside"]),
            project_ids=["project-inside", "project-outside"],
        )
        self.assertEqual(outcome.outcome, "ask_manager")

    # Meetings (part 2) ------------------------------------------------------

    async def _upcoming(self) -> list[MeetingInfo]:
        manager = self.subject.meeting_manager_id or self.subject.manager_id
        meetings = await self.b.meetings.upcoming(manager)
        self.assertTrue(meetings, "the contract needs a manager with an upcoming meeting")
        return meetings

    def _join(self, meeting_id: str) -> JoinRequest:
        return JoinRequest(
            organisation_id=self.subject.organisation_id,
            meeting_id=meeting_id,
            manager_id=self.subject.meeting_manager_id or self.subject.manager_id,
            assistant_flow_id=self.subject.assistant_flow_id,
            display_name="A manager's assistant (notes)",
        )

    async def test_meetings_upcoming_have_organiser_start_end_and_series(self) -> None:
        now = await self.b.clock.now()
        for meeting in await self._upcoming():
            self.assertIsInstance(meeting, MeetingInfo)
            await self._assert_utc_recent(meeting.starts_at)
            self.assertLess(meeting.starts_at, meeting.ends_at)
            self.assertGreater(meeting.ends_at, now)  # upcoming: not ended
            self.assertTrue(meeting.organiser_id)

    async def test_meetings_for_someone_unknown_are_none(self) -> None:
        self.assertEqual(await self.b.meetings.upcoming(f"no-such-person-{uuid4()}"), [])

    async def test_meetings_join_answers_and_asking_again_is_safe(self) -> None:
        meeting = (await self._upcoming())[0]
        first = await self.b.meetings.join(self._join(meeting.id))
        again = await self.b.meetings.join(self._join(meeting.id))
        for result in (first, again):
            self.assertIsInstance(result, JoinResult)
            if result.outcome == "refused":
                self.assertTrue(is_plain_sentence(result.reason), result.reason)
        if first.outcome == "joined":
            self.assertEqual(again.outcome, "joined")

    async def test_meetings_join_as_a_silent_note_taker_only(self) -> None:
        self.assertEqual(self._join("m").role, "silent_note_taker")
        with self.assertRaises(ValueError):
            JoinRequest.model_validate({**self._join("m").model_dump(), "role": "speaker"})

    async def test_meetings_leave_is_idempotent(self) -> None:
        meeting = (await self._upcoming())[0]
        await self.b.meetings.join(self._join(meeting.id))
        for _ in range(2):
            result = await self.b.meetings.leave(meeting.id, self.subject.assistant_flow_id)
            self.assertIsInstance(result, LeaveResult)
            self.assertTrue(result.left)

    async def test_meetings_no_transcript_for_a_meeting_not_attended(self) -> None:
        missing = await self.b.meetings.transcript(f"no-meeting-{uuid4()}", "no-assistant")
        self.assertIsNone(missing)

    # Knowledge (part 2) -----------------------------------------------------

    async def test_knowledge_add_and_remove_are_idempotent(self) -> None:
        note = KnowledgeNote(
            note_id=str(uuid4()),
            organisation_id=self.subject.organisation_id,
            manager_id=self.subject.manager_id,
            title="A meeting",
            date="2026-11-20",
            content="Decision: something was decided (part 1)",
        )
        for _ in range(2):
            added = await self.b.knowledge.add(note)
            self.assertIsInstance(added, KnowledgeResult)
            self.assertTrue(added.done)
        for _ in range(2):
            removed = await self.b.knowledge.remove(note.organisation_id, note.note_id)
            self.assertTrue(removed.done)

    # Notices, records, clock ------------------------------------------------

    async def test_notices_answer_delivered_or_not(self) -> None:
        result = await self.b.notices.notify(
            Notice(
                organisation_id=self.subject.organisation_id,
                person_id=self.subject.non_administrator_id,
                subject_kind="action_item",
                subject_id=str(uuid4()),
                text="Something waits for you.",
            )
        )
        self.assertIsInstance(result, NoticeResult)
        self.assertTrue(result.delivered)

    async def test_records_write_one_record(self) -> None:
        result = await self.b.records.write(
            AuditRecord(
                name="assistant.contract_checked",
                organisation_id=self.subject.organisation_id,
                who=self.subject.manager_id,
                item_kind="contract",
                item_id=str(uuid4()),
                state_before="none",
                state_after="checked",
                at=await self.b.clock.now(),
            )
        )
        self.assertIsInstance(result, RecordResult)
        self.assertTrue(result.written)

    async def test_clock_gives_utc_and_does_not_go_back(self) -> None:
        first = await self.b.clock.now()
        second = await self.b.clock.now()
        await self._assert_utc_recent(first)
        self.assertGreaterEqual(second, first)
