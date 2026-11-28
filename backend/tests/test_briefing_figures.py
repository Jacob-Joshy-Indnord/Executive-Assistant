"""Part 2, Story 2.1 The briefing: work out briefing figures (task-YlbECQzytHY).

The module works out the figures when the briefing is due on the manager's schedule
or asked for, counted from part 1's records and Flow's data as part 1 defines them,
and starts one briefing piece of work with them in the input pack. The schedule and
the sections are set in Settings next to it.
"""

from __future__ import annotations

import json
import unittest
from datetime import UTC, date, datetime, time, timedelta

from assistant.models.boundary import WorkKind
from assistant.models.preferences import Weekday
from assistant.models.records import (
    ActionItem,
    ConfirmedStatement,
    DecisionPaperState,
    ItemState,
    StatementWording,
)
from assistant.rules import briefing as rules
from assistant.rules.settings import DEFAULTS, Settings
from standins.sample import LEGAL_LEAD, ORGANISATION_ID
from tests.briefing_helpers import MANAGER, Briefings, section
from tests.world import CRM, build_world

MONDAY = datetime(2026, 11, 23, 8, 0, tzinfo=UTC)
"""When the seeded world starts: the manager took the assistant on at this moment."""
NEXT_MONDAY = datetime(2026, 11, 30, 8, 0, tzinfo=UTC)
FIGURE_SECTIONS = ["action_items", "assignments", "spend", "decisions", "project_status"]


def settings_in(time_zone: str, **changes: object) -> Settings:
    return Settings(**{**DEFAULTS.__dict__, "time_zone": time_zone, **changes})


def item(due: date, state: ItemState) -> ActionItem:
    return ActionItem(
        organisation_id=ORGANISATION_ID,
        created_at=MONDAY,
        text="Check it",
        owner_id=LEGAL_LEAD.id,
        due_date=due,
        origin="manager",
        state=state,
    )


