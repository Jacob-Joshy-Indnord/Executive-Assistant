"""Part 2, Stories "Attend for me" and "The organiser agrees" (task-emxs7ABym2g,
task-A2V1LDsgg0I, task-xKg7N_ZHbyQ, task-A5BMOggRojo, task-tuorsXn_dKc)."""

from __future__ import annotations

import ast
import inspect
import re
import unittest
from datetime import timedelta
from pathlib import Path
from typing import Any

from assistant.boundary.interfaces import Boundary, Meetings
from assistant.models.boundary import ActionKind, ApprovalLevel, JoinRequest, WorkResult
from assistant.models.records import (
    AttendanceState,
    MeetingAttendance,
    PersonRequest,
)
from assistant.rules import meetings as rules
from assistant.rules.sentences import is_plain_sentence
from assistant.service.attendance import ALREADY_BOOKED
from standins import build_standins
from standins.meetings import GUEST_REFUSAL, sample_meetings
from standins.sample import (
    DEFAULT_START,
    FINANCE_LEAD,
    OPERATIONS_DIRECTOR,
    ORGANISATION_ID,
    PROGRAMME_LEAD,
    SALES_DIRECTOR,
)
from tests.meeting_helpers import MANAGER, ORGANISER, OWNER, TRANSCRIPT_WORDS, captured_logs
from tests.meeting_helpers import Meetings as Steps
from tests.test_api_visibility import EXPECTATIONS
from tests.work_helpers import SOURCE
from tests.world import SEEDED_MEETING, build_world

MODULE_ROOT = Path(__file__).resolve().parents[1] / "assistant"
REVIEW, VENDOR, PARTNER = sample_meetings()
PRODUCTS = re.compile(
    r"\b(zoom|teams|webex|google meet|skype|slack|vexa|gotomeeting|bluejeans|whereby)\b",
    re.IGNORECASE,
)


