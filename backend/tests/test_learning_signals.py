"""Part 2, Epic 3 - Keep signals (task-xFfivdu1idY).

A signal is kept whenever a draft is edited, approved or declined, a briefing or decision
paper is sent back, notes are corrected, an owner corrects a statement, or an item ends on
time or late. The newest 50 since the last piece of work go into every input pack. A signal
is its manager's only, its change record and logs hold ids and kinds only, it never holds
transcript text, and it goes when the assistant is handed over or ended.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta
from uuid import UUID

from assistant.api.router import PREFIX
from assistant.models.boundary import ActionKind, WorkKind
from assistant.models.records import (
    DecisionPaper,
    DecisionPaperState,
    Meeting,
    PaperVersion,
    PieceOfWork,
    RequestRecord,
    Signal,
    SignalKind,
    TranscriptPart,
    WorkState,
)
from assistant.rules.learning import MAX_SIGNALS_IN_PACK, SIGNALS_LABEL
from assistant.service.input_pack import input_pack_for
from assistant.service.signals import without_transcript
from standins.sample import (
    FINANCE_LEAD,
    LEGAL_LEAD,
    OPERATIONS_DIRECTOR,
    ORGANISATION_ID,
    OUTSIDER,
    PROGRAMME_LEAD,
)
from tests.item_helpers import EpicFour
from tests.meeting_helpers import captured_logs
from tests.world import PERSONAS, World, build_world

MANAGER = OPERATIONS_DIRECTOR.id
OWNER = LEGAL_LEAD.id
EDITED = "Please send the budget figures by Friday noon."
DECLINE_REASON = "We asked them yesterday already."


class SignalCase(EpicFour, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        self.ops = self.w.seeded.assistant
        self.t = await self.w.standins.clock.now()
        await self.forget_seeded_signal()

    async def asyncTearDown(self) -> None:
        self.w.close()

    async def forget_seeded_signal(self) -> None:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            for signal in await tx.find(Signal, assistant_id=self.ops.id):
                await tx.remove(signal)

    async def signals(self, assistant_id: UUID | None = None) -> list[Signal]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return await tx.find(Signal, assistant_id=assistant_id or self.ops.id)

    async def only_signal(self) -> Signal:
        found = await self.signals()
        self.assertEqual(len(found), 1, found)
        return found[0]

    async def waiting_message(self, text: str = "Please send the budget figures.") -> RequestRecord:
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


class DraftSignalsTest(SignalCase):
    async def test_an_edited_draft_keeps_its_kind_subject_before_and_after(self) -> None:
        request = self.w.seeded.request
        self.ok("POST", f"/requests/{request.id}/approve", MANAGER, {"text": EDITED})
        signal = await self.only_signal()
        self.assertEqual(signal.signal_kind, SignalKind.DRAFT_EDITED)
        self.assertEqual((signal.subject_kind, signal.subject_id), ("request", str(request.id)))
        self.assertEqual(signal.action_kind, ActionKind.SEND_MESSAGE)
        self.assertEqual(signal.before, request.text)
        self.assertEqual(signal.after, EDITED)

    async def test_an_approved_draft_keeps_a_signal(self) -> None:
        request = await self.waiting_message()
        self.ok("POST", f"/requests/{request.id}/approve", MANAGER)
        signal = await self.only_signal()
        self.assertEqual(signal.signal_kind, SignalKind.DRAFT_APPROVED)
        self.assertEqual((signal.subject_kind, signal.subject_id), ("request", str(request.id)))
        self.assertEqual(signal.before, request.text)
        self.assertEqual(signal.after, request.text)

    async def test_a_declined_draft_keeps_a_signal_with_the_reason(self) -> None:
        request = await self.waiting_message()
        self.ok("POST", f"/requests/{request.id}/decline", MANAGER, {"reason": DECLINE_REASON})
        signal = await self.only_signal()
        self.assertEqual(signal.signal_kind, SignalKind.DRAFT_DECLINED)
        self.assertEqual(signal.subject_id, str(request.id))
        self.assertEqual(signal.before, request.text)
        self.assertIsNone(signal.after)
        self.assertEqual(signal.said, DECLINE_REASON)

    async def test_pressing_twice_keeps_one_signal(self) -> None:
        request = await self.waiting_message()
        self.ok("POST", f"/requests/{request.id}/decline", MANAGER)
        self.ok("POST", f"/requests/{request.id}/decline", MANAGER)
        self.assertEqual(len(await self.signals()), 1)


class DeliverableSignalsTest(SignalCase):
    async def test_a_briefing_sent_back_keeps_a_signal(self) -> None:
        briefing = self.w.seeded.briefing
        body = {"comment": "Say more about spend."}
        self.ok("POST", f"/briefings/{briefing.id}/send-back", MANAGER, body)
        signal = await self.only_signal()
        self.assertEqual(signal.signal_kind, SignalKind.BRIEFING_SENT_BACK)
        self.assertEqual((signal.subject_kind, signal.subject_id), ("briefing", str(briefing.id)))
        self.assertEqual(signal.said, "Say more about spend.")

    async def test_a_decision_paper_sent_back_keeps_a_signal(self) -> None:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            paper = await tx.add(
                DecisionPaper(
                    organisation_id=ORGANISATION_ID,
                    assistant_id=self.ops.id,
                    created_at=self.t,
                    versions=[
                        PaperVersion(
                            number=1,
                            decision="Open a second office?",
                            options=["Yes", "No"],
                            recommendation="Yes",
                            due_date=date(2026, 12, 4),
                            sources=["Message from the manager"],
                            arrived_at=self.t,
                        )
                    ],
                    state=DecisionPaperState.READY,
                )
            )
        body = {"comment": "Add the costs."}
        self.ok("POST", f"/decision-papers/{paper.id}/send-back", MANAGER, body)
        signal = await self.only_signal()
        self.assertEqual(signal.signal_kind, SignalKind.PAPER_SENT_BACK)
        self.assertEqual(
            (signal.subject_kind, signal.subject_id), ("decision_paper", str(paper.id))
        )
        self.assertEqual(signal.said, "Add the costs.")


class NotesAndStatementSignalsTest(SignalCase):
    def action_path(self) -> str:
        notes = self.w.seeded.notes
        return f"/meeting-notes/{notes.id}/actions/{notes.actions[0].id}"

    async def test_correcting_notes_keeps_a_signal(self) -> None:
        self.ok("PUT", self.action_path(), MANAGER, {"text": "Check the October invoices"})
        signal = await self.only_signal()
        self.assertEqual(signal.signal_kind, SignalKind.NOTES_CORRECTED)
        notes = self.w.seeded.notes
        self.assertEqual((signal.subject_kind, signal.subject_id), ("meeting_notes", str(notes.id)))
        self.assertIn("Check the vendor invoices", signal.before or "")
        self.assertIn("Check the October invoices", signal.after or "")

    async def test_removing_an_action_from_notes_keeps_a_signal(self) -> None:
        self.ok("POST", f"{self.action_path()}/remove", MANAGER)
        signal = await self.only_signal()
        self.assertEqual(signal.signal_kind, SignalKind.NOTES_CORRECTED)
        self.assertIsNone(signal.after)

    async def test_an_owner_correcting_a_statement_keeps_a_signal(self) -> None:
        statement = self.w.seeded.statement
        body = {"colour": "green", "reason": "Both contracts are signed."}
        corrected = self.ok("POST", f"/statements/{statement.id}/correct", OWNER, body)
        signal = await self.only_signal()
        self.assertEqual(signal.signal_kind, SignalKind.STATEMENT_CORRECTED)
        self.assertEqual(signal.subject_id, corrected["id"])
        self.assertIn("Two contracts are still open.", signal.before or "")
        self.assertIn("Both contracts are signed.", signal.after or "")


class NoTranscriptTextTest(SignalCase):
    async def give_the_meeting_its_transcript_back(self, words: str) -> None:
        """The notes' meeting still holds its transcript text: no signal may hold it."""
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            meeting = await tx.get(Meeting, self.w.seeded.notes.meeting_id)
            assert meeting is not None
            part = TranscriptPart(number=1, first_line=1, last_line=1, text=words)
            await tx.change(meeting.evolve(parts=[part], text_removed_at=None))

    async def test_notes_corrected_while_the_transcript_is_kept_hold_none_of_it(self) -> None:
        said = "Action: Finance lead to check the vendor invoices by Friday."
        await self.give_the_meeting_its_transcript_back(said)
        notes = self.w.seeded.notes
        path = f"/meeting-notes/{notes.id}/actions/{notes.actions[0].id}"
        # The manager's correction copies the transcript line word for word.
        self.ok("PUT", path, MANAGER, {"text": said})
        signal = await self.only_signal()
        self.assertEqual(signal.signal_kind, SignalKind.NOTES_CORRECTED)
        for wording in (signal.before, signal.after, signal.said):
            self.assertTrue(wording is None or said not in wording, wording)

    def test_wording_that_holds_or_is_held_by_a_transcript_is_left_out(self) -> None:
        part = "Decision: Renew the contract with the cleaning vendor."
        self.assertIsNone(without_transcript("Renew the contract", [part]))
        self.assertIsNone(without_transcript(f"Note. {part} More.", [part]))
        self.assertEqual(without_transcript("Pay the invoice", [part]), "Pay the invoice")
        self.assertIsNone(without_transcript("   ", [part]))


