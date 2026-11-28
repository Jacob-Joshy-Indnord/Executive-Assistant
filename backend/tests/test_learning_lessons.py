"""Part 2, Epic 3 - Propose and state lessons (task-9Pc8XeTMnCc).

The ``propose_learning`` tool (area, lesson, evidence) keeps a lesson as proposed when it
names signals kept for this assistant, and refuses one whose evidence is neither such
signals nor a word-for-word quote. A quote of a message the manager sent to this assistant,
or a lesson the manager types, is stated: the manager's own words, kept at once. Each answer
of part 1's preferences form is one kept lesson. Only kept lessons reach the input pack.
"""

from __future__ import annotations

import unittest
from typing import Any
from uuid import uuid4

from assistant.models.boundary import ActionKind
from assistant.models.records import (
    Lesson,
    LessonState,
    RequestRecord,
    Signal,
    SignalKind,
)
from assistant.rules.learning import LESSONS_LABEL, quotes
from assistant.service.input_pack import input_pack_for
from standins.sample import FINANCE_LEAD, ORGANISATION_ID
from tests.item_helpers import EpicFour
from tests.work_helpers import MANAGER, EpicThree
from tests.world import World, build_world

QUOTED = "Always copy the Finance lead on budget messages."


def proposal(evidence: Any, lesson: str = "Copy the Finance lead on budget messages.") -> Any:
    return {"tool": "propose_learning", "area": "drafts", "lesson": lesson, "evidence": evidence}


