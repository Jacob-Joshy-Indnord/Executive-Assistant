"""Story 4.3: give an update (task-Ib_B9PuuP5M)."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from assistant.models.records import PersonRequestState, RequestState
from assistant.rules import items as rules
from assistant.rules.items import needs_confirmation
from standins.sample import FINANCE_LEAD, SALES_DIRECTOR
from tests.item_helpers import MANAGER, OWNER, EpicFour
from tests.world import World, build_world

MONDAY_10 = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)
WEDNESDAY_9 = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)
NOTE = "Two of the five contracts are read; <b>the</b> rest by Thursday."


class OwnerUpdatesTest(EpicFour, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        await self.at(MONDAY_10)
        self.item = self.give()

    async def asyncTearDown(self) -> None:
        self.w.close()

    def test_br16_who_wrote_the_words_decides(self) -> None:
        """BR-16: the owner's own words count as confirmed; anyone else's need confirming."""
        self.assertFalse(needs_confirmation(OWNER, OWNER))
        self.assertTrue(needs_confirmation("flow-assistant-ops", OWNER))

    async def test_one_step_update(self) -> None:
        """Story 4.3 - when the owner answers a request or opens their item at any time, they
        choose On track, Done or Blocked, can add a note, and can ask for another date."""
        opened = self.view(self.item, OWNER)
        self.assertEqual(opened["can"], ["accept", "propose_date", "decline"])
        self.assertEqual(self.refused(
            "POST",
            f"/action-items/{self.item['id']}/updates",
            OWNER,
            {"word": "on_track"}),  # fmt: skip
            rules.ACCEPT_FIRST,
        )
        self.accept(self.item)
        self.assertEqual(self.view(self.item, OWNER)["can"], ["update"])
        mine = self.ok("GET", "/my-action-items", OWNER)["items"]
        self.assertIn(self.item["id"], [i["id"] for i in mine])
        updated = self.update(self.item, "on_track", note=NOTE, date_asked_for="2026-12-02")
        self.assertEqual(updated["last_update"]["word"], "on_track")
        self.assertEqual(updated["date_asked_for"], "2026-12-02")
        path = f"/action-items/{self.item['id']}/updates"
        for body, sentence in [
            ({"word": "late"}, rules.UNKNOWN_WORD),
            ({"word": "blocked", "note": "x" * 1001}, rules.NOTE_TOO_LONG),
            ({"word": "on_track", "date_asked_for": "2026-11-20"}, rules.DUE_IN_PAST),
        ]:
            with self.subTest(sentence):
                self.assertEqual(self.refused("POST", path, OWNER, body), sentence)
        self.assertEqual(self.update(self.item, "blocked")["last_update"]["note"], None)

    async def test_update_shown_verbatim_and_waiting_requests_withdrawn(self) -> None:
        """Story 4.3 - when an update is given, the manager sees it word for word with its
        time (BR-16), and waiting requests about it are withdrawn."""
        self.accept(self.item)
        await self.at(WEDNESDAY_9)
        await self.run_due()  # the follow-up waits for the manager's approval
        (request,) = await self.requests_about(self.item)
        self.assertEqual(request.state, RequestState.WAITING)
        await self.later(hours=2)
        self.update(self.item, "blocked", note=NOTE)
        shown = self.view(self.item)["last_update"]
        self.assertEqual(
            (shown["word"], shown["label"], shown["note"], shown["at"], shown["confirmed"]),
            ("blocked", "Blocked", NOTE, "2026-11-25T11:00:00+00:00", True),
        )
        (request,) = await self.requests_about(self.item)
        self.assertEqual(request.state, RequestState.WITHDRAWN)
        self.assertEqual(self.ok("GET", "/requests", MANAGER)["count"], 1)  # the seeded one
        self.assertTrue(
            all(a.state is not PersonRequestState.SENT for a in await self.asks(self.item))
        )

    async def test_done_ends_follow_ups(self) -> None:
        """Story 4.3 - when the update is Done, the item is done and no more follow-ups are
        sent."""
        self.accept(self.item)
        done = self.update(self.item, "done", note="All signed.")
        self.assertEqual(done["state"], "done")
        self.assertEqual(done["can"], [])
        await self.at(datetime(2026, 11, 27, 9, 0, tzinfo=UTC))
        await self.run_due()
        self.assertEqual(await self.requests_about(self.item), [])
        self.assertEqual(self.refused(
            "POST",
            f"/action-items/{self.item['id']}/updates",
            OWNER,
            {"word": "on_track"}),  # fmt: skip
            rules.ENDED,
        )

    async def test_others_items_not_found(self) -> None:
        """Story 4.3 - when an owner opens an item that is not theirs, it is not found."""
        self.accept(self.item)
        for person in (FINANCE_LEAD.id, SALES_DIRECTOR.id):
            for method, suffix, body in [
                ("GET", "", None),
                ("POST", "/updates", {"word": "done"}),
                ("POST", "/accept", None),
            ]:
                with self.subTest(person=person, address=suffix):
                    response = self.call(method, f"/action-items/{self.item['id']}{suffix}",
                                         person, body)  # fmt: skip
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.json()["detail"]["code"], "not_found")
        self.assertEqual(self.ok("GET", "/my-action-items", FINANCE_LEAD.id)["items"], [])
        self.assertEqual(self.refused(
            "POST",
            f"/action-items/{self.item['id']}/updates",
            MANAGER,
            {"word": "done"}),  # fmt: skip
            rules.ONLY_THE_OWNER,
        )
        self.assertEqual(self.view(self.item)["state"], "open")


if __name__ == "__main__":
    unittest.main()