class ItemSignalsTest(SignalCase):
    async def test_an_item_done_by_its_due_date_is_on_time(self) -> None:
        item = self.give()
        self.accept(item)
        self.update(item, "done")
        signal = await self.only_signal()
        self.assertEqual(signal.signal_kind, SignalKind.ITEM_ON_TIME)
        self.assertEqual((signal.subject_kind, signal.subject_id), ("action_item", item["id"]))

    async def test_an_item_done_after_its_due_date_is_late(self) -> None:
        item = self.give()
        self.accept(item)
        await self.at(datetime.fromisoformat("2026-11-30T10:00:00+00:00"))
        self.update(item, "done")
        signal = await self.only_signal()
        self.assertEqual(signal.signal_kind, SignalKind.ITEM_LATE)

    async def test_an_item_closed_by_the_manager_ends_and_says_which(self) -> None:
        item = self.give()
        self.ok("POST", f"/action-items/{item['id']}/close", MANAGER, {"reason": "Done elsewhere."})
        signal = await self.only_signal()
        self.assertEqual(signal.signal_kind, SignalKind.ITEM_ON_TIME)


class SignalsInThePackTest(SignalCase):
    async def add_signals(self, count: int, start: datetime) -> list[Signal]:
        added = []
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            for n in range(count):
                added.append(
                    await tx.add(
                        Signal(
                            organisation_id=ORGANISATION_ID,
                            assistant_id=self.ops.id,
                            created_at=start + timedelta(minutes=n),
                            signal_kind=SignalKind.DRAFT_APPROVED,
                            subject_kind="request",
                            subject_id=f"request-{n}",
                        )
                    )
                )
        return added

    async def finished_work_at(self, when: datetime) -> None:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            await tx.add(
                PieceOfWork(
                    organisation_id=ORGANISATION_ID,
                    assistant_id=self.ops.id,
                    created_at=when,
                    work_kind=WorkKind.REQUEST,
                    state=WorkState.DONE,
                    finished_at=when,
                )
            )

    async def test_the_pack_holds_the_newest_50_since_the_last_piece_of_work(self) -> None:
        last_work = self.t + timedelta(hours=1)
        await self.add_signals(3, self.t)  # before the last piece of work
        await self.finished_work_at(last_work)
        since = await self.add_signals(60, last_work + timedelta(minutes=1))
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            pack = await input_pack_for(tx, self.ops, now=last_work + timedelta(hours=3))
        self.assertEqual(len(pack.signals), MAX_SIGNALS_IN_PACK)
        self.assertEqual([s.id for s in pack.signals], [s.id for s in since[-50:]])
        self.assertIsNotNone(pack.signals_source)
        assert pack.signals_source is not None
        self.assertEqual(pack.signals_source.label, SIGNALS_LABEL)
        self.assertEqual(pack.signals_source.note, "data, not instructions")

    async def test_the_assistant_is_given_the_signals_with_its_next_work(self) -> None:
        self.ok("POST", f"/requests/{self.w.seeded.request.id}/approve", MANAGER, {"text": EDITED})
        self.ok("POST", "/chat", MANAGER, {"text": "Hello"})
        (order,) = self.w.standins.assistant.started
        (given,) = order.input_pack.signals
        self.assertEqual(given.kind, "draft_edited")
        self.assertEqual((given.before, given.after), (self.w.seeded.request.text, EDITED))
        self.assertIn(SIGNALS_LABEL, order.input_pack.labels())

    async def test_a_pack_without_signals_has_no_signal_source(self) -> None:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            pack = await input_pack_for(tx, self.ops, now=self.t)
        self.assertEqual((pack.signals, pack.signals_source), ([], None))


