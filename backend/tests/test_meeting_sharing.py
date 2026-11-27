"""Part 2, Story 1.1 Share a transcript: sharing, the approval check, withdrawal and the
organiser's list (task-g9AxkT1yhOg, task-seYuA3Mq8lg)."""

from __future__ import annotations

import ast
import base64
import unittest
from pathlib import Path

from assistant.api.router import PREFIX
from assistant.models.boundary import ActionKind, ApprovalLevel
from assistant.models.records import MeetingNotes, MeetingState, RequestState
from assistant.rules import meetings as rules
from assistant.rules.sentences import is_plain_sentence
from standins.sample import ORGANISATION_ID
from tests.meeting_helpers import (
    ADMIN,
    CRM,
    MANAGER,
    ORGANISER,
    OWNER,
    VENDORS,
    Meetings,
    captured_logs,
    result_with,
)
from tests.world import bare, build_world

MODULE_ROOT = Path(__file__).resolve().parents[1] / "assistant"


class ShareATranscriptTest(Meetings, unittest.IsolatedAsyncioTestCase):
    """Story 1.1 Share a transcript (task-g9AxkT1yhOg)."""

    async def asyncSetUp(self) -> None:
        self.w = await build_world()

    async def asyncTearDown(self) -> None:
        self.w.close()

    async def count(self) -> int:
        return len(await self.meetings())

    async def test_a_complete_transcript_is_accepted_when_meeting_notes_are_allowed(self) -> None:
        """Story 1.1 - a complete transcript is accepted when meeting notes are allowed."""
        before = await self.count()
        response = self.share()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["state"], "shared")
        meeting = await self.meeting(response.json()["id"])
        self.assertEqual(await self.count(), before + 1)
        self.assertEqual(meeting.state, MeetingState.SHARED)
        self.assertTrue(meeting.ran_it and meeting.people_knew)
        self.assertEqual(
            (meeting.title, meeting.organiser_id, meeting.manager_id, meeting.project_ids),
            ("Vendor review", ORGANISER, MANAGER, [VENDORS]),
        )
        self.assertEqual(meeting.meeting_date.isoformat(), "2026-11-20")
        self.assertTrue(meeting.text_kept)

    async def test_pasted_text_is_accepted_too(self) -> None:
        response = self.share_text()
        self.assertEqual(response.status_code, 200, response.text)
        meeting = await self.meeting(response.json()["id"])
        self.assertEqual(meeting.source_format, "text")

    async def test_sharing_is_refused_when_meeting_notes_are_not_allowed(self) -> None:
        """Story 1.1 - sharing is refused when meeting notes are not allowed."""
        self.allow_meeting_notes(False)
        before = await self.count()
        message = self.refused("POST", "/transcripts", ORGANISER, self.share_body())
        self.assertEqual(message, rules.NOTES_NOT_ALLOWED)
        self.assertTrue(is_plain_sentence(message))
        self.assertEqual(await self.count(), before)

    async def test_sharing_is_refused_when_a_tick_is_missing(self) -> None:
        """Story 1.1 - sharing is refused when a tick is missing."""
        before = await self.count()
        for missing in ("ran_it", "people_knew"):
            with self.subTest(missing):
                body = self.share_body(**{missing: False})
                self.assertEqual(
                    self.refused("POST", "/transcripts", ORGANISER, body), rules.TICK_MISSING
                )
        self.assertEqual(await self.count(), before)

    async def test_a_wrong_non_text_or_oversized_file_is_refused(self) -> None:
        """Story 1.1 - a wrong, non-text or oversized file is refused."""
        too_big = "x" * (rules.MAX_TRANSCRIPT_BYTES + 1)
        cases = {
            "another type": ({"file_name": "minutes.pdf"}, rules.WRONG_TYPE),
            "not text": (
                {"file_base64": base64.b64encode(b"\x00\xff\xfe binary").decode()},
                rules.NOT_TEXT,
            ),
            "not utf-8": (
                {"file_base64": base64.b64encode("café".encode("latin-1")).decode()},
                rules.NOT_TEXT,
            ),
            "larger than 2 MB": (
                {"file_base64": base64.b64encode(too_big.encode()).decode()},
                rules.TOO_LARGE,
            ),
            "pasted text larger than 2 MB": (
                {"file_name": None, "file_base64": None, "pasted_text": too_big},
                rules.TOO_LARGE,
            ),
        }
        before = await self.count()
        for label, (changes, sentence) in cases.items():
            with self.subTest(label):
                body = self.share_body(**changes)
                self.assertEqual(self.refused("POST", "/transcripts", ORGANISER, body), sentence)
        self.assertEqual(await self.count(), before)

    def test_uploads_are_never_written_to_disk_or_run(self) -> None:
        """Story 1.1 - nothing is written to disk: the module has no file writes at all."""
        writes = {"open", "write_text", "write_bytes", "mkstemp", "NamedTemporaryFile"}
        found = []
        for path in sorted(MODULE_ROOT.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                name = None
                if isinstance(node, ast.Call):
                    func = node.func
                    name = func.attr if isinstance(func, ast.Attribute) else getattr(
                        func,
                        "id",
                        None,
                    )
                if isinstance(node, ast.Import | ast.ImportFrom):
                    modules = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
                    if any(m.split(".")[0] in ("tempfile", "shutil") for m in modules):
                        found.append(f"{path.name}: imports a file module")
                if name in writes:
                    found.append(f"{path.name}: {name}")
        self.assertEqual(found, [])

    async def test_writing_the_notes_goes_through_the_approval_check(self) -> None:
        """Story 1.1 - writing the notes goes through the approval check, starting at with
        approval."""
        levels = {lv["kind"]: lv for lv in self.ok("GET", "/approval-levels", MANAGER)}
        kind = levels["write_meeting_notes"]
        self.assertEqual(kind["label"], "Write notes from a shared transcript")
        self.assertEqual(kind["level"], "with_approval")
        self.assertTrue(kind["allowed_limits"]["projects"])
        response = self.share()
        checked = [r for r, _ in self.w.standins.approval.checked]
        self.assertEqual(checked[-1].kind, ActionKind.WRITE_MEETING_NOTES)
        self.assertEqual(checked[-1].level, ApprovalLevel.WITH_APPROVAL)
        self.assertEqual(checked[-1].project_ids, [VENDORS])
        (request,) = await self.requests_about(response.json()["id"])
        self.assertEqual(request.state, RequestState.WAITING)

    async def test_without_approval_a_transcript_inside_the_limit_is_carried_out(self) -> None:
        """Story 1.1 - without approval, a transcript inside the limit is carried out."""
        await self.set_level("write_meeting_notes", "without_approval", projects=[VENDORS, CRM])
        response = self.share(project_ids=[VENDORS])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["state"], "writing")
        (request,) = await self.requests_about(response.json()["id"])
        self.assertEqual(
            (request.state, request.allowed_by), (RequestState.DONE, "without_approval")
        )
        self.assertEqual(len(self.started_notes()), 1)
        self.assertEqual(self.ok("GET", "/requests", MANAGER)["count"], 1)  # only the seeded one

    async def test_without_approval_a_transcript_outside_the_limit_waits(self) -> None:
        """Story 1.1 - without approval, a transcript outside the limit waits for the manager,
        and is not refused."""
        await self.set_level("write_meeting_notes", "without_approval", projects=[CRM])
        response = self.share(project_ids=[CRM, VENDORS])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["state"], "shared")
        waiting = self.ok("GET", "/requests", MANAGER)["requests"]
        self.assertIn("write_meeting_notes", [r["kind"] for r in waiting])
        self.assertEqual(self.started_notes(), [])

    async def test_with_approval_the_request_always_waits(self) -> None:
        """Story 1.1 - with approval, the request always waits for the manager."""
        for projects in ([], [CRM], [VENDORS, CRM]):
            with self.subTest(projects):
                response = self.share(project_ids=projects)
                self.assertEqual(response.json()["state"], "shared")
                (request,) = await self.requests_about(response.json()["id"])
                self.assertEqual(request.state, RequestState.WAITING)
        self.assertEqual(self.started_notes(), [])

    async def test_the_manager_approves_and_the_notes_are_started(self) -> None:
        response = self.share()
        (request,) = await self.requests_about(response.json()["id"])
        self.ok("POST", f"/requests/{request.id}/approve", MANAGER)
        meeting = await self.meeting(response.json()["id"])
        self.assertEqual(meeting.state, MeetingState.WRITING)
        self.assertEqual(len(self.started_notes()), 1)

    async def test_not_allowed_is_refused(self) -> None:
        """Story 1.1 - not allowed is refused, and nothing is kept."""
        await self.set_level("write_meeting_notes", "not_allowed")
        before = await self.count()
        message = self.refused("POST", "/transcripts", ORGANISER, self.share_body())
        self.assertEqual(message, rules.KIND_NOT_ALLOWED)
        self.assertEqual(await self.count(), before)

    async def test_each_refusal_reason_has_its_own_sentence(self) -> None:
        """Story 1.1 - each refusal reason has its own sentence."""
        sentences = {
            "tick missing": self.refused(
                "POST", "/transcripts", ORGANISER, self.share_body(ran_it=False)
            ),
            "another type": self.refused(
                "POST", "/transcripts", ORGANISER, self.share_body(file_name="notes.docx")
            ),
            "not text": self.refused(
                "POST",
                "/transcripts",
                ORGANISER,
                self.share_body(file_base64=base64.b64encode(b"\x00\x01").decode()),
            ),
            "larger than 2 MB": self.refused(
                "POST",
                "/transcripts",
                ORGANISER,
                self.share_body(
                    file_base64=base64.b64encode(
                        b"x" * (rules.MAX_TRANSCRIPT_BYTES + 1)
                    ).decode()
                ),
            ),
        }
        await self.set_level("write_meeting_notes", "not_allowed")
        sentences["kind not allowed"] = self.refused(
            "POST", "/transcripts", ORGANISER, self.share_body()
        )
        self.allow_meeting_notes(False)
        sentences["meeting notes not allowed"] = self.refused(
            "POST", "/transcripts", ORGANISER, self.share_body()
        )
        self.assertEqual(len(set(sentences.values())), len(sentences), sentences)
        for label, sentence in sentences.items():
            self.assertTrue(is_plain_sentence(sentence), (label, sentence))
        self.assertEqual(len(set(rules.SHARING_REFUSALS)), len(rules.SHARING_REFUSALS))

    async def test_the_administrators_choice_applies_to_the_next_transcript(self) -> None:
        """Story 1.1 - the administrator's choice applies to the next transcript, and notes
        that already exist stay."""
        seeded_notes = f"/meeting-notes/{self.w.seeded.notes.id}"
        self.allow_meeting_notes(False)
        settings = self.ok("GET", "/organisation/settings", ADMIN)
        self.assertFalse(settings["meeting_notes_allowed"])
        self.assertEqual(
            self.refused("POST", "/transcripts", ORGANISER, self.share_body()),
            rules.NOTES_NOT_ALLOWED,
        )
        self.assertEqual(self.ok("GET", seeded_notes, MANAGER)["title"], "Vendor review")
        self.allow_meeting_notes(True)
        self.assertEqual(self.share().status_code, 200)
        self.assertEqual(self.ok("GET", seeded_notes, MANAGER)["title"], "Vendor review")

    async def test_only_an_administrator_changes_the_setting(self) -> None:
        for person in (MANAGER, OWNER, ORGANISER):
            with self.subTest(person):
                response = self.call(
                    "PUT", "/organisation/settings", person, {"meeting_notes_allowed": False}
                )
                self.assertEqual(response.status_code, 404)
        self.assertEqual(self.share().status_code, 200)

    def test_meeting_notes_are_not_allowed_by_default(self) -> None:
        fresh = bare()
        settings = fresh.call("GET", f"{PREFIX}/organisation/settings", ADMIN).json()
        self.assertFalse(settings["meeting_notes_allowed"])

    async def test_a_project_the_manager_cannot_see_is_refused(self) -> None:
        message = self.refused(
            "POST", "/transcripts", ORGANISER, self.share_body(project_ids=["no-such-project"])
        )
        self.assertEqual(message, rules.UNKNOWN_PROJECT)

    async def test_a_person_without_an_assistant_cannot_be_shared_with(self) -> None:
        message = self.refused(
            "POST", "/transcripts", ORGANISER, self.share_body(manager_id=OWNER, project_ids=[])
        )
        self.assertEqual(message, rules.NO_ASSISTANT)

    async def test_sharing_writes_no_transcript_text_anywhere(self) -> None:
        with captured_logs(self) as logs:
            self.share()
        self.assert_no_transcript_text(logs)


