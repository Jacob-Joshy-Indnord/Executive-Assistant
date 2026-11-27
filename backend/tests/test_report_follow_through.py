"""Story 5.3: corrections and missing confirmations (task-82lYqc6BkLU)."""

from __future__ import annotations

import unittest
from typing import Any

from assistant.models.records import (
    AttentionState,
    RequestState,
    StatementDraft,
    StatementWording,
)
from assistant.rules import status as rules
from standins.sample import FINANCE_LEAD, LEGAL_LEAD, ORGANISATION_ID, PROGRAMME_LEAD
from tests.item_helpers import MANAGER
from tests.status_helpers import ASK_TIME, HIRING, MONDAY_10, VENDOR, EpicFive
from tests.world import World, build_world

DRAFTED_REASON = "Words the owner did not stand behind."


class ReportFollowThroughTest(EpicFive, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        await self.at(MONDAY_10)
        self.follow(VENDOR)
        self.follow(HIRING)  # confirmed by the Finance lead

    async def asyncTearDown(self) -> None:
        self.w.close()

    async def test_correction_details_shown(self) -> None:
        """Story 5.3 - when the manager opens a correction, they see who made it, when, and
        which points changed, and not the wording the owner did not confirm."""
        await self.set_level("ask_owner", "without_approval")
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            await tx.add(
                StatementDraft(
                    organisation_id=ORGANISATION_ID,
                    assistant_id=self.w.seeded.assistant.id,
                    created_at=MONDAY_10,
                    project_id=VENDOR,
                    owner_id=LEGAL_LEAD.id,
                    wording=StatementWording(
                        reason=DRAFTED_REASON, what_changed="Nothing.", what_comes_next="Wait."
                    ),
                    source="News since the last report: Vendor consolidation",
                    author_id=self.w.seeded.assistant.flow_id,
                )
            )
        await self.at(ASK_TIME)
        await self.run_due()
        request = next(
            r for r in self.ok("GET", "/status-requests", LEGAL_LEAD.id)["requests"]
            if r["project"]["id"] == VENDOR
        )
        self.answer(request["id"], LEGAL_LEAD.id, colour="amber", reason="Legal review is late.",
                    what_changed="Nothing.", what_comes_next="Chase legal.")  # fmt: skip
        report = self.ok("POST", "/reports", MANAGER)
        line = next(x for x in report["lines"] if x["project_id"] == VENDOR)
        self.assertEqual(line["mark_words"], "Corrected by its owner")
        opened = self.call("GET", f"/statements/{line['statement_id']}", MANAGER)
        correction = opened.json()["correction"]
        self.assertEqual(correction["by"], {"id": LEGAL_LEAD.id, "display_name": "Legal lead"})
        self.assertEqual(correction["at"], ASK_TIME.isoformat())
        self.assertEqual(correction["points"], ["reason", "what_comes_next"])
        self.assertNotIn(DRAFTED_REASON, opened.text)
        self.assertNotIn("Wait.", opened.text)
        self.assertNotIn("unconfirmed_wording", opened.json())

    async def missed_entry(self) -> Any:
        found = [e for e in await self.project_entries(HIRING) if e.state is AttentionState.OPEN]
        return found[0] if found else None

    async def test_two_missed_reports_raise_attention(self) -> None:
        """Story 5.3 - when two reports in a row have no confirmation for a project, an
        attention entry offers Ask again, Change who confirms, and Stop following."""
        self.ok("POST", "/reports", MANAGER)
        self.assertIsNone(await self.missed_entry())  # one report: not yet
        await self.later(days=1)
        self.ok("POST", "/reports", MANAGER)
        entry = await self.missed_entry()
        self.assertEqual(entry.reason_code, rules.MISSED_REPORTS)
        self.assertEqual(entry.choices, ["Ask again", "Change who confirms", "Stop following"])
        shown = self.ok("GET", f"/attention/{entry.id}", MANAGER)
        self.assertEqual((shown["project"]["name"], shown["confirmer"]["display_name"]),
                         ("Hiring plan", "Finance lead"))  # fmt: skip
        self.ok("POST", "/reports", MANAGER)  # a third: the same entry, not another
        self.assertEqual(len(await self.project_entries(HIRING)), 1)

    async def test_a_confirmation_between_reports_starts_the_count_again(self) -> None:
        await self.set_level("ask_owner", "without_approval")
        self.ok("POST", "/reports", MANAGER)
        await self.at(ASK_TIME)
        await self.run_due()
        (request,) = self.ok("GET", "/status-requests", FINANCE_LEAD.id)["requests"]
        self.answer(request["id"], FINANCE_LEAD.id, colour="green", reason="On plan.")
        self.ok("POST", "/reports", MANAGER)
        self.ok("POST", "/reports", MANAGER)
        self.assertIsNone(await self.missed_entry())  # one miss since the confirmation

    async def test_ask_again_change_who_confirms_and_stop_following(self) -> None:
        for _ in range(2):
            self.ok("POST", "/reports", MANAGER)
            await self.later(hours=1)
        entry = await self.missed_entry()
        asked = self.ok("POST", f"/attention/{entry.id}/decide", MANAGER, {"choice": "Ask again"})
        self.assertEqual((asked["state"], asked["decision"]), ("decided", "Ask again"))
        (round_,) = await self.status_rounds()
        self.assertEqual((round_.target_id, round_.state), (FINANCE_LEAD.id, RequestState.WAITING))

        self.ok("POST", "/reports", MANAGER)
        entry = await self.missed_entry()
        self.assertEqual(
            self.refused("POST", f"/attention/{entry.id}/decide", MANAGER,
                         {"choice": "Change who confirms"}),  # fmt: skip
            "Choose who confirms the project's status.",
        )
        self.ok("POST", f"/attention/{entry.id}/decide", MANAGER,
                {"choice": "Change who confirms", "person_id": PROGRAMME_LEAD.id})  # fmt: skip
        self.assertEqual(self.project(HIRING)["confirmer"]["id"], PROGRAMME_LEAD.id)
        (round_,) = await self.status_rounds()
        self.assertEqual(round_.state, RequestState.WITHDRAWN)  # the Finance lead confirms no more
        self.assertNotIn(
            "status_round", [r["kind"] for r in self.ok("GET", "/requests", MANAGER)["requests"]]
        )

        self.ok("POST", "/reports", MANAGER)
        self.ok("POST", "/reports", MANAGER)
        entry = await self.missed_entry()
        stopped = self.ok("POST", f"/attention/{entry.id}/decide", MANAGER,
                          {"choice": "Stop following"})  # fmt: skip
        self.assertEqual(stopped["decision"], "Stop following")
        self.assertFalse(self.project(HIRING)["followed"])


if __name__ == "__main__":
    unittest.main()