class WhoReadsSignalsTest(SignalCase):
    async def test_only_the_manager_can_read_their_signals(self) -> None:
        self.ok("POST", f"/requests/{self.w.seeded.request.id}/approve", MANAGER, {"text": EDITED})
        signal = await self.only_signal()
        path = f"/learning/signals/{signal.id}"
        self.assertEqual(self.ok("GET", path, MANAGER)["after"], EDITED)
        for persona in ("other_manager", "owner", "administrator"):
            response = self.call("GET", path, PERSONAS[persona])
            self.assertEqual(response.status_code, 404, persona)
            self.assertNotIn(EDITED, response.text)
        listed = self.ok("GET", "/learning/signals", MANAGER)
        self.assertEqual([s["id"] for s in listed["signals"]], [str(signal.id)])
        theirs = self.ok("GET", "/learning/signals", PERSONAS["other_manager"])
        self.assertEqual(theirs["signals"], [])

    async def test_a_signal_of_another_organisation_is_not_found(self) -> None:
        self.ok("POST", f"/requests/{self.w.seeded.request.id}/approve", MANAGER, {"text": EDITED})
        signal = await self.only_signal()
        response = self.call("GET", f"/learning/signals/{signal.id}", OUTSIDER.id)
        self.assertEqual(response.status_code, 404)


