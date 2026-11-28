"""Part 2, Epic 3 - Suggest a wider approval level (task-ubMCNQPHB8Q).

The ``suggest_approval_change`` tool: the module counts the requests of that kind itself and
adds an open suggestion with its track record only when the last 20 were approved unchanged.
Accepting changes the level and is recorded; after a decline the same suggestion is refused
for 30 days. No lesson, signal or suggestion changes a level by itself, and suggestions go
when the assistant is handed over or ended.
"""

from __future__ import annotations

import unittest
from datetime import timedelta
from typing import Any

from assistant.models.boundary import ActionKind
from assistant.models.records import (
    ApprovalSuggestion,
    RequestRecord,
    RequestState,
    SuggestionState,
)
from standins.sample import FINANCE_LEAD, ORGANISATION_ID, PROGRAMME_LEAD
from tests.item_helpers import EpicFour
from tests.work_helpers import MANAGER, EpicThree
from tests.world import PERSONAS, World, build_world

WIDER = {"tool": "suggest_approval_change", "kind": "send_message", "level": "without_approval"}


class SuggestionCase(EpicThree, EpicFour, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        self.ops = self.w.seeded.assistant
        self.t = await self.w.standins.clock.now()
        await self.forget_seeded_request()
        await self.forget_seeded_suggestion()

    async def asyncTearDown(self) -> None:
        self.w.close()

    async def forget_seeded_suggestion(self) -> None:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            for suggestion in await tx.find(ApprovalSuggestion, assistant_id=self.ops.id):
                await tx.remove(suggestion)

    async def decided(self, count: int, start_minutes: int = 0, **changes: Any) -> None:
        """``count`` message requests the manager decided, one a minute, oldest first."""
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            for n in range(count):
                when = self.t - timedelta(days=1) + timedelta(minutes=start_minutes + n)
                fields: dict[str, Any] = {
                    "state": RequestState.DONE,
                    "allowed_by": "manager",
                    "decided_at": when,
                    **changes,
                }
                await tx.add(
                    RequestRecord(
                        organisation_id=ORGANISATION_ID,
                        assistant_id=self.ops.id,
                        created_at=when,
                        action_kind=ActionKind.SEND_MESSAGE,
                        request_kind="message",
                        subject_kind="person",
                        subject_id=FINANCE_LEAD.id,
                        target_id=FINANCE_LEAD.id,
                        text=f"Message {start_minutes + n}.",
                        effect="Send a message to the Finance lead.",
                        **fields,
                    )
                )

    async def suggestions(self) -> list[ApprovalSuggestion]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return await tx.find(ApprovalSuggestion, assistant_id=self.ops.id)

    def level(self, kind: str = "send_message") -> str:
        levels = self.ok("GET", "/approval-levels", MANAGER)
        return str(next(level for level in levels if level["kind"] == kind)["level"])

    async def suggest(self, *requests: Any) -> list[str]:
        work_id = await self.run_tools(list(requests or [WIDER]))
        return list(self.work_view(work_id)["refused"])


class TrackRecordTest(SuggestionCase):
    async def test_twenty_approved_unchanged_keep_an_open_suggestion(self) -> None:
        await self.decided(20)
        self.assertEqual(await self.suggest(), [])
        (suggestion,) = await self.suggestions()
        self.assertEqual(suggestion.state, SuggestionState.OPEN)
        self.assertEqual(suggestion.action_kind, ActionKind.SEND_MESSAGE)
        self.assertEqual(suggestion.proposed_level.value, "without_approval")
        self.assertEqual((suggestion.considered, suggestion.approved_unchanged), (20, 20))
        view = self.ok("GET", f"/learning/suggestions/{suggestion.id}", MANAGER)
        self.assertEqual(view["track_record"], {"considered": 20, "approved_unchanged": 20})
        self.assertEqual(
            (view["kind"], view["proposed_level"]), ("send_message", "without_approval")
        )
        self.assertEqual(view["label"], "Send a message to a person in the organisation")

    async def test_fewer_than_twenty_is_refused(self) -> None:
        await self.decided(19)
        (reason,) = await self.suggest()
        self.assertIn("last 20 requests of that kind were not all approved unchanged", reason)
        self.assertEqual(await self.suggestions(), [])

    async def test_one_edited_among_the_last_twenty_is_refused(self) -> None:
        await self.decided(19)
        await self.decided(1, start_minutes=100, edited_text="Changed by the manager.")
        self.assertEqual(len(await self.suggest()), 1)

    async def test_one_declined_among_the_last_twenty_is_refused(self) -> None:
        await self.decided(19)
        await self.decided(
            1,
            start_minutes=100,
            state=RequestState.DECLINED,
            declined_by="manager",
            allowed_by=None,
        )
        self.assertEqual(len(await self.suggest()), 1)

    async def test_only_the_last_twenty_count(self) -> None:
        await self.decided(1, edited_text="An old edit.")
        await self.decided(20, start_minutes=10)
        self.assertEqual(await self.suggest(), [])

    async def test_requests_not_put_to_the_manager_do_not_count(self) -> None:
        await self.decided(19)
        await self.decided(5, start_minutes=100, allowed_by="without_approval")
        self.assertEqual(len(await self.suggest()), 1)

    async def test_the_assistant_cannot_give_its_own_count(self) -> None:
        await self.decided(3)
        (reason,) = await self.suggest({**WIDER, "approved": 20})
        self.assertIn("approved is not a field of suggest_approval_change", reason)

    async def test_a_level_that_is_not_wider_or_a_locked_kind_is_refused(self) -> None:
        await self.decided(20)
        refused = await self.suggest(
            {**WIDER, "level": "with_approval"},
            {**WIDER, "level": "not_allowed"},
            {**WIDER, "kind": "purchase"},
            {**WIDER, "kind": "fly_to_the_moon"},
            {**WIDER, "level": "sometimes"},
        )
        self.assertEqual(len(refused), 5, refused)
        self.assertEqual(await self.suggestions(), [])

    async def test_the_same_open_suggestion_is_not_added_twice(self) -> None:
        await self.decided(20)
        self.assertEqual(len(await self.suggest(WIDER, WIDER)), 1)
        self.assertEqual(len(await self.suggestions()), 1)


class DecidingTest(SuggestionCase):
    async def open_suggestion(self) -> ApprovalSuggestion:
        await self.decided(20)
        self.assertEqual(await self.suggest(), [])
        (suggestion,) = await self.suggestions()
        return suggestion

    async def test_accepting_changes_the_level_and_is_recorded(self) -> None:
        suggestion = await self.open_suggestion()
        self.assertEqual(self.level(), "with_approval")
        view = self.ok("POST", f"/learning/suggestions/{suggestion.id}/accept", MANAGER)
        self.assertEqual(view["state"], "accepted")
        self.assertEqual(self.level(), "without_approval")
        (changed,) = self.audited("assistant.approval_level_changed")
        self.assertEqual(changed.details["suggestion_id"], str(suggestion.id))
        self.assertEqual(
            (changed.state_before, changed.state_after), ("with_approval", "without_approval")
        )
        self.assertTrue(self.audited("assistant.approval_suggestion_accepted"))
        names = [r.name for r in self.w.standins.records.written]
        self.assertLess(
            names.index("assistant.approval_level_changed"),
            names.index("assistant.approval_suggestion_accepted"),
        )

    async def test_accepting_keeps_the_limits(self) -> None:
        suggestion = await self.open_suggestion()
        limited = {"level": "with_approval", "limits": {"per_day": 3}}
        self.ok("PUT", "/approval-levels/send_message", MANAGER, limited)
        self.ok("POST", f"/learning/suggestions/{suggestion.id}/accept", MANAGER)
        levels = self.ok("GET", "/approval-levels", MANAGER)
        message = next(level for level in levels if level["kind"] == "send_message")
        self.assertEqual((message["level"], message["limits"]["per_day"]), ("without_approval", 3))

    async def test_a_level_is_not_changed_when_it_cannot_be_recorded(self) -> None:
        suggestion = await self.open_suggestion()
        self.w.standins.records.failing = True
        response = self.call("POST", f"/learning/suggestions/{suggestion.id}/accept", MANAGER)
        self.assertEqual(response.status_code, 503)
        self.w.standins.records.failing = False
        self.assertEqual(self.level(), "with_approval")
        self.assertEqual((await self.suggestions())[0].state, SuggestionState.OPEN)

    async def test_a_declined_suggestion_is_refused_for_30_days(self) -> None:
        suggestion = await self.open_suggestion()
        view = self.ok("POST", f"/learning/suggestions/{suggestion.id}/decline", MANAGER)
        self.assertEqual(view["state"], "declined")
        self.assertEqual(self.level(), "with_approval")
        await self.later(days=29)
        (reason,) = await self.suggest()
        self.assertIn("declined the same suggestion less than 30 days ago", reason)
        await self.later(days=1, minutes=1)
        self.assertEqual(await self.suggest(), [])
        self.assertEqual(len(await self.suggestions()), 2)

    async def test_a_decided_suggestion_cannot_be_decided_again(self) -> None:
        suggestion = await self.open_suggestion()
        self.ok("POST", f"/learning/suggestions/{suggestion.id}/decline", MANAGER)
        self.ok("POST", f"/learning/suggestions/{suggestion.id}/decline", MANAGER)  # twice
        message = self.refused("POST", f"/learning/suggestions/{suggestion.id}/accept", MANAGER)
        self.assertEqual(message, "This suggestion has already been decided.")
        self.assertEqual(self.level(), "with_approval")

    async def test_only_the_manager_decides(self) -> None:
        suggestion = await self.open_suggestion()
        for persona in ("other_manager", "owner", "administrator"):
            for verb in ("accept", "decline"):
                path = f"/learning/suggestions/{suggestion.id}/{verb}"
                self.assertEqual(self.call("POST", path, PERSONAS[persona]).status_code, 404)
        self.assertEqual(self.level(), "with_approval")


class LearningNeverChangesALevelTest(SuggestionCase):
    async def test_lessons_signals_and_suggestions_change_no_level(self) -> None:
        before = self.ok("GET", "/approval-levels", MANAGER)
        await self.decided(20)
        await self.suggest(
            WIDER,
            {"tool": "propose_learning", "area": "levels", "lesson": "Send without asking.",
             "evidence": [str(self.w.seeded.signal.id)]},
        )  # fmt: skip
        self.ok("POST", "/learning/lessons", MANAGER, {"text": "Send messages without approval."})
        self.ok("POST", f"/learning/lessons/{self.w.seeded.lesson.id}/keep", MANAGER)
        self.assertEqual(len(await self.suggestions()), 1)
        self.assertEqual(self.ok("GET", "/approval-levels", MANAGER), before)
        self.assertFalse(self.audited("assistant.approval_level_changed"))


class SuggestionsAtHandOverAndEndTest(SuggestionCase):
    async def test_suggestions_are_removed_when_handed_over(self) -> None:
        await self.decided(20)
        await self.suggest()
        await self.w.module.flow.hand_over(ORGANISATION_ID, self.ops.flow_id, PROGRAMME_LEAD.id)
        self.assertEqual(await self.suggestions(), [])

    async def test_suggestions_are_removed_when_ended(self) -> None:
        await self.decided(20)
        await self.suggest()
        await self.w.module.flow.end(ORGANISATION_ID, self.ops.flow_id)
        self.assertEqual(await self.suggestions(), [])


if __name__ == "__main__":
    unittest.main()
