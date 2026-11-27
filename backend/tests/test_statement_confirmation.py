"""Story 5.2: confirm or correct a statement (task-KIfX59TwOBI)."""

from __future__ import annotations

import unittest
from datetime import timedelta
from typing import Any

from assistant.models.records import (
    DraftState,
    PersonRequestState,
    StatementDraft,
    StatementWording,
)
from assistant.rules import status as rules
from assistant.rules.items import needs_confirmation
from standins.sample import LEGAL_LEAD, ORGANISATION_ID
from tests.item_helpers import MANAGER
from tests.status_helpers import ASK_TIME, CRM, MONDAY_10, VENDOR, EpicFive
from tests.world import World, build_world

OWNER = LEGAL_LEAD.id
DRAFTED = StatementWording(
    reason="Two vendors have not replied.",
    what_changed="The export was fixed.",
    what_comes_next="Chase the two vendors.",
)


class StatementConfirmationTest(EpicFive, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        await self.at(MONDAY_10)
        self.follow(VENDOR)
        await self.set_level("ask_owner", "without_approval")

    async def asyncTearDown(self) -> None:
        self.w.close()

    async def add_draft(self, wording: StatementWording = DRAFTED) -> StatementDraft:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return await tx.add(
                StatementDraft(
                    organisation_id=ORGANISATION_ID,
                    assistant_id=self.w.seeded.assistant.id,
                    created_at=await self.w.standins.clock.now(),
                    project_id=VENDOR,
                    owner_id=OWNER,
                    wording=wording,
                    source="News since the last report: Vendor consolidation",
                    author_id=self.w.seeded.assistant.flow_id,
                )
            )

    async def asked(self) -> dict[str, Any]:
        """The Legal lead's open requests, by project, after the time to ask."""
        await self.at(ASK_TIME)
        await self.run_due()
        listed = self.ok("GET", "/status-requests", OWNER)["requests"]
        return {r["project"]["id"]: r for r in listed}

    async def test_owner_sees_full_request(self) -> None:
        """Story 5.2 - when the owner opens a request, they see who it is for, the project,
        the wording, its source label and when it is needed."""
        await self.add_draft()
        request = (await self.asked())[VENDOR]
        shown = self.ok("GET", f"/status-requests/{request['id']}", OWNER)
        self.assertEqual(shown["for"]["display_name"], "Operations director")
        self.assertEqual(shown["project"], {"id": VENDOR, "name": "Vendor consolidation"})
        self.assertEqual(shown["wording"], DRAFTED.model_dump())
        self.assertEqual(shown["source"], "News since the last report: Vendor consolidation")
        self.assertEqual(shown["needed_by"], "2026-11-30T08:00:00+00:00")
        self.assertEqual([c["colour"] for c in shown["colours"]], ["green", "amber", "red"])
        response = self.call("GET", f"/status-requests/{request['id']}", MANAGER)
        self.assertEqual(response.status_code, 404)  # the draft never reaches the manager

    async def test_colour_and_reason_required(self) -> None:
        """Story 5.2 - when the owner confirms, they have chosen a colour and given a reason;
        for an assistant draft no colour was preselected."""
        await self.add_draft()
        request = (await self.asked())[VENDOR]
        self.assertIsNone(request["colour"])
        path = f"/status-requests/{request['id']}/answer"
        cases = {
            rules.CHOOSE_COLOUR: {"reason": "Fine."},
            rules.REASON_NEEDED: {"colour": "green", "reason": " "},
            rules.REASON_TOO_LONG: {"colour": "green", "reason": "x" * 501},
            rules.FIELD_TOO_LONG: {"colour": "green", "reason": "Ok.", "what_changed": "x" * 301},
        }
        for sentence, body in cases.items():
            with self.subTest(sentence):
                self.assertEqual(self.refused("POST", path, OWNER, body), sentence)
        confirmed = self.answer(request["id"], OWNER, colour="red", reason="x" * 500,
                                what_changed="y" * 300)  # fmt: skip
        self.assertEqual(confirmed["colour_words"], "Red")

    async def test_confirmed_without_changes(self) -> None:
        """Story 5.2 - confirmed without changes: the statement is confirmed."""
        draft = await self.add_draft()
        request = (await self.asked())[VENDOR]
        await self.later(hours=2)
        confirmed = self.answer(request["id"], OWNER, colour="amber", **DRAFTED.model_dump())
        self.assertEqual((confirmed["mark"], confirmed["mark_words"]),
                         ("confirmed", "Confirmed"))  # fmt: skip
        self.assertEqual(confirmed["author"]["id"], self.w.seeded.assistant.flow_id)
        self.assertEqual(confirmed["owner_id"], OWNER)
        self.assertIsNone(confirmed["correction"])
        (stored,) = await self.statements(VENDOR)
        self.assertEqual((stored.draft_id, stored.unconfirmed_wording), (draft.id, None))
        (answered,) = await self.drafts(VENDOR)
        self.assertEqual(answered.state, DraftState.ANSWERED)
        asks = [a for a in await self.status_asks(OWNER) if a.subject_id == str(draft.id)]
        self.assertEqual([a.state for a in asks], [PersonRequestState.ANSWERED])

    async def test_corrected_keeps_both_wordings_author_and_times(self) -> None:
        """Story 5.2 - with changes, it is corrected, and both wordings, the author and the
        times are kept; the wording the owner did not confirm is never shown (BR-5)."""
        draft = await self.add_draft()
        request = (await self.asked())[VENDOR]
        await self.later(hours=3)
        corrected = self.answer(
            request["id"], OWNER, colour="amber", reason="One vendor has not replied.",
            what_changed=DRAFTED.what_changed, what_comes_next=DRAFTED.what_comes_next,
        )  # fmt: skip
        self.assertEqual(corrected["mark"], "corrected")
        self.assertEqual(corrected["author"]["id"], OWNER)
        self.assertEqual(corrected["correction"]["points"], ["reason"])
        self.assertEqual(corrected["correction"]["at"], "2026-11-26T12:00:00+00:00")
        self.assertEqual(corrected["correction"]["drafted_at"], draft.created_at.isoformat())
        (stored,) = await self.statements(VENDOR)
        self.assertEqual(stored.unconfirmed_wording, DRAFTED)  # kept in the record
        for person in (MANAGER, OWNER):
            response = self.call("GET", f"/statements/{stored.id}", person)
            self.assertNotIn("Two vendors have not replied.", response.text)

    async def test_versions_immutable_and_corrections_added(self) -> None:
        """Story 5.2 - no one can change or remove a confirmed statement; its owner can add a
        correction at any time and the earlier version is kept."""
        request = (await self.asked())[VENDOR]
        first = self.answer(request["id"], OWNER, colour="green", reason="On plan.")
        (stored,) = await self.statements(VENDOR)
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            with self.assertRaises(TypeError):
                await tx.change(stored)  # type: ignore[type-var]
            with self.assertRaises(TypeError):
                await tx.remove(stored)  # type: ignore[arg-type]
        await self.later(days=2)
        path = f"/statements/{first['id']}/correct"
        later = self.ok("POST", path, OWNER, {"colour": "amber", "reason": "One vendor is late."})
        self.assertEqual((later["mark"], later["previous_version_id"]), ("corrected", first["id"]))
        self.assertEqual(later["correction"]["points"], ["colour", "reason"])
        self.assertEqual(later["correction"]["of"], "earlier_version")
        self.assertEqual(len(await self.statements(VENDOR)), 2)
        self.assertEqual(self.ok("GET", f"/statements/{first['id']}", MANAGER)["colour"], "green")
        self.assertEqual(
            self.refused("POST", path, OWNER, {"colour": "red", "reason": "Late."}),
            rules.ONLY_LATEST,
        )
        response = self.call("POST", f"/statements/{later['id']}/correct", MANAGER,
                             {"colour": "red", "reason": "Late."})  # fmt: skip
        self.assertEqual(response.status_code, 404)  # only its owner

    async def test_older_drafts_replaced(self) -> None:
        """Story 5.2 - when a newer draft is sent while an older one is unanswered, the older
        one is marked replaced."""
        older = await self.add_draft()
        await self.asked()
        newer = await self.add_draft(StatementWording(reason="All vendors replied."))
        self.ok("POST", "/reports", MANAGER)  # a new window: the Legal lead is asked again
        await self.at(ASK_TIME + timedelta(days=7))
        await self.run_due()
        drafts = {d.id: d.state for d in await self.drafts(VENDOR)}
        self.assertEqual(drafts, {older.id: DraftState.REPLACED, newer.id: DraftState.WITH_OWNER})
        asks = {a.subject_id: a.state for a in await self.status_asks(OWNER)}
        self.assertEqual(asks[str(older.id)], PersonRequestState.WITHDRAWN)
        self.assertEqual(asks[str(newer.id)], PersonRequestState.SENT)

    async def test_owner_written_counts_as_confirmed(self) -> None:
        """Story 5.2 - when the owner writes the status themselves, it counts as confirmed."""
        request = (await self.asked())[CRM]
        self.assertEqual(request["start"], "empty")
        written = self.answer(request["id"], OWNER, colour="amber", reason="Import is late.")
        self.assertEqual((written["mark"], written["author"]["id"]), ("confirmed", OWNER))
        self.assertEqual(written["source"], "Legal lead")
        self.assertFalse(needs_confirmation(written["author"]["id"], written["owner_id"]))
        self.assertEqual(self.refused(
            "POST",
            f"/status-requests/{request['id']}/answer",
            OWNER,
            {"colour": "green", "reason": "Again."}),  # fmt: skip
            rules.ALREADY_ANSWERED,
        )


if __name__ == "__main__":
    unittest.main()
