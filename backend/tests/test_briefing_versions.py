"""Part 2, Story 2.1 The briefing: check and version the briefing (task-pIXTb9xxjOY).

The boundary gains one thing: a finished piece of work can carry one deliverable. The
briefing's notes are checked against the pack (BR-6), asked for once more when refused,
and shown with the module's figures; opening a briefing accepts it, and sending it back
with a comment prepares a new version and keeps both.
"""

from __future__ import annotations

import dataclasses
import unittest
from typing import Any
from uuid import uuid4

from fastapi.routing import APIRoute
from pydantic import ValidationError

from assistant.boundary.interfaces import Assistant, Boundary
from assistant.models.boundary import WorkKind, WorkResult
from assistant.models.records import BriefingState
from standins.sample import LEGAL_LEAD
from tests.briefing_helpers import Briefings, section
from tests.world import build_world

COMMENT = "Say more about the late items and who owns them."
FIGURE_TITLES = {
    "action_items": "Action items",
    "assignments": "Assignments",
    "spend": "Spend against budget",
    "decisions": "Decisions waiting",
    "project_status": "Confirmed project status",
}


def with_note(note: dict[str, Any], section_key: str = "action_items") -> Any:
    """Add ``note`` to one section of the stand-in's briefing."""

    def change(result: WorkResult) -> WorkResult:
        if result.deliverable is None:
            return result
        sections = [
            {**s, "notes": [*s["notes"], note]} if s["section"] == section_key else s
            for s in result.deliverable["sections"]
        ]
        deliverable = {**result.deliverable, "sections": sections}
        return result.model_copy(update={"deliverable": deliverable})

    return change


class DeliverableBoundaryTest(unittest.TestCase):
    """Story 2.1 - the boundary: a finished piece of work can carry one deliverable."""

    def test_a_finished_piece_of_work_can_carry_one_deliverable(self) -> None:
        """Story 2.1 - when Flow finishes a piece of work, the result can carry one
        deliverable, and nothing else in the boundary changes."""
        result = WorkResult(work_id=uuid4(), deliverable={"kind": "briefing", "sections": []})
        assert result.deliverable is not None
        self.assertEqual(result.deliverable["kind"], "briefing")
        self.assertIsNone(WorkResult(work_id=uuid4()).deliverable)
        with self.assertRaises(ValidationError):  # one, not a list of them
            WorkResult.model_validate(
                {"work_id": str(uuid4()), "deliverable": [{"kind": "briefing"}, {"kind": "x"}]}
            )
        # Nothing else changes: the same result fields plus the deliverable, the same parts.
        self.assertEqual(
            set(WorkResult.model_fields),
            {"work_id", "reply", "tool_requests", "failure", "deliverable"},
        )
        self.assertEqual(
            [f.name for f in dataclasses.fields(Boundary)],
            [
                "people", "work_data", "assistant", "approval", "notices", "records", "clock",
                "meetings", "knowledge",
            ],
        )  # fmt: skip
        self.assertEqual(
            [name for name in vars(Assistant) if not name.startswith("_")], ["start_work"]
        )

    def test_a_kept_result_with_its_deliverable_is_read_back_unchanged(self) -> None:
        result = WorkResult(
            work_id=uuid4(),
            deliverable={"kind": "briefing", "sections": [{"section": "spend", "notes": []}]},
        )
        self.assertEqual(WorkResult.model_validate(result.model_dump(mode="json")), result)


