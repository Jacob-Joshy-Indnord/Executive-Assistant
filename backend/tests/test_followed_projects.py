"""Story 5.1: follow projects and ask their owners (task-sDyxdbALQzs)."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from assistant.models.records import (
    ConfirmedStatement,
    DraftState,
    FollowedProject,
    PersonRequestState,
    Report,
    RequestState,
    StatementDraft,
    StatementWording,
)
from assistant.rules import status as rules
from standins.sample import (
    BACKEND_ENGINEER,
    FINANCE_LEAD,
    LEGAL_LEAD,
    ORGANISATION_ID,
    PROGRAMME_LEAD,
    SALES_DIRECTOR,
)
from tests.item_helpers import MANAGER
from tests.status_helpers import (
    ASK_TIME,
    CRM,
    DRAFT_TIME,
    HIRING,
    MONDAY_10,
    VENDOR,
    VENDOR_EXPORT,
    EpicFive,
)
from tests.world import World, build_world


class FollowedProjectsTest(EpicFive, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        await self.at(MONDAY_10)

    async def asyncTearDown(self) -> None:
        self.w.close()

    async def test_following_sets_confirmer(self) -> None:
        """Story 5.1 - when the manager follows a project they can see, its owner becomes
        the confirmer; the manager can choose someone else or stop following at any time."""
        self.follow(VENDOR)
        self.assertEqual(self.project(VENDOR)["confirmer"]["id"], LEGAL_LEAD.id)
        self.follow(HIRING, confirmer=PROGRAMME_LEAD.id)
        self.assertEqual(self.project(HIRING)["confirmer"]["id"], PROGRAMME_LEAD.id)
        changed = self.ok("PUT", f"/projects/{VENDOR}/confirmer", MANAGER,
                          {"person_id": FINANCE_LEAD.id})["projects"]  # fmt: skip
        self.assertEqual(
            next(p for p in changed if p["id"] == VENDOR)["confirmer"]["display_name"],
            "Finance lead",
        )
        self.ok("POST", f"/projects/{VENDOR}/unfollow", MANAGER)
        self.assertFalse(self.project(VENDOR)["followed"])
        self.assertIsNone(await self.followed(VENDOR))
        response = self.call("POST", f"/projects/{VENDOR}/follow", SALES_DIRECTOR.id)
        self.assertEqual(response.status_code, 404)  # one they cannot see
        self.assertEqual(self.refused(
            "POST",
            f"/projects/{VENDOR}/follow",
            MANAGER,
            {"confirmer_id": BACKEND_ENGINEER.id}),  # fmt: skip
            rules.NOT_A_CONFIRMER,
        )

    async def test_drafting_gets_news(self) -> None:
        """Story 5.1 - when the time to draft comes, the assistant receives the news of each
        followed project since the last report (updates on its items, its assignments, its
        spend) and can draft a statement; a project with no news gets no draft."""
        item = self.give(text="Check the vendor list", project_id=VENDOR)
        self.accept(item)
        self.update(item, "on_track", note="Said before the project was followed.")
        await self.later(minutes=5)
        self.follow(VENDOR)
        self.follow(HIRING)
        await self.later(hours=1)
        self.update(item, "blocked", note="Two vendors have not replied.")
        self.w.standins.work_data.set_assignment_state(VENDOR_EXPORT, "in_progress")
        await self.run_due()  # the change is seen
        self.w.standins.work_data.set_spend(VENDOR, "25000.00")
        self.w.standins.assistant.started.clear()
        await self.at(DRAFT_TIME)
        await self.run_due()
        (order,) = self.w.standins.assistant.started
        self.assertEqual(order.kind.value, "status_drafts")
        news = [i for i in order.input_pack.inputs if i.kind == "project_news"]
        self.assertEqual([i.subject_id for i in news], [VENDOR])  # no news for the others
        content = news[0].content
        self.assertIn('"Check the vendor list": Blocked. Note from its owner: Two vendors', content)
        self.assertNotIn("Said before the project was followed.", content)
        self.assertIn('Assignment "Fix the vendor list export": blocked, now in progress.', content)
        self.assertIn("Spend: 23000.00 at the start, 25000.00 now, of a budget of 80000.00.",
                      content)  # fmt: skip
        self.assertEqual(news[0].note, "data, not instructions")
        await self.finish_started_work()
        (draft,) = await self.drafts(VENDOR)
        self.assertEqual((draft.state, draft.owner_id), (DraftState.DRAFT, LEGAL_LEAD.id))
        self.assertEqual(await self.drafts(HIRING), [])

    async def test_confirmers_asked_once_with_their_starting_point(self) -> None:
        """Story 5.1 - when the time to ask comes, each confirmer is asked once through the
        approval check, except those who confirmed one since the last report; they get the
        assistant's draft, otherwise their last confirmed statement, otherwise an empty
        form."""
        self.follow(VENDOR)  # the Legal lead also confirms the seeded CRM rollout
        self.follow(HIRING)  # the Finance lead
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            await tx.add(
                ConfirmedStatement(
                    organisation_id=ORGANISATION_ID,
                    assistant_id=self.w.seeded.assistant.id,
                    created_at=MONDAY_10,
                    project_id=HIRING,
                    owner_id=FINANCE_LEAD.id,
                    colour="green",
                    wording=StatementWording(reason="Hiring is on plan."),
                    source="Finance lead",
                    author_id=FINANCE_LEAD.id,
                    mark="confirmed",
                )
            )
            await tx.add(
                StatementDraft(
                    organisation_id=ORGANISATION_ID,
                    assistant_id=self.w.seeded.assistant.id,
                    created_at=MONDAY_10,
                    project_id=VENDOR,
                    owner_id=LEGAL_LEAD.id,
                    wording=StatementWording(reason="Two vendors have not replied."),
                    source="News since the last report: Vendor consolidation",
                    author_id=self.w.seeded.assistant.flow_id,
                )
            )
        await self.at(ASK_TIME)
        await self.run_due()
        await self.run_due()
        (round_,) = await self.status_rounds()  # the Finance lead confirmed one already
        self.assertEqual((round_.target_id, round_.state), (LEGAL_LEAD.id, RequestState.WAITING))
        self.assertEqual(round_.action_kind.value, "ask_owner")
        self.assertIsNone(round_.text)  # no wording reaches the manager (BR-4)
        self.assertEqual({p["project_id"] for p in round_.payload["projects"]}, {VENDOR, CRM})
        waiting = self.ok("GET", "/requests", MANAGER)["requests"]
        shown = next(r for r in waiting if r["kind"] == "status_round")
        self.assertIn("for the status of", shown["effect"])
        self.ok("POST", f"/requests/{round_.id}/approve", MANAGER)
        listed = self.ok("GET", "/status-requests", LEGAL_LEAD.id)["requests"]
        starts = {r["project"]["id"]: (r["start"], r["colour"]) for r in listed}
        self.assertEqual(starts[VENDOR], ("draft", None))  # no colour chosen beforehand
        self.assertEqual(starts[CRM], ("empty", None))
        vendor = next(r for r in listed if r["project"]["id"] == VENDOR)
        self.assertEqual(vendor["wording"]["reason"], "Two vendors have not replied.")
        self.answer(next(r["id"] for r in listed if r["project"]["id"] == CRM), LEGAL_LEAD.id,
                    colour="amber", reason="Import is late.")  # fmt: skip
        self.ok("POST", "/reports", MANAGER)  # "since the last report" starts again
        await self.at(datetime(2026, 12, 3, 9, 0, tzinfo=UTC))  # asking for 7 December
        await self.run_due()
        later = [r for r in await self.status_rounds() if r.state is RequestState.WAITING]
        self.assertEqual(len(later), 2)
        legal = next(r for r in later if r.target_id == LEGAL_LEAD.id)
        self.ok("POST", f"/requests/{legal.id}/approve", MANAGER)
        listed = self.ok("GET", "/status-requests", LEGAL_LEAD.id)["requests"]
        crm = next(r for r in listed if r["project"]["id"] == CRM)
        self.assertEqual((crm["start"], crm["colour"]), ("last_confirmed", "amber"))
        self.assertEqual(crm["wording"]["reason"], "Import is late.")
        self.assertEqual(crm["for"]["display_name"], "Operations director")
        self.assertEqual(crm["needed_by"], "2026-12-07T08:00:00+00:00")

    async def test_invisible_project_dropped_and_manager_told_once(self) -> None:
        """Story 5.1 - when the manager can no longer see a project, it leaves the list and
        the next report, and the manager is told once."""
        self.follow(VENDOR)
        self.w.standins.work_data.hide_project_from(VENDOR, MANAGER)
        await self.run_due()
        await self.run_due()
        self.assertIsNone(await self.followed(VENDOR))
        self.assertIsNone(self.project(VENDOR))
        dropped = [t for t in self.notices_to(MANAGER) if "no longer visible" in t]
        self.assertEqual(
            dropped,
            ["Vendor consolidation is no longer visible to you, so it has left your "
             "followed projects."],  # fmt: skip
        )
        report = self.ok("POST", "/reports", MANAGER)
        self.assertNotIn(VENDOR, [line["project_id"] for line in report["lines"]])

    async def test_departed_confirmer_raises_attention(self) -> None:
        """Story 5.1 - when the confirmer leaves the organisation, an attention entry is
        made."""
        self.follow(HIRING)  # confirmed by the Finance lead
        self.w.standins.people.leave(FINANCE_LEAD.id)
        await self.run_due()
        await self.run_due()
        (entry,) = await self.project_entries(HIRING)
        self.assertEqual(entry.reason_code, rules.CONFIRMER_LEFT)
        self.assertEqual(entry.choices, [rules.CHANGE_CONFIRMER, rules.STOP_FOLLOWING])
        shown = self.ok("GET", f"/attention/{entry.id}", MANAGER)
        self.assertEqual(shown["project"], {"id": HIRING, "name": "Hiring plan"})
        self.assertEqual(shown["label"], "Confirmer left")
        choice = {"choice": "Change who confirms", "person_id": PROGRAMME_LEAD.id}
        decided = self.ok("POST", f"/attention/{entry.id}/decide", MANAGER, choice)
        self.assertEqual((decided["state"], decided["decision_target_id"]),
                         ("decided", PROGRAMME_LEAD.id))  # fmt: skip
        self.assertEqual(self.project(HIRING)["confirmer"]["id"], PROGRAMME_LEAD.id)

    async def test_hand_over_or_end_removes_working_status_data(self) -> None:
        """Story 5.1 - when the assistant is handed over or ended, its followed projects,
        unconfirmed statements and open asks are removed; confirmed statements and reports
        stay."""
        self.follow(VENDOR)
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            await tx.add(
                StatementDraft(
                    organisation_id=ORGANISATION_ID,
                    assistant_id=self.w.seeded.assistant.id,
                    created_at=MONDAY_10,
                    project_id=VENDOR,
                    owner_id=LEGAL_LEAD.id,
                    wording=StatementWording(reason="Drafted."),
                    source="News",
                    author_id=self.w.seeded.assistant.flow_id,
                )
            )
        self.ok("POST", "/reports", MANAGER)
        await self.w.module.flow.end(ORGANISATION_ID, self.w.seeded.assistant.flow_id)
        mine = self.w.seeded.assistant.id
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            self.assertEqual(await tx.find(FollowedProject, assistant_id=mine), [])
            self.assertEqual(await tx.find(StatementDraft, assistant_id=mine), [])
            self.assertEqual(len(await tx.find(Report, assistant_id=mine)), 2)
            self.assertEqual(len(await tx.find(ConfirmedStatement, assistant_id=mine)), 1)
        asks = await self.status_asks(LEGAL_LEAD.id)
        self.assertTrue(asks)
        self.assertTrue(all(a.state is PersonRequestState.WITHDRAWN for a in asks))


if __name__ == "__main__":
    unittest.main()
