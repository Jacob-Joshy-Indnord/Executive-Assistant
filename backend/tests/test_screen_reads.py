"""Epic 6: what the screens read besides the addresses of Epics 2 to 5.

The owner page (task-pUh2iBQ4jww) lists each request with who asks and by when, and the
owner's confirmed statements with Correct; pickers name people (BR-12); a piece of work
shows its reply (task-_OgsYqPdzLk).
"""

from __future__ import annotations

import unittest

from assistant.api.router import PREFIX
from standins.sample import FINANCE_LEAD, LEGAL_LEAD, OPERATIONS_DIRECTOR
from tests.world import PERSONAS, World, build_world

MANAGER = PERSONAS["manager"]
OWNER = PERSONAS["owner"]


class ScreenReadsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()

    async def asyncTearDown(self) -> None:
        self.w.close()

    def get(self, path: str, person: str) -> dict:
        response = self.w.call("GET", f"{PREFIX}{path}", person)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_owner_items_say_who_asks_and_by_when(self) -> None:
        item = self.w.call(
            "POST",
            f"{PREFIX}/action-items",
            MANAGER,
            {"text": "Send the contract", "owner_id": LEGAL_LEAD.id, "due_date": "2026-12-04"},
        ).json()
        mine = {i["id"]: i for i in self.get("/my-action-items", OWNER)["items"]}
        self.assertEqual(mine[item["id"]]["for"]["display_name"], OPERATIONS_DIRECTOR.display_name)
        self.assertIsNone(mine[item["id"]]["ask"])  # before working hours: not sent yet
        await self.w.standins.clock.forward_hour()
        await self.w.module.flow.run_due()
        mine = {i["id"]: i for i in self.get("/my-action-items", OWNER)["items"]}
        ask = mine[item["id"]]["ask"]
        self.assertEqual(ask["asks"], "accept")
        self.assertTrue(ask["answer_by"])

    def test_my_statements_lists_the_latest_version_and_a_correction_keeps_both(self) -> None:
        seeded = self.w.seeded.statement
        listed = self.get("/my-statements", OWNER)["statements"]
        self.assertEqual([s["id"] for s in listed], [str(seeded.id)])
        self.assertEqual(listed[0]["for"]["display_name"], OPERATIONS_DIRECTOR.display_name)
        self.assertNotIn("unconfirmed_wording", listed[0])
        corrected = self.w.call(
            "POST",
            f"{PREFIX}/statements/{seeded.id}/correct",
            OWNER,
            {"colour": "red", "reason": "The contracts are late."},
        ).json()
        self.assertEqual(corrected["previous_version_id"], str(seeded.id))
        listed = self.get("/my-statements", OWNER)["statements"]
        self.assertEqual([s["id"] for s in listed], [corrected["id"]])
        earlier = self.get(f"/statements/{seeded.id}", OWNER)
        self.assertEqual(earlier["wording"]["reason"], "Two contracts are still open.")
        self.assertEqual(self.get("/my-statements", MANAGER)["statements"], [])

    def test_people_are_named_ignoring_case(self) -> None:
        found = self.get("/people?name=finance LEAD", MANAGER)["people"]
        self.assertEqual([p["id"] for p in found], [FINANCE_LEAD.id])
        self.assertEqual(self.get("/people?name=Nobody", MANAGER)["people"], [])
        self.assertEqual(self.get("/people?name=", MANAGER)["people"], [])

    def test_a_piece_of_work_shows_its_reply(self) -> None:
        work = self.get(f"/work/{self.w.seeded.work.id}", MANAGER)
        self.assertIn("reply", work)
        self.assertEqual(work["state"], "failed")


if __name__ == "__main__":
    unittest.main()