class MeetingsBoundaryTest(unittest.IsolatedAsyncioTestCase):
    """Attend for me: add Meetings to the boundary and its stand-in (task-emxs7ABym2g)."""

    async def asyncSetUp(self) -> None:
        self.s = build_standins()

    def test_the_boundary_gains_meetings_and_names_no_product(self) -> None:
        """Attend for me - the boundary has Meetings with four requests and names no product."""
        requests = {
            name
            for name, member in inspect.getmembers(Meetings)
            if inspect.iscoroutinefunction(member)
        }
        self.assertEqual(requests, {"upcoming", "join", "leave", "transcript"})
        self.assertIn("meetings", Boundary.__dataclass_fields__)
        shapes = [
            MODULE_ROOT / "boundary" / "interfaces.py",
            MODULE_ROOT / "models" / "boundary.py",
        ]
        for path in shapes:
            self.assertIsNone(PRODUCTS.search(path.read_text()), path.name)

    def test_the_module_reaches_a_meeting_only_through_the_boundary(self) -> None:
        """Attend for me - nothing in the module connects to a meeting service, records sound
        or joins a meeting; every such request goes through the boundary."""
        banned = {"pyaudio", "sounddevice", "soundfile", "wave", "audioop", "playwright",
                  "selenium", "websockets", "aiortc", "zoomus", "msal", "webrtcvad"}  # fmt: skip
        problems = []
        for path in sorted(MODULE_ROOT.rglob("*.py")):
            source = path.read_text()
            if PRODUCTS.search(source):
                problems.append(f"{path.name} names a product")
            for node in ast.walk(ast.parse(source)):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                problems += [f"{path.name}: {n}" for n in names if n.split(".")[0] in banned]
                if isinstance(node, ast.Attribute) and node.attr in ("join", "leave", "transcript"):
                    owner = ast.unparse(node.value)
                    if owner.endswith("meetings") and not owner.endswith("boundary.meetings"):
                        problems.append(f"{path.name}: {owner}.{node.attr}")
        self.assertEqual(problems, [])

    async def test_the_stand_in_gives_the_operations_director_three_upcoming_meetings(self) -> None:
        """Attend for me - one they organise, one the Programme lead organises, and one with a
        guest from outside the organisation."""
        meetings = await self.s.meetings.upcoming(OPERATIONS_DIRECTOR.id)
        self.assertEqual(len(meetings), 3)
        self.assertEqual(
            [m.organiser_id for m in meetings],
            [OPERATIONS_DIRECTOR.id, PROGRAMME_LEAD.id, OPERATIONS_DIRECTOR.id],
        )
        self.assertIsNotNone(meetings[0].series_id)
        self.assertTrue(PARTNER.outside_guest)
        self.assertEqual(await self.s.meetings.upcoming(SALES_DIRECTOR.id), [])

    async def test_joining_the_meeting_with_an_outside_guest_is_refused(self) -> None:
        """Attend for me - joining the meeting with the guest from outside is refused."""
        result = await self.s.meetings.join(_join(PARTNER.id))
        self.assertEqual((result.outcome, result.reason), ("refused", GUEST_REFUSAL))
        self.assertTrue(is_plain_sentence(result.reason))

    async def test_the_stand_in_has_three_switches(self) -> None:
        """Attend for me - 'kept waiting', 'join refused' and 'no transcript'."""
        self.s.meetings.kept_waiting = True
        self.assertEqual((await self.s.meetings.join(_join(REVIEW.id))).outcome, "waiting")
        self.s.meetings.kept_waiting = False
        self.assertEqual((await self.s.meetings.join(_join(REVIEW.id))).outcome, "joined")
        self.s.meetings.join_refused = True
        self.assertEqual((await self.s.meetings.join(_join(VENDOR.id))).outcome, "refused")
        self.s.meetings.no_transcript = True
        await self.s.clock.move_to(REVIEW.ends_at + timedelta(hours=1))
        self.assertIsNone(await self.s.meetings.transcript(REVIEW.id, "flow-assistant-ops"))

    async def test_the_sample_transcript_comes_10_minutes_after_the_meeting_ends(self) -> None:
        """Attend for me - as the clock moves to 10 minutes after the end of a meeting the
        assistant attended, the sample transcript is delivered, with at least a decision, an
        action and an open point."""
        clock, meetings = self.s.clock, self.s.meetings
        await clock.move_to(VENDOR.starts_at)
        await meetings.join(_join(VENDOR.id))
        await clock.move_to(VENDOR.ends_at + timedelta(minutes=9))
        self.assertIsNone(await meetings.transcript(VENDOR.id, "flow-assistant-ops"))
        await clock.move_to(VENDOR.ends_at + timedelta(minutes=10))
        transcript = await meetings.transcript(VENDOR.id, "flow-assistant-ops")
        assert transcript is not None
        said = [s.text for s in transcript.segments]
        for mark in ("Decision:", "Action:", "Open:"):
            self.assertTrue(any(t.startswith(mark) for t in said), mark)
        self.assertEqual(transcript.title, "Vendor review")
        self.assertTrue(transcript.manager_attended)
        self.assertIsNone(await meetings.transcript(REVIEW.id, "flow-assistant-ops"))


def _join(meeting_id: str) -> JoinRequest:
    return JoinRequest(
        organisation_id=ORGANISATION_ID,
        meeting_id=meeting_id,
        manager_id=OPERATIONS_DIRECTOR.id,
        assistant_flow_id="flow-assistant-ops",
        display_name="Operations director's assistant (notes)",
    )