class LessonCase(EpicThree, EpicFour, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        self.ops = self.w.seeded.assistant
        self.t = await self.w.standins.clock.now()
        await self.forget_seeded_request()

    async def asyncTearDown(self) -> None:
        self.w.close()

    async def lessons(self) -> list[Lesson]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return await tx.find(Lesson, assistant_id=self.ops.id)

    async def lesson_with(self, text: str) -> Lesson:
        found = [lesson for lesson in await self.lessons() if lesson.text == text]
        self.assertEqual(len(found), 1, [lesson.text for lesson in await self.lessons()])
        return found[0]

    async def pack(self) -> Any:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return await input_pack_for(tx, self.ops, now=await self.w.standins.clock.now())

    def refusals(self, work_id: Any) -> list[str]:
        return list(self.work_view(work_id)["refused"])


class ProposedLessonsTest(LessonCase):
    async def test_a_proposed_lesson_names_the_signals_it_rests_on(self) -> None:
        signal = self.w.seeded.signal
        work_id = await self.run_tools([proposal([str(signal.id)])])
        self.assertEqual(self.refusals(work_id), [])
        lesson = await self.lesson_with("Copy the Finance lead on budget messages.")
        self.assertEqual(lesson.state, LessonState.PROPOSED)
        self.assertEqual(lesson.origin, "assistant")
        self.assertEqual(lesson.area, "drafts")
        self.assertEqual(lesson.signal_ids, [signal.id])
        self.assertEqual(lesson.signal_kinds, [SignalKind.DRAFT_EDITED])

    async def test_a_single_signal_id_as_text_is_signals_too(self) -> None:
        work_id = await self.run_tools([proposal(str(self.w.seeded.signal.id))])
        self.assertEqual(self.refusals(work_id), [])
        lesson = await self.lesson_with("Copy the Finance lead on budget messages.")
        self.assertEqual(lesson.state, LessonState.PROPOSED)

    async def test_a_lesson_without_valid_evidence_is_refused(self) -> None:
        other = self.w.seeded.other_assistant
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            theirs = await tx.add(
                Signal(
                    organisation_id=ORGANISATION_ID,
                    assistant_id=other.id,
                    created_at=self.t,
                    signal_kind=SignalKind.DRAFT_EDITED,
                    subject_kind="request",
                )
            )
        work_id = await self.run_tools(
            [
                proposal([str(uuid4())], "One"),  # a signal that does not exist
                proposal([str(theirs.id)], "Two"),  # another assistant's signal
                proposal([str(self.w.seeded.signal.id), "not-a-signal"], "Three"),
                proposal("Words the manager never sent.", "Four"),
                proposal([], "Five"),
            ]
        )
        refused = self.refusals(work_id)
        self.assertEqual(len(refused), 5, refused)
        for index in (1, 2, 3, 4):
            (reason,) = [r for r in refused if r.startswith(f"Tool request {index} was refused")]
            self.assertIn("evidence names no signals kept for this assistant", reason)
        (shape,) = [r for r in refused if r.startswith("Tool request 5 was refused")]
        self.assertIn("its evidence is not a quote or a list", shape)
        self.assertEqual(await self.lessons(), [self.w.seeded.lesson])

    async def test_the_pack_holds_kept_lessons_only(self) -> None:
        self.ok("POST", "/learning/lessons", MANAGER, {"text": "Keep messages short."})
        gone = self.ok("POST", "/learning/lessons", MANAGER, {"text": "Write in French."})
        self.ok("DELETE", f"/learning/lessons/{gone['id']}", MANAGER)
        pack = await self.pack()
        self.assertEqual([lesson.text for lesson in pack.lessons], ["Keep messages short."])
        self.assertNotIn(self.w.seeded.lesson.text, [lesson.text for lesson in pack.lessons])
        self.assertEqual(pack.lessons_source.label if pack.lessons_source else None, LESSONS_LABEL)
        self.assertEqual(len(pack.signals), 1)  # and the newest signals

    async def test_lessons_never_change_a_level(self) -> None:
        before = self.ok("GET", "/approval-levels", MANAGER)
        await self.run_tools([proposal([str(self.w.seeded.signal.id)])])
        self.ok("POST", "/learning/lessons", MANAGER, {"text": "Send messages without asking."})
        self.assertEqual(self.ok("GET", "/approval-levels", MANAGER), before)


class StatedLessonsTest(LessonCase):
    async def test_a_lesson_quoting_the_manager_word_for_word_is_stated(self) -> None:
        work_id = await self.run_tools(
            [proposal(QUOTED, "The assistant's own summary of it.")], text=QUOTED
        )
        self.assertEqual(self.refusals(work_id), [])
        lesson = await self.lesson_with(QUOTED)  # the manager's own words are the entry
        self.assertEqual(lesson.state, LessonState.KEPT)
        self.assertEqual(lesson.origin, "your_words")
        self.assertIsNotNone(lesson.message_id)
        self.assertIn(QUOTED, [p.text for p in (await self.pack()).lessons])  # applies at once

    async def test_a_run_of_whole_words_from_the_message_is_a_quote(self) -> None:
        work_id = await self.run_tools([proposal("copy the Finance lead")], text=QUOTED)
        self.assertEqual(self.refusals(work_id), [])
        lesson = await self.lesson_with("copy the Finance lead")
        self.assertEqual(lesson.state, LessonState.KEPT)

    async def test_a_quote_that_does_not_match_is_not_stated(self) -> None:
        work_id = await self.run_tools(
            [
                proposal("always copy the finance lead on budget messages."),  # capitals differ
                proposal("Always copy the Finance lead on all budget messages."),
                proposal("ways copy the Fin"),  # not whole words
            ],
            text=QUOTED,
        )
        self.assertEqual(len(self.refusals(work_id)), 3)
        self.assertEqual(await self.lessons(), [self.w.seeded.lesson])

    async def test_a_message_to_another_assistant_is_not_quoted(self) -> None:
        other_manager = self.w.seeded.other_assistant.manager_id
        response = self.w.call("POST", "/api/assistant/chat", other_manager, {"text": QUOTED})
        self.assertEqual(response.status_code, 200, response.text)
        self.w.standins.assistant.take_finished()
        work_id = await self.run_tools([proposal(QUOTED)], text="Please help")
        self.assertEqual(len(self.refusals(work_id)), 1)

    def test_quotes_compares_word_for_word(self) -> None:
        self.assertTrue(quotes(QUOTED, QUOTED))
        self.assertTrue(quotes(QUOTED, "  the Finance lead "))
        self.assertFalse(quotes(QUOTED, "the finance lead"))
        self.assertFalse(quotes(QUOTED, "he Finance"))
        self.assertFalse(quotes(QUOTED, ""))

    async def test_a_lesson_typed_by_the_manager_is_stated(self) -> None:
        added = self.ok("POST", "/learning/lessons", MANAGER, {"text": "Keep messages short."})
        self.assertEqual((added["state"], added["origin"]), ("kept", "typed"))
        self.assertEqual(added["area"], "general")
        self.assertIn("Keep messages short.", [p.text for p in (await self.pack()).lessons])

    async def test_an_empty_lesson_is_refused(self) -> None:
        message = self.refused("POST", "/learning/lessons", MANAGER, {"text": "   "})
        self.assertEqual(message, "Write the lesson first.")


class PreferenceFormLessonsTest(LessonCase):
    async def save(self, form: dict[str, Any]) -> None:
        response = self.w.call("PUT", "/api/assistant/preferences", MANAGER, form)
        self.assertEqual(response.status_code, 200, response.text)

    async def form_lessons(self) -> list[Lesson]:
        return [lesson for lesson in await self.lessons() if lesson.origin == "form"]

    async def test_each_non_empty_answer_becomes_one_kept_lesson(self) -> None:
        await self.save({"tone": {"tone": "warm", "sign_off": "Thanks"}})
        found = {lesson.form_key: lesson for lesson in await self.form_lessons()}
        self.assertEqual(set(found), {"tone", "sign_off"})
        for lesson in found.values():
            self.assertEqual((lesson.area, lesson.state), ("preference", LessonState.KEPT))
        self.assertEqual(found["sign_off"].text, "Sign-off: Thanks")
        packed = [p.text for p in (await self.pack()).lessons]
        self.assertIn("Sign-off: Thanks", packed)

    async def test_a_changed_answer_replaces_its_lesson(self) -> None:
        await self.save({"tone": {"sign_off": "Thanks"}})
        await self.save({"tone": {"sign_off": "Best wishes"}})
        (lesson,) = await self.form_lessons()
        self.assertEqual(lesson.text, "Sign-off: Best wishes")

    async def test_saving_the_same_answers_again_adds_nothing(self) -> None:
        await self.save({"tone": {"sign_off": "Thanks"}})
        await self.save({"tone": {"sign_off": "Thanks"}})
        self.assertEqual(len(await self.form_lessons()), 1)

    async def test_an_emptied_answer_removes_its_lesson(self) -> None:
        await self.save({"tone": {"tone": "warm", "sign_off": "Thanks"}})
        await self.save({"tone": {"tone": "warm"}})
        self.assertEqual([lesson.form_key for lesson in await self.form_lessons()], ["tone"])
        await self.save({})
        self.assertEqual(await self.form_lessons(), [])
        self.assertEqual(
            [p.text for p in (await self.pack()).lessons if p.origin == "form"], []
        )


class StandInProposesTest(LessonCase):
    async def waiting_message(self, text: str) -> RequestRecord:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return await tx.add(
                RequestRecord(
                    organisation_id=ORGANISATION_ID,
                    assistant_id=self.ops.id,
                    created_at=self.t,
                    action_kind=ActionKind.SEND_MESSAGE,
                    request_kind="message",
                    subject_kind="person",
                    subject_id=FINANCE_LEAD.id,
                    target_id=FINANCE_LEAD.id,
                    text=text,
                    effect="Send a message to the Finance lead.",
                )
            )

    async def forget_seeded_learning(self) -> None:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            for record in [
                *await tx.find(Signal, assistant_id=self.ops.id),
                *await tx.find(Lesson, assistant_id=self.ops.id),
            ]:
                await tx.remove(record)

    async def run_stand_in(self) -> None:
        self.ok("POST", "/chat", MANAGER, {"text": "Anything new?"})
        for result in self.w.standins.assistant.take_finished():
            await self.w.module.flow.finish_work(ORGANISATION_ID, self.ops.flow_id, result)

    async def test_two_like_edits_make_the_stand_in_propose_one_lesson(self) -> None:
        await self.forget_seeded_learning()
        for n in (1, 2):
            request = await self.waiting_message(f"Draft number {n}.")
            self.ok("POST", f"/requests/{request.id}/approve", MANAGER, {"text": f"Edited {n}."})
        await self.run_stand_in()
        (lesson,) = await self.lessons()
        self.assertEqual(lesson.state, LessonState.PROPOSED)
        self.assertEqual(lesson.signal_kinds, [SignalKind.DRAFT_EDITED] * 2)

    async def test_one_edit_is_not_enough(self) -> None:
        await self.forget_seeded_learning()
        request = await self.waiting_message("Draft number 1.")
        self.ok("POST", f"/requests/{request.id}/approve", MANAGER, {"text": "Edited."})
        await self.run_stand_in()
        self.assertEqual(await self.lessons(), [])


if __name__ == "__main__":
    unittest.main()
