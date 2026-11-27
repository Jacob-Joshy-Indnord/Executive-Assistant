"""Story 2.2: approval levels and limits."""

from __future__ import annotations

import ast
import asyncio
import unittest
from pathlib import Path
from typing import Any
from uuid import uuid4

from assistant.api.router import PREFIX
from assistant.models.boundary import ActionKind, ApprovalLevel, ApprovalLimits
from assistant.models.records import ApprovalLevelRecord, AssistantRecord
from assistant.rules.approval_kinds import ACTION_KINDS, NEVER_ALLOWED
from assistant.rules.sentences import is_plain_sentence
from assistant.service.approval_levels import approval_request_for, level_for
from standins.sample import (
    BACKEND_ENGINEER,
    FINANCE_LEAD,
    LEGAL_LEAD,
    OPERATIONS_DIRECTOR,
    ORGANISATION_ADMINISTRATOR,
    ORGANISATION_ID,
    SALES_DIRECTOR,
)
from tests.world import World, build_world_sync

MANAGER = OPERATIONS_DIRECTOR.id
MODULE_ROOT = Path(__file__).resolve().parents[1] / "assistant"


class ApprovalLevelsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.w: World = build_world_sync()
        self.assistant: AssistantRecord = self.w.seeded.assistant

    def tearDown(self) -> None:
        self.w.close()

    def _levels(self, person_id: str = MANAGER) -> dict[str, Any]:
        response = self.w.call("GET", f"{PREFIX}/approval-levels", person_id)
        self.assertEqual(response.status_code, 200, response.text)
        return {level["kind"]: level for level in response.json()}

    def _change(self, kind: str, body: dict[str, Any], person_id: str = MANAGER) -> Any:
        return self.w.call("PUT", f"{PREFIX}/approval-levels/{kind}", person_id, body)

    def _level_for(self, kind: ActionKind) -> tuple[ApprovalLevel, ApprovalLimits]:
        async def read() -> tuple[ApprovalLevel, ApprovalLimits]:
            async with self.w.store.transaction(ORGANISATION_ID) as tx:
                return await level_for(tx, self.assistant, kind)

        return asyncio.run(read())

    def test_defaults_on_setup(self) -> None:
        """Story 2.2 - every kind has its section 8.2 starting level."""
        levels = self._levels()
        self.assertEqual(set(levels), {k.value for k in ACTION_KINDS})
        self.assertEqual(
            {kind: (v["level"], v["limits"]["per_day"], v["locked"]) for kind, v in levels.items()},
            {
                "ask_owner": ("with_approval", None, False),
                "add_action_item": ("with_approval", 10, False),
                "send_message": ("with_approval", 5, False),
                "purchase": ("not_allowed", None, True),
                "write_meeting_notes": ("with_approval", None, False),
                "attend_meeting": ("with_approval", None, False),
            },
        )
        self.assertTrue(levels["send_message"]["allowed_limits"]["people"])
        self.assertFalse(levels["purchase"]["allowed_limits"]["per_day"])

    def test_change_is_recorded_first_and_applies_to_the_next_request(self) -> None:
        """Story 2.2 - change recorded and applies next."""
        response = self._change(
            "add_action_item",
            {"level": "without_approval", "limits": {"people": ["legal LEAD"], "per_day": 3}},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["level"], "without_approval")
        self.assertEqual(
            body["limits"],
            {
                "people": [{"id": LEGAL_LEAD.id, "display_name": "Legal lead"}],
                "per_day": 3,
                "projects": None,
            },
        )
        record = self.w.standins.records.written[-1]
        self.assertEqual(
            (record.name, record.state_before, record.state_after, record.who),
            ("assistant.approval_level_changed", "with_approval", "without_approval", MANAGER),
        )
        self.assertEqual(
            record.details,
            {"action_kind": "add_action_item", "per_day": 3, "people": [LEGAL_LEAD.id]},
        )

        async def next_requests() -> list[str]:
            async with self.w.store.transaction(ORGANISATION_ID) as tx:
                outcomes = []
                for target, count in ((LEGAL_LEAD.id, 0), (FINANCE_LEAD.id, 0), (LEGAL_LEAD.id, 3)):
                    request = await approval_request_for(
                        tx,
                        self.assistant,
                        ActionKind.ADD_ACTION_ITEM,
                        request_id=uuid4(),
                        effect="The item is added.",
                        target_id=target,
                        count_today=count,
                    )
                    self.assertEqual(request.level, ApprovalLevel.WITHOUT_APPROVAL)
                    outcomes.append((await self.w.standins.approval.check(request)).outcome)
                return outcomes

        self.assertEqual(asyncio.run(next_requests()), ["carry_out", "ask_manager", "ask_manager"])

    def test_change_is_not_made_when_it_cannot_be_recorded(self) -> None:
        self.w.standins.records.failing = True
        response = self._change("send_message", {"level": "without_approval"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["message"], "Not saved. Try again.")
        self.w.standins.records.failing = False
        self.assertEqual(self._levels()["send_message"]["level"], "with_approval")

    def test_only_the_manager_changes_levels(self) -> None:
        """Story 2.2 - anyone else is refused, and nothing the assistant returns can change one."""
        for person in (
            LEGAL_LEAD.id,
            ORGANISATION_ADMINISTRATOR.id,
            BACKEND_ENGINEER.id,  # a virtual employee
        ):
            with self.subTest(person):
                response = self._change("send_message", {"level": "without_approval"}, person)
                self.assertEqual(response.status_code, 404)
        # another manager changes only their own assistant's levels
        own = self._change("send_message", {"level": "without_approval"}, SALES_DIRECTOR.id)
        self.assertEqual(own.status_code, 200)
        theirs = self._levels(SALES_DIRECTOR.id)["send_message"]
        self.assertEqual(theirs["level"], "without_approval")
        self.assertEqual(self._levels()["send_message"]["level"], "with_approval")

    def test_no_path_from_assistant_results_to_a_level(self) -> None:
        """Levels change only through the manager's address; one place writes level records."""
        changers, writers = [], []
        for path in MODULE_ROOT.rglob("*.py"):
            relative = path.relative_to(MODULE_ROOT).as_posix()
            for node in ast.walk(ast.parse(path.read_text())):
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr == "change"
                    and isinstance(node.value, ast.Attribute)
                    and node.value.attr == "levels"
                ):
                    changers.append(relative)
                if isinstance(node, ast.Name) and node.id == "ApprovalLevelRecord":
                    writers.append(relative)
        self.assertEqual(set(changers), {"api/router.py"})
        self.assertEqual(
            set(writers) - {"models/records.py", "rules/visibility.py"},
            {"service/approval_levels.py"},
        )

    def test_purchases_stay_not_allowed(self) -> None:
        """Story 2.2 - the purchase kind is refused and stays not allowed."""
        for level in ("with_approval", "without_approval", "not_allowed"):
            with self.subTest(level):
                response = self._change("purchase", {"level": level})
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["detail"]["message"], NEVER_ALLOWED)
        self.assertEqual(self._levels()["purchase"]["level"], "not_allowed")

        async def tamper() -> None:
            async with self.w.store.transaction(ORGANISATION_ID) as tx:
                found = await tx.find(
                    ApprovalLevelRecord,
                    assistant_id=self.assistant.id,
                    action_kind=ActionKind.PURCHASE,
                )
                await tx.change(found[0].evolve(level=ApprovalLevel.WITHOUT_APPROVAL))

        asyncio.run(tamper())  # even a record changed behind the module's back
        self.assertEqual(self._level_for(ActionKind.PURCHASE)[0], ApprovalLevel.NOT_ALLOWED)

    def test_nonsensical_limits_are_refused_with_one_sentence(self) -> None:
        """Story 2.2 - nonsensical limits refused (422)."""
        cases = {
            "daily number below 1": (
                "send_message",
                {"level": "without_approval", "limits": {"per_day": 0}},
                "A daily number must be at least 1.",
            ),
            "daily number too large": (
                "send_message",
                {"level": "without_approval", "limits": {"per_day": 1000}},
                "A daily number must be at most 100.",
            ),
            "empty list of people": (
                "ask_owner",
                {"level": "without_approval", "limits": {"people": []}},
                "Name at least one person, or leave the list out.",
            ),
            "unknown person": (
                "ask_owner",
                {"level": "without_approval", "limits": {"people": ["Nobody Known"]}},
                "No one in the organisation is called Nobody Known.",
            ),
            "unknown level": ("send_message", {"level": "sometimes"}, None),
            "unknown limit": (
                "send_message",
                {"level": "with_approval", "limits": {"per_week": 3}},
                None,
            ),
        }
        for label, (kind, body, message) in cases.items():
            with self.subTest(label):
                response = self._change(kind, body)
                self.assertEqual(response.status_code, 422, response.text)
                detail = response.json()["detail"]
                self.assertTrue(is_plain_sentence(detail["message"]), detail["message"])
                if message:
                    self.assertEqual(detail["message"], message)
        levels = self._levels()
        self.assertEqual(levels["send_message"]["limits"]["per_day"], 5)
        self.assertEqual(levels["ask_owner"]["level"], "with_approval")

    def test_unknown_kind_is_not_found(self) -> None:
        self.assertEqual(self._change("teleport", {"level": "with_approval"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