class AttendingTest(Steps, unittest.IsolatedAsyncioTestCase):
    """Shared steps: the seeded world, meeting notes allowed, the clock at Monday 08:00."""

    async def asyncSetUp(self) -> None:
        self.w = await build_world()

    async def asyncTearDown(self) -> None:
        self.w.close()

    def attend(self, meeting: Any, series: bool = False, person: str = MANAGER) -> Any:
        return self.call(
            "POST", f"/meetings/upcoming/{meeting.id}/attend", person, {"series": series}
        )

    async def at(self, when: Any) -> None:
        await self.w.standins.clock.move_to(when)
        await self.w.module.flow.run_due()

    async def booking(self, meeting: Any) -> MeetingAttendance:
        (found,) = [a for a in await self.attendances() if a.meeting_ref == meeting.id]
        return found

    def requests_for(self, person: str) -> list[Any]:
        return self.ok("GET", "/meeting-requests", person)["requests"]

    def joined(self, meeting: Any) -> list[Any]:
        return [r for r in self.w.standins.meetings.join_requests if r.meeting_id == meeting.id]


class AttendanceRecordTest(AttendingTest):
    """Attend for me: keep the meeting attendance record (task-A2V1LDsgg0I)."""

    def test_the_attendance_record_has_ten_states(self) -> None:
        """Attend for me - its state is one of the ten."""
        self.assertEqual(
            {s.value for s in AttendanceState},
            {"booked", "waiting_for_organiser", "declined", "joining", "attending", "left",
             "writing_notes", "ready", "failed", "withdrawn"},
        )  # fmt: skip

    async def test_a_change_is_recorded_before_it_takes_effect(self) -> None:
        """Attend for me - the record of each change is written before the change takes effect:
        when it cannot be written, nothing changes and nothing is asked of the meeting
        service."""
        self.attend(REVIEW)
        booked = self.w.standins.records.written[-1]
        self.assertEqual(
            (booked.name, booked.item_kind, booked.state_after),
            ("assistant.attendance_booked", "meeting_attendance", "booked"),
        )
        self.w.standins.records.failing = True
        await self.at(REVIEW.starts_at)
        self.assertEqual((await self.booking(REVIEW)).state, AttendanceState.BOOKED)
        self.assertEqual(self.joined(REVIEW), [])
        self.w.standins.records.failing = False
        await self.at(REVIEW.starts_at + timedelta(minutes=1))
        self.assertEqual((await self.booking(REVIEW)).state, AttendanceState.ATTENDING)
        names = [r.state_after for r in self.w.standins.records.written
                 if r.item_kind == "meeting_attendance"]  # fmt: skip
        self.assertEqual(names[-2:], ["joining", "attending"])

    async def test_no_record_holds_the_transcript_the_notes_or_any_other_text(self) -> None:
        """Attend for me - an attendance record, and the record of a change to it, hold no
        transcript, no notes and no other text."""
        text_fields = {
            name
            for name, field in MeetingAttendance.model_fields.items()
            if "str" in str(field.annotation) and "Literal" not in str(field.annotation)
        }
        self.assertEqual(text_fields, {"organisation_id", "manager_id", "meeting_ref",
                                       "organiser_id", "series_id"})  # fmt: skip
        self.attend(VENDOR)
        self.ok(
            "POST",
            f"/meeting-requests/{self.requests_for(ORGANISER)[0]['id']}/accept",
            ORGANISER,
        )
        await self.run_vendor_review()
        attendance = await self.booking(VENDOR)
        self.assertEqual(attendance.state, AttendanceState.READY)
        stored = attendance.model_dump_json()
        self.assertNotIn("Vendor review", stored)
        for words in TRANSCRIPT_WORDS:
            self.assertNotIn(words, stored)

    async def run_vendor_review(self) -> None:
        await self.at(VENDOR.starts_at)
        await self.at(VENDOR.ends_at)
        await self.at(VENDOR.ends_at + timedelta(minutes=10))
        await self.finish_notes()

    async def test_transcript_text_from_an_attended_meeting_is_never_in_a_record_notice_or_log(
        self,
    ) -> None:
        """Attend for me - when the assistant is booked, joins, leaves, gets a transcript or
        writes its notes, no record, notice or log holds any of the transcript's text."""
        with captured_logs(self) as logs:
            self.attend(VENDOR)
            ask = self.requests_for(ORGANISER)[0]
            self.ok("POST", f"/meeting-requests/{ask['id']}/accept", ORGANISER)
            await self.run_vendor_review()
        self.assertEqual((await self.booking(VENDOR)).state, AttendanceState.READY)
        self.assert_no_transcript_text(logs)

    async def test_bookings_and_notes_are_visible_to_the_manager_only(self) -> None:
        """Attend for me - an organiser, an owner or another manager finds nothing; an
        organiser sees only the state of the request made to them."""
        self.attend(VENDOR)
        ask = self.requests_for(ORGANISER)[0]
        self.ok("POST", f"/meeting-requests/{ask['id']}/accept", ORGANISER)
        await self.run_vendor_review()
        attendance = await self.booking(VENDOR)
        meeting = await self.meeting(attendance.meeting_id or "")
        paths = [
            f"/meetings/bookings/{attendance.id}",
            f"/meetings/{meeting.id}",
            f"/meeting-notes/{meeting.notes_id}",
        ]
        others = (ORGANISER, OWNER, SALES_DIRECTOR.id, FINANCE_LEAD.id)
        for path in paths:
            self.assertEqual(self.call("GET", path, MANAGER).status_code, 200)
            for person in others:
                with self.subTest(path=path, person=person):
                    self.assertEqual(self.call("GET", path, person).status_code, 404)
        self.assertEqual(self.ok("GET", "/meetings/bookings", SALES_DIRECTOR.id)["bookings"], [])
        (seen,) = self.requests_for(ORGANISER)
        self.assertEqual(seen["state"], "accepted")
        # An attended meeting is not something the organiser shared: it is not in their list.
        self.assertNotIn(str(meeting.id), str(self.ok("GET", "/transcripts", ORGANISER)))

    def test_the_visibility_test_knows_every_new_address(self) -> None:
        """Attend for me - the visibility test knows every address added for the meetings the
        assistant attends."""
        for address in (
            ("GET", "/api/assistant/meetings/upcoming"),
            ("POST", "/api/assistant/meetings/upcoming/{meeting_ref}/attend"),
            ("GET", "/api/assistant/meetings/bookings"),
            ("GET", "/api/assistant/meetings/bookings/{booking_id}"),
            ("GET", "/api/assistant/meeting-requests"),
            ("GET", "/api/assistant/meeting-requests/{request_id}"),
            ("POST", "/api/assistant/meeting-requests/{request_id}/accept"),
            ("POST", "/api/assistant/meeting-requests/{request_id}/decline"),
            ("POST", "/api/assistant/meeting-requests/{request_id}/remove"),
        ):
            self.assertIn(address, EXPECTATIONS)


