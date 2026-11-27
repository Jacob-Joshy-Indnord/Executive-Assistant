"""Story 5.3: the status report (task-Bqj19ZGi0VI)."""

from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, time
from typing import Any

from assistant.models.preferences import PreferencesForm, Weekday
from assistant.models.records import Report, ReportLine, StatementDraft, StatementWording
from assistant.rules.reports import ask_time, draft_time, line_text, report_times
from assistant.rules.settings import DEFAULTS, Settings
from standins.sample import FINANCE_LEAD, LEGAL_LEAD, ORGANISATION_ID
from tests.item_helpers import MANAGER
from tests.status_helpers import (
    ASK_TIME,
    CRM,
    HIRING,
    MONDAY_10,
    REPORT_TIME,
    VENDOR,
    EpicFive,
)
from tests.world import World, build_world, viewer

UNCONFIRMED = "The assistant's newer words nobody confirmed."


def settings(**changes: Any) -> Settings:
    return Settings(**{**DEFAULTS.__dict__, **changes})


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)  # type: ignore[misc]


class ReportScheduleTest(unittest.TestCase):
    ANCHOR = date(2026, 11, 23)

    def reports_in(self, s: Settings, start: datetime, end: datetime) -> list[datetime]:
        return [t for t in report_times(start, s, self.ANCHOR) if start <= t < end]

    def test_weekly_by_default_with_ask_and_draft_times(self) -> None:
        found = self.reports_in(DEFAULTS, utc(2026, 11, 24), utc(2026, 12, 15))
        self.assertEqual(found, [utc(2026, 11, 30, 8), utc(2026, 12, 7, 8), utc(2026, 12, 14, 8)])
        self.assertEqual(ask_time(utc(2026, 11, 30, 8), DEFAULTS), utc(2026, 11, 26, 9))
        self.assertEqual(draft_time(utc(2026, 11, 30, 8), DEFAULTS), utc(2026, 11, 25, 9))

    def test_monthly_report_day(self) -> None:
        """Story 5.3 - a monthly report is on the first chosen weekday of the month."""
        monthly = settings(report_frequency="monthly")
        self.assertEqual(
            self.reports_in(monthly, utc(2026, 11, 24), utc(2027, 2, 28)),
            [utc(2026, 12, 7, 8), utc(2027, 1, 4, 8), utc(2027, 2, 1, 8)],
        )
        thursdays = settings(report_frequency="monthly", report_day=Weekday.THURSDAY)
        self.assertEqual(
            self.reports_in(thursdays, utc(2026, 11, 24), utc(2027, 1, 31)),
            [utc(2026, 12, 3, 8), utc(2027, 1, 7, 8)],
        )

    def test_every_two_weeks_and_off(self) -> None:
        fortnightly = settings(report_frequency="every_two_weeks")
        self.assertEqual(
            self.reports_in(fortnightly, utc(2026, 11, 24), utc(2026, 12, 31)),
            [utc(2026, 12, 7, 8), utc(2026, 12, 21, 8)],
        )
        self.assertEqual(self.reports_in(settings(report_frequency="off"), utc(2026, 11, 1),
                                         utc(2027, 1, 1)), [])  # fmt: skip

    def test_report_time_stays_local_across_clock_changes(self) -> None:
        london = settings(time_zone="Europe/London", report_time=time(8, 0))
        found = self.reports_in(london, utc(2026, 10, 15), utc(2026, 11, 3))
        self.assertEqual(
            found, [utc(2026, 10, 19, 7), utc(2026, 10, 26, 8), utc(2026, 11, 2, 8)]
        )


