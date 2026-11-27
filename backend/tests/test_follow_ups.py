"""Story 4.2: follow up on time (task-SvmFXBrl37E)."""

from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, date, datetime, time
from typing import Any

from assistant.models.preferences import PreferencesForm
from assistant.models.records import PersonRequestState, RequestState
from assistant.rules import items as rules
from assistant.rules.follow_ups import follow_up_times, overdue_at, sendable_at
from assistant.rules.settings import DEFAULTS, Settings
from tests.item_helpers import MANAGER, OWNER, EpicFour
from tests.world import World, build_world, viewer

MONDAY_10 = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)
WEDNESDAY_9 = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)
FRIDAY_9 = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)


def settings(**changes: Any) -> Settings:
    return Settings(**{**DEFAULTS.__dict__, **changes})


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)  # type: ignore[misc]


class FollowUpRulesTest(unittest.TestCase):
    def test_default_schedule_half_way_and_due_date(self) -> None:
        """Story 4.2 - accepted with a due date more than two working days away: 09:00 on the
        half-way working day (rounded up) and 09:00 on the due date."""
        # Monday to Friday: Tue, Wed, Thu, Fri are four working days; half-way is the second.
        self.assertEqual(
            follow_up_times(MONDAY_10, date(2026, 11, 27), DEFAULTS),
            [utc(2026, 11, 25, 9, 0), utc(2026, 11, 27, 9, 0)],
        )
        # Thursday to the next Friday: six working days, rounded up to the third (Tuesday).
        self.assertEqual(
            follow_up_times(utc(2026, 11, 26, 15, 0), date(2026, 12, 4), DEFAULTS),
            [utc(2026, 12, 1, 9, 0), utc(2026, 12, 4, 9, 0)],
        )
        # Five working days: half-way rounds up to the third.
        self.assertEqual(
            follow_up_times(MONDAY_10, date(2026, 11, 30), DEFAULTS)[0], utc(2026, 11, 26, 9, 0)
        )

    def test_within_two_working_days_only_the_due_date(self) -> None:
        self.assertEqual(
            follow_up_times(MONDAY_10, date(2026, 11, 25), DEFAULTS), [utc(2026, 11, 25, 9, 0)]
        )
        # Friday to Tuesday is two working days, across the weekend.
        self.assertEqual(
            follow_up_times(utc(2026, 11, 27, 10, 0), date(2026, 12, 1), DEFAULTS),
            [utc(2026, 12, 1, 9, 0)],
        )
        # Due today after 09:00: that follow-up has already passed.
        self.assertEqual(follow_up_times(MONDAY_10, date(2026, 11, 23), DEFAULTS), [])

    def test_number_chosen_for_all_items(self) -> None:
        self.assertEqual(follow_up_times(MONDAY_10, date(2026, 11, 27), settings(follow_ups=0)), [])
        self.assertEqual(
            follow_up_times(MONDAY_10, date(2026, 12, 4), settings(follow_ups=3)),
            [utc(2026, 11, 26, 9, 0), utc(2026, 12, 1, 9, 0), utc(2026, 12, 4, 9, 0)],
        )

    def test_dst_safe_times(self) -> None:
        """Story 4.2 - when clocks change in the manager's time zone, 09:00 stays 09:00."""
        london = settings(time_zone="Europe/London")
        # Accepted Monday 19 October; due Wednesday 28 October. Half-way is Friday 23
        # (summer time, UTC+1); the due date is after the clocks go back on 25 October.
        times = follow_up_times(utc(2026, 10, 19, 10, 0), date(2026, 10, 28), london)
        self.assertEqual(times, [utc(2026, 10, 23, 8, 0), utc(2026, 10, 28, 9, 0)])
        self.assertEqual(overdue_at(date(2026, 3, 30), london), utc(2026, 3, 30, 17, 0))
        self.assertEqual(sendable_at(utc(2026, 10, 24, 12, 0), london), utc(2026, 10, 26, 9, 0))

    def test_working_hours(self) -> None:
        self.assertEqual(sendable_at(utc(2026, 11, 23, 7, 0), DEFAULTS), utc(2026, 11, 23, 9, 0))
        self.assertEqual(sendable_at(MONDAY_10, DEFAULTS), MONDAY_10)
        self.assertEqual(sendable_at(utc(2026, 11, 27, 18, 0), DEFAULTS), utc(2026, 11, 30, 9, 0))


