"""Story 6.2: the waiting count for Flow's top bar, and notices to the manager when a
report or an attention entry is ready, those that arise together sharing one
(task--SyIaIqmydk)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from assistant.api.router import PREFIX
from assistant.models.records import AttentionEntry, Report
from assistant.service.manager_notices import notice_text
from standins.sample import ORGANISATION_ID
from standins.schedule import connect_schedule
from tests.world import PERSONAS, World, build_world

MANAGER = PERSONAS["manager"]
OWNER = PERSONAS["owner"]


class ManagerNoticeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        self.notices = self.w.module.services.manager_notices
        self.assistant = self.w.seeded.assistant
        self.w.standins.notices.sent.clear()

    async def asyncTearDown(self) -> None:
        self.w.close()

    def told(self) -> list[str]:
        return [n.text for n in self.w.standins.notices.sent if n.person_id == MANAGER]

    async def ready(self, entries: int = 0, reports: int = 0) -> None:
        now = await self.w.module.boundary.clock.now()
        mine = {
            "organisation_id": ORGANISATION_ID,
            "assistant_id": self.assistant.id,
            "created_at": now,
        }
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            for n in range(entries):
                await tx.add(
                    AttentionEntry(
                        **mine, subject_kind="assistant", subject_id=f"s{n}-{now}",
                        reason="It needs you.", choices=["Yes", "No"],
                    )
                )  # fmt: skip
            for _ in range(reports):
                await tx.add(Report(**mine, made_by="schedule", due_at=now))

    async def later(self, minutes: int = 1) -> None:
        await self.w.standins.clock.forward(timedelta(minutes=minutes))

    # The count ---------------------------------------------------------------------------

    def test_top_bar_count_is_what_waits_for_the_manager(self) -> None:
        count = self.w.call("GET", f"{PREFIX}/waiting-count", MANAGER).json()
        self.assertEqual(count, {"count": 2, "approvals": 1, "attention": 1})
        request_id = self.w.seeded.request.id
        self.w.call("POST", f"{PREFIX}/requests/{request_id}/decline", MANAGER, {})
        count = self.w.call("GET", f"{PREFIX}/waiting-count", MANAGER).json()
        self.assertEqual(count["count"], 1)
        self.assertEqual(self.w.call("GET", f"{PREFIX}/waiting-count", OWNER).status_code, 404)

    # Notices -----------------------------------------------------------------------------

    async def test_what_arises_together_shares_one_notice(self) -> None:
        await self.later()  # after the seeded entry, which is told about first
        await self.notices.send_ready(self.assistant)
        self.w.standins.notices.sent.clear()
        await self.later()
        await self.ready(entries=2, reports=1)
        self.assertTrue(await self.notices.send_ready(self.assistant))
        self.assertEqual(
            self.told(),
            [
                "Your Executive Assistant has a new status report"
                " and 2 things that need your attention."
            ],
        )
        notice = self.w.standins.notices.sent[0]
        self.assertEqual(
            (notice.subject_kind, notice.subject_id), ("assistant", str(self.assistant.id))
        )

    async def test_each_is_told_once(self) -> None:
        await self.later()
        self.assertTrue(await self.notices.send_ready(self.assistant))
        self.assertFalse(await self.notices.send_ready(self.assistant))
        self.assertEqual(len(self.told()), 1)
        await self.later()
        await self.ready(entries=1)
        self.assertTrue(await self.notices.send_ready(self.assistant))
        self.assertEqual(
            self.told()[-1], "Your Executive Assistant has one thing that needs your attention."
        )

    async def test_something_made_at_the_same_moment_after_a_notice_is_told_next(self) -> None:
        self.assertTrue(await self.notices.send_ready(self.assistant))  # the seeded entry
        await self.ready(entries=1)  # the clock has not moved
        self.assertTrue(await self.notices.send_ready(self.assistant))
        self.assertFalse(await self.notices.send_ready(self.assistant))
        self.assertEqual(len(self.told()), 2)

    async def test_report_now_is_the_managers_own_click(self) -> None:
        await self.later()
        await self.notices.send_ready(self.assistant)
        self.w.standins.notices.sent.clear()
        await self.later()
        self.assertEqual(self.w.call("POST", f"{PREFIX}/reports", MANAGER).status_code, 200)
        self.assertFalse(await self.notices.send_ready(self.assistant))
        self.assertEqual(self.told(), [])

    async def test_nothing_is_sent_while_paused_and_it_is_told_on_resume(self) -> None:
        await self.later()
        self.w.call("POST", f"{PREFIX}/assistant/pause", MANAGER)
        self.assertFalse(await self.notices.send_ready(self.assistant))
        self.assertEqual(self.told(), [])
        self.w.call("POST", f"{PREFIX}/assistant/resume", MANAGER)
        self.assertEqual(len(self.told()), 1)

    async def test_an_owners_answer_that_raises_an_entry_tells_the_manager(self) -> None:
        await self.later()
        await self.notices.send_ready(self.assistant)
        self.w.standins.notices.sent.clear()
        await self.later()
        item_id = self.w.seeded.item.id
        response = self.w.call(
            "POST", f"{PREFIX}/action-items/{item_id}/decline", OWNER, {"reason": "Not mine."}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            self.told(), ["Your Executive Assistant has one thing that needs your attention."]
        )

    async def test_a_scheduled_report_tells_the_manager(self) -> None:
        connect_schedule(self.w.standins.clock, self.w.module)
        await self.later()
        await self.notices.send_ready(self.assistant)
        self.w.standins.notices.sent.clear()
        await self.w.standins.clock.move_to(datetime.fromisoformat("2026-11-30T08:05:00+00:00"))
        self.assertTrue(any("new status report" in text for text in self.told()), self.told())

    async def test_the_notice_holds_counts_only(self) -> None:
        self.assertEqual(notice_text(2, 0), "Your Executive Assistant has 2 new status reports.")
        await self.later()
        await self.ready(entries=1)
        await self.notices.send_ready(self.assistant)
        self.assertNotIn("It needs you.", " ".join(self.told()))

    async def test_a_notice_that_cannot_be_delivered_is_not_retried(self) -> None:
        await self.later()
        self.w.standins.notices.failing = True
        self.assertFalse(await self.notices.send_ready(self.assistant))
        self.w.standins.notices.failing = False
        self.assertFalse(await self.notices.send_ready(self.assistant))
        self.assertEqual(self.told(), [])


if __name__ == "__main__":
    unittest.main()
