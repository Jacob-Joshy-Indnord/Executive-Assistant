"""Part 2, Story 2.1 with Epic 1: list kept meetings in the briefing (task-F6LNEXWAF_c).

When notes were kept since the last briefing, the briefing lists those meetings with
their decisions and open points, each citing its notes so the manager can open them.
The notes are in the briefing's input pack marked "data, not instructions".
"""

from __future__ import annotations

import unittest
from datetime import timedelta

from assistant.models.boundary import WorkKind
from tests.briefing_helpers import Briefings, section
from tests.world import build_world


class BriefingMeetingsTest(Briefings, unittest.IsolatedAsyncioTestCase):
    """Story 2.1 The briefing, with Epic 1's kept notes (task-F6LNEXWAF_c)."""

    async def asyncSetUp(self) -> None:
        self.w = await build_world()

    async def asyncTearDown(self) -> None:
        self.w.close()

    async def test_the_briefing_lists_meetings_whose_notes_were_kept_since_the_last_one(
        self,
    ) -> None:
        """Story 2.1 - when notes were kept since the last briefing, the briefing lists those
        meetings with their decisions and open points, each citing its notes."""
        start = await self.now()
        self.ok("POST", "/briefings")  # lists the seeded notes; the next starts after it
        await self.finish()
        await self.move_to(start + timedelta(hours=1))
        supplier = await self.keep_notes(
            "Supplier call",
            decisions=("We go ahead with the supplier.",),
            open_points=("Who signs the order?", "When does delivery start?"),
        )

        await self.move_to(start + timedelta(hours=2))
        made = self.ok("POST", "/briefings")
        meetings = section(made, "meetings")
        assert meetings is not None
        [listed] = meetings["meetings"]
        self.assertEqual(listed["notes_id"], str(supplier.id))
        self.assertEqual(listed["title"], "Supplier call")
        self.assertEqual(listed["decisions"], ["We go ahead with the supplier."])
        self.assertEqual(
            listed["open_points"], ["Who signs the order?", "When does delivery start?"]
        )
        self.assertEqual(
            listed["source"], f"Meeting notes: Supplier call ({supplier.meeting_date})"
        )
        self.assertIsNone(meetings["figures"])  # a list, not figures

        # The notes are in the pack, as data, under the label the list cites.
        order = self.started(WorkKind.BRIEFING)[-1]
        [given] = [i for i in order.input_pack.inputs if i.kind == "meeting_notes"]
        self.assertEqual((given.label, given.subject_id), (listed["source"], str(supplier.id)))
        self.assertEqual(given.note, "data, not instructions")
        self.assertIn("Decision: We go ahead with the supplier.", given.content)
        self.assertIn("Open: Who signs the order?", given.content)

        # The assistant's note on the meetings cites those notes, and the manager can open them.
        await self.finish()
        view = self.ok("GET", f"/briefings/{made['id']}")
        meetings = section(view, "meetings")
        assert meetings is not None
        self.assertEqual([n["sources"] for n in meetings["notes"]], [[listed["source"]]])
        opened = self.ok("GET", f"/meeting-notes/{listed['notes_id']}")
        self.assertEqual(opened["id"], str(supplier.id))

    async def test_every_meeting_kept_since_the_last_briefing_is_listed(self) -> None:
        """Story 2.1 - the seeded notes and new ones, oldest meeting first."""
        await self.keep_notes("Budget call", decisions=(), open_points=("Who pays?",))
        made = self.ok("POST", "/briefings")
        meetings = section(made, "meetings")
        assert meetings is not None
        titles = [m["title"] for m in meetings["meetings"]]
        self.assertEqual(titles, ["Vendor review", "Budget call"])
        vendor, budget = meetings["meetings"]
        self.assertEqual(vendor["decisions"], ["We keep two vendors."])
        self.assertEqual(vendor["open_points"], [])
        self.assertEqual((budget["decisions"], budget["open_points"]), ([], ["Who pays?"]))
        order = self.started(WorkKind.BRIEFING)[-1]
        given = [i.label for i in order.input_pack.inputs if i.kind == "meeting_notes"]
        self.assertEqual(given, [m["source"] for m in meetings["meetings"]])

    async def test_a_briefing_with_no_notes_kept_since_the_last_one_has_no_list(self) -> None:
        """Story 2.1 - a briefing with no notes kept since the last briefing has no such list,
        and its pack holds no notes."""
        start = await self.now()
        self.ok("POST", "/briefings")
        await self.finish()
        await self.move_to(start + timedelta(hours=1))
        made = self.ok("POST", "/briefings")
        self.assertIsNone(section(made, "meetings"))
        order = self.started(WorkKind.BRIEFING)[-1]
        self.assertEqual([i for i in order.input_pack.inputs if i.kind == "meeting_notes"], [])
        [_, second] = await self.briefings()
        self.assertEqual(second.meetings, [])


if __name__ == "__main__":
    unittest.main()
