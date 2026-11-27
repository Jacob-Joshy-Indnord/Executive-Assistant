"""Story 1.1: a boundary failure during a person's action is safe and plain."""

from __future__ import annotations

import unittest
from datetime import date
from typing import Any

from assistant.errors import BoundaryUnavailable
from assistant.models.records import ActionItem
from assistant.module import create_module
from assistant.rules.sentences import is_plain_sentence
from assistant.store.sqlite import SqliteStore
from tests.fakes import FIXED_NOW, spy_boundary

ORG = "org-1"


class BoundaryFailureRollsBackTest(unittest.IsolatedAsyncioTestCase):
    async def test_failure_mid_action_leaves_no_record_half_changed(self) -> None:
        injected, _ = spy_boundary()
        module = create_module(injected, SqliteStore())
        async with module.store.transaction(ORG) as tx:
            item = await tx.add(
                ActionItem(
                    organisation_id=ORG,
                    created_at=FIXED_NOW,
                    text="Finish the review",
                    owner_id="legal",
                    due_date=date(2026, 11, 27),
                    origin="manager",
                )
            )

        notices: Any = injected.notices
        notices.raise_with = TimeoutError("read timed out after 30s")
        with self.assertRaises(BoundaryUnavailable) as caught:
            async with module.store.transaction(ORG) as tx:
                current = await tx.get(ActionItem, item.id)
                assert current is not None
                await tx.change(current.evolve(text="Send the signed copy"))
                await tx.add(_second_item())
                await module.boundary.notices.notify(None)  # type: ignore[arg-type]

        self.assertTrue(is_plain_sentence(caught.exception.message))
        self.assertNotIn("timed out", caught.exception.message)
        async with module.store.transaction(ORG) as tx:
            self.assertEqual(await tx.get(ActionItem, item.id), item)
            self.assertEqual(len(await tx.find(ActionItem)), 1)


def _second_item() -> ActionItem:
    return ActionItem(
        organisation_id=ORG,
        created_at=FIXED_NOW,
        text="Another",
        owner_id="legal",
        due_date=date(2026, 11, 27),
        origin="manager",
    )


if __name__ == "__main__":
    unittest.main()