class AttendForMeTest(AttendingTest):
    """Attend for me: book Attend for me (task-xKg7N_ZHbyQ)."""

    async def test_the_manager_sees_their_upcoming_meetings_each_with_attend_for_me(self) -> None:
        """Attend for me - the manager sees their upcoming meetings, as the meeting service
        gives them, each with Attend for me."""
        page = self.ok("GET", "/meetings/upcoming", MANAGER)
        page["meetings"] = [m for m in page["meetings"] if m["id"] != SEEDED_MEETING.id]
        self.assertEqual(
            [
                (m["title"], m["organiser"]["display_name"], m["attend_for_me"])
                for m in page["meetings"]
            ],
            [
                ("Weekly operations review", "Operations director", True),
                ("Vendor review", "Programme lead", True),
                ("Partner briefing", "Operations director", True),
            ],
        )
        self.assertEqual(page["meetings"][0]["series_id"], REVIEW.series_id)

    async def test_attend_for_me_books_the_assistant_without_approval(self) -> None:
        """Attend for me - for one meeting or a series; the manager's own decision."""
        checked = len(self.w.standins.approval.checked)
        response = self.attend(REVIEW)
        self.assertEqual(response.status_code, 200, response.text)
        (booked,) = response.json()["booked"]
        self.assertEqual((booked["state"], booked["booked_by"]), ("booked", "manager"))
        self.assertEqual(len(self.w.standins.approval.checked), checked)
        self.assertEqual(self.ok("GET", "/requests", MANAGER)["count"], 1)  # only the seeded one
        page = self.ok("GET", "/meetings/upcoming", MANAGER)["meetings"]
        self.assertEqual(page[0]["booking"]["state"], "booked")
        self.assertFalse(page[0]["attend_for_me"])
        self.assertEqual(self.refused("POST", f"/meetings/upcoming/{REVIEW.id}/attend", MANAGER),
                         ALREADY_BOOKED)  # fmt: skip

    async def test_attend_for_me_books_every_meeting_of_a_series(self) -> None:
        later = sample_meetings()[0]
        later.key = "operations_review_next_week"
        later.starts_at += timedelta(days=7)
        later.ends_at += timedelta(days=7)
        self.w.standins.meetings.meetings[later.id] = later
        booked = self.ok(
            "POST",
            f"/meetings/upcoming/{REVIEW.id}/attend",
            MANAGER,
            {"series": True},
        )
        self.assertEqual(
            sorted(b["meeting_ref"] for b in booked["booked"]), sorted([REVIEW.id, later.id])
        )
        self.assertEqual(len(await self.attendances()), 2)

    async def test_the_assistants_own_proposal_goes_through_the_approval_check(self) -> None:
        """Attend for me - attend_meeting (meeting, reason, source) goes through the approval
        check as 'Attend a meeting and take notes', which starts at with approval."""
        levels = {lv["kind"]: lv for lv in self.ok("GET", "/approval-levels", MANAGER)}
        self.assertEqual(
            (levels["attend_meeting"]["label"], levels["attend_meeting"]["level"]),
            ("Attend a meeting and take notes", "with_approval"),
        )
        piece = await self.propose(
            {
                "tool": "attend_meeting",
                "meeting": REVIEW.id,
                "reason": "You asked for notes.",
                "source": SOURCE,
            }
        )
        self.assertEqual(piece["refused"], [])
        (request,) = [r for r in self.ok("GET", "/requests", MANAGER)["requests"]
                      if r["kind"] == "attend_meeting"]  # fmt: skip
        self.assertEqual(request["effect"], "Attend Weekly operations review and take notes.")
        last, _ = self.w.standins.approval.checked[-1]
        self.assertEqual(
            (last.kind, last.level),
            (ActionKind.ATTEND_MEETING, ApprovalLevel.WITH_APPROVAL),
        )
        self.assertEqual(await self.attendances(), [])
        self.ok("POST", f"/requests/{request['id']}/approve", MANAGER)
        attendance = await self.booking(REVIEW)
        self.assertEqual(
            (attendance.state, attendance.booked_by),
            (AttendanceState.BOOKED, "assistant"),
        )

    async def propose(self, tool_request: dict[str, Any]) -> Any:
        self.ok("POST", "/chat", MANAGER, {"text": "Please take notes at my review."})
        (started,) = self.w.standins.assistant.take_finished()
        await self.w.module.flow.finish_work(
            ORGANISATION_ID,
            self.w.seeded.assistant.flow_id,
            WorkResult(work_id=started.work_id, reply="Done.", tool_requests=[tool_request]),
        )
        return self.ok("GET", f"/work/{started.work_id}", MANAGER)

    async def test_a_meeting_the_manager_cannot_see_or_one_already_booked_is_refused(self) -> None:
        """Attend for me - attend_meeting naming a meeting the manager cannot see, or one the
        assistant is already booked for, is refused with a reason."""
        unseen = await self.propose({"tool": "attend_meeting", "meeting": "no-such-meeting",
                                     "reason": "Notes.", "source": SOURCE})  # fmt: skip
        self.assertEqual(
            unseen["refused"],
            ["Tool request 1 was refused because the manager cannot see the meeting it names."],
        )
        self.attend(REVIEW)
        booked = await self.propose({"tool": "attend_meeting", "meeting": REVIEW.id,
                                     "reason": "Notes.", "source": SOURCE})  # fmt: skip
        self.assertEqual(
            booked["refused"],
            [
                "Tool request 1 was refused because the assistant is already booked for that "
                "meeting."
            ],
        )
        self.assertNotIn(
            "attend_meeting",
            [r["kind"] for r in self.ok("GET", "/requests", MANAGER)["requests"]],
        )

    async def test_attend_for_me_is_refused_when_meeting_notes_are_not_allowed(self) -> None:
        """Attend for me - refused with one sentence when the organisation's existing setting
        does not allow meeting notes, and nothing is booked."""
        self.allow_meeting_notes(False)
        message = self.refused("POST", f"/meetings/upcoming/{REVIEW.id}/attend", MANAGER, {})
        self.assertEqual(message, rules.NOTES_NOT_ALLOWED)
        self.assertTrue(is_plain_sentence(message))
        self.assertEqual(await self.attendances(), [])
        upcoming = self.ok("GET", "/meetings/upcoming", MANAGER)["meetings"]
        self.assertFalse(any(m["attend_for_me"] for m in upcoming))
        proposal = await self.propose({"tool": "attend_meeting", "meeting": REVIEW.id,
                                       "reason": "Notes.", "source": SOURCE})  # fmt: skip
        self.assertEqual(len(proposal["refused"]), 1)

    async def test_a_meeting_that_is_cancelled_withdraws_the_booking(self) -> None:
        """Attend for me - when the meeting is cancelled before the assistant joins, the booking
        is withdrawn and the manager is told."""
        self.attend(REVIEW)
        self.w.standins.meetings.cancel(REVIEW.id)
        await self.at(DEFAULT_START + timedelta(minutes=5))
        attendance = await self.booking(REVIEW)
        self.assertEqual(
            (attendance.state, attendance.because),
            (AttendanceState.WITHDRAWN, "cancelled"),
        )
        self.assertIn(
            "Your meeting was cancelled, so your assistant will not attend it.",
            self.notices_to(MANAGER),
        )
        await self.at(REVIEW.starts_at)
        self.assertEqual(self.joined(REVIEW), [])

    async def test_a_meeting_that_ends_before_the_assistant_joins_withdraws_it(self) -> None:
        """Attend for me - a meeting that ends before the assistant joins withdraws the booking
        and the manager is told."""
        self.attend(REVIEW)
        self.ok("POST", "/assistant/pause", MANAGER)
        await self.at(REVIEW.ends_at + timedelta(minutes=5))
        self.ok("POST", "/assistant/resume", MANAGER)
        attendance = await self.booking(REVIEW)
        self.assertEqual(
            (attendance.state, attendance.because),
            (AttendanceState.WITHDRAWN, "ended_before_joining"),
        )
        self.assertEqual(self.joined(REVIEW), [])
        self.assertIn(
            "Your meeting ended before your assistant joined it.",
            self.notices_to(MANAGER),
        )


