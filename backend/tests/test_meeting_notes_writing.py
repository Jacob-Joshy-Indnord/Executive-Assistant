"""Part 2, Story 1.2 Write the notes, with sources, and the story "A summary, not a
transcript" (task-TWgGEBSFEBs, task-PKZsklrBubo, task-jY5-gavZj0Y)."""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from assistant.models.boundary import (
    DATA_NOT_INSTRUCTIONS,
    InputPack,
    MeetingTranscript,
    PackInput,
    TranscriptSegment,
    WorkKind,
    WorkOrder,
    WorkResult,
)
from assistant.models.records import AttendanceState, MeetingAttendance, MeetingState, WorkState
from assistant.rules import meetings as rules
from assistant.service.actions import Action, perform
from assistant.service.meetings import keep_attended
from standins.meetings import sample_meetings, sample_segments
from standins.sample_transcript import SAMPLE_LINES, sample_text, sample_vtt
from tests.meeting_helpers import (
    MANAGER,
    ORGANISER,
    VENDORS,
    Meetings,
    captured_logs,
    result_with,
)
from tests.world import build_world


def notes_request(result: WorkResult) -> dict[str, Any]:
    (request,) = [r for r in result.tool_requests if r.get("tool") == "meeting_notes"]
    return dict(request)


def changed_notes(**changes: Any) -> Any:
    """Alter the stand-in's ``meeting_notes`` request before it is handed back."""

    def change(result: WorkResult) -> WorkResult:
        request = notes_request(result)
        for key, value in changes.items():
            request[key] = value(request) if callable(value) else value
        return result.model_copy(update={"tool_requests": [request]})

    return change


