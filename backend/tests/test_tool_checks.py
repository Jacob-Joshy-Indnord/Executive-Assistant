"""Story 3.2: check what the assistant returns (task-P5o9heWzPLA)."""

from __future__ import annotations

import unittest
from typing import Any
from uuid import uuid4

from assistant.api.router import PREFIX
from assistant.models.boundary import Person
from assistant.models.records import AttentionEntry, RequestState, StatementDraft
from assistant.rules.sentences import is_plain_sentence
from assistant.rules.tools import MAX_TOOL_REQUESTS
from standins.assistant import MISSING_SOURCE, UNKNOWN_NAME
from standins.sample import FINANCE_LEAD, LEGAL_LEAD, ORGANISATION_ID, sample_id
from tests.work_helpers import MANAGER, SOURCE, EpicThree, draft, item
from tests.world import World, build_world

REMIND = "Remind the Legal lead to finish the review by Friday"
VENDOR = sample_id("project/vendor_consolidation")


class ToolChecksTest(EpicThree, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        await self.forget_seeded_request()

    async def asyncTearDown(self) -> None:
        self.w.close()

    async def _refused(self, tool_requests: list[Any]) -> list[str]:
        work_id = await self.run_tools(tool_requests)
        return list(self.work_view(work_id)["refused"])

    async def test_malformed_requests_refused_individually(self) -> None:
        """Story 3.2 - an unknown tool, an unknown or missing field, or text over its limit
        is refused with a reason, and the other requests are still handled."""
        refused = await self._refused(
            [
                {"tool": "pay_invoice", "amount": "10.00"},
                item(priority="high"),
                {k: v for k, v in item().items() if k != "owner"},
                item(text="x" * 301),  # action item 300
                {"tool": "status_statement", "project": VENDOR, "reason": "r" * 501,
                 "source": SOURCE},  # reason 500  # fmt: skip
                {"tool": "status_statement", "project": VENDOR, "reason": "Late.",
                 "what_changed": "w" * 301, "source": SOURCE},  # statement field 300  # fmt: skip
                draft(text="m" * 2001),  # message 2,000
                {"tool": "ask_for_update", "project": VENDOR, "owner": "Legal lead",
                 "question": "q" * 2001},  # fmt: skip
                {"no": "tool"},
                "not even an object",
                item(),  # the one good request
            ]
        )
        self.assertEqual(
            refused,
            [
                "Tool request 1 was refused because there is no tool called pay_invoice.",
                "Tool request 2 was refused because priority is not a field of action_item.",
                "Tool request 3 was refused because it has no owner.",
                "Tool request 4 was refused because its text is over 300 characters.",
                "Tool request 5 was refused because its reason is over 500 characters.",
                "Tool request 6 was refused because its what changed is over 300 characters.",
                "Tool request 7 was refused because its text is over 2,000 characters.",
                "Tool request 8 was refused because its question is over 2,000 characters.",
                "Tool request 9 was refused because it names no tool.",
                "Tool request 10 was refused because it is not a tool request.",
            ],
        )
        for reason in refused:
            self.assertTrue(is_plain_sentence(reason), reason)
        (handled,) = await self.requests()
        self.assertEqual(
            (handled.request_kind, handled.text), ("action_item", "Finish the contract review")
        )
        self.assertEqual(handled.state, RequestState.WAITING)

    async def test_text_at_its_limit_is_accepted(self) -> None:
        refused = await self._refused([item(text="x" * 300), draft(text="m" * 2000)])
        self.assertEqual(refused, [])
        self.assertEqual(len(await self.requests()), 2)

    async def test_names_must_match_exactly_one_ignoring_case(self) -> None:
        """Story 3.2 - a name that does not match exactly one person or virtual employee,
        ignoring case, is refused and the reason gives the name."""
        refused = await self._refused(
            [item(owner="lEGAL LEAD"), item(owner="Nobody Of That Name"),
             draft(to="backend engineer")]  # fmt: skip
        )
        self.assertEqual(
            refused,
            ["Tool request 2 was refused because no one in the organisation is called "
             "Nobody Of That Name."],  # fmt: skip
        )
        owners = [r.target_id for r in await self.requests()]
        self.assertEqual(owners, [LEGAL_LEAD.id, sample_id("person/backend_engineer")])

    async def test_a_name_shared_by_two_is_refused_with_the_name(self) -> None:
        people = self.w.standins.people

        async def two_called(organisation_id: str, name: str) -> list[Person]:
            base = {"display_name": "Finance lead", "kind": "person",
                    "organisation_id": ORGANISATION_ID}  # fmt: skip
            return [Person(id="a", **base), Person(id="b", **base)]  # type: ignore[arg-type]

        people.find_by_name = two_called  # type: ignore[method-assign]
        refused = await self._refused([draft()])
        self.assertEqual(
            refused,
            ["Tool request 1 was refused because more than one person is called Finance lead."],
        )
        self.assertEqual(await self.requests(), [])

    async def test_someone_of_another_organisation_does_not_count(self) -> None:
        people = self.w.standins.people
        real = people.find_by_name

        async def with_outsider(organisation_id: str, name: str) -> list[Person]:
            found = await real(organisation_id, name)
            outsider = Person(id="x", display_name=name, kind="person", organisation_id="other")
            return [*found, outsider]

        people.find_by_name = with_outsider  # type: ignore[method-assign]
        self.assertEqual(await self._refused([item()]), [])

    async def test_unknown_source_refused(self) -> None:
        """Story 3.2 - a request whose source was not in the input pack is refused."""
        refused = await self._refused([item(source="Something I made up"), item()])
        self.assertEqual(
            refused,
            ["Tool request 1 was refused because its source Something I made up was not in "
             "the input pack."],  # fmt: skip
        )
        self.assertEqual(len(await self.requests()), 1)

    async def test_over_50_requests_capped(self) -> None:
        """Story 3.2 - when more than 50 tool requests are returned, those after the fiftieth
        are refused."""
        refused = await self._refused([draft(text=f"Message {n}") for n in range(55)])
        self.assertEqual(
            refused,
            [f"Tool request {n} was refused because only 50 tool requests are handled for one "
             "piece of work." for n in range(51, 56)],  # fmt: skip
        )
        handled = await self.requests()
        self.assertEqual(len(handled), MAX_TOOL_REQUESTS)
        self.assertEqual(handled[-1].text, "Message 49")

    async def test_refusals_visible_to_manager(self) -> None:
        """Story 3.2 - when something was refused the manager sees how many and can open the
        reasons. Uses the scripted assistant's faults switch."""
        self.w.standins.assistant.faults = True
        response = self.w.call("POST", f"{PREFIX}/chat", MANAGER, {"text": REMIND})
        work_id = response.json()["work"]["id"]
        for result in self.w.standins.assistant.take_finished():
            await self.w.module.flow.finish_work(
                ORGANISATION_ID, self.w.seeded.assistant.flow_id, result
            )
        chat = self.w.call("GET", f"{PREFIX}/chat", MANAGER).json()["messages"]
        summary = chat[-1]["work"]
        self.assertEqual(summary["refused_count"], 4)
        work = self.work_view(work_id)
        self.assertEqual(work["refused_count"], 4)
        reasons = " ".join(work["refused"])
        self.assertIn(UNKNOWN_NAME, reasons)
        self.assertIn(MISSING_SOURCE, reasons)
        self.assertIn("over 300 characters", reasons)
        self.assertIn("priority is not a field", reasons)
        (proposed,) = await self.requests()  # the good one is still handled
        self.assertEqual(proposed.target_id, LEGAL_LEAD.id)
        # and the next piece of work is told what was refused last time
        self.w.standins.assistant.faults = False
        self.w.call("POST", f"{PREFIX}/chat", MANAGER, {"text": "Thanks"})
        latest = self.w.standins.assistant.started[-1].input_pack
        self.assertEqual(latest.refused_last_time, work["refused"])

    async def test_text_never_executes(self) -> None:
        """Story 3.2 - markup and scripts in text from the assistant or an owner do not run:
        text is kept word for word and only ever returned as plain text in JSON."""
        markup = '<script>alert("x")</script><img src=x onerror=alert(1)>'
        sent = self.w.call("POST", f"{PREFIX}/chat", MANAGER, {"text": markup})
        (started,) = self.w.standins.assistant.take_finished()
        from assistant.models.boundary import WorkResult

        await self.w.module.flow.finish_work(
            ORGANISATION_ID,
            self.w.seeded.assistant.flow_id,
            WorkResult(
                work_id=started.work_id,
                reply=f"Reply {markup}",
                tool_requests=[draft(text=markup), item(owner=f"<b>{markup}</b>")],
            ),
        )
        for response in (
            sent,
            self.w.call("GET", f"{PREFIX}/chat", MANAGER),
            self.w.call("GET", f"{PREFIX}/requests", MANAGER),
            self.w.call("GET", f"{PREFIX}/work/{started.work_id}", MANAGER),
        ):
            with self.subTest(response.url.path):
                self.assertEqual(response.headers["content-type"], "application/json")
                self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        messages = self.w.call("GET", f"{PREFIX}/chat", MANAGER).json()["messages"]
        self.assertEqual([m["text"] for m in messages], [markup, f"Reply {markup}"])
        (request,) = self.waiting()["requests"]
        self.assertEqual(request["text"], markup)  # kept word for word, never rewritten
        (reason,) = self.work_view(started.work_id)["refused"]
        self.assertNotIn("<", reason)  # a quoted name is made plain before it is shown
        self.assertTrue(reason.startswith("Tool request 2 was refused because no one"))

    async def test_projects_items_and_choices_are_checked(self) -> None:
        refused = await self._refused(
            [
                {"tool": "ask_for_update", "project": "Moon base", "owner": "Legal lead",
                 "question": "How is it going?"},  # fmt: skip
                {"tool": "ask_for_update", "item": str(uuid4()), "owner": "Legal lead",
                 "question": "How is it going?"},  # fmt: skip
                {"tool": "ask_for_update", "owner": "Legal lead", "question": "Both?",
                 "item": str(self.w.seeded.item.id), "project": VENDOR},  # fmt: skip
                {"tool": "raise_attention", "subject": "Budget", "known": "Over plan.",
                 "choices": ["Only one"], "source": SOURCE},  # fmt: skip
                item(due_date="2026-11-20"),
                item(due_date="next Friday"),
                item(follow_up_times=["2026-11-25T09:00:00"]),
                {"tool": "ask_for_update", "project": "vendor CONSOLIDATION",
                 "owner": "Legal lead", "question": "Where are we?"},  # fmt: skip
                {"tool": "raise_attention", "subject": "Budget", "known": "Over plan.",
                 "choices": ["Wait", "Ask finance"], "source": SOURCE},  # fmt: skip
            ]
        )
        self.assertEqual(
            refused,
            [
                "Tool request 1 was refused because no project the manager can see is called "
                "Moon base.",
                "Tool request 2 was refused because the item it names was not found.",
                "Tool request 3 was refused because it needs one item or project, not 2.",
                "Tool request 4 was refused because its choices are not two to four choices.",
                "Tool request 5 was refused because its due date is in the past.",
                "Tool request 6 was refused because its due date is not a date.",
                "Tool request 7 was refused because its follow up times holds a time without a "
                "time zone.",
            ],
        )
        (ask,) = await self.requests()
        self.assertEqual((ask.subject_kind, ask.subject_id), ("project", VENDOR))
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            found = await tx.find(AttentionEntry, assistant_id=self.w.seeded.assistant.id)
        (entry,) = [e for e in found if e.id != self.w.seeded.attention.id]
        self.assertEqual((entry.reason, entry.choices), ("Budget", ["Wait", "Ask finance"]))

    async def test_the_assistant_cannot_move_a_date_or_reassign(self) -> None:
        """BR-1: a change to an item may touch its text and follow-up times only."""
        seeded = self.w.seeded.item
        refused = await self._refused(
            [
                item(item=str(seeded.id), due_date="2026-12-04"),
                item(item=str(seeded.id), owner="Finance lead", due_date="2026-11-27"),
                item(item=str(seeded.id), text="Finish the review today", due_date="2026-11-27"),
            ]
        )
        self.assertEqual(
            refused,
            [
                "Tool request 1 was refused because the assistant cannot move a due date.",
                "Tool request 2 was refused because the assistant cannot give an item to "
                "someone else.",
            ],
        )
        (change,) = await self.requests()
        self.assertEqual(
            (change.request_kind, change.subject_id), ("action_item_change", str(seeded.id))
        )
        self.assertNotEqual(FINANCE_LEAD.id, change.target_id)

    async def test_statement_drafts_are_kept_for_the_confirmer(self) -> None:
        await self._refused(
            [{"tool": "status_statement", "project": VENDOR, "reason": "Contracts are late.",
              "what_changed": "Two are open.", "source": SOURCE}]  # fmt: skip
        )
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            (statement,) = await tx.find(StatementDraft, assistant_id=self.w.seeded.assistant.id)
        self.assertEqual((statement.owner_id, statement.wording.reason),
                         (LEGAL_LEAD.id, "Contracts are late."))  # fmt: skip


if __name__ == "__main__":
    unittest.main()