class OrganiserAgreesTest(AttendingTest):
    """The organiser agrees: ask the organiser on Requests for you (task-A5BMOggRojo)."""

    async def test_an_organiser_who_is_not_the_manager_is_asked(self) -> None:
        """The organiser agrees - its organiser is asked on Requests for you, and the assistant
        attends only if they accept."""
        booked = self.attend(VENDOR).json()["booked"][0]
        self.assertEqual(booked["state"], "waiting_for_organiser")
        (request,) = self.requests_for(ORGANISER)
        self.assertEqual(request["state"], "waiting")
        self.assertEqual(request["asked_by"], "Operations director's assistant (notes)")
        self.assertIn(
            "The Operations director's assistant asks to attend Vendor review to take notes.",
            self.notices_to(ORGANISER),
        )
        accepted = self.ok("POST", f"/meeting-requests/{request['id']}/accept", ORGANISER)
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual((await self.booking(VENDOR)).state, AttendanceState.BOOKED)
        await self.at(VENDOR.starts_at)
        self.assertEqual((await self.booking(VENDOR)).state, AttendanceState.ATTENDING)
        self.assertEqual(len(self.joined(VENDOR)), 1)

    async def test_a_decline_keeps_the_assistant_out(self) -> None:
        """The organiser agrees - when the organiser declines, the assistant does not attend and
        the manager is told why."""
        self.attend(VENDOR)
        (request,) = self.requests_for(ORGANISER)
        declined = self.ok("POST", f"/meeting-requests/{request['id']}/decline", ORGANISER)
        self.assertEqual(declined["state"], "declined")
        self.assertEqual((await self.booking(VENDOR)).state, AttendanceState.DECLINED)
        self.assertIn(
            "The organiser of Vendor review declined, so your assistant will not attend it.",
            self.notices_to(MANAGER),
        )
        await self.at(VENDOR.starts_at)
        self.assertEqual(self.joined(VENDOR), [])

    async def test_no_answer_by_the_start_keeps_the_assistant_out(self) -> None:
        """The organiser agrees - an organiser who has not answered when the meeting starts
        leaves the booking withdrawn, and the manager is told why."""
        self.attend(VENDOR)
        await self.at(VENDOR.starts_at)
        attendance = await self.booking(VENDOR)
        self.assertEqual(
            (attendance.state, attendance.because),
            (AttendanceState.WITHDRAWN, "no_answer"),
        )
        self.assertEqual(self.joined(VENDOR), [])
        self.assertIn(
            "The organiser of Vendor review had not answered by the start, so your assistant did "
            "not attend.",
            self.notices_to(MANAGER),
        )
        (request,) = self.requests_for(ORGANISER)
        self.assertEqual((request["state"], request["can_accept"]), ("withdrawn", False))
        self.refused("POST", f"/meeting-requests/{request['id']}/accept", ORGANISER)

    async def test_the_managers_own_meeting_needs_no_request(self) -> None:
        """The organiser agrees - when the manager organises the meeting, nobody is asked and
        the assistant is booked."""
        self.attend(REVIEW)
        self.assertEqual((await self.booking(REVIEW)).state, AttendanceState.BOOKED)
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            asks = [a for a in await tx.find(PersonRequest) if a.asks == "attend"]
        self.assertEqual([a.subject_id for a in asks], [str(self.w.seeded.attendance.id)])

    async def test_the_organiser_sees_only_the_state_of_the_request(self) -> None:
        """The organiser agrees - they see the meeting, whose assistant asks and the state of
        the request, and nothing of the transcript or the notes."""
        self.attend(VENDOR)
        (request,) = self.requests_for(ORGANISER)
        self.ok("POST", f"/meeting-requests/{request['id']}/accept", ORGANISER)
        await self.at(VENDOR.starts_at)
        await self.at(VENDOR.ends_at)
        await self.at(VENDOR.ends_at + timedelta(minutes=10))
        await self.finish_notes()
        seen = self.ok("GET", f"/meeting-requests/{request['id']}", ORGANISER)
        self.assertEqual(
            set(seen),
            {"id", "meeting", "asked_by", "state", "can_accept", "can_decline", "can_remove"},
        )
        self.assertEqual(set(seen["meeting"]), {"title", "starts_at", "ends_at"})
        text = str(seen) + str(self.requests_for(ORGANISER))
        for words in [*TRANSCRIPT_WORDS, "We keep two vendors"]:
            self.assertNotIn(words, text)
        for person in (MANAGER, OWNER):
            with self.subTest(person):
                path = f"/meeting-requests/{request['id']}"
                self.assertEqual(self.call("GET", path, person).status_code, 404)


