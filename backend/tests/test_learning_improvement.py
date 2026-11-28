"""Part 2, Epic 3 - Count this month's improvement (task-y54jhe69eWA).

The module works out, for this month and last month: items closed by their due date,
statements confirmed before the report, drafts approved unchanged and corrections by owners,
for the manager only. A month is the calendar month in the manager's time zone, 'the report'
is part 1's regular report, and in the first month last month's figures show as none.
"""

from __future__ import annotations

import unittest
from datetime import datetime
from typing import Any

from assistant.models.boundary import ActionKind
from assistant.models.records import (
    ConfirmedStatement,
    Report,
    ReportLine,
    RequestRecord,
    StatementWording,
)
from assistant.service.learning import FIGURES
from standins.sample import FINANCE_LEAD, LEGAL_LEAD, ORGANISATION_ID
from tests.item_helpers import EpicFour
from tests.work_helpers import MANAGER, EpicThree
from tests.world import PERSONAS, World, build_world

NONE = dict.fromkeys(FIGURES)


def at(text: str) -> datetime:
    return datetime.fromisoformat(text)


class ImprovementCase(EpicThree, EpicFour, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        self.ops = self.w.seeded.assistant
        self.t = await self.w.standins.clock.now()
        await self.forget_seeded_request()

    async def asyncTearDown(self) -> None:
        self.w.close()

    def improvement(self, person: str = MANAGER) -> Any:
        return self.ok("GET", "/learning/improvement", person)

    @staticmethod
    def figures(month: dict[str, Any]) -> dict[str, Any]:
        return {name: month[name] for name in FIGURES}

    async def approve_draft(self, edited: str | None = None) -> None:
        now = await self.w.standins.clock.now()
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            request = await tx.add(
                RequestRecord(
                    organisation_id=ORGANISATION_ID,
                    assistant_id=self.ops.id,
                    created_at=now,
                    action_kind=ActionKind.SEND_MESSAGE,
                    request_kind="message",
                    subject_kind="person",
                    subject_id=FINANCE_LEAD.id,
                    target_id=FINANCE_LEAD.id,
                    text="Please send the figures.",
                    effect="Send a message to the Finance lead.",
                )
            )
        body = {"text": edited} if edited is not None else None
        self.ok("POST", f"/requests/{request.id}/approve", MANAGER, body)

    async def regular_report_of(
        self, *statements: ConfirmedStatement, made_by: str = "schedule"
    ) -> None:
        now = await self.w.standins.clock.now()
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            await tx.add(
                Report(
                    organisation_id=ORGANISATION_ID,
                    assistant_id=self.ops.id,
                    created_at=now,
                    made_by=made_by,  # type: ignore[arg-type]
                    lines=[
                        ReportLine(
                            project_id=s.project_id,
                            project_name="Vendor consolidation",
                            statement_id=s.id,
                            mark=s.mark,
                        )
                        for s in statements
                    ],
                )
            )

    async def confirmed(self, project_id: str = "vendor-consolidation") -> ConfirmedStatement:
        now = await self.w.standins.clock.now()
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return await tx.add(
                ConfirmedStatement(
                    organisation_id=ORGANISATION_ID,
                    assistant_id=self.ops.id,
                    created_at=now,
                    project_id=project_id,
                    owner_id=LEGAL_LEAD.id,
                    colour="green",
                    wording=StatementWording(reason="On plan."),
                    source="Legal lead",
                    author_id="the-assistant",
                    mark="confirmed",
                )
            )


class FiguresTest(ImprovementCase):
    async def test_in_the_first_month_last_months_figures_show_as_none(self) -> None:
        page = self.improvement()
        self.assertEqual(page["this_month"]["month"], "2026-11")
        self.assertEqual(page["last_month"]["month"], "2026-10")
        self.assertEqual(self.figures(page["last_month"]), NONE)
        for figure in page["figures"]:
            self.assertIsNone(figure["last_month"])
            self.assertIsInstance(figure["this_month"], int)

    async def test_this_months_figures_stand_beside_last_months(self) -> None:
        # November: one item done on time, one draft approved unchanged, one statement
        # confirmed before the regular report; the seeded statement is an owner's correction.
        item = self.give()
        self.accept(item)
        self.update(item, "done")
        await self.approve_draft()
        statement = await self.confirmed()
        await self.later(hours=1)
        await self.regular_report_of(statement)
        # December: two drafts approved unchanged, and a late item.
        late = self.give()
        self.accept(late)
        await self.at(at("2026-12-02T10:00:00+00:00"))
        self.update(late, "done")
        await self.approve_draft()
        await self.approve_draft()

        page = self.improvement()
        self.assertEqual(page["this_month"]["month"], "2026-12")
        self.assertEqual(
            self.figures(page["this_month"]),
            {
                "items_closed_by_due_date": 0,
                "statements_confirmed_before_report": 0,
                "drafts_approved_unchanged": 2,
                "corrections_by_owners": 0,
            },
        )
        self.assertEqual(
            self.figures(page["last_month"]),
            {
                "items_closed_by_due_date": 1,
                "statements_confirmed_before_report": 1,
                "drafts_approved_unchanged": 1,
                "corrections_by_owners": 1,
            },
        )
        by_name = {f["figure"]: f for f in page["figures"]}
        self.assertEqual(by_name["drafts_approved_unchanged"], {
            "figure": "drafts_approved_unchanged", "this_month": 2, "last_month": 1,
        })  # fmt: skip

    async def test_an_edited_draft_is_not_approved_unchanged(self) -> None:
        await self.approve_draft(edited="Please send the figures today.")
        self.assertEqual(self.improvement()["this_month"]["drafts_approved_unchanged"], 0)

    async def test_only_the_regular_report_counts_and_only_what_came_before_it(self) -> None:
        early = await self.confirmed()
        await self.later(hours=1)
        await self.regular_report_of(early, made_by="manager")  # "Report now" is not the report
        await self.later(hours=1)
        await self.regular_report_of(early)
        await self.later(hours=1)
        await self.regular_report_of(early)  # already counted for the report before
        self.assertEqual(self.improvement()["this_month"]["statements_confirmed_before_report"], 1)

    async def test_corrections_by_owners_count_from_part_ones_records(self) -> None:
        self.assertEqual(self.improvement()["this_month"]["corrections_by_owners"], 1)  # seeded
        body = {"colour": "green", "reason": "Both contracts are signed."}
        self.ok("POST", f"/statements/{self.w.seeded.statement.id}/correct", LEGAL_LEAD.id, body)
        self.assertEqual(self.improvement()["this_month"]["corrections_by_owners"], 2)


class CalendarMonthTest(ImprovementCase):
    async def test_figures_are_counted_by_the_month_in_the_managers_time_zone(self) -> None:
        form = {"working_time": {"time_zone": "Pacific/Auckland"}}
        response = self.w.call("PUT", "/api/assistant/preferences", MANAGER, form)
        self.assertEqual(response.status_code, 200, response.text)
        # 12:00 UTC on 30 November is 01:00 on 1 December in Auckland.
        await self.at(at("2026-11-30T12:00:00+00:00"))
        await self.approve_draft()
        page = self.improvement()
        self.assertEqual(page["time_zone"], "Pacific/Auckland")
        self.assertEqual(page["this_month"]["month"], "2026-12")
        self.assertEqual(page["this_month"]["drafts_approved_unchanged"], 1)
        self.assertEqual(page["last_month"]["drafts_approved_unchanged"], 0)  # not the first

    async def test_in_utc_the_same_moment_is_still_november(self) -> None:
        await self.at(at("2026-11-30T12:00:00+00:00"))
        await self.approve_draft()
        page = self.improvement()
        self.assertEqual(page["this_month"]["month"], "2026-11")
        self.assertEqual(page["this_month"]["drafts_approved_unchanged"], 1)

    async def test_the_month_after_the_first_shows_zero_not_none(self) -> None:
        await self.at(at("2027-01-05T10:00:00+00:00"))
        page = self.improvement()
        self.assertEqual(page["last_month"]["month"], "2026-12")
        self.assertEqual(set(self.figures(page["last_month"]).values()), {0})


class WhoSeesTheFiguresTest(ImprovementCase):
    async def test_the_figures_are_served_to_the_manager_only(self) -> None:
        for persona in ("owner", "administrator"):
            response = self.call("GET", "/learning/improvement", PERSONAS[persona])
            self.assertEqual(response.status_code, 404, persona)
        theirs = self.improvement(PERSONAS["other_manager"])
        self.assertEqual(theirs["this_month"]["corrections_by_owners"], 0)  # their own only

    async def test_what_ive_learned_shows_the_figures(self) -> None:
        page = self.ok("GET", "/learning", MANAGER)
        self.assertIn("improvement", page)
        self.assertEqual(page["improvement"]["this_month"]["month"], "2026-11")
        self.assertEqual(len(page["suggestions"]), 1)
        self.assertEqual(len(page["lessons"]), 1)


if __name__ == "__main__":
    unittest.main()