class ReportsTest(EpicFive, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        await self.at(MONDAY_10)
        self.follow(VENDOR)
        self.follow(HIRING)
        await self.set_level("ask_owner", "without_approval")

    async def asyncTearDown(self) -> None:
        self.w.close()

    async def stored_reports(self) -> list[Report]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            found = await tx.find(Report, assistant_id=self.w.seeded.assistant.id)
        return sorted(found, key=lambda r: r.created_at)

    async def confirm_round(self) -> None:
        """Ask on Thursday: the Legal lead confirms the vendor project, then corrects it, and
        gives the CRM rollout's status; the Finance lead does not answer."""
        await self.at(ASK_TIME)
        await self.run_due()
        legal = {r["project"]["id"]: r for r in
                 self.ok("GET", "/status-requests", LEGAL_LEAD.id)["requests"]}  # fmt: skip
        self.answer(legal[VENDOR]["id"], LEGAL_LEAD.id, colour="amber",
                    reason="Legal review is late.", what_changed="One contract signed.",
                    what_comes_next="Sign the second contract.")  # fmt: skip
        statement = (await self.statements(VENDOR))[0]
        self.ok("POST", f"/statements/{statement.id}/correct", LEGAL_LEAD.id,
                {"colour": "amber", "reason": "Legal review is late.",
                 "what_changed": "Two contracts signed."})  # fmt: skip
        self.answer(legal[CRM]["id"], LEGAL_LEAD.id, colour="green", reason="On plan.")

    async def test_one_line_per_project_with_mark(self) -> None:
        """Story 5.3 - the report has one line per followed project with name, colour in
        words, reason, what changed, what comes next, source, date and one mark."""
        await self.confirm_round()
        await self.at(REPORT_TIME)
        await self.run_due()
        await self.run_due()
        (report,) = self.ok("GET", "/reports", MANAGER)["reports"][:1]
        self.assertEqual((report["made_by"], report["due_at"]),
                         ("schedule", "2026-11-30T08:00:00+00:00"))  # fmt: skip
        lines = {line["project_id"]: line for line in report["lines"]}
        self.assertEqual(set(lines), {CRM, VENDOR, HIRING})
        vendor = lines[VENDOR]
        self.assertEqual(
            {k: vendor[k] for k in ("project_name", "colour_words", "reason", "what_changed",
                                     "what_comes_next", "source", "date", "mark_words")},
            {"project_name": "Vendor consolidation", "colour_words": "Amber",
             "reason": "Legal review is late.", "what_changed": "Two contracts signed.",
             "what_comes_next": "", "source": "Legal lead", "date": "2026-11-26",
             "mark_words": "Corrected by its owner"},
        )  # fmt: skip
        self.assertEqual(
            vendor["text"],
            "Vendor consolidation. Amber. Legal review is late. Corrected by its owner.",
        )
        self.assertEqual(lines[CRM]["mark_words"], "Confirmed")
        self.assertEqual(lines[HIRING]["mark_words"], "No confirmed status yet")
        self.assertEqual(len(await self.stored_reports()), 2)  # the seeded one and this

    async def test_unconfirmed_wording_never_shown(self) -> None:
        """Story 5.3 - when a newer statement is not confirmed in time, the line shows the last
        confirmed status with Awaiting confirmation and the unconfirmed wording appears
        nowhere."""
        await self.confirm_round()
        self.ok("POST", "/reports", MANAGER)
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            await tx.add(
                StatementDraft(
                    organisation_id=ORGANISATION_ID,
                    assistant_id=self.w.seeded.assistant.id,
                    created_at=await self.w.standins.clock.now(),
                    project_id=CRM,
                    owner_id=LEGAL_LEAD.id,
                    wording=StatementWording(reason=UNCONFIRMED),
                    source="News since the last report: CRM rollout",
                    author_id=self.w.seeded.assistant.flow_id,
                )
            )
        await self.at(datetime(2026, 12, 3, 9, 0, tzinfo=UTC))  # asked for 7 December
        await self.run_due()
        await self.at(datetime(2026, 12, 7, 8, 0, tzinfo=UTC))
        await self.run_due()
        report = self.ok("GET", "/reports", MANAGER)["reports"][0]
        crm = next(line for line in report["lines"] if line["project_id"] == CRM)
        self.assertEqual((crm["mark"], crm["colour"], crm["reason"]),
                         ("awaiting_confirmation", "green", "On plan."))  # fmt: skip
        for path in ("/reports", "/status", f"/reports/{report['id']}", "/requests", "/done",
                     "/attention"):  # fmt: skip
            with self.subTest(path):
                self.assertNotIn(UNCONFIRMED, self.call("GET", path, MANAGER).text)

    async def test_colour_changes_named(self) -> None:
        """Story 5.3 - when colours differ from the previous report, "Changed since last
        report" names each one."""
        await self.confirm_round()
        first = self.ok("POST", "/reports", MANAGER)
        self.assertEqual(first["changed_since_last"], [])
        (latest,) = [s for s in await self.statements(VENDOR) if s.previous_version_id]
        self.ok("POST", f"/statements/{latest.id}/correct", LEGAL_LEAD.id,
                {"colour": "red", "reason": "Legal review stopped."})  # fmt: skip
        second = self.ok("POST", "/reports", MANAGER)
        self.assertEqual(
            second["changed_since_last"],
            [{"project_id": VENDOR, "project_name": "Vendor consolidation"}],
        )
        changed = [line["project_id"] for line in second["lines"] if line["changed_since_last"]]
        self.assertEqual(changed, [VENDOR])

    async def test_reports_immutable_later_confirmation_on_page_and_next_report(self) -> None:
        """Story 5.3 - a report never changes; a later confirmation shows on the page and in
        the next report."""
        made = self.ok("POST", "/reports", MANAGER)
        (stored,) = [r for r in await self.stored_reports() if str(r.id) == made["id"]]
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            with self.assertRaises(TypeError):
                await tx.change(stored)  # type: ignore[type-var]
            with self.assertRaises(TypeError):
                await tx.remove(stored)  # type: ignore[arg-type]
        await self.confirm_round()
        page = self.ok("GET", "/status", MANAGER)
        self.assertEqual(
            {line["project_id"]: line["mark"] for line in page["lines"]}[VENDOR], "corrected"
        )
        self.assertEqual(page["next_report_at"], "2026-11-30T08:00:00+00:00")
        self.assertEqual(self.ok("GET", f"/reports/{made['id']}", MANAGER), made)
        later = self.ok("POST", "/reports", MANAGER)
        self.assertEqual(
            {line["project_id"]: line["mark"] for line in later["lines"]}[VENDOR], "corrected"
        )

    async def test_report_now(self) -> None:
        """Story 5.3 - when the manager chooses "Report now", a report is made at once."""
        before = len(await self.stored_reports())
        made = self.ok("POST", "/reports", MANAGER)
        self.assertEqual(made["made_by"], "manager")
        self.assertEqual(made["created_at"], MONDAY_10.isoformat())
        self.assertEqual(len(await self.stored_reports()), before + 1)
        self.assertEqual(len(self.audited("assistant.report_made")), 1)

    async def test_monthly_schedule_runs_on_the_first_chosen_weekday(self) -> None:
        form = PreferencesForm.model_validate({"report": {"frequency": "monthly"}})
        await self.w.module.services.setup.save(viewer(MANAGER), form)
        await self.at(REPORT_TIME)
        await self.run_due()
        self.assertEqual(len(await self.stored_reports()), 1)  # not on 30 November
        self.assertEqual(await self.status_asks(FINANCE_LEAD.id), [])
        await self.at(datetime(2026, 12, 3, 9, 0, tzinfo=UTC))
        await self.run_due()
        await self.at(datetime(2026, 12, 7, 8, 0, tzinfo=UTC))
        await self.run_due()
        reports = await self.stored_reports()
        self.assertEqual(reports[-1].due_at, datetime(2026, 12, 7, 8, 0, tzinfo=UTC))
        finance_asks = await self.status_asks(FINANCE_LEAD.id)
        self.assertEqual({a.created_at for a in finance_asks},
                         {datetime(2026, 12, 3, 9, 0, tzinfo=UTC)})  # fmt: skip

    def test_line_in_words(self) -> None:
        line = ReportLine(project_id="p", project_name="Hiring plan", mark="no_confirmed_status")
        self.assertEqual(line_text(line), "Hiring plan. No confirmed status yet.")


if __name__ == "__main__":
    unittest.main()