class PlainNameAndRemovalTest(AttendingTest):
    """The organiser agrees: attend under a plain name, and leave when removed
    (task-tuorsXn_dKc)."""

    async def test_everyone_sees_whose_assistant_it_is_and_that_it_takes_notes(self) -> None:
        """The organiser agrees - the assistant attends under "<manager's name>'s assistant
        (notes)"."""
        self.attend(REVIEW)
        await self.at(REVIEW.starts_at)
        self.assertEqual(
            self.w.standins.meetings.display_names(REVIEW.id),
            ["Operations director's assistant (notes)"],
        )
        self.assertEqual(self.joined(REVIEW)[0].role, "silent_note_taker")

    async def test_a_removed_assistant_leaves_and_the_notes_stop_there(self) -> None:
        """The organiser agrees - when the organiser removes the assistant on Requests for you,
        it leaves, and the notes use only what was said before it left."""
        self.attend(VENDOR)
        (request,) = self.requests_for(ORGANISER)
        self.ok("POST", f"/meeting-requests/{request['id']}/accept", ORGANISER)
        await self.at(VENDOR.starts_at)
        seen = self.ok("GET", f"/meeting-requests/{request['id']}", ORGANISER)
        self.assertTrue(seen["can_remove"])
        await self.w.standins.clock.move_to(VENDOR.starts_at + timedelta(minutes=12))
        removed = self.ok("POST", f"/meeting-requests/{request['id']}/remove", ORGANISER)
        self.assertEqual(removed["state"], "removed")
        attendance = await self.booking(VENDOR)
        self.assertEqual(
            (attendance.state, attendance.because),
            (AttendanceState.LEFT, "organiser_removed"),
        )
        self.assertIn((VENDOR.id, self.w.seeded.assistant.flow_id), self.w.standins.meetings.left)
        self.assertIn(
            "The organiser of Vendor review removed your assistant from the meeting.",
            self.notices_to(MANAGER),
        )
        await self.at(VENDOR.ends_at)
        await self.at(VENDOR.ends_at + timedelta(minutes=10))
        attendance = await self.booking(VENDOR)
        meeting = await self.meeting(attendance.meeting_id or "")
        self.assertEqual(len(meeting.parts), 3)  # said at 1, 6 and 11 minutes; it left at 12
        await self.finish_notes()
        notes = self.ok(
            "GET",
            f"/meeting-notes/{(await self.meeting(meeting.id)).notes_id}",
            MANAGER,
        )
        self.assertEqual(len(notes["decisions"]), 1)
        self.assertEqual(notes["actions"], [])

    async def test_an_assistant_removed_in_the_meeting_itself_leaves_too(self) -> None:
        self.attend(REVIEW)
        await self.at(REVIEW.starts_at)
        await self.w.standins.clock.move_to(REVIEW.starts_at + timedelta(minutes=7))
        await self.w.standins.meetings.remove_in_meeting(REVIEW.id, self.w.seeded.assistant.flow_id)
        await self.at(REVIEW.ends_at)
        attendance = await self.booking(REVIEW)
        self.assertEqual(attendance.because, "organiser_removed")
        self.assertEqual(attendance.left_at, REVIEW.starts_at + timedelta(minutes=7))
        await self.at(REVIEW.ends_at + timedelta(minutes=10))
        meeting = await self.meeting((await self.booking(REVIEW)).meeting_id or "")
        self.assertEqual(len(meeting.parts), 2)

    async def test_a_waiting_request_cannot_be_removed(self) -> None:
        self.attend(VENDOR)
        (request,) = self.requests_for(ORGANISER)
        self.refused("POST", f"/meeting-requests/{request['id']}/remove", ORGANISER)
        self.assertEqual((await self.booking(VENDOR)).state, AttendanceState.WAITING_FOR_ORGANISER)


if __name__ == "__main__":
    unittest.main()