class SplitTranscriptTest(Meetings, unittest.IsolatedAsyncioTestCase):
    """Story 1.2 Write the notes, with sources: split and request notes (task-TWgGEBSFEBs)."""

    async def asyncSetUp(self) -> None:
        self.w = await build_world()

    async def asyncTearDown(self) -> None:
        self.w.close()

    def test_a_vtt_transcript_is_split_into_numbered_parts_with_times(self) -> None:
        """Story 1.2 - a .vtt transcript is split into numbered parts with times."""
        parts = rules.split("vtt", sample_vtt())
        self.assertEqual([p.number for p in parts], list(range(1, len(SAMPLE_LINES) + 1)))
        self.assertEqual((parts[0].starts, parts[0].ends), ("00:00:00.000", "00:00:25.000"))
        self.assertEqual((parts[2].starts, parts[2].ends), ("00:01:00.000", "00:01:25.000"))
        self.assertEqual(parts[2].speaker, "Operations director")
        self.assertEqual(parts[2].text, "Decision: We keep two vendors for office supplies.")
        self.assertTrue(all(p.first_line is None for p in parts))

    def test_short_cue_times_and_notes_are_read(self) -> None:
        vtt = "WEBVTT\n\nNOTE a note\n\n01:02.500 --> 01:04.000\nHello there.\n"
        (part,) = rules.split("vtt", vtt)
        self.assertEqual((part.number, part.starts, part.ends), (1, "00:01:02.500", "00:01:04.000"))
        self.assertIsNone(part.speaker)

    def test_a_text_transcript_is_split_into_numbered_parts_with_line_numbers(self) -> None:
        """Story 1.2 - a .txt or pasted transcript is split into numbered parts, each with
        its line numbers."""
        parts = rules.split("text", "First line.\n\nThird line.\n  \nFifth line.\n")
        self.assertEqual(
            [(p.number, p.first_line, p.last_line, p.text) for p in parts],
            [(1, 1, 1, "First line."), (2, 3, 3, "Third line."), (3, 5, 5, "Fifth line.")],
        )
        self.assertTrue(all(p.starts is None for p in parts))

    def test_a_transcript_from_an_attended_meeting_is_numbered_the_same_way(self) -> None:
        """Story 1.2 - a transcript from an attended meeting is numbered in order, each part
        with its times and the speaker's name where one is given."""
        start = datetime(2026, 11, 25, 14, 0, tzinfo=UTC)

        def segment(minutes: float, text: str, speaker: str | None = None) -> TranscriptSegment:
            at = start + timedelta(minutes=minutes)
            return TranscriptSegment(
                starts_at=at, ends_at=at + timedelta(seconds=20), speaker=speaker, text=text
            )

        parts = rules.split_segments(
            [segment(5, "Second.", "Legal lead"), segment(1, "First."), segment(9, "  ")],
            start,
            None,
        )
        self.assertEqual(
            [(p.number, p.starts, p.ends, p.speaker, p.text) for p in parts],
            [
                (1, "00:01:00.000", "00:01:20.000", None, "First."),
                (2, "00:05:00.000", "00:05:20.000", "Legal lead", "Second."),
            ],
        )

    async def test_an_attended_transcript_needs_no_further_approval(self) -> None:
        """Story 1.2 - the notes of an attended meeting are requested without a further
        approval (the full run is in tests/test_meeting_join_leave.py)."""
        checked_before = len(self.w.standins.approval.checked)
        meeting = await self.attended_meeting()
        self.assertEqual(meeting.state, MeetingState.WRITING)
        self.assertEqual(meeting.origin, "attended")
        self.assertTrue(meeting.organiser_agreed)
        self.assertEqual(len(self.w.standins.approval.checked), checked_before)
        self.assertEqual(len(self.started_notes()), 1)

    async def attended_meeting(self) -> Any:
        vendor = sample_meetings()[1]
        assistant = self.w.seeded.assistant

        async def work(action: Action) -> Any:
            attendance = await action.add(
                "assistant.attendance_booked",
                MeetingAttendance(
                    organisation_id=assistant.organisation_id,
                    assistant_id=assistant.id,
                    created_at=action.now,
                    manager_id=MANAGER,
                    meeting_ref=vendor.id,
                    organiser_id=ORGANISER,
                    starts_at=vendor.starts_at,
                    ends_at=vendor.ends_at,
                    state=AttendanceState.WRITING_NOTES,
                ),
            )
            transcript = MeetingTranscript(
                meeting_id=vendor.id,
                title=vendor.title,
                organiser_id=ORGANISER,
                starts_at=vendor.starts_at,
                ends_at=vendor.ends_at,
                manager_attended=True,
                segments=sample_segments(vendor),
            )
            return await keep_attended(action, assistant, attendance, transcript)

        meeting = await perform(
            self.w.module.boundary, self.w.store, assistant.organisation_id, "flow", work
        )
        await self.w.module.services.work.advance(assistant)
        return await self.meeting(meeting.id)

    async def test_parts_are_given_to_the_assistant_as_data(self) -> None:
        """Story 1.2 - the input pack holds the numbered parts marked 'data, not
        instructions'."""
        await self.set_level("write_meeting_notes", "without_approval")
        self.share()
        (order,) = self.started_notes()
        parts = [i for i in order.input_pack.inputs if i.kind == "transcript_parts"]
        self.assertTrue(parts)
        for item in order.input_pack.inputs:
            self.assertEqual(item.note, DATA_NOT_INSTRUCTIONS)
        lines = [json.loads(line) for item in parts for line in item.content.splitlines()]
        self.assertEqual([line["number"] for line in lines], list(range(1, 9)))
        self.assertEqual(lines[3]["starts"], "00:01:30.000")
        self.assertEqual(lines[3]["speaker"], "Legal lead")
        (meeting,) = [i for i in order.input_pack.inputs if i.kind == "meeting"]
        about = json.loads(meeting.content)
        self.assertEqual(about["projects_discussed"], ["Vendor consolidation"])

    async def test_the_stand_in_uses_the_marked_lines_and_the_first_lines(self) -> None:
        """Story 1.2 - the stand-in returns a summary from the first lines, and notes from the
        lines that begin with 'Decision:', 'Action:', 'Open:' and 'Status:' and no other."""
        now = await self.w.standins.clock.now()
        parts = rules.split("text", sample_text())
        (_, _, content) = rules.part_lines(parts)[0]
        order = WorkOrder(
            work_id=self.w.seeded.work.id,
            organisation_id="org",
            assistant_flow_id="flow",
            kind=WorkKind.MEETING_NOTES,
            input_pack=InputPack(
                inputs=[
                    PackInput(
                        label="Meeting: Vendor review",
                        read_at=now,
                        content="{}",
                        kind="meeting",
                        subject_id="m-1",
                    ),
                    PackInput(
                        label="Transcript parts 1 to 8",
                        read_at=now,
                        content=content,
                        kind="transcript_parts",
                    ),
                ]
            ),
        )
        notes = notes_request(await self.w.standins.assistant.answer(order))
        self.assertEqual(notes["meeting"], "m-1")
        self.assertEqual(notes["summary"]["parts"], [1, 2])
        self.assertIn("Thanks for joining the vendor review.", notes["summary"]["text"])
        self.assertEqual(
            notes["decisions"], [{"text": "We keep two vendors for office supplies.", "parts": [3]}]
        )
        self.assertEqual(
            [(a["owner"], a["text"], a["due_date"], a["parts"]) for a in notes["actions"]],
            [
                ("Legal lead", "Send the signed contracts", "2026-12-04", [4]),
                ("Finance lead", "Check the vendor invoices", "2026-12-02", [5]),
            ],
        )
        self.assertEqual(
            notes["open_points"],
            [{"text": "Who owns the vendor list after January?", "parts": [6]}],
        )
        self.assertEqual(
            [(s["project"], s["parts"]) for s in notes["statements"]],
            [("Vendor consolidation", [7])],
        )
        used = {p for key in ("decisions", "actions", "open_points", "statements")
                for line in notes[key] for p in line["parts"]}  # fmt: skip
        self.assertEqual(used, {3, 4, 5, 6, 7})  # the unmarked lines 1, 2 and 8 are not notes

    async def test_a_failure_is_shown_and_can_be_tried_again(self) -> None:
        """Story 1.2 - when the notes cannot be written, the meeting is failed, the manager
        sees why, and can try again while the transcript is kept."""
        await self.set_level("write_meeting_notes", "without_approval")
        shared = self.share().json()
        await self.finish_notes(
            result_with(failure="The assistant ran out of time.", tool_requests=[])
        )
        view = self.ok("GET", f"/meetings/{shared['id']}", MANAGER)
        self.assertEqual(
            (view["state"], view["reason"], view["can_retry"], view["text_kept"]),
            ("failed", "The assistant ran out of time.", True, True),
        )
        self.assertIn("The notes of Vendor review could not be written.", self.notices_to(MANAGER))
        retried = self.ok("POST", f"/meetings/{shared['id']}/retry", MANAGER)
        self.assertEqual(retried["state"], "writing")
        self.assertEqual(len(self.started_notes()), 2)
        await self.finish_notes()
        self.assertEqual((await self.meeting(shared["id"])).state, MeetingState.READY)

    async def test_a_refused_start_or_an_answer_without_notes_also_fails(self) -> None:
        await self.set_level("write_meeting_notes", "without_approval")
        self.w.standins.assistant.refusing = True
        refused = self.share().json()
        meeting = await self.meeting(refused["id"])
        self.assertEqual(
            (meeting.state, meeting.reason),
            (MeetingState.FAILED, "The assistant is not available right now."),
        )
        self.w.standins.assistant.refusing = False
        empty = self.share(title="Second").json()
        await self.finish_notes(result_with(tool_requests=[], reply=None))
        meeting = await self.meeting(empty["id"])
        self.assertEqual(
            (meeting.state, meeting.reason),
            (MeetingState.FAILED, rules.NO_NOTES_RETURNED),
        )

    async def test_only_failed_notes_can_be_tried_again(self) -> None:
        shared = self.share().json()
        message = self.refused("POST", f"/meetings/{shared['id']}/retry", MANAGER)
        self.assertEqual(message, rules.NOT_RETRYABLE)