class FollowUpStepTest(EpicFour, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        await self.at(MONDAY_10)
        self.item = self.give()
        self.accept(self.item)

    async def asyncTearDown(self) -> None:
        self.w.close()

    async def follow_ups(self, item: Any = None) -> list[Any]:
        return [
            r for r in await self.requests_about(item or self.item) if r.payload.get("follow_up")
        ]

    async def test_follow_up_asks_owner_through_the_approval_check(self) -> None:
        """Story 4.2 - when a follow-up time is reached, the owner is asked for an update
        through the approval check."""
        await self.at(WEDNESDAY_9)
        await self.run_due()
        (request,) = await self.follow_ups()
        self.assertEqual((request.request_kind, request.target_id), ("update_ask", OWNER))
        self.assertEqual(request.state, RequestState.WAITING)  # starts "with approval"
        self.assertIn("Review the vendor contracts", request.text or "")
        self.assertEqual(self.w.standins.approval.checked[-1][0].kind.value, "ask_owner")
        self.ok("POST", f"/requests/{request.id}/approve", MANAGER)
        asks = [a for a in await self.asks(self.item) if a.asks == "update"]
        self.assertEqual([a.state for a in asks], [PersonRequestState.SENT])

    async def test_without_approval_the_follow_up_is_sent_at_once(self) -> None:
        await self.set_level("ask_owner", "without_approval")
        await self.at(WEDNESDAY_9)
        await self.run_due()
        (request,) = await self.follow_ups()
        self.assertEqual(request.state, RequestState.DONE)
        self.assertEqual(
            [a.asks for a in await self.asks(self.item) if a.state is PersonRequestState.SENT],
            ["update"],
        )

    async def test_custom_times_apply(self) -> None:
        """Story 4.2 - when the manager sets other times, or none, for an item, those apply."""
        self.ok("PUT", f"/action-items/{self.item['id']}/follow-ups", MANAGER,
                {"times": ["2026-11-24T11:00:00+00:00"]})  # fmt: skip
        await self.at(WEDNESDAY_9)
        await self.run_due()
        (request,) = await self.follow_ups()
        self.assertEqual(request.created_at, WEDNESDAY_9)  # the 11:00 one, run late, once
        other = self.give(text="Sign the renewal")
        self.accept(other)
        self.ok("PUT", f"/action-items/{other['id']}/follow-ups", MANAGER, {"times": []})
        await self.at(FRIDAY_9)
        await self.run_due()
        self.assertEqual(await self.follow_ups(other), [])

    async def test_recent_update_suppresses_follow_up(self) -> None:
        """Story 4.2 - when the owner gave an update in the last working day, that
        follow-up is not sent."""
        await self.at(utc(2026, 11, 24, 15, 0))
        self.update(self.item, "on_track", note="Half the contracts are read.")
        await self.at(WEDNESDAY_9)
        await self.run_due()
        self.assertEqual(await self.follow_ups(), [])
        self.assertIn("follow_up_skipped", [e["event"] for e in self.view(self.item)["history"]])
        await self.at(FRIDAY_9)  # two days since the update: asked again
        await self.run_due()
        self.assertEqual(len(await self.follow_ups()), 1)

    async def test_not_allowed_is_shown(self) -> None:
        """Story 4.2 - when asking owners is not allowed, no follow-up is sent and the item
        says so."""
        await self.set_level("ask_owner", "not_allowed")
        await self.at(WEDNESDAY_9)
        await self.run_due()
        self.assertEqual(await self.follow_ups(), [])
        self.assertEqual(self.view(self.item)["follow_up_note"], rules.ASKING_NOT_ALLOWED)

    async def test_follow_ups_sent_once(self) -> None:
        """Story 4.2 - when the step runs twice, late or in two places, each follow-up is
        sent once."""
        await self.at(WEDNESDAY_9)
        await self.run_due()
        await self.run_due()
        await asyncio.gather(*(self.run_due() for _ in range(4)))
        self.assertEqual(len(await self.follow_ups()), 1)
        other = self.give(text="Sign the renewal", due="2026-12-04")
        accepted = self.accept(other)  # Tuesday 1 and Friday 4 December, 09:00
        self.assertEqual(len(accepted["follow_up_times"]), 2)
        await self.set_level("ask_owner", "without_approval")
        await self.at(utc(2026, 12, 4, 10, 0))  # both missed: one follow-up, not two
        await self.run_due()
        await self.run_due()
        self.assertEqual(len(await self.follow_ups(other)), 1)

    async def test_working_hours_and_daily_notices(self) -> None:
        """Story 4.2 - follow-ups go out in working hours and within the number of notices a
        day; the rest waits for the next working day."""
        form = PreferencesForm.model_validate({"follow_ups": {"notices_per_owner_per_day": 1}})
        await self.w.module.services.setup.save(viewer(MANAGER), form)
        late = self.give(text="Sign the renewal")  # its ask shares the 10:00 notice
        self.accept(late)
        times = {"times": ["2026-11-23T11:00:00+00:00", "2026-11-23T20:00:00+00:00"]}
        self.ok("PUT", f"/action-items/{late['id']}/follow-ups", MANAGER, times)
        await self.at(utc(2026, 11, 23, 11, 0))
        await self.run_due()
        self.assertEqual(await self.follow_ups(late), [])  # one notice today already
        tuesday_9 = utc(2026, 11, 24, 9, 0)
        self.assertEqual(self.view(late)["follow_up_waiting_until"], tuesday_9.isoformat())
        await self.at(utc(2026, 11, 23, 20, 0))
        await self.run_due()
        self.assertEqual(await self.follow_ups(late), [])  # outside working hours
        await self.at(tuesday_9)
        await self.run_due()
        (request,) = await self.follow_ups(late)
        self.assertEqual(request.created_at, tuesday_9)

    async def test_follow_ups_use_the_managers_time_zone(self) -> None:
        form = PreferencesForm.model_validate(
            {"working_time": {"time_zone": "America/New_York", "starts": time(9, 0)}}
        )
        await self.w.module.services.setup.save(viewer(MANAGER), form)
        other = self.give(text="Sign the renewal")
        accepted = self.accept(other)
        self.assertEqual(
            accepted["follow_up_times"],
            ["2026-11-25T14:00:00+00:00", "2026-11-27T14:00:00+00:00"],
        )


if __name__ == "__main__":
    unittest.main()
