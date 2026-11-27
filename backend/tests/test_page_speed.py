"""Story 6.2 / section 14: with 200 open items and 50 waiting requests, the Assistant
page's data is ready within one second (task-_OgsYqPdzLk).

The page asks for these addresses when it opens; they are timed together here, against
the SQLite demonstration store.
"""

from __future__ import annotations

import time
import unittest
from datetime import date, timedelta

from assistant.api.router import PREFIX
from assistant.models.boundary import ActionKind
from assistant.models.records import ActionItem, ItemState, RequestRecord
from standins.sample import FINANCE_LEAD, LEGAL_LEAD, ORGANISATION_ID
from tests.world import PERSONAS, World, build_world

PAGE_ADDRESSES = [
    ("POST", "/assistant/opened"),
    ("GET", "/status"),
    ("GET", "/requests"),
    ("GET", "/attention"),
    ("GET", "/action-items"),
    ("GET", "/done"),
    ("GET", "/chat"),
]


class PageSpeedTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        assistant = self.w.seeded.assistant
        now = await self.w.module.boundary.clock.now()
        mine = {"organisation_id": ORGANISATION_ID, "assistant_id": assistant.id, "created_at": now}
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            for n in range(200):
                await tx.add(
                    ActionItem(
                        **mine,
                        text=f"Item {n}",
                        owner_id=LEGAL_LEAD.id if n % 2 else FINANCE_LEAD.id,
                        due_date=date(2026, 12, 1) + timedelta(days=n % 30),
                        origin="manager",
                        accepted_at=now,
                        state=ItemState.OPEN,
                    )
                )
            for n in range(49):  # with the seeded one, 50 wait
                await tx.add(
                    RequestRecord(
                        **mine,
                        action_kind=ActionKind.SEND_MESSAGE,
                        request_kind="message",
                        subject_kind="person",
                        subject_id=FINANCE_LEAD.id,
                        target_id=FINANCE_LEAD.id,
                        text=f"Message {n}",
                        effect="Send a message to the Finance lead.",
                    )
                )

    async def asyncTearDown(self) -> None:
        self.w.close()

    def test_the_pages_data_is_ready_within_one_second(self) -> None:
        manager = PERSONAS["manager"]
        started = time.perf_counter()
        answers = {
            path: self.w.call(method, f"{PREFIX}{path}", manager, {} if method == "POST" else None)
            for method, path in PAGE_ADDRESSES
        }
        took = time.perf_counter() - started
        for path, response in answers.items():
            self.assertEqual(response.status_code, 200, (path, response.text))
        self.assertEqual(answers["/action-items"].json()["open"], 200)
        self.assertEqual(answers["/requests"].json()["count"], 50)
        self.assertLess(took, 1.0, f"the page's data took {took:.2f}s")


if __name__ == "__main__":
    unittest.main()
