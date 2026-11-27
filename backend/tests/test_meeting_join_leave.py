"""Part 2, Stories "Join and leave" and "A summary, not a transcript" (task-EJiUSI8eAMg,
task-p7Ngw92weMA, task-hGtxCFEkks8)."""

from __future__ import annotations

import unittest
from datetime import timedelta

from assistant.models.records import AttendanceState, MeetingState
from assistant.module import create_module
from assistant.store.sqlite import SqliteStore
from standins import build_standins
from standins.meetings import sample_meetings
from standins.sample import FINANCE_LEAD, OPERATIONS_DIRECTOR, ORGANISATION_ID, SALES_DIRECTOR
from tests.meeting_helpers import MANAGER, ORGANISER, OWNER, VENDORS, captured_logs
from tests.test_meeting_attendance import AttendingTest
from tests.world import viewer

REVIEW, VENDOR, PARTNER = sample_meetings()


class JoinWhenTheMeetingStartsTest(AttendingTest):
    """Join and leave: join when the meeting starts (task-EJiUSI8eAMg)."""

    async def test_the_assistant_is_let_in_as_a_silent_note_taker(self) -> None:
        """Join and leave - when the meeting starts, the module asks the meeting service,
        through the boundary, to let the assistant in as a silent note-taker."""
        self.attend(REVIEW)
        await self.at(REVIEW.starts_at - timedelta(minutes=1))
        self.assertEqual(self.joined(REVIEW), [])
        await self.at(REVIEW.starts_at)
        (request,) = self.joined(REVIEW)
        self.assertEqual(request.role, "silent_note_taker")
        self.assertEqual(request.manager_id, MANAGER)
        attendance = await self.booking(REVIEW)
        self.assertEqual(
            (attendance.state, attendance.join_asked_at),
            (AttendanceState.ATTENDING, REVIEW.starts_at),
        )

    async def test_a_refused_join_stops_and_the_manager_is_told_why(self) -> None:
        """Join and leave - a refused join stops, and the manager is told why."""
        self.attend(PARTNER)
        await self.at(PARTNER.starts_at)
        attendance = await self.booking(PARTNER)
        self.assertEqual(
            (attendance.state, attendance.because),
            (AttendanceState.FAILED, "join_refused"),
        )
        self.assertIn(
            "Your assistant could not join Partner briefing because the meeting service "
            "refused it.",
            self.notices_to(MANAGER),
        )
        await self.at(PARTNER.starts_at + timedelta(minutes=10))
        self.assertEqual(len(self.joined(PARTNER)), 1)

    async def test_a_long_waiting_join_stops_after_10_minutes(self) -> None:
        """Join and leave - still waiting to be let in after 10 minutes, it stops trying and the
        manager is told why."""
        self.w.standins.meetings.kept_waiting = True
        self.attend(REVIEW)
        await self.at(REVIEW.starts_at)
        self.assertEqual((await self.booking(REVIEW)).state, AttendanceState.JOINING)
        await self.at(REVIEW.starts_at + timedelta(minutes=9))
        self.assertEqual((await self.booking(REVIEW)).state, AttendanceState.JOINING)
        await self.at(REVIEW.starts_at + timedelta(minutes=10))
        attendance = await self.booking(REVIEW)
        self.assertEqual(
            (attendance.state, attendance.because),
            (AttendanceState.FAILED, "not_let_in"),
        )
        self.assertIn((REVIEW.id, self.w.seeded.assistant.flow_id), self.w.standins.meetings.left)
        self.assertIn(
            "Your assistant was not let in to Weekly operations review within 10 minutes, so it "
            "stopped trying.",
            self.notices_to(MANAGER),
        )
        asked = len(self.joined(REVIEW))
        await self.at(REVIEW.ends_at + timedelta(hours=3))
        self.assertEqual(len(self.joined(REVIEW)), asked)

    async def test_an_assistant_let_in_while_waiting_attends(self) -> None:
        self.w.standins.meetings.kept_waiting = True
        self.attend(REVIEW)
        await self.at(REVIEW.starts_at)
        self.w.standins.meetings.kept_waiting = False
        await self.at(REVIEW.starts_at + timedelta(minutes=4))
        self.assertEqual((await self.booking(REVIEW)).state, AttendanceState.ATTENDING)

    async def test_a_paused_assistant_does_not_join(self) -> None:
        """Join and leave - a paused assistant does not join."""
        self.attend(REVIEW)
        self.ok("POST", "/assistant/pause", MANAGER)
        await self.at(REVIEW.starts_at)
        await self.at(REVIEW.starts_at + timedelta(minutes=30))
        self.assertEqual(self.joined(REVIEW), [])
        self.assertEqual((await self.booking(REVIEW)).state, AttendanceState.BOOKED)

    async def test_an_ended_assistant_does_not_join(self) -> None:
        """Join and leave - an ended assistant does not join."""
        self.attend(REVIEW)
        await self.w.module.flow.end(ORGANISATION_ID, self.w.seeded.assistant.flow_id)
        await self.at(REVIEW.starts_at)
        self.assertEqual(self.joined(REVIEW), [])

    async def test_an_assistant_its_manager_has_not_opened_does_not_join(self) -> None:
        """Join and leave - an assistant whose manager has not opened it yet does not join."""
        standins = build_standins()
        module = create_module(standins.boundary, SqliteStore())
        await module.flow.set_up(ORGANISATION_ID, OPERATIONS_DIRECTOR.id, "Assistant", "flow-new")
        await module.services.organisation.change(
            viewer("admin", administrator=True), meeting_notes_allowed=True
        )
        await module.services.attendance.attend(viewer(MANAGER), REVIEW.id)
        await standins.clock.move_to(REVIEW.starts_at)
        await module.flow.run_due()
        self.assertEqual(standins.meetings.join_requests, [])

    async def test_a_due_step_that_runs_twice_late_or_in_two_places_joins_once(self) -> None:
        """Join and leave - the step that does what is due runs twice, late or in two places,
        and the assistant joins once."""
        self.attend(REVIEW)
        elsewhere = create_module(self.w.standins.boundary, self.w.store)
        await self.w.standins.clock.move_to(REVIEW.starts_at + timedelta(minutes=5))  # late
        await self.w.module.flow.run_due()
        await elsewhere.flow.run_due()
        await self.w.module.flow.run_due()
        self.assertEqual(len(self.joined(REVIEW)), 1)
        self.assertEqual((await self.booking(REVIEW)).state, AttendanceState.ATTENDING)
        joins = [
            r
            for r in self.w.standins.records.written
            if r.item_kind == "meeting_attendance" and r.state_after == "attending"
        ]
        self.assertEqual(len(joins), 1)