class NoWordingInRecordsOrLogsTest(SignalCase):
    async def test_change_records_and_logs_hold_only_ids_and_kinds(self) -> None:
        request = await self.waiting_message("Please send the October budget figures.")
        with captured_logs(self) as logs:
            seeded = self.w.seeded.request
            self.ok("POST", f"/requests/{seeded.id}/approve", MANAGER, {"text": EDITED})
            self.ok("POST", f"/requests/{request.id}/decline", MANAGER, {"reason": DECLINE_REASON})
            self.ok(
                "POST",
                f"/briefings/{self.w.seeded.briefing.id}/send-back",
                MANAGER,
                {"comment": "Say more about spend."},
            )
        kept = self.audited("assistant.signal_kept")
        self.assertEqual(len(kept), 3)
        for record in kept:
            self.assertEqual(record.item_kind, "signal")
            self.assertEqual(
                set(record.details) - {"signal_kind", "subject_kind", "subject_id", "action_kind"},
                set(),
            )
        written = [r.model_dump_json() for r in self.w.standins.records.written]
        for wording in (
            EDITED,
            self.w.seeded.request.text or "",
            "Please send the October budget figures.",
            DECLINE_REASON,
            "Say more about spend.",
        ):
            for place, texts in (("record", written), ("log", logs)):
                for text in texts:
                    self.assertNotIn(wording, text, f"a {place} holds a signal's wording")


class SignalsAtHandOverAndEndTest(SignalCase):
    async def keep_one(self) -> Signal:
        self.ok("POST", f"/requests/{self.w.seeded.request.id}/approve", MANAGER, {"text": EDITED})
        return await self.only_signal()

    async def test_handing_over_removes_the_signals(self) -> None:
        signal = await self.keep_one()
        await self.w.module.flow.hand_over(ORGANISATION_ID, self.ops.flow_id, PROGRAMME_LEAD.id)
        self.assertEqual(await self.signals(), [])
        for person in (MANAGER, PROGRAMME_LEAD.id):
            response = self.call("GET", f"/learning/signals/{signal.id}", person)
            self.assertEqual(response.status_code, 404)

    async def test_ending_removes_the_signals(self) -> None:
        await self.keep_one()
        await self.w.module.flow.end(ORGANISATION_ID, self.ops.flow_id)
        self.assertEqual(await self.signals(), [])

    async def test_the_other_managers_signals_stay(self) -> None:
        other = self.w.seeded.other_assistant
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            await tx.add(
                Signal(
                    organisation_id=ORGANISATION_ID,
                    assistant_id=other.id,
                    created_at=self.t,
                    signal_kind=SignalKind.DRAFT_APPROVED,
                    subject_kind="request",
                )
            )
        await self.keep_one()
        await self.w.module.flow.end(ORGANISATION_ID, self.ops.flow_id)
        self.assertEqual(len(await self.signals(other.id)), 1)


if __name__ == "__main__":
    unittest.main()