class BriefingFiguresTest(Briefings, unittest.IsolatedAsyncioTestCase):
    """Story 2.1 The briefing (task-YlbECQzytHY)."""

    async def asyncSetUp(self) -> None:
        self.w = await build_world()

    async def asyncTearDown(self) -> None:
        self.w.close()

    async def test_the_module_works_out_the_briefings_figures(self) -> None:
        """Story 2.1 - when the briefing is due or asked for, the module works out: action
        items open, due this week and late; assignments in progress and waiting for the
        manager; spend against budget for each project; decisions waiting; confirmed
        project status."""
        # The seeded item waits for its owner and is due Friday 27 November.
        await self.add_item(date(2026, 11, 25), ItemState.OPEN)  # open, due this week
        await self.add_item(date(2026, 11, 20), ItemState.OPEN)  # open and late
        await self.add_item(date(2026, 11, 24), ItemState.DONE)  # done: counted nowhere
        await self.add_item(date(2026, 12, 3), ItemState.WAITING_FOR_OWNER)  # next week
        await self.add_paper(DecisionPaperState.READY)
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            await tx.add(
                ConfirmedStatement(
                    organisation_id=ORGANISATION_ID,
                    assistant_id=self.assistant_id,
                    created_at=MONDAY,
                    project_id=CRM,
                    owner_id=LEGAL_LEAD.id,
                    colour="green",
                    wording=StatementWording(reason="The import is on plan."),
                    source="Legal lead",
                    author_id=LEGAL_LEAD.id,
                    mark="confirmed",
                )
            )

        view = self.ok("POST", "/briefings")

        self.assertEqual([s["key"] for s in view["sections"]][:5], FIGURE_SECTIONS)
        items = section(view, "action_items")
        assert items is not None
        self.assertEqual(items["figures"], {"open": 2, "due_this_week": 2, "late": 1})

        assignments = await self.w.standins.work_data.assignments(MANAGER)
        work = section(view, "assignments")
        assert work is not None
        self.assertEqual(
            work["figures"],
            {
                "in_progress": sum(a.state == "in_progress" for a in assignments),
                "waiting_for_manager": sum(a.state == "waiting_for_review" for a in assignments),
            },
        )
        self.assertGreater(work["figures"]["in_progress"], 0)
        self.assertGreater(work["figures"]["waiting_for_manager"], 0)

        projects = await self.w.standins.work_data.projects(MANAGER)
        spend = section(view, "spend")
        assert spend is not None
        self.assertEqual(
            {(p["project_id"], p["spend"], p["budget"]) for p in spend["figures"]},
            {(p.id, p.spend, p.budget) for p in projects},
        )

        decisions = section(view, "decisions")
        assert decisions is not None
        self.assertEqual(decisions["figures"], {"decisions_waiting": 1})

        status = section(view, "project_status")
        assert status is not None
        [line] = status["figures"]
        self.assertEqual(
            (line["project_id"], line["colour"], line["mark"]), (CRM, "green", "confirmed")
        )
        self.assertEqual(line["wording"]["reason"], "The import is on plan.")

    async def test_the_figures_are_in_the_input_pack_of_one_briefing_piece_of_work(self) -> None:
        """Story 2.1 - the module starts a briefing piece of work with the figures in its
        input pack, each marked data, not instructions."""
        view = self.ok("POST", "/briefings")
        [order] = self.started(WorkKind.BRIEFING)
        figures = {i.subject_id: i for i in order.input_pack.inputs if i.kind == "briefing_figures"}
        self.assertEqual(list(figures), FIGURE_SECTIONS)
        for key, given in figures.items():
            shown = section(view, key)
            assert shown is not None
            self.assertEqual(json.loads(given.content), shown["figures"])
            self.assertEqual(given.note, "data, not instructions")
            self.assertTrue(given.label.startswith("Figures: "))
        [briefing] = await self.briefings()
        self.assertEqual(briefing.work_id, order.work_id)
        self.assertEqual(briefing.made_by, "manager")

    async def test_figures_are_counted_as_part_1_defines_them(self) -> None:
        """Story 2.1 - each figure is counted from part 1's records as part 1 defines them,
        with 'late' and 'this week' in the manager's time zone."""
        # Sunday 29 November, 20:00 in UTC is already Monday 30 November, 09:00 in Auckland.
        now = datetime(2026, 11, 29, 20, 0, tzinfo=UTC)
        due_tuesday = item(date(2026, 12, 1), ItemState.OPEN)
        utc = rules.action_item_figures([due_tuesday], now, settings_in("UTC"))
        auckland = rules.action_item_figures([due_tuesday], now, settings_in("Pacific/Auckland"))
        self.assertEqual(utc.due_this_week, 0)
        self.assertEqual(auckland.due_this_week, 1)

        # Late after the end of the working day of the due date, in the manager's time zone.
        late_at = datetime(2026, 11, 27, 10, 0, tzinfo=UTC)  # Friday 23:00 in Auckland
        due_friday = item(date(2026, 11, 27), ItemState.WAITING_FOR_OWNER)
        utc_late = rules.action_item_figures([due_friday], late_at, settings_in("UTC")).late
        self.assertEqual(utc_late, 0)
        self.assertEqual(
            rules.action_item_figures([due_friday], late_at, settings_in("Pacific/Auckland")).late,
            1,
        )
        # ... or that many working days later when the manager said so (part 1, section 8.1).
        patient = settings_in("Pacific/Auckland", late_after_working_days=1)
        self.assertEqual(rules.action_item_figures([due_friday], late_at, patient).late, 0)
        # Done, closed or declined items are neither due nor late.
        for state in (ItemState.DONE, ItemState.CLOSED, ItemState.DECLINED):
            ended = item(date(2026, 11, 27), state)
            figures = rules.action_item_figures([ended], late_at, settings_in("UTC"))
            self.assertEqual((figures.open, figures.due_this_week, figures.late), (0, 0, 0))

        # Through the module: the manager's saved time zone applies.
        await self.set_time_zone("Pacific/Auckland")
        await self.add_item(date(2026, 11, 30), ItemState.OPEN)
        await self.move_to(now)
        view = self.ok("POST", "/briefings")
        items = section(view, "action_items")
        assert items is not None
        # Monday 30 November is this week in Auckland; the seeded item (due 27th) is late there.
        self.assertEqual(items["figures"], {"open": 1, "due_this_week": 1, "late": 1})

    async def test_decisions_waiting_counts_papers_in_state_ready_only(self) -> None:
        """Story 2.1 - 'Decisions waiting' counts only papers in state ready; a paper sent back
        is not waiting for the manager."""
        await self.add_paper(DecisionPaperState.READY)
        await self.add_paper(DecisionPaperState.SENT_BACK)
        await self.add_paper(DecisionPaperState.DECIDED)
        await self.add_paper(DecisionPaperState.PREPARING)
        await self.add_paper(DecisionPaperState.NOT_PREPARED)
        view = self.ok("POST", "/briefings")
        decisions = section(view, "decisions")
        assert decisions is not None
        self.assertEqual(decisions["figures"], {"decisions_waiting": 1})

    async def test_a_due_briefing_is_prepared_once(self) -> None:
        """Story 2.1 - when the scheduled day and time arrive in the manager's time zone, one
        briefing piece of work is started, and a second pass over the same due time starts
        none."""
        await self.move_to(NEXT_MONDAY - timedelta(minutes=1))
        await self.w.module.flow.run_due()
        self.assertEqual(await self.briefings(), [])

        await self.move_to(NEXT_MONDAY)
        await self.w.module.flow.run_due()
        await self.w.module.flow.run_due()
        [briefing] = await self.briefings()
        self.assertEqual((briefing.made_by, briefing.due_at), ("schedule", NEXT_MONDAY))
        self.assertEqual(len(self.started(WorkKind.BRIEFING)), 1)

        await self.move_to(NEXT_MONDAY + timedelta(hours=3))
        await self.w.module.flow.run_due()
        self.assertEqual(len(await self.briefings()), 1)

    async def test_the_due_time_is_in_the_managers_time_zone(self) -> None:
        """Story 2.1 - Mondays at 08:00 in New York is 13:00 in UTC."""
        await self.set_time_zone("America/New_York")
        page = self.ok("GET", "/briefing/settings")
        self.assertEqual(
            (page["frequency"], page["day"], page["time"]), ("weekly", "monday", "08:00")
        )
        self.assertEqual(page["next_briefing_at"], "2026-11-23T13:00:00+00:00")
        await self.move_to(datetime(2026, 11, 23, 12, 59, tzinfo=UTC))
        await self.w.module.flow.run_due()
        self.assertEqual(await self.briefings(), [])
        await self.move_to(datetime(2026, 11, 23, 13, 0, tzinfo=UTC))
        await self.w.module.flow.run_due()
        self.assertEqual(len(await self.briefings()), 1)

    async def test_the_schedule_follows_the_managers_setting(self) -> None:
        """Story 2.1 - when the manager sets the briefing to weekly, every two weeks, monthly
        or off, with a day and time, the next briefing is due accordingly, and none is due
        when it is off."""
        page = self.set_briefing(frequency="weekly", day="wednesday", time="10:30")
        self.assertEqual(page["next_briefing_at"], "2026-11-25T10:30:00+00:00")
        await self.move_to(datetime(2026, 11, 25, 10, 30, tzinfo=UTC))
        await self.w.module.flow.run_due()
        [briefing] = await self.briefings()
        self.assertEqual(briefing.due_at, datetime(2026, 11, 25, 10, 30, tzinfo=UTC))
        self.assertEqual(
            self.ok("GET", "/briefings")["next_briefing_at"], "2026-12-02T10:30:00+00:00"
        )

        page = self.set_briefing(frequency="off")
        self.assertIsNone(page["next_briefing_at"])
        self.assertIsNone(self.ok("GET", "/briefings")["next_briefing_at"])
        for days in (7, 14, 21, 28, 35):
            await self.move_to(datetime(2026, 11, 25, 10, 30, tzinfo=UTC) + timedelta(days=days))
            await self.w.module.flow.run_due()
        self.assertEqual(len(await self.briefings()), 1)

    async def test_a_changed_schedule_is_kept(self) -> None:
        """Story 2.1 - the schedule setting is kept, and a frequency outside the four choices
        is refused."""
        self.set_briefing(frequency="monthly", day="tuesday", time="09:15")
        page = self.ok("GET", "/briefing/settings")
        self.assertEqual(
            (page["frequency"], page["day"], page["time"]), ("monthly", "tuesday", "09:15")
        )
        response = self.call("PUT", "/briefing/settings", body={"frequency": "daily"})
        self.assertEqual(response.status_code, 422)

    async def test_every_two_weeks_counts_from_the_first_briefing_after_it_was_set(self) -> None:
        """Story 2.1 - set to every two weeks with a day and time, the first briefing falls on
        the next such day and time, and each later one two weeks after the one before."""
        await self.move_to(datetime(2026, 11, 25, 12, 0, tzinfo=UTC))  # a Wednesday
        page = self.set_briefing(frequency="every_two_weeks", day="monday", time="08:00")
        self.assertEqual(page["next_briefing_at"], "2026-11-30T08:00:00+00:00")

        for monday in (date(2026, 11, 30), date(2026, 12, 7), date(2026, 12, 14)):
            await self.move_to(datetime.combine(monday, time(8, 0), tzinfo=UTC))
            await self.w.module.flow.run_due()
        self.assertEqual(
            [b.due_at for b in await self.briefings()],
            [NEXT_MONDAY, datetime(2026, 12, 14, 8, 0, tzinfo=UTC)],
        )

        schedule = rules.Schedule(
            "every_two_weeks", Weekday.MONDAY, time(8, 0), "UTC",
            datetime(2026, 11, 25, 12, 0, tzinfo=UTC),
        )  # fmt: skip
        times = rules.briefing_times(datetime(2026, 12, 20, tzinfo=UTC), schedule)
        self.assertEqual(
            [t.date() for t in times[:4]],
            [date(2026, 11, 30), date(2026, 12, 14), date(2026, 12, 28), date(2027, 1, 11)],
        )

    async def test_a_monthly_briefing_falls_on_the_first_chosen_weekday(self) -> None:
        """Story 2.1 - set to monthly with a day and time, it is due on the first such weekday
        of each month, at that time."""
        page = self.set_briefing(frequency="monthly", day="tuesday", time="09:00")
        self.assertEqual(page["next_briefing_at"], "2026-12-01T09:00:00+00:00")
        schedule = rules.Schedule("monthly", Weekday.TUESDAY, time(9, 0), "UTC", MONDAY)
        times = rules.briefing_times(datetime(2027, 1, 15, tzinfo=UTC), schedule)
        self.assertEqual(
            [t for t in times if t > MONDAY][:3],
            [
                datetime(2026, 12, 1, 9, 0, tzinfo=UTC),
                datetime(2027, 1, 5, 9, 0, tzinfo=UTC),
                datetime(2027, 2, 2, 9, 0, tzinfo=UTC),
            ],
        )
        await self.move_to(datetime(2026, 12, 8, 9, 0, tzinfo=UTC))  # the second Tuesday
        await self.w.module.flow.run_due()
        [briefing] = await self.briefings()
        self.assertEqual(briefing.due_at, datetime(2026, 12, 1, 9, 0, tzinfo=UTC))

    async def test_the_briefing_holds_the_sections_ticked_in_settings(self) -> None:
        """Story 2.1 - the manager ticks or unticks sections in Settings next to the briefing's
        schedule, where all are on by default; the briefing holds the ticked sections and none
        that are unticked."""
        page = self.ok("GET", "/briefing/settings")
        self.assertTrue(all(s["ticked"] for s in page["sections"]))
        self.assertEqual(len(page["sections"]), 6)

        page = self.set_briefing(sections={"spend": False, "decisions": False})
        ticked = [s["key"] for s in page["sections"] if s["ticked"]]
        self.assertEqual(ticked, ["action_items", "assignments", "project_status", "meetings"])
        view = self.ok("POST", "/briefings")
        self.assertEqual(
            [s["key"] for s in view["sections"]],
            ["action_items", "assignments", "project_status", "meetings"],
        )
        [briefing] = await self.briefings()
        self.assertIsNone(briefing.figures.spend)
        self.assertIsNone(briefing.figures.decisions_waiting)
        [order] = self.started(WorkKind.BRIEFING)
        given = {i.subject_id for i in order.input_pack.inputs if i.kind == "briefing_figures"}
        self.assertEqual(given, {"action_items", "assignments", "project_status"})

        self.set_briefing(sections={"spend": True})
        view = self.ok("POST", "/briefings")
        self.assertIsNotNone(section(view, "spend"))
        self.assertIsNone(section(view, "decisions"))

        message = self.refused("PUT", "/briefing/settings", body={"sections": {"weather": True}})
        self.assertEqual(message, "Choose sections from those the briefing has.")

    async def test_meetings_since_the_last_briefing_shows_only_when_notes_were_kept(
        self,
    ) -> None:
        """Story 2.1 - a briefing's sections are the five groups of figures, plus Meetings
        since the last briefing only when notes were kept since the last briefing."""
        first = self.ok("POST", "/briefings")  # the seeded notes were kept before it
        self.assertEqual(
            [s["key"] for s in first["sections"]], [*FIGURE_SECTIONS, "meetings"]
        )
        await self.move_to(MONDAY + timedelta(hours=1))
        second = self.ok("POST", "/briefings")
        self.assertEqual([s["key"] for s in second["sections"]], FIGURE_SECTIONS)

        await self.move_to(MONDAY + timedelta(hours=2))
        await self.keep_notes()
        await self.move_to(MONDAY + timedelta(hours=3))
        third = self.ok("POST", "/briefings")
        self.assertEqual(
            [s["key"] for s in third["sections"]], [*FIGURE_SECTIONS, "meetings"]
        )

        await self.keep_notes(title="Budget call")
        self.set_briefing(sections={"meetings": False})
        await self.move_to(MONDAY + timedelta(hours=4))
        fourth = self.ok("POST", "/briefings")
        self.assertEqual([s["key"] for s in fourth["sections"]], FIGURE_SECTIONS)

    async def test_only_the_manager_reaches_the_briefing(self) -> None:
        """Story 2.1 - the briefing and its settings are the manager's only."""
        view = self.ok("POST", "/briefings")
        for person in (LEGAL_LEAD.id,):
            response = self.call("GET", f"/briefings/{view['id']}", person)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(self.call("GET", "/briefing/settings", person).status_code, 404)
            self.assertEqual(self.call("POST", "/briefings", person).status_code, 404)


if __name__ == "__main__":
    unittest.main()
