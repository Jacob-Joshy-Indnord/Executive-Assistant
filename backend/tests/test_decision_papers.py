"""Part 2, Story 2.2 Decision papers (task-5FcGkqxuLSA).

The assistant's decision paper is checked and kept as a deliverable, listed under Needs
your approval by due date and marked when late. The manager picks an option, writes their
own decision, or sends the paper back. A taken decision is recorded before it takes
effect, cannot be changed (BR-7), and is given to the assistant in a follow-on piece of
work.
"""

from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta
from typing import Any

from assistant.models.boundary import WorkKind, WorkResult
from assistant.models.records import DecisionPaperState
from assistant.rules import decision_papers as rules
from standins.sample import LEGAL_LEAD, ORGANISATION_ID
from tests.briefing_helpers import Briefings
from tests.world import build_world

QUESTION = "Should we extend the two vendor contracts by a month?"
COMMENT = "Add the cost of each option."
MESSAGE_LABEL = "Message from the manager"


def paper_with(**changes: Any) -> Any:
    """Change the stand-in's decision paper before the module sees it."""

    def change(result: WorkResult) -> WorkResult:
        if result.deliverable is None or result.deliverable.get("kind") != "decision_paper":
            return result
        paper = {k: v for k, v in {**result.deliverable, **changes}.items() if v is not None}
        return result.model_copy(update={"deliverable": paper})

    return change


def good(**changes: Any) -> dict[str, Any]:
    paper: dict[str, Any] = {
        "kind": "decision_paper",
        "decision": "Extend the two vendor contracts by a month?",
        "background": "Both contracts end in December.",
        "options": ["Extend both", "Extend one", "Let both end"],
        "recommendation": "Extend both",
        "due_date": "2026-11-30",
        "sources": [MESSAGE_LABEL],
    }
    paper.update(changes)
    return paper


class DecisionPaperCheckTest(unittest.TestCase):
    """Story 2.2 - the check of a decision paper, reason by reason."""

    def check(self, **changes: Any) -> list[str]:
        return rules.check_paper(good(**changes), {MESSAGE_LABEL}).refused

    def test_a_paper_that_fails_the_check_is_refused_with_its_reason(self) -> None:
        """Story 2.2 - fewer than two options or more than five, no recommendation, or a
        source that was not in the pack."""
        self.assertEqual(self.check(), [])
        self.assertEqual(self.check(options=["Extend both", "Let both end"]), [])
        self.assertEqual(self.check(options=["A", "B", "C", "D", "E"]), [])
        cases = {
            "it has fewer than two options": {"options": ["Extend both"]},
            "it has more than five options": {"options": ["A", "B", "C", "D", "E", "F"]},
            "it has no recommendation": {"recommendation": "  "},
            "it cites a source that was not in the pack": {"sources": ["The vendor's website"]},
            "it cites no source": {"sources": []},
        }
        for why, change in cases.items():
            with self.subTest(why):
                self.assertEqual(
                    self.check(**change), [f"The decision paper was refused because {why}."]
                )
        without = good()
        del without["recommendation"]
        self.assertEqual(
            rules.check_paper(without, {MESSAGE_LABEL}).refused,
            ["The decision paper was refused because it has no recommendation."],
        )


