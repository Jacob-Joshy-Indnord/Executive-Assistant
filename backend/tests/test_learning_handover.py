"""Part 2, Epic 3 - Remove everything at handover or end (task-zbl9d-E5WpU).

When the assistant is handed over or ended, using part 1's steps for these, its lessons,
signals, notes, transcripts and deliverables are removed and can no longer be read. Every
booking for a meeting that has not ended and every request waiting on an organiser is
withdrawn, and an assistant in a meeting leaves it; no transcript is asked for and no notes
are written for that meeting. Suggestions go the same way.
"""

from __future__ import annotations

import unittest
from datetime import timedelta
from typing import Any
from uuid import UUID

from assistant.errors import BoundaryUnavailable
from assistant.models.records import (
    ApprovalSuggestion,
    AttendanceState,
    Briefing,
    DecisionPaper,
    Lesson,
    Meeting,
    MeetingAttendance,
    MeetingNotes,
    PersonRequest,
    PersonRequestState,
    RemovedLesson,
    Signal,
    TakenDecision,
)
from standins.sample import ORGANISATION_ID, PROGRAMME_LEAD
from tests.item_helpers import EpicFour
from tests.world import SEEDED_MEETING, World, build_world


class HandOverOrEndCase(EpicFour, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        self.s = self.w.seeded
        self.ops = self.s.assistant
        self.t = await self.w.standins.clock.now()
        self.manager = self.ops.manager_id

    async def asyncTearDown(self) -> None:
        self.w.close()

    async def hand_over(self) -> None:
        await self.w.module.flow.hand_over(ORGANISATION_ID, self.ops.flow_id, PROGRAMME_LEAD.id)

    async def end(self) -> None:
        await self.w.module.flow.end(ORGANISATION_ID, self.ops.flow_id)

    async def mine(self, record_type: Any) -> list[Any]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return list(await tx.find(record_type, assistant_id=self.ops.id))

    async def get(self, record_type: Any, record_id: UUID) -> Any:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return await tx.get(record_type, record_id)

    async def add_attendance(self, state: AttendanceState, **changes: Any) -> MeetingAttendance:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return await tx.add(
                MeetingAttendance(
                    organisation_id=ORGANISATION_ID,
                    assistant_id=self.ops.id,
                    created_at=self.t,
                    manager_id=self.manager,
                    meeting_ref=changes.pop("meeting_ref", SEEDED_MEETING.id),
                    organiser_id=self.manager,
                    starts_at=changes.pop("starts_at", self.t - timedelta(minutes=30)),
                    ends_at=changes.pop("ends_at", self.t + timedelta(minutes=30)),
                    state=state,
                    **changes,
                )
            )


class RemovedAtHandOverAndEndTest(HandOverOrEndCase):
    async def check_everything_learned_and_prepared_is_gone(self) -> None:
        for record_type in (
            Lesson,
            RemovedLesson,
            Signal,
            ApprovalSuggestion,
            MeetingNotes,
            Meeting,
            Briefing,
            DecisionPaper,
        ):
            self.assertEqual(await self.mine(record_type), [], record_type.__name__)
        # ... and can no longer be read, by the manager or anyone else.
        for path in (
            f"/learning/lessons/{self.s.lesson.id}",
            f"/learning/signals/{self.s.signal.id}",
            f"/learning/suggestions/{self.s.suggestion.id}",
            f"/meeting-notes/{self.s.notes.id}",
            f"/meetings/{self.s.shared_meeting.id}",
            f"/briefings/{self.s.briefing.id}",
            f"/decision-papers/{self.s.paper.id}",
        ):
            for person in (self.manager, PROGRAMME_LEAD.id):
                self.assertEqual(self.call("GET", path, person).status_code, 404, path)

    async def test_handing_over_removes_what_the_assistant_learned_and_prepared(self) -> None:
        self.ok("DELETE", f"/learning/lessons/{self.s.lesson.id}", self.manager)  # a digest
        self.ok("POST", "/learning/lessons", self.manager, {"text": "Keep messages short."})
        await self.hand_over()
        await self.check_everything_learned_and_prepared_is_gone()
        # The new manager starts with an empty record.
        page = self.ok("GET", "/learning/lessons", PROGRAMME_LEAD.id)
        self.assertEqual(page["lessons"], [])

    async def test_ending_removes_what_the_assistant_learned_and_prepared(self) -> None:
        await self.end()
        await self.check_everything_learned_and_prepared_is_gone()

    async def test_each_removal_is_recorded(self) -> None:
        await self.end()
        for name in (
            "lesson_removed",
            "signal_removed",
            "approval_suggestion_removed",
            "meeting_notes_removed",
            "transcript_removed",
            "briefing_removed",
            "decision_paper_removed",
        ):
            records = self.audited(f"assistant.{name}")
            self.assertTrue(records, name)
            self.assertTrue(all(r.details.get("because") == "ended" for r in records), name)

    async def test_taken_decisions_and_shared_notes_in_flow_stay(self) -> None:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            taken = await tx.add(
                TakenDecision(
                    organisation_id=ORGANISATION_ID,
                    assistant_id=self.ops.id,
                    created_at=self.t,
                    paper_id=self.s.paper.id,
                    version=1,
                    option=1,
                    text="Renew it",
                    decided_by=self.manager,
                )
            )
        self.ok("POST", f"/meeting-notes/{self.s.notes.id}/share", self.manager)
        self.assertEqual(len(self.w.standins.knowledge.notes), 1)
        await self.hand_over()
        self.assertIsNotNone(await self.get(TakenDecision, taken.id))
        self.assertEqual(len(self.w.standins.knowledge.notes), 1)
        self.assertNotIn("remove", [told for told, _ in self.w.standins.knowledge.told])

    async def test_suggestions_go_without_changing_a_level(self) -> None:
        before = self.ok("GET", "/approval-levels", self.manager)
        message = next(level for level in before if level["kind"] == "send_message")
        self.assertEqual(message["level"], "with_approval")
        await self.hand_over()
        self.assertEqual(await self.mine(ApprovalSuggestion), [])
        self.assertFalse(self.audited("assistant.approval_suggestion_accepted"))


class MeetingsAtHandOverAndEndTest(HandOverOrEndCase):
    async def test_a_booking_waiting_on_the_organiser_and_its_request_are_withdrawn(
        self,
    ) -> None:
        await self.hand_over()
        attendance = await self.get(MeetingAttendance, self.s.attendance.id)
        self.assertEqual(attendance.state, AttendanceState.WITHDRAWN)
        self.assertEqual(attendance.because, "handed_over")
        self.assertFalse(attendance.transcript_wanted)
        ask = await self.get(PersonRequest, self.s.organiser_ask.id)
        self.assertEqual(ask.state, PersonRequestState.WITHDRAWN)
        # The organiser sees the request withdrawn.
        organiser = self.s.organiser_ask.person_id
        view = self.ok("GET", f"/meeting-requests/{self.s.organiser_ask.id}", organiser)
        self.assertEqual(view["state"], "withdrawn")

    async def test_a_booking_is_withdrawn_when_the_assistant_is_ended(self) -> None:
        booked = await self.add_attendance(
            AttendanceState.BOOKED,
            meeting_ref="later-meeting",
            starts_at=self.t + timedelta(days=2),
            ends_at=self.t + timedelta(days=2, hours=1),
        )
        await self.end()
        withdrawn = await self.get(MeetingAttendance, booked.id)
        self.assertEqual((withdrawn.state, withdrawn.because), (AttendanceState.WITHDRAWN, "ended"))

    async def test_an_assistant_in_a_meeting_leaves_it_and_asks_for_no_transcript(self) -> None:
        attending = await self.add_attendance(AttendanceState.ATTENDING, meeting_ref="now-meeting")
        joining = await self.add_attendance(AttendanceState.JOINING, meeting_ref="soon-meeting")
        await self.end()
        for record in (attending, joining):
            left = await self.get(MeetingAttendance, record.id)
            self.assertEqual(left.state, AttendanceState.LEFT)
            self.assertEqual(left.because, "ended")
            self.assertFalse(left.transcript_wanted)
            self.assertIn((record.meeting_ref, self.ops.flow_id), self.w.standins.meetings.left)
        # Time passes: no transcript is asked for and no notes are written.
        await self.later(hours=3)
        await self.run_due()
        for record in (attending, joining):
            self.assertIsNone((await self.get(MeetingAttendance, record.id)).meeting_id)
        self.assertEqual(await self.mine(Meeting), [])
        self.assertEqual(await self.mine(MeetingNotes), [])

    async def test_a_meeting_left_before_its_transcript_came_asks_for_none(self) -> None:
        left = await self.add_attendance(
            AttendanceState.LEFT,
            meeting_ref="ended-meeting",
            ends_at=self.t - timedelta(minutes=5),
            left_at=self.t - timedelta(minutes=5),
        )
        await self.hand_over()
        self.assertFalse((await self.get(MeetingAttendance, left.id)).transcript_wanted)

    async def test_a_booking_for_a_meeting_that_has_ended_is_left_as_it_is(self) -> None:
        over = await self.add_attendance(
            AttendanceState.BOOKED,
            meeting_ref="over-meeting",
            starts_at=self.t - timedelta(hours=2),
            ends_at=self.t - timedelta(hours=1),
        )
        await self.end()
        self.assertEqual((await self.get(MeetingAttendance, over.id)).state, AttendanceState.BOOKED)

    async def test_a_meeting_service_that_cannot_be_reached_does_not_stop_the_end(self) -> None:
        attending = await self.add_attendance(AttendanceState.ATTENDING, meeting_ref="now")

        async def unreachable(meeting_id: str, assistant_flow_id: str) -> Any:
            raise BoundaryUnavailable()

        self.w.standins.meetings.leave = unreachable  # type: ignore[method-assign]
        await self.end()
        left = await self.get(MeetingAttendance, attending.id)
        self.assertEqual(left.state, AttendanceState.LEFT)


if __name__ == "__main__":
    unittest.main()