class CheckNotesTest(Meetings, unittest.IsolatedAsyncioTestCase):
    """Story 1.2 Write the notes, with sources: check notes and remove transcript text
    (task-PKZsklrBubo), and A summary, not a transcript (task-jY5-gavZj0Y)."""

    async def asyncSetUp(self) -> None:
        self.w = await build_world()
        await self.follow(VENDORS)
        await self.set_level("write_meeting_notes", "without_approval")

    async def asyncTearDown(self) -> None:
        self.w.close()

    async def written(self, change: Any = None) -> tuple[Any, Any, Any]:
        shared = self.share().json()
        (work_id,) = await self.finish_notes(change)
        meeting = await self.meeting(shared["id"])
        notes = None
        if meeting.notes_id:
            notes = self.ok("GET", f"/meeting-notes/{meeting.notes_id}", MANAGER)
        return meeting, notes, await self.piece(work_id)

    async def test_every_kept_line_names_its_transcript_parts(self) -> None:
        """Story 1.2 - each kept decision, action and statement names the parts it came from."""
        meeting, notes, piece = await self.written()
        self.assertEqual(piece.refused, [])
        self.assertEqual([d["parts"] for d in notes["decisions"]], [[3]])
        self.assertEqual([a["parts"] for a in notes["actions"]], [[4], [5]])
        self.assertEqual([s["parts"] for s in notes["statements"]], [[7]])
        self.assertEqual([o["parts"] for o in notes["open_points"]], [[6]])
        self.assertEqual(notes["summary"]["parts"], [1, 2])
        self.assertEqual([p["number"] for p in notes["parts"]], list(range(1, 9)))

    async def test_a_line_naming_a_missing_part_is_refused_alone(self) -> None:
        """Story 1.2 - a line that names a part that does not exist is refused, and the other
        lines are kept (the stand-in's faults switch)."""
        self.w.standins.assistant.faults = True
        meeting, notes, piece = await self.written()
        self.assertEqual(meeting.state, MeetingState.READY)
        self.assertEqual(
            [d["text"] for d in notes["decisions"]],
            ["We keep two vendors for office supplies."],
        )
        self.assertIn(
            "The notes' decision 2 was refused because it names part 9999, which does not exist.",
            piece.refused,
        )
        self.assertEqual(len(notes["actions"]), 2)

    async def test_a_line_over_300_characters_is_refused_alone(self) -> None:
        """Story 1.2 - a line over 300 characters is refused like any other failed line."""
        long = "x" * 301
        _, notes, piece = await self.written(
            changed_notes(actions=lambda r: [*r["actions"], {**r["actions"][0], "text": long}])
        )
        self.assertEqual(len(notes["actions"]), 2)
        self.assertIn(
            "The notes' action 3 was refused because its text is over 300 characters.",
            piece.refused,
        )
        self.assertEqual(len(notes["decisions"]), 1)

    async def test_an_unmatched_owner_is_kept_as_owner_not_found(self) -> None:
        """Story 1.2 - an action whose owner does not match exactly one person or virtual
        employee is kept with 'Owner not found'."""
        _, notes, _ = await self.written(
            changed_notes(
                actions=lambda r: [{**r["actions"][0], "owner": "Nobody Of That Name"}]
            )
        )
        (action,) = notes["actions"]
        self.assertIsNone(action["owner"])
        self.assertEqual(action["owner_label"], "Owner not found")
        self.assertEqual(action["owner_name"], "Nobody Of That Name")
        self.assertFalse(action["can_keep"])

    async def test_a_statement_about_an_unfollowed_project_is_dropped(self) -> None:
        """Story 1.2 - a statement about a project the manager does not follow is dropped."""
        _, notes, piece = await self.written(
            changed_notes(
                statements=lambda r: [
                    *r["statements"],
                    {"project": "Hiring plan", "reason": "On plan.", "parts": [7]},
                    {"project": "Not a project", "reason": "Unknown.", "parts": [7]},
                ]
            )
        )
        self.assertEqual(
            [s["project"]["name"] for s in notes["statements"]],
            ["Vendor consolidation"],
        )
        self.assertEqual(notes["dropped_statements"], 2)
        self.assertEqual(piece.refused, [])

    async def test_the_text_is_removed_once_the_notes_pass_the_checks(self) -> None:
        """Story 1.2 - once the notes pass the checks, the text is removed, the part numbers
        and times stay, and the meeting is ready (the first of three ways)."""
        meeting, _, _ = await self.written()
        self.assertEqual(meeting.state, MeetingState.READY)
        self.assertFalse(meeting.text_kept)
        self.assertIsNotNone(meeting.text_removed_at)
        self.assertEqual([p.number for p in meeting.parts], list(range(1, 9)))
        self.assertEqual(meeting.parts[0].starts, "00:00:00.000")
        self.assertTrue(all(p.text is None and p.speaker is None for p in meeting.parts))

    async def test_the_text_is_removed_after_7_days_at_the_latest(self) -> None:
        """Story 1.2 - 7 days after the text arrived, a registered due step removes it once;
        the part numbers and times stay (the third of three ways; withdrawal is the second,
        in tests/test_meeting_sharing.py)."""
        await self.set_level("write_meeting_notes", "with_approval")
        shared = self.share().json()
        clock = self.w.standins.clock
        await clock.forward(timedelta(days=7) - timedelta(minutes=1))
        await self.w.module.flow.run_due()
        self.assertTrue((await self.meeting(shared["id"])).text_kept)
        await clock.forward(timedelta(minutes=1))
        await self.w.module.flow.run_due()
        await self.w.module.flow.run_due()  # a second run removes nothing more
        meeting = await self.meeting(shared["id"])
        self.assertFalse(meeting.text_kept)
        self.assertEqual([p.number for p in meeting.parts], list(range(1, 9)))
        self.assertTrue(all(p.starts for p in meeting.parts))
        removals = [
            r
            for r in self.w.standins.records.written
            if r.name == "assistant.transcript_text_removed" and r.item_id == shared["id"]
        ]
        self.assertEqual(len(removals), 1)

    async def test_a_meeting_without_notes_moves_to_removed_and_the_manager_is_told(self) -> None:
        """Story 1.2 - when the 7 days pass while the meeting is shared, writing or failed, it
        is removed, Try again is no longer offered, and the manager is told."""
        failed = self.share(title="Failed one").json()
        await self.finish_notes(result_with(failure="It failed.", tool_requests=[]))
        await self.set_level("write_meeting_notes", "with_approval")
        waiting = self.share(title="Waiting one").json()
        await self.w.standins.clock.forward(timedelta(days=7))
        await self.w.module.flow.run_due()
        for shared in (failed, waiting):
            view = self.ok("GET", f"/meetings/{shared['id']}", MANAGER)
            self.assertEqual((view["state"], view["can_retry"]), ("removed", False))
            self.assertEqual(
                self.refused("POST", f"/meetings/{shared['id']}/retry", MANAGER),
                rules.NOT_RETRYABLE,
            )
        told = self.notices_to(MANAGER)
        self.assertIn("The transcript of Failed one was removed without notes.", told)
        self.assertIn("The transcript of Waiting one was removed without notes.", told)
        (request,) = await self.requests_about(waiting["id"])
        self.assertEqual(request.state.value, "withdrawn")

    async def test_the_text_is_removed_after_7_days_while_paused_too(self) -> None:
        await self.set_level("write_meeting_notes", "with_approval")
        shared = self.share().json()
        self.ok("POST", "/assistant/pause", MANAGER)
        await self.w.standins.clock.forward(timedelta(days=7))
        await self.w.module.flow.run_due()
        self.assertFalse((await self.meeting(shared["id"])).text_kept)
        self.assertEqual(self.ok("GET", "/assistant", MANAGER)["state"], "paused")

    async def test_transcript_text_is_never_in_a_record_notice_or_log(self) -> None:
        """Story 1.2 - when a transcript is shared, its notes are written, it is withdrawn or it
        runs out, no record, notice or log holds any of its text."""
        with captured_logs(self) as logs:
            await self.written()
            withdrawn = self.share(title="Withdrawn").json()
            self.ok("POST", f"/transcripts/{withdrawn['id']}/withdraw", ORGANISER)
            await self.set_level("write_meeting_notes", "with_approval")
            self.share(title="Runs out")
            await self.w.standins.clock.forward(timedelta(days=7))
            await self.w.module.flow.run_due()
        self.assert_no_transcript_text(logs)

    # A summary, not a transcript (task-jY5-gavZj0Y) ---------------------------------------

    async def test_the_notes_hold_a_summary_decisions_actions_open_points_and_status(self) -> None:
        """A summary, not a transcript - the notes hold a summary of at most 600 characters,
        the decisions, the actions with owners and due dates, the open points and the status
        of followed projects; every line names its transcript parts."""
        _, notes, _ = await self.written()
        self.assertLessEqual(len(notes["summary"]["text"]), 600)
        self.assertEqual(notes["summary"]["parts"], [1, 2])
        self.assertEqual(
            [(a["owner"]["display_name"], a["due_date"]) for a in notes["actions"]],
            [("Legal lead", "2026-12-04"), ("Finance lead", "2026-12-02")],
        )
        self.assertEqual(
            [o["text"] for o in notes["open_points"]], ["Who owns the vendor list after January?"]
        )
        self.assertEqual(
            [(s["project"]["name"], s["state"]) for s in notes["statements"]],
            [("Vendor consolidation", "awaiting_owner")],
        )
        lines = [notes["summary"], *notes["decisions"], *notes["actions"], *notes["open_points"],
                 *notes["statements"]]  # fmt: skip
        self.assertTrue(all(line["parts"] for line in lines))
        self.assertNotIn("Thanks, that is all for today.", json.dumps(notes))

    async def test_a_summary_or_open_point_without_valid_parts_is_refused_alone(self) -> None:
        """A summary, not a transcript - a summary longer than 600 characters, or a summary or
        open point naming a missing part, is refused and the other lines are kept."""
        cases = {
            "summary too long": (
                {"summary": {"text": "y" * 601, "parts": [1]}},
                "The notes' summary was refused because its text is over 600 characters.",
            ),
            "summary names a missing part": (
                {"summary": {"text": "A summary.", "parts": [1, 42]}},
                "The notes' summary was refused because it names part 42, which does not exist.",
            ),
            "open point names a missing part": (
                {"open_points": [{"text": "Who decides?", "parts": [0]}]},
                "The notes' open point 1 was refused because it names part 0, which does not "
                "exist.",
            ),
        }
        for label, (changes, reason) in cases.items():
            with self.subTest(label):
                _, notes, piece = await self.written(changed_notes(**changes))
                self.assertIn(reason, piece.refused)
                self.assertEqual(len(notes["decisions"]), 1)
                self.assertEqual(len(notes["actions"]), 2)
                if label.startswith("summary"):
                    self.assertIsNone(notes["summary"])
                else:
                    self.assertEqual(notes["open_points"], [])
                    self.assertIsNotNone(notes["summary"])

    async def test_a_summary_of_600_characters_is_kept(self) -> None:
        _, notes, piece = await self.written(
            changed_notes(summary={"text": "z" * 600, "parts": [1, 2]})
        )
        self.assertEqual(len(notes["summary"]["text"]), 600)
        self.assertEqual(piece.refused, [])

    async def test_notes_for_another_meeting_or_kind_of_work_are_refused(self) -> None:
        _, notes, piece = await self.written(changed_notes(meeting="another-meeting"))
        self.assertIsNone(notes)
        self.assertEqual(
            piece.refused,
            ["Tool request 1 was refused because it names a meeting this work is not about."],
        )
        self.assertEqual(piece.state, WorkState.DONE)


if __name__ == "__main__":
    unittest.main()