class WithdrawAndListTest(Meetings, unittest.IsolatedAsyncioTestCase):
    """Story 1.1 Share a transcript: withdraw and list (task-seYuA3Mq8lg)."""

    async def asyncSetUp(self) -> None:
        self.w = await build_world()

    async def asyncTearDown(self) -> None:
        self.w.close()

    async def test_withdrawing_removes_the_text_and_tells_the_manager(self) -> None:
        """Story 1.1 - withdrawing removes the text and tells the manager."""
        shared = self.share().json()
        (request,) = await self.requests_about(shared["id"])
        with captured_logs(self) as logs:
            withdrawn = self.ok("POST", f"/transcripts/{shared['id']}/withdraw", ORGANISER)
        self.assertEqual(
            withdrawn, {"id": shared["id"], "title": "Vendor review", "state": "withdrawn"}
        )
        meeting = await self.meeting(shared["id"])
        self.assertEqual(meeting.state, MeetingState.WITHDRAWN)
        self.assertFalse(meeting.text_kept)
        self.assertEqual([p.number for p in meeting.parts], list(range(1, 9)))
        self.assertTrue(all(p.starts for p in meeting.parts))
        (request,) = await self.requests_about(shared["id"])
        self.assertEqual(request.state, RequestState.WITHDRAWN)
        self.assertEqual(
            self.notices_to(MANAGER),
            ["The Programme lead withdrew the transcript of Vendor review."],
        )
        waiting = self.ok("GET", "/requests", MANAGER)["requests"]
        self.assertNotIn("write_meeting_notes", [r["kind"] for r in waiting])
        self.assert_no_transcript_text(logs)

    async def test_withdrawing_is_offered_until_the_notes_are_written(self) -> None:
        meeting = await self.write_notes()
        self.assertEqual(meeting.state, MeetingState.READY)
        message = self.refused("POST", f"/transcripts/{meeting.id}/withdraw", ORGANISER)
        self.assertEqual(message, rules.NOT_WITHDRAWABLE)

    async def test_a_failed_transcript_can_be_withdrawn(self) -> None:
        await self.set_level("write_meeting_notes", "without_approval")
        shared = self.share().json()
        await self.finish_notes(
            result_with(failure="The assistant could not read it.", tool_requests=[])
        )
        self.assertEqual((await self.meeting(shared["id"])).state, MeetingState.FAILED)
        self.ok("POST", f"/transcripts/{shared['id']}/withdraw", ORGANISER)
        self.assertFalse((await self.meeting(shared["id"])).text_kept)

    async def test_notes_that_arrive_after_a_withdrawal_are_discarded(self) -> None:
        """Story 1.1 - notes that arrive after a withdrawal are discarded."""
        await self.set_level("write_meeting_notes", "without_approval")
        shared = self.share().json()
        self.assertEqual(shared["state"], "writing")
        self.ok("POST", f"/transcripts/{shared['id']}/withdraw", ORGANISER)
        (work_id,) = await self.finish_notes()
        self.assertIsNone(await self.notes_of(shared["id"]))
        meeting = await self.meeting(shared["id"])
        self.assertEqual(meeting.state, MeetingState.WITHDRAWN)
        piece = await self.piece(work_id)
        self.assertTrue(any("discarded" in reason for reason in piece.refused), piece.refused)
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            self.assertEqual(
                [n for n in await tx.find(MeetingNotes) if n.meeting_id == meeting.id], []
            )

    async def test_the_organiser_sees_only_title_and_state(self) -> None:
        """Story 1.1 - the organiser sees only the title and state of what they shared, and
        nothing the assistant wrote."""
        written = await self.write_notes()
        budget = self.share(title="Budget call").json()
        seeded = str(self.w.seeded.notes.meeting_id)
        listed = [
            t
            for t in self.ok("GET", "/transcripts", ORGANISER)["transcripts"]
            if t["id"] != seeded
        ]
        self.assertEqual(
            {t["id"]: (t["title"], t["state"]) for t in listed},
            {str(written.id): ("Vendor review", "ready"), budget["id"]: ("Budget call", "writing")},
        )
        for entry in listed:
            self.assertEqual(set(entry), {"id", "title", "state"})
        # Nothing the assistant wrote: the notes stay the manager's only.
        notes = await self.notes_of(written.id)
        assert notes is not None
        self.assertEqual(self.call("GET", f"/meeting-notes/{notes.id}", ORGANISER).status_code, 404)
        self.assertEqual(self.call("GET", f"/meetings/{written.id}", ORGANISER).status_code, 404)

    async def test_only_the_organiser_withdraws_what_they_shared(self) -> None:
        shared = self.share().json()
        for person in (MANAGER, OWNER, ADMIN):
            with self.subTest(person):
                response = self.call("POST", f"/transcripts/{shared['id']}/withdraw", person)
                self.assertEqual(response.status_code, 404)
        self.assertEqual((await self.meeting(shared["id"])).state, MeetingState.SHARED)

    async def test_others_do_not_see_what_someone_shared(self) -> None:
        self.share()
        self.assertEqual(self.ok("GET", "/transcripts", MANAGER)["transcripts"], [])
        owner_list = self.ok("GET", "/transcripts", OWNER)["transcripts"]
        self.assertEqual([t["title"] for t in owner_list], ["Contract review"])  # their own


if __name__ == "__main__":
    unittest.main()