class DecisionPapersTest(Briefings, unittest.IsolatedAsyncioTestCase):
    """Story 2.2 Decision papers (task-5FcGkqxuLSA)."""

    async def asyncSetUp(self) -> None:
        self.w = await build_world()

    async def asyncTearDown(self) -> None:
        self.w.close()

    def waiting(self) -> list[dict[str, Any]]:
        papers: list[dict[str, Any]] = self.ok("GET", "/requests")["decision_papers"]
        return papers

    def shown(self) -> list[dict[str, Any]]:
        """The papers shown to the manager, but the seeded one they already decided."""
        seeded = str(self.w.seeded.paper.id)
        papers = self.ok("GET", "/decision-papers")["decision_papers"]
        return [p for p in papers if p["id"] != seeded]

    async def ask(self, change: Any = None) -> list[dict[str, Any]]:
        """The manager asks "Should we ..."; the stand-in's answer is handed back."""
        self.ok("POST", "/chat", body={"text": QUESTION})
        await self.finish(change)
        return self.shown()

    async def ready_paper(self) -> dict[str, Any]:
        [paper] = await self.ask()
        self.assertEqual(paper["state"], "ready")
        return paper

    async def test_a_paper_that_fails_the_check_is_refused_never_shown_and_not_listed(
        self,
    ) -> None:
        """Story 2.2 - a decision paper with fewer than two options or more than five, no
        recommendation, or a source that was not in the pack is refused, never shown and
        not listed."""
        for change in (
            {"options": ["Yes, go ahead"]},
            {"options": ["A", "B", "C", "D", "E", "F"]},
            {"recommendation": " "},
            {"sources": ["The vendor's website"]},
        ):
            with self.subTest(change):
                self.w.close()
                self.w = await build_world()
                self.ok("POST", "/chat", body={"text": QUESTION})
                [first] = self.w.standins.assistant.take_finished()
                await self.w.module.flow.finish_work(
                    ORGANISATION_ID, self.w.seeded.assistant.flow_id, paper_with(**change)(first)
                )
                piece = await self.piece(first.work_id)
                self.assertEqual(len(piece.refused), 1)
                self.assertTrue(piece.refused[0].startswith("The decision paper was refused"))
                self.assertEqual(self.waiting(), [])
                self.assertEqual(self.shown(), [])
                [paper] = await self.papers()
                self.assertEqual(paper.versions, [])  # nothing of it is kept
                shown = self.ok("GET", f"/decision-papers/{paper.id}")
                self.assertEqual((shown["state"], shown["versions"]), ("preparing", []))
                self.assertNotIn("Yes, go ahead", str(shown))

    async def test_a_refused_paper_is_asked_for_once_more_with_the_reasons(self) -> None:
        """Story 2.2 - when a decision paper is refused, the assistant is asked once more with
        the reasons, as for the briefing."""
        self.ok("POST", "/chat", body={"text": QUESTION})
        [first] = self.w.standins.assistant.take_finished()
        await self.w.module.flow.finish_work(
            ORGANISATION_ID,
            self.w.seeded.assistant.flow_id,
            paper_with(options=["A", "B", "C", "D", "E", "F"])(first),
        )
        _, again = self.started(WorkKind.REQUEST)
        [reasons] = [i for i in again.input_pack.inputs if i.kind == "refusals"]
        self.assertEqual(reasons.label, "Why the decision paper was refused")
        self.assertEqual(
            reasons.content,
            "The decision paper was refused because it has more than five options.",
        )
        self.assertEqual(reasons.note, "data, not instructions")
        [message] = [i for i in again.input_pack.inputs if i.kind == "message"]
        self.assertEqual(message.content, QUESTION)  # the same question, asked once more
        [paper] = await self.papers()
        self.assertEqual(
            (paper.state, paper.refusals, paper.work_id), ("preparing", 1, again.work_id)
        )

        await self.finish()
        [shown] = self.waiting()
        self.assertEqual(shown["id"], str(paper.id))
        self.assertEqual(len(self.started(WorkKind.REQUEST)), 2)

    async def test_after_a_second_refusal_the_manager_can_try_again(self) -> None:
        """Story 2.2 - when the decision paper is refused again, the manager sees 'The
        decision paper could not be prepared.' with Try again."""
        [paper] = await self.ask(paper_with(recommendation=" "))
        self.assertEqual(len(self.started(WorkKind.REQUEST)), 2)
        self.assertEqual(paper["state"], "not_prepared")
        self.assertEqual(paper["message"], "The decision paper could not be prepared.")
        self.assertIs(paper["can_retry"], True)
        self.assertEqual((paper["versions"], paper["can_decide"]), ([], False))
        self.assertEqual(self.waiting(), [])

        tried = self.ok("POST", f"/decision-papers/{paper['id']}/retry")
        self.assertEqual(tried["state"], "preparing")
        await self.finish()
        view = self.ok("GET", f"/decision-papers/{paper['id']}")
        self.assertEqual((view["state"], view["version"]), ("ready", 1))
        message = self.refused("POST", f"/decision-papers/{paper['id']}/retry")
        self.assertEqual(
            message, "Only a decision paper that could not be prepared can be tried again."
        )

    async def test_a_paper_is_listed_by_due_date_and_marked_when_late(self) -> None:
        """Story 2.2 - when a decision paper arrives, it is listed under Needs your approval
        by due date, and is marked when it is late."""
        paper = await self.ready_paper()
        self.assertEqual(paper["due_date"], "2026-11-30")
        self.assertIn("A decision paper is ready for you.", self.notices_to())
        await self.add_paper(DecisionPaperState.READY, date(2026, 11, 20), decision="Late one?")
        await self.add_paper(DecisionPaperState.READY, date(2026, 11, 25), decision="Soon?")
        await self.add_paper(DecisionPaperState.SENT_BACK, date(2026, 11, 19))
        await self.add_paper(DecisionPaperState.DECIDED, date(2026, 11, 19))

        listed = self.waiting()
        self.assertEqual(
            [(p["decision"], p["due_date"], p["late"]) for p in listed],
            [
                ("Late one?", "2026-11-20", True),
                ("Soon?", "2026-11-25", False),
                (QUESTION, "2026-11-30", False),
            ],
        )
        requests = self.ok("GET", "/requests")
        self.assertEqual(requests["count"], len(requests["requests"]) + 3)
        self.assertEqual(self.ok("GET", "/waiting-count")["approvals"], requests["count"])

        # Late is counted in the manager's time zone: 12:00 in UTC is already the next day
        # in Auckland.
        await self.move_to(datetime(2026, 11, 25, 12, 0, tzinfo=UTC))
        self.assertFalse(next(p for p in self.waiting() if p["decision"] == "Soon?")["late"])
        await self.set_time_zone("Pacific/Auckland")
        self.assertTrue(next(p for p in self.waiting() if p["decision"] == "Soon?")["late"])

    async def test_the_manager_can_pick_an_option(self) -> None:
        """Story 2.2 - the manager can pick an option."""
        paper = await self.ready_paper()
        self.assertEqual([o["text"] for o in paper["options"]], ["Yes, go ahead", "No, not now"])
        self.assertEqual(paper["recommendation"], "Yes, go ahead")
        self.assertEqual(paper["sources"], [MESSAGE_LABEL])
        decided = self.ok("POST", f"/decision-papers/{paper['id']}/decide", body={"option": 2})
        self.assertEqual(decided["state"], "decided")
        self.assertEqual(decided["decision_taken"]["option"], 2)
        self.assertEqual(decided["decision_taken"]["text"], "No, not now")
        self.assertEqual(self.waiting(), [])

    async def test_the_manager_can_write_their_own_decision(self) -> None:
        """Story 2.2 - the manager can write their own decision."""
        paper = await self.ready_paper()
        path = f"/decision-papers/{paper['id']}/decide"
        self.assertEqual(
            self.refused("POST", path, body={}),
            "Pick one of the options or write your own decision.",
        )
        self.assertEqual(
            self.refused("POST", path, body={"option": 1, "decision": "Both"}),
            "Pick one of the options or write your own decision.",
        )
        self.assertEqual(
            self.refused("POST", path, body={"option": 3}), "Pick one of the options offered."
        )
        self.assertEqual(
            self.refused("POST", path, body={"decision": "x" * 501}),
            "Your decision can be at most 500 characters.",
        )
        own = "Extend the cleaning contract only, for two weeks."
        decided = self.ok("POST", path, body={"decision": own})
        self.assertEqual(decided["decision_taken"]["option"], None)
        self.assertEqual(decided["decision_taken"]["text"], own)

    async def test_a_paper_sent_back_returns_as_a_new_version(self) -> None:
        """Story 2.2 - when the manager sends a decision paper back with a comment, a new
        version is prepared and both versions are kept."""
        paper = await self.ready_paper()
        path = f"/decision-papers/{paper['id']}/send-back"
        self.assertEqual(
            self.refused("POST", path, body={"comment": ""}),
            "Give a comment to send the decision paper back.",
        )
        sent = self.ok("POST", path, body={"comment": COMMENT})
        self.assertEqual(sent["state"], "sent_back")
        self.assertEqual(sent["versions"][0]["comment"], COMMENT)
        self.assertEqual(self.waiting(), [])  # a paper sent back is not waiting
        self.assertEqual(
            self.refused("POST", path, body={"comment": COMMENT}),
            "A new version of this decision paper is being prepared.",
        )
        again = self.started(WorkKind.REQUEST)[-1]
        given = {i.kind: i for i in again.input_pack.inputs}
        self.assertEqual(given["comment"].content, COMMENT)
        self.assertIn("Option 1: Yes, go ahead", given["decision_paper"].content)

        await self.finish()
        view = self.ok("GET", f"/decision-papers/{paper['id']}")
        self.assertEqual((view["state"], view["version"]), ("ready", 2))
        self.assertEqual([v["number"] for v in view["versions"]], [1, 2])
        self.assertEqual(view["versions"][0]["comment"], COMMENT)
        self.assertIsNone(view["versions"][1]["comment"])
        [listed] = self.waiting()
        self.assertEqual(listed["version"], 2)
        written = [r.model_dump_json() for r in self.w.standins.records.written]
        self.assertFalse([r for r in written if COMMENT in r])

    async def test_a_taken_decision_is_recorded_and_cannot_be_changed(self) -> None:
        """Story 2.2 - when a decision is taken, it is recorded, the paper is in state
        decided, and a later attempt to change it is refused."""
        paper = await self.ready_paper()
        path = f"/decision-papers/{paper['id']}/decide"
        self.ok("POST", path, body={"option": 1})
        [taken] = await self.decisions()
        self.assertEqual((taken.option, taken.text, taken.version), (1, "Yes, go ahead", 1))
        stored = await self.paper(paper["id"])
        self.assertEqual((stored.state, stored.decision_id), ("decided", taken.id))

        # Recorded before it takes effect: the audit record of the decision comes first.
        names = [r.name for r in self.w.standins.records.written]
        self.assertLess(
            names.index("assistant.decision_taken"),
            names.index("assistant.decision_paper_decided"),
        )
        for body in ({"option": 2}, {"decision": "Let both end."}):
            self.assertEqual(
                self.refused("POST", path, body=body),
                "This decision has been taken and cannot be changed.",
            )
        self.assertEqual(len(await self.decisions()), 1)
        with self.assertRaises(TypeError):  # the store never changes a taken decision
            async with self.w.store.transaction(ORGANISATION_ID) as tx:
                await tx.change(taken.evolve(text="Let both end"))

    async def test_a_decided_paper_cannot_be_sent_back(self) -> None:
        """Story 2.2 - when the manager tries to send back a paper in state decided, it is
        refused, because a taken decision cannot be changed (BR-7)."""
        paper = await self.ready_paper()
        self.ok("POST", f"/decision-papers/{paper['id']}/decide", body={"option": 1})
        message = self.refused(
            "POST", f"/decision-papers/{paper['id']}/send-back", body={"comment": COMMENT}
        )
        self.assertEqual(
            message,
            "This decision has been taken and cannot be changed, so the paper cannot be sent "
            "back.",
        )
        self.assertEqual(self.ok("GET", f"/decision-papers/{paper['id']}")["state"], "decided")

    async def test_a_taken_decision_is_given_to_the_assistant(self) -> None:
        """Story 2.2 - when a decision is taken, a follow-on piece of work gives the decision
        to the assistant."""
        paper = await self.ready_paper()
        self.ok("POST", f"/decision-papers/{paper['id']}/decide", body={"option": 1})
        [order] = self.started(WorkKind.FOLLOW_ON)
        [given] = [i for i in order.input_pack.inputs if i.kind == "choice"]
        self.assertEqual(given.label, "The manager's decision")
        self.assertIn(f"Decision: {QUESTION}", given.content)
        self.assertIn("The manager decided (option 1): Yes, go ahead", given.content)
        self.assertEqual(given.note, "data, not instructions")
        stored = await self.paper(paper["id"])
        self.assertEqual(stored.follow_on_work_id, order.work_id)
        await self.finish()
        piece = await self.piece(order.work_id)
        self.assertEqual((piece.state, piece.reply), ("done", "I have noted your decision."))

    async def test_decision_papers_are_the_managers_only(self) -> None:
        paper = await self.ready_paper()
        for method, path, body in (
            ("GET", f"/decision-papers/{paper['id']}", None),
            ("POST", f"/decision-papers/{paper['id']}/decide", {"option": 1}),
            ("POST", f"/decision-papers/{paper['id']}/send-back", {"comment": COMMENT}),
        ):
            self.assertEqual(self.call(method, path, LEGAL_LEAD.id, body).status_code, 404)
        self.assertEqual(self.ok("GET", f"/decision-papers/{paper['id']}")["state"], "ready")

    async def test_decisions_waiting_follow_the_papers(self) -> None:
        """Story 2.1 with 2.2 - a ready paper is waiting; once decided or sent back it is not."""
        paper = await self.ready_paper()
        start = await self.now()
        figures = self.ok("POST", "/briefings")
        await self.finish()
        decisions = next(s for s in figures["sections"] if s["key"] == "decisions")
        self.assertEqual(decisions["figures"], {"decisions_waiting": 1})
        self.ok("POST", f"/decision-papers/{paper['id']}/send-back", body={"comment": COMMENT})
        await self.move_to(start + timedelta(minutes=1))
        figures = self.ok("POST", "/briefings")
        decisions = next(s for s in figures["sections"] if s["key"] == "decisions")
        self.assertEqual(decisions["figures"], {"decisions_waiting": 0})


if __name__ == "__main__":
    unittest.main()