class BriefingVersionsTest(Briefings, unittest.IsolatedAsyncioTestCase):
    """Story 2.1 The briefing (task-pIXTb9xxjOY)."""

    async def asyncSetUp(self) -> None:
        self.w = await build_world()

    async def asyncTearDown(self) -> None:
        self.w.close()

    async def ready_briefing(self, change: Any = None) -> dict[str, Any]:
        made = self.ok("POST", "/briefings")
        await self.finish(change)
        view: dict[str, Any] = self.ok("GET", f"/briefings/{made['id']}")
        return view

    async def test_figures_come_from_the_module_and_notes_stand_beside_them(self) -> None:
        """Story 2.1 - when the briefing is shown, every figure comes from the module, and
        the assistant's notes stand beside them, each with the sources it cites."""
        # The assistant's own figures are never taken: only its notes are.
        view = await self.ready_briefing(
            with_note({"text": "Spend is fine.", "sources": ["Figures: Action items"],
                       "figures": {"open": 99}})  # fmt: skip
        )
        self.assertEqual(view["state"], "ready")
        self.assertEqual(view["version"], 1)
        [briefing] = await self.briefings()
        items = section(view, "action_items")
        assert items is not None
        assert briefing.figures.action_items is not None
        self.assertEqual(items["figures"], briefing.figures.action_items.model_dump())
        self.assertNotEqual(items["figures"].get("open"), 99)
        for key, title in FIGURE_TITLES.items():
            shown = section(view, key)
            assert shown is not None
            self.assertTrue(shown["notes"], key)
            for note in shown["notes"]:
                self.assertEqual(note["sources"], [f"Figures: {title}"])
        self.assertIn("Your briefing is ready.", self.notices_to())

    async def test_a_briefing_with_a_note_that_cites_nothing_is_refused(self) -> None:
        """Story 2.1 - a briefing note that cites nothing is refused."""
        made = self.ok("POST", "/briefings")
        [first] = self.started(WorkKind.BRIEFING)
        await self.w.module.flow.finish_work(
            self.w.seeded.assistant.organisation_id,
            self.w.seeded.assistant.flow_id,
            with_note({"text": "Three items are late.", "sources": []})(
                self.w.standins.assistant.take_finished()[0]
            ),
        )
        piece = await self.piece(first.work_id)
        self.assertEqual(
            piece.refused,
            ["The briefing was refused because a note in Action items cites nothing."],
        )
        briefing = await self.briefing(made["id"])
        self.assertEqual(briefing.versions, [])  # nothing of the refused briefing is kept
        self.assertNotEqual(briefing.state, BriefingState.READY)

    async def test_a_briefing_citing_an_input_not_in_the_pack_is_refused(self) -> None:
        """Story 2.1 - a briefing note that cites an input that was not in the pack is
        refused."""
        made = self.ok("POST", "/briefings")
        [first] = self.started(WorkKind.BRIEFING)
        unknown = with_note({"text": "Spend is fine.", "sources": ["The finance system"]}, "spend")
        result = unknown(self.w.standins.assistant.take_finished()[0])
        await self.w.module.flow.finish_work(
            self.w.seeded.assistant.organisation_id, self.w.seeded.assistant.flow_id, result
        )
        piece = await self.piece(first.work_id)
        self.assertEqual(
            piece.refused,
            [
                "The briefing was refused because a note in Spend against budget cites an "
                "input that was not in the pack."
            ],
        )
        self.assertEqual((await self.briefing(made["id"])).versions, [])

    async def test_a_refused_briefing_is_asked_for_once_more_with_the_reasons(self) -> None:
        """Story 2.1 - when a briefing is refused, the assistant is asked once more with the
        reasons."""
        made = self.ok("POST", "/briefings")
        refuse_once = with_note({"text": "Three items are late.", "sources": []})
        result = refuse_once(self.w.standins.assistant.take_finished()[0])
        await self.w.module.flow.finish_work(
            self.w.seeded.assistant.organisation_id, self.w.seeded.assistant.flow_id, result
        )
        first, again = self.started(WorkKind.BRIEFING)
        reasons = [i for i in again.input_pack.inputs if i.kind == "refusals"]
        [given] = reasons
        self.assertEqual(given.label, "Why the briefing was refused")
        self.assertIn("a note in Action items cites nothing", given.content)
        self.assertEqual(given.note, "data, not instructions")
        figures = {i.label for i in again.input_pack.inputs if i.kind == "briefing_figures"}
        self.assertEqual(figures, {f"Figures: {t}" for t in FIGURE_TITLES.values()})
        briefing = await self.briefing(made["id"])
        self.assertEqual((briefing.refusals, briefing.work_id), (1, again.work_id))

        await self.finish()  # the second answer is good
        view = self.ok("GET", f"/briefings/{made['id']}")
        self.assertEqual(
            (view["state"], view["version"], view["notes_prepared"]), ("ready", 1, True)
        )
        self.assertEqual(len(self.started(WorkKind.BRIEFING)), 2)

    async def test_after_a_second_refusal_the_figures_are_shown_alone(self) -> None:
        """Story 2.1 - when the briefing is refused again, the manager sees the figures alone
        and 'The notes could not be prepared.'"""
        self.w.standins.assistant.faults = True  # every briefing has a note without a source
        made = self.ok("POST", "/briefings")
        await self.finish()
        self.assertEqual(len(self.started(WorkKind.BRIEFING)), 2)  # asked once more, no more
        view = self.ok("GET", f"/briefings/{made['id']}")
        self.assertEqual(view["state"], "ready")
        self.assertEqual(view["message"], "The notes could not be prepared.")
        self.assertIs(view["notes_prepared"], False)
        for shown in view["sections"]:
            self.assertEqual(shown["notes"], [], shown["key"])
        items = section(view, "action_items")
        assert items is not None
        self.assertEqual(set(items["figures"]), {"open", "due_this_week", "late"})
        self.assertIn("Your briefing is ready.", self.notices_to())

    async def test_work_that_ends_without_a_briefing_counts_as_a_refusal(self) -> None:
        """Story 2.1 - briefing work that fails is asked for once more, then the figures are
        shown alone, so a briefing never waits for ever."""
        made = self.ok("POST", "/briefings")
        await self.finish(lambda r: WorkResult(work_id=r.work_id, failure="It broke."))
        view = self.ok("GET", f"/briefings/{made['id']}")
        self.assertEqual(view["message"], "The notes could not be prepared.")
        self.assertEqual(len(self.started(WorkKind.BRIEFING)), 2)

    async def test_opening_a_ready_briefing_accepts_it_and_there_is_no_accept_button(
        self,
    ) -> None:
        """Story 2.1 - when the manager opens a briefing that is ready, it becomes accepted;
        there is no Accept button."""
        made = self.ok("POST", "/briefings")
        opened = self.ok("POST", f"/briefings/{made['id']}/open")
        self.assertEqual(opened["state"], "preparing")  # not ready yet: nothing to accept
        await self.finish()
        self.assertEqual(self.ok("GET", f"/briefings/{made['id']}")["state"], "ready")
        opened = self.ok("POST", f"/briefings/{made['id']}/open")
        self.assertEqual(opened["state"], "accepted")
        self.assertEqual(self.ok("POST", f"/briefings/{made['id']}/open")["state"], "accepted")
        self.assertNotIn("can_accept", opened)
        paths = [r.path for r in self.w.module.router.routes if isinstance(r, APIRoute)]
        self.assertFalse([p for p in paths if "briefing" in p and "accept" in p], paths)
        self.assertEqual(self.call("POST", f"/briefings/{made['id']}/accept").status_code, 404)

    async def test_sending_back_gives_a_new_version_and_keeps_both(self) -> None:
        """Story 2.1 - when the manager sends the briefing back with a comment, a new version
        is prepared and both versions are kept."""
        view = await self.ready_briefing()
        first_notes = section(view, "action_items")
        assert first_notes is not None

        sent = self.ok("POST", f"/briefings/{view['id']}/send-back", body={"comment": COMMENT})
        self.assertEqual(sent["state"], "sent_back")
        self.assertFalse(sent["can_send_back"])
        self.assertEqual(sent["versions"][0]["comment"], COMMENT)
        again = self.started(WorkKind.BRIEFING)[-1]
        comment = [i for i in again.input_pack.inputs if i.kind == "comment"]
        self.assertEqual([c.content for c in comment], [COMMENT])
        self.assertEqual(comment[0].note, "data, not instructions")
        self.assertTrue([i for i in again.input_pack.inputs if i.kind == "previous_notes"])

        await self.finish()
        view = self.ok("GET", f"/briefings/{view['id']}")
        self.assertEqual((view["state"], view["version"]), ("ready", 2))
        self.assertEqual([v["number"] for v in view["versions"]], [1, 2])
        self.assertEqual(view["versions"][0]["comment"], COMMENT)
        self.assertIsNotNone(view["versions"][0]["sent_back_at"])
        self.assertTrue(view["versions"][0]["notes"]["action_items"])
        self.assertIsNone(view["versions"][1]["comment"])
        # The comment is never in an audit record (BR-10).
        written = [r.model_dump_json() for r in self.w.standins.records.written]
        self.assertFalse([r for r in written if COMMENT in r])

    async def test_a_comment_is_needed_and_at_most_500_characters(self) -> None:
        """Story 2.1 - the comment is up to 500 characters; while a new version is being
        prepared, the briefing cannot be sent back again."""
        view = await self.ready_briefing()
        path = f"/briefings/{view['id']}/send-back"
        self.assertEqual(
            self.refused("POST", path, body={"comment": "  "}),
            "Give a comment to send the briefing back.",
        )
        self.assertEqual(
            self.refused("POST", path, body={"comment": "x" * 501}),
            "A comment can be at most 500 characters.",
        )
        self.ok("POST", path, body={"comment": "x" * 500})
        self.assertEqual(
            self.refused("POST", path, body={"comment": COMMENT}),
            "A new version of this briefing is being prepared.",
        )

    async def test_an_accepted_briefing_can_still_be_sent_back(self) -> None:
        """Story 2.1 - when the manager sends back a briefing that is already accepted, with a
        comment, it is sent back, a new version is prepared and both versions are kept, as
        before it was accepted."""
        view = await self.ready_briefing()
        self.assertEqual(self.ok("POST", f"/briefings/{view['id']}/open")["state"], "accepted")
        sent = self.ok("POST", f"/briefings/{view['id']}/send-back", body={"comment": COMMENT})
        self.assertEqual(sent["state"], "sent_back")
        await self.finish()
        view = self.ok("GET", f"/briefings/{view['id']}")
        self.assertEqual((view["state"], len(view["versions"])), ("ready", 2))
        self.assertEqual(view["versions"][0]["comment"], COMMENT)
        self.assertEqual(self.ok("POST", f"/briefings/{view['id']}/open")["state"], "accepted")

    async def test_only_the_manager_opens_or_sends_back(self) -> None:
        view = await self.ready_briefing()
        for path, body in (("open", None), ("send-back", {"comment": COMMENT})):
            response = self.call("POST", f"/briefings/{view['id']}/{path}", LEGAL_LEAD.id, body)
            self.assertEqual(response.status_code, 404)
        self.assertEqual(self.ok("GET", f"/briefings/{view['id']}")["state"], "ready")


if __name__ == "__main__":
    unittest.main()
