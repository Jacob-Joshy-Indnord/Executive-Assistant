"""Part 2, Story "Remembered meetings": search, questions and sharing of kept notes
(task-TCN9mqw4zHs, task-Iv7u7mWG8nc)."""

from __future__ import annotations

import inspect
import unittest
from typing import Any

from assistant.boundary.interfaces import Boundary, Knowledge
from assistant.models.boundary import DATA_NOT_INSTRUCTIONS
from assistant.models.records import ChatMessage
from standins.sample import ORGANISATION_ID, SALES_DIRECTOR
from tests.meeting_helpers import MANAGER, ORGANISER, OWNER, VENDORS, Meetings
from tests.test_meeting_attendance import MODULE_ROOT, PRODUCTS
from tests.world import build_world


class RememberedMeetingsTest(Meetings, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w = await build_world()
        await self.follow(VENDORS)
        meeting = await self.write_notes(title="Supplier review")
        self.notes_id = str(meeting.notes_id)
        self.path = f"/meeting-notes/{self.notes_id}"

    async def asyncTearDown(self) -> None:
        self.w.close()

    def search(self, who: str = MANAGER, **query: str) -> Any:
        params = "&".join(f"{k}={v}" for k, v in query.items())
        return self.call("GET", f"/meeting-notes/search?{params}", who)


class SearchAndAnswerTest(RememberedMeetingsTest):
    """Remembered meetings: search kept notes and answer from them (task-TCN9mqw4zHs)."""

    def found(self, **query: str) -> list[str]:
        response = self.search(**query)
        self.assertEqual(response.status_code, 200, response.text)
        return [r["notes_id"] for r in response.json()["results"]]

    async def test_the_manager_can_search_the_kept_notes(self) -> None:
        """Remembered meetings - the manager searches kept notes by words, meeting, project or
        person, and each result opens the notes with their sources."""
        seeded = str(self.w.seeded.notes.id)
        self.assertEqual(self.found(words="office supplies"), [self.notes_id])
        self.assertEqual(self.found(meeting="supplier"), [self.notes_id])
        self.assertEqual(self.found(project="Vendor consolidation"), [self.notes_id])
        self.assertEqual(self.found(project=VENDORS), [self.notes_id])
        self.assertEqual(sorted(self.found(person="Finance lead")), sorted([self.notes_id, seeded]))
        self.assertEqual(self.found(person="Legal lead"), [self.notes_id])
        self.assertEqual(sorted(self.found(words="vendors", person="Programme lead")),
                         sorted([self.notes_id, seeded]))  # fmt: skip
        self.assertEqual(self.found(words="nothing like this"), [])
        (result,) = self.search(words="office supplies").json()["results"]
        self.assertEqual(
            result["lines"],
            [
                {
                    "kind": "decision",
                    "text": "We keep two vendors for office supplies.",
                    "parts": [3],
                }
            ],
        )
        opened = self.ok("GET", f"/meeting-notes/{result['notes_id']}", MANAGER)
        self.assertEqual(opened["decisions"][0]["parts"], [3])
        self.assertEqual(opened["parts"][2]["starts"], "00:01:00.000")

    async def test_search_never_finds_an_unconfirmed_statement(self) -> None:
        self.assertEqual(self.found(words="one is open"), [])  # only its statement says so

    async def test_a_question_about_a_past_meeting_is_answered_from_its_notes(self) -> None:
        """Remembered meetings - when the manager asks about a past meeting, the input pack
        holds the matching notes, marked as data, and the reply cites them."""
        self.ok("POST", "/chat", MANAGER, {"text": "What did we decide at the supplier review?"})
        (result,) = self.w.standins.assistant.take_finished()
        (order,) = [o for o in self.w.standins.assistant.started if o.work_id == result.work_id]
        notes = [i for i in order.input_pack.inputs if i.kind == "meeting_notes"]
        self.assertEqual([n.subject_id for n in notes], [self.notes_id])
        self.assertEqual(notes[0].note, DATA_NOT_INSTRUCTIONS)
        self.assertEqual(notes[0].label, "Meeting notes: Supplier review (2026-11-20)")
        self.assertIn(
            "Decision: We keep two vendors for office supplies. (part 3)",
            notes[0].content,
        )
        self.assertNotIn("Two contracts are signed", notes[0].content)  # not yet confirmed
        await self.w.module.flow.finish_work(
            ORGANISATION_ID, self.w.seeded.assistant.flow_id, result
        )
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            replies = [m.text for m in await tx.find(ChatMessage, author="assistant")]
        self.assertTrue(replies[-1].startswith("From Meeting notes: Supplier review (2026-11-20):"))
        self.assertIn("We keep two vendors for office supplies.", replies[-1])

    async def test_a_question_about_something_else_gets_no_notes(self) -> None:
        self.ok("POST", "/chat", MANAGER, {"text": "Please book a room for Friday."})
        (order,) = self.w.standins.assistant.started[-1:]
        self.assertEqual([i for i in order.input_pack.inputs if i.kind == "meeting_notes"], [])

    async def test_nobody_but_the_manager_can_search_the_notes(self) -> None:
        """Remembered meetings - an organiser, an owner or another manager finds nothing."""
        for person in (ORGANISER, OWNER):
            with self.subTest(person):
                self.assertEqual(self.search(person, words="vendors").status_code, 404)
        other = self.search(SALES_DIRECTOR.id, words="vendors")
        self.assertEqual((other.status_code, other.json()["count"]), (200, 0))


class ShareThroughKnowledgeTest(RememberedMeetingsTest):
    """Remembered meetings: share a kept note through the boundary (task-Iv7u7mWG8nc)."""

    def test_the_boundary_gains_knowledge_and_names_no_product(self) -> None:
        """Remembered meetings - the boundary has Knowledge with two requests, add and remove,
        and names no product."""
        requests = {
            name
            for name, member in inspect.getmembers(Knowledge)
            if inspect.iscoroutinefunction(member)
        }
        self.assertEqual(requests, {"add", "remove"})
        self.assertIn("knowledge", Boundary.__dataclass_fields__)
        self.assertIsNone(PRODUCTS.search((MODULE_ROOT / "boundary" / "interfaces.py").read_text()))

    async def test_a_shared_note_is_told_to_flow_and_nothing_else_is(self) -> None:
        """Remembered meetings - when the manager shares a kept note, Flow is told (Knowledge:
        add); nothing is shared otherwise, and the stand-in keeps a list."""
        knowledge = self.w.standins.knowledge
        self.assertEqual(knowledge.told, [])  # writing and reading notes shares nothing
        shared = self.ok("POST", f"{self.path}/share", MANAGER)
        self.assertTrue(shared["shared"])
        self.assertEqual(knowledge.told, [("add", self.notes_id)])
        note = knowledge.notes[self.notes_id]
        self.assertEqual(
            (note.title, note.date, note.manager_id),
            ("Supplier review", "2026-11-20", MANAGER),
        )
        self.assertIn("Decision: We keep two vendors for office supplies. (part 3)", note.content)
        self.assertNotIn("Two contracts are signed", note.content)
        self.ok("POST", f"{self.path}/share", MANAGER)  # pressed twice: told once
        self.assertEqual(knowledge.told, [("add", self.notes_id)])

    async def test_a_note_that_is_no_longer_shared_is_taken_back(self) -> None:
        """Remembered meetings - when the manager stops sharing a note, Flow is told (Knowledge:
        remove), and the stand-in's list shows it."""
        self.ok("POST", f"{self.path}/share", MANAGER)
        unshared = self.ok("POST", f"{self.path}/unshare", MANAGER)
        self.assertFalse(unshared["shared"])
        self.assertEqual(
            self.w.standins.knowledge.told, [("add", self.notes_id), ("remove", self.notes_id)]
        )
        self.assertEqual(self.w.standins.knowledge.notes, {})

    async def test_deleting_a_shared_note_takes_it_back_from_flow(self) -> None:
        """Remembered meetings - when the manager deletes a shared note, Flow is told to remove
        it (Knowledge: remove)."""
        self.ok("POST", f"{self.path}/share", MANAGER)
        self.ok("DELETE", self.path, MANAGER)
        self.assertEqual(self.w.standins.knowledge.told[-1], ("remove", self.notes_id))
        self.assertEqual(self.w.standins.knowledge.notes, {})

    async def test_a_note_that_cannot_be_shared_is_not_marked_shared(self) -> None:
        self.w.standins.knowledge.failing = True
        response = self.call("POST", f"{self.path}/share", MANAGER)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["message"], "The note could not be shared.")
        self.assertFalse(self.ok("GET", self.path, MANAGER)["shared"])

    async def test_shared_notes_stay_in_flow_at_handover_or_end(self) -> None:
        """Remembered meetings - when the assistant is handed over or ended, notes already
        shared stay in Flow; Flow is not told to remove them."""
        self.ok("POST", f"{self.path}/share", MANAGER)
        await self.w.module.flow.hand_over(
            ORGANISATION_ID, self.w.seeded.assistant.flow_id, ORGANISER
        )
        self.assertEqual(self.w.standins.knowledge.told, [("add", self.notes_id)])
        self.assertIn(self.notes_id, self.w.standins.knowledge.notes)
        await self.w.module.flow.end(ORGANISATION_ID, self.w.seeded.assistant.flow_id)
        self.assertEqual(self.w.standins.knowledge.told, [("add", self.notes_id)])
        self.assertIn(self.notes_id, self.w.standins.knowledge.notes)


if __name__ == "__main__":
    unittest.main()
