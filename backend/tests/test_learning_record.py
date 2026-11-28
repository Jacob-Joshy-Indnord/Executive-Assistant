"""Part 2, Epic 3 - Manage the learning record (task-zmzC336CoUs).

The manager lists lessons with their text, one-line evidence and origin, and keeps, edits,
adds or removes a lesson or clears the record, each recorded before it takes effect. A
proposal that repeats a removed lesson in the same words (ignoring capitals and spacing) is
refused, and no more than 200 lessons of any origin are kept; proposed ones do not count.
"""

from __future__ import annotations

import unittest
from typing import Any

from assistant.models.records import Lesson, LessonState
from assistant.rules.learning import LIMIT_REACHED, MAX_KEPT_LESSONS
from standins.sample import ORGANISATION_ID
from tests.item_helpers import EpicFour
from tests.work_helpers import MANAGER, EpicThree
from tests.world import PERSONAS, World, build_world

SEEDED_TEXT = "Give a day in every request for figures."


class RecordCase(EpicThree, EpicFour, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        self.ops = self.w.seeded.assistant
        self.t = await self.w.standins.clock.now()
        self.seeded_path = f"/learning/lessons/{self.w.seeded.lesson.id}"
        await self.forget_seeded_request()

    async def asyncTearDown(self) -> None:
        self.w.close()

    async def lessons(self) -> list[Lesson]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return await tx.find(Lesson, assistant_id=self.ops.id)

    def listed(self) -> dict[str, Any]:
        listed = self.ok("GET", "/learning/lessons", MANAGER)["lessons"]
        return {lesson["text"]: lesson for lesson in listed}

    async def next_pack_lessons(self) -> list[str]:
        """The lessons given with the next piece of work."""
        self.w.standins.assistant.started.clear()
        self.ok("POST", "/chat", MANAGER, {"text": "What is next?"})
        (order,) = self.w.standins.assistant.started
        for result in self.w.standins.assistant.take_finished():
            await self.w.module.flow.finish_work(ORGANISATION_ID, self.ops.flow_id, result)
        return [lesson.text for lesson in order.input_pack.lessons]

    async def keep_many(self, count: int) -> None:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            for n in range(count):
                await tx.add(
                    Lesson(
                        organisation_id=ORGANISATION_ID,
                        assistant_id=self.ops.id,
                        created_at=self.t,
                        area="general",
                        text=f"Lesson number {n}.",
                        origin="typed",
                        kept_at=self.t,
                        state=LessonState.KEPT,
                    )
                )

    def propose(self, text: str) -> dict[str, Any]:
        return {
            "tool": "propose_learning",
            "area": "drafts",
            "lesson": text,
            "evidence": [str(self.w.seeded.signal.id)],
        }


class ShowingLessonsTest(RecordCase):
    async def test_each_lesson_shows_text_evidence_and_origin(self) -> None:
        self.ok("POST", "/learning/lessons", MANAGER, {"text": "Keep messages short."})
        self.w.call("PUT", "/api/assistant/preferences", MANAGER, {"tone": {"sign_off": "Thanks"}})
        await self.run_tools(
            [{**self.propose("x"), "lesson": "x", "evidence": "Always copy the Finance lead."}],
            text="Always copy the Finance lead.",
        )
        listed = self.listed()
        expected = {
            SEEDED_TEXT: ("assistant", "From 1 draft you edited."),
            "Keep messages short.": ("typed", "Written by you on 23 November 2026."),
            "Sign-off: Thanks": ("form", "Your answer about sign-off in the preferences form."),
            "Always copy the Finance lead.": (
                "your_words",
                "Quoted word for word from your message of 23 November 2026.",
            ),
        }
        self.assertEqual(set(listed), set(expected))
        for text, (origin, evidence) in expected.items():
            self.assertEqual((listed[text]["origin"], listed[text]["evidence"]), (origin, evidence))
            self.assertNotIn("\n", listed[text]["evidence"])
            self.assertTrue(listed[text]["origin_label"])
        self.assertEqual(self.ok("GET", "/learning/lessons", MANAGER)["kept"], 3)


class ManagersChangesTest(RecordCase):
    async def test_keeping_a_proposed_lesson_applies_to_the_next_piece_of_work(self) -> None:
        self.assertNotIn(SEEDED_TEXT, await self.next_pack_lessons())
        kept = self.ok("POST", f"{self.seeded_path}/keep", MANAGER)
        self.assertEqual(kept["state"], "kept")
        self.assertIn(SEEDED_TEXT, await self.next_pack_lessons())

    async def test_an_edited_proposed_lesson_is_kept_in_the_managers_wording(self) -> None:
        edited = self.ok("PUT", self.seeded_path, MANAGER, {"text": "Give two days for figures."})
        self.assertEqual((edited["text"], edited["state"]), ("Give two days for figures.", "kept"))
        self.assertTrue(edited["edited"])
        self.assertEqual(await self.next_pack_lessons(), ["Give two days for figures."])

    async def test_an_edited_kept_lesson_applies_at_once(self) -> None:
        added = self.ok("POST", "/learning/lessons", MANAGER, {"text": "Keep messages short."})
        self.ok("PUT", f"/learning/lessons/{added['id']}", MANAGER, {"text": "Keep it brief."})
        self.assertEqual(await self.next_pack_lessons(), ["Keep it brief."])

    async def test_adding_and_removing_apply_to_the_next_piece_of_work(self) -> None:
        added = self.ok("POST", "/learning/lessons", MANAGER, {"text": "Keep messages short."})
        self.assertEqual(await self.next_pack_lessons(), ["Keep messages short."])
        self.ok("DELETE", f"/learning/lessons/{added['id']}", MANAGER)
        self.assertEqual(await self.next_pack_lessons(), [])

    async def test_clearing_the_record_removes_every_lesson_each_counting_as_removed(
        self,
    ) -> None:
        self.ok("POST", "/learning/lessons", MANAGER, {"text": "Keep messages short."})
        self.assertEqual(await self.next_pack_lessons(), ["Keep messages short."])
        cleared = self.ok("DELETE", "/learning/lessons", MANAGER)
        self.assertEqual((cleared["lessons"], cleared["kept"]), ([], 0))
        self.assertEqual(await self.lessons(), [])
        self.assertEqual(len(self.audited("assistant.lesson_removed")), 2)
        self.assertEqual(await self.next_pack_lessons(), [])
        # Each counts as removed: neither is proposed again in the same words.
        work_id = await self.run_tools(
            [self.propose(SEEDED_TEXT), self.propose("keep messages short.")]
        )
        self.assertEqual(len(self.work_view(work_id)["refused"]), 2)

    async def test_each_change_is_recorded_before_it_takes_effect(self) -> None:
        added = self.ok("POST", "/learning/lessons", MANAGER, {"text": "Keep messages short."})
        self.ok("PUT", f"/learning/lessons/{added['id']}", MANAGER, {"text": "Keep it brief."})
        self.ok("POST", f"{self.seeded_path}/keep", MANAGER)
        self.ok("DELETE", f"/learning/lessons/{added['id']}", MANAGER)
        for name in ("lesson_kept", "lesson_edited", "lesson_removed"):
            self.assertTrue(self.audited(f"assistant.{name}"), name)
        self.w.standins.records.failing = True
        for method, path, body in (
            ("POST", "/learning/lessons", {"text": "Write in French."}),
            ("PUT", self.seeded_path, {"text": "Changed."}),
            ("DELETE", self.seeded_path, None),
            ("DELETE", "/learning/lessons", None),
        ):
            response = self.call(method, path, MANAGER, body)
            self.assertEqual(response.status_code, 503, (method, path))
            self.assertEqual(response.json()["detail"]["message"], "Not saved. Try again.")
        self.w.standins.records.failing = False
        self.assertEqual([lesson.text for lesson in await self.lessons()], [SEEDED_TEXT])


class SameWordsTest(RecordCase):
    async def test_a_removed_lesson_is_not_proposed_again_in_the_same_words(self) -> None:
        self.ok("DELETE", self.seeded_path, MANAGER)
        work_id = await self.run_tools(
            [
                self.propose("give a  DAY in every request\tfor figures."),
                self.propose("Give a week in every request for figures."),
            ]
        )
        refused = self.work_view(work_id)["refused"]
        self.assertEqual(len(refused), 1, refused)
        self.assertIn("a lesson in the same words was removed", refused[0])
        self.assertEqual(
            [lesson.text for lesson in await self.lessons()],
            ["Give a week in every request for figures."],
        )

    async def test_the_removed_words_are_kept_only_as_a_digest(self) -> None:
        self.ok("DELETE", self.seeded_path, MANAGER)
        records = [r.model_dump_json() for r in self.w.standins.records.written]
        self.assertFalse(any(SEEDED_TEXT in r for r in records))

    async def test_a_stated_quote_of_removed_words_is_refused_too(self) -> None:
        self.ok("DELETE", self.seeded_path, MANAGER)
        quote = {**self.propose("x"), "evidence": SEEDED_TEXT}
        work_id = await self.run_tools([quote], text=SEEDED_TEXT)
        self.assertEqual(len(self.work_view(work_id)["refused"]), 1)

    async def test_the_same_lesson_twice_is_refused(self) -> None:
        work_id = await self.run_tools([self.propose(SEEDED_TEXT.upper())])
        (reason,) = self.work_view(work_id)["refused"]
        self.assertIn("the same lesson is already in the record", reason)


class TwoHundredTest(RecordCase):
    async def test_no_more_than_200_lessons_are_kept(self) -> None:
        await self.keep_many(MAX_KEPT_LESSONS)
        self.assertEqual(
            self.refused("POST", "/learning/lessons", MANAGER, {"text": "One more."}),
            LIMIT_REACHED,
        )
        self.assertEqual(self.refused("POST", f"{self.seeded_path}/keep", MANAGER), LIMIT_REACHED)
        self.assertEqual(
            self.refused("PUT", self.seeded_path, MANAGER, {"text": "Edited."}), LIMIT_REACHED
        )
        quote = {**self.propose("x"), "evidence": "Always be brief."}
        work_id = await self.run_tools([quote], text="Always be brief.")
        (reason,) = self.work_view(work_id)["refused"]
        self.assertIn("200 lessons are kept already", reason)
        # Until one is removed.
        some = next(lesson for lesson in await self.lessons() if lesson.state is LessonState.KEPT)
        self.ok("DELETE", f"/learning/lessons/{some.id}", MANAGER)
        self.assertEqual(self.ok("POST", f"{self.seeded_path}/keep", MANAGER)["state"], "kept")

    async def test_proposed_lessons_do_not_count(self) -> None:
        await self.keep_many(MAX_KEPT_LESSONS - 1)
        work_id = await self.run_tools([self.propose(f"Proposal {n}.") for n in range(5)])
        self.assertEqual(self.work_view(work_id)["refused"], [])
        added = self.ok("POST", "/learning/lessons", MANAGER, {"text": "The 200th."})
        self.assertEqual(added["state"], "kept")
        self.assertEqual(self.ok("GET", "/learning/lessons", MANAGER)["kept"], MAX_KEPT_LESSONS)

    async def test_the_form_counts_towards_the_200(self) -> None:
        await self.keep_many(MAX_KEPT_LESSONS)
        response = self.w.call(
            "PUT", "/api/assistant/preferences", MANAGER, {"tone": {"sign_off": "Thanks"}}
        )
        self.assertEqual(response.status_code, 422, response.text)


class WhoReadsTheRecordTest(RecordCase):
    async def test_only_the_manager_can_read_a_lesson_or_the_record(self) -> None:
        for persona in ("other_manager", "owner", "administrator"):
            person = PERSONAS[persona]
            response = self.call("GET", self.seeded_path, person)
            self.assertEqual(response.status_code, 404, persona)
            for method, path, body in (
                ("PUT", self.seeded_path, {"text": "Changed."}),
                ("POST", f"{self.seeded_path}/keep", None),
                ("DELETE", self.seeded_path, None),
            ):
                self.assertEqual(self.call(method, path, person, body).status_code, 404)
            listed = self.call("GET", "/learning/lessons", person)
            self.assertNotIn(SEEDED_TEXT, listed.text)
        self.assertEqual([lesson.text for lesson in await self.lessons()], [SEEDED_TEXT])


if __name__ == "__main__":
    unittest.main()