class LeaveAndAskForTheTranscriptTest(AttendingTest):
    """Join and leave: leave and ask for the transcript (task-p7Ngw92weMA)."""

    async def attended(self, meeting: object = REVIEW) -> None:
        self.attend(meeting)
        await self.at(REVIEW.starts_at)

    async def test_the_assistant_leaves_at_the_end_and_asks_for_the_transcript(self) -> None:
        """Join and leave - when the meeting ends, the assistant leaves and asks for the
        transcript."""
        await self.attended()
        await self.at(REVIEW.ends_at)
        attendance = await self.booking(REVIEW)
        self.assertEqual(
            (attendance.state, attendance.left_at),
            (AttendanceState.LEFT, REVIEW.ends_at),
        )
        self.assertIsNone(attendance.because)
        self.assertEqual(
            self.w.standins.meetings.left,
            [(REVIEW.id, self.w.seeded.assistant.flow_id)],
        )
        self.assertIsNone(attendance.meeting_id)  # asked; it comes 10 minutes later
        await self.at(REVIEW.ends_at + timedelta(minutes=10))
        self.assertEqual((await self.booking(REVIEW)).state, AttendanceState.WRITING_NOTES)

    async def test_the_manager_is_told_when_no_transcript_has_come_after_2_hours(self) -> None:
        """Join and leave - when no transcript has come 2 hours after the meeting ended, the
        manager is told that no notes can be written."""
        self.w.standins.meetings.no_transcript = True
        await self.attended()
        await self.at(REVIEW.ends_at + timedelta(hours=1, minutes=50))
        self.assertEqual((await self.booking(REVIEW)).state, AttendanceState.LEFT)
        await self.at(REVIEW.ends_at + timedelta(hours=2))
        attendance = await self.booking(REVIEW)
        self.assertEqual(
            (attendance.state, attendance.because),
            (AttendanceState.FAILED, "no_transcript"),
        )
        self.assertIn(
            "No transcript came for your meeting, so no notes can be written.",
            self.notices_to(MANAGER),
        )

    async def test_a_transcript_that_arrives_is_kept_as_numbered_parts(self) -> None:
        """Join and leave - the transcript is kept as numbered parts under the rules of 'Write
        the notes, with sources', and the attendance record is in state writing notes."""
        await self.attended()
        await self.at(REVIEW.ends_at + timedelta(minutes=10))
        attendance = await self.booking(REVIEW)
        self.assertEqual(attendance.state, AttendanceState.WRITING_NOTES)
        meeting = await self.meeting(attendance.meeting_id or "")
        self.assertEqual(
            (meeting.title, meeting.organiser_id, meeting.meeting_date, meeting.origin),
            ("Weekly operations review", MANAGER, REVIEW.starts_at.date(), "attended"),
        )
        self.assertTrue(meeting.organiser_agreed)
        self.assertFalse(meeting.ran_it or meeting.people_knew)
        self.assertEqual(meeting.state, MeetingState.WRITING)
        self.assertEqual([p.number for p in meeting.parts], list(range(1, 9)))
        self.assertEqual(
            (meeting.parts[1].starts, meeting.parts[1].speaker), ("00:06:00.000", "Legal lead")
        )
        self.assertEqual(meeting.text_arrived_at, REVIEW.ends_at + timedelta(minutes=10))
        self.assertEqual(len(self.started_notes()), 1)

    async def test_the_text_of_an_attended_meeting_is_removed_when_the_notes_pass(self) -> None:
        """Join and leave - the text is removed once the notes have passed the checks; the part
        numbers and times stay."""
        await self.attended()
        await self.at(REVIEW.ends_at + timedelta(minutes=10))
        await self.finish_notes()
        meeting = await self.meeting((await self.booking(REVIEW)).meeting_id or "")
        self.assertEqual(meeting.state, MeetingState.READY)
        self.assertFalse(meeting.text_kept)
        self.assertTrue(all(p.starts for p in meeting.parts))

    async def test_the_text_of_an_attended_meeting_is_removed_after_7_days(self) -> None:
        """Join and leave - 7 days after the transcript arrived, its text is removed and the
        part numbers and times stay, never in a record, a notice or a log."""
        self.w.standins.assistant.refusing = True  # the notes cannot be written
        with captured_logs(self) as logs:
            await self.attended()
            arrived = REVIEW.ends_at + timedelta(minutes=10)
            await self.at(arrived)
            meeting = await self.meeting((await self.booking(REVIEW)).meeting_id or "")
            self.assertEqual(meeting.state, MeetingState.FAILED)
            await self.at(arrived + timedelta(days=7) - timedelta(minutes=1))
            self.assertTrue((await self.meeting(meeting.id)).text_kept)
            await self.at(arrived + timedelta(days=7))
        removed = await self.meeting(meeting.id)
        self.assertEqual(removed.state, MeetingState.REMOVED)
        self.assertFalse(removed.text_kept)
        self.assertEqual([p.number for p in removed.parts], list(range(1, 9)))
        self.assert_no_transcript_text(logs)


class TellTheManagerTest(AttendingTest):
    """A summary, not a transcript: tell the manager, and review the notes
    (task-hGtxCFEkks8)."""

    async def ready_notes(self) -> str:
        await self.follow(VENDORS)
        self.attend(VENDOR)
        (request,) = self.requests_for(ORGANISER)
        self.ok("POST", f"/meeting-requests/{request['id']}/accept", ORGANISER)
        await self.at(VENDOR.starts_at)
        await self.at(VENDOR.ends_at + timedelta(minutes=10))
        await self.finish_notes()
        attendance = await self.booking(VENDOR)
        self.assertEqual(attendance.state, AttendanceState.READY)
        meeting = await self.meeting(attendance.meeting_id or "")
        self.assertEqual(meeting.state, MeetingState.READY)
        return f"/meeting-notes/{meeting.notes_id}"

    async def test_the_manager_gets_a_notice_and_the_notes_say_whether_they_attended(self) -> None:
        """A summary, not a transcript - when the notes are ready, the manager gets a notice,
        and the notes say whether the manager attended."""
        path = await self.ready_notes()
        self.assertIn("The notes of Vendor review are ready.", self.notices_to(MANAGER))
        notes = self.ok("GET", path, MANAGER)
        self.assertEqual((notes["origin"], notes["manager_attended"]), ("attended", True))
        self.assertEqual(notes["organiser"]["display_name"], "Programme lead")

    async def test_actions_and_statements_are_reviewed_as_for_any_notes(self) -> None:
        """A summary, not a transcript - each action can be kept, edited or removed, and each
        project statement goes to its owner to confirm."""
        path = await self.ready_notes()
        notes = self.ok("GET", path, MANAGER)
        first, second = notes["actions"]
        edited = self.ok(
            "PUT",
            f"{path}/actions/{first['id']}",
            MANAGER,
            {"due_date": "2026-12-07"},
        )
        self.assertEqual(edited["actions"][0]["due_date"], "2026-12-07")
        kept = self.ok("POST", f"{path}/actions/{first['id']}/keep", MANAGER)
        self.assertEqual(kept["actions"][0]["state"], "kept")
        removed = self.ok("POST", f"{path}/actions/{second['id']}/remove", MANAGER)
        self.assertEqual(removed["actions"][1]["state"], "removed")
        (statement,) = notes["statements"]
        self.assertEqual(statement["state"], "awaiting_owner")
        confirm = [r for r in self.ok("GET", "/requests", MANAGER)["requests"]
                   if r["kind"] == "statement_confirm"]  # fmt: skip
        self.assertEqual([r["target"]["id"] for r in confirm], [OWNER])

    async def test_notes_are_visible_to_the_manager_only(self) -> None:
        """A summary, not a transcript - an organiser, an owner or another manager finds
        nothing; an owner sees only their own items and statements."""
        path = await self.ready_notes()
        notes = self.ok("GET", path, MANAGER)
        self.ok("POST", f"{path}/actions/{notes['actions'][0]['id']}/keep", MANAGER)
        for person in (ORGANISER, OWNER, FINANCE_LEAD.id, SALES_DIRECTOR.id):
            with self.subTest(person):
                self.assertEqual(self.call("GET", path, person).status_code, 404)
                found = self.call("GET", "/meeting-notes/search?words=vendor", person)
                if person == SALES_DIRECTOR.id:  # another manager finds only their own: none
                    self.assertEqual(found.json()["count"], 0)
                else:
                    self.assertEqual(found.status_code, 404)
        mine = self.ok("GET", "/my-action-items", OWNER)["items"]
        self.assertEqual([i["text"] for i in mine if i["text"].startswith("Send the signed")],
                         ["Send the signed contracts"])  # fmt: skip
        self.assertEqual(self.ok("GET", "/my-action-items", FINANCE_LEAD.id)["items"], [])


if __name__ == "__main__":
    unittest.main()
