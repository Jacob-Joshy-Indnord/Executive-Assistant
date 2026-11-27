"""Regressions found in review of Epic 3: each guards a failure that once got through."""

from __future__ import annotations

import unittest
from datetime import timedelta
from typing import Any

from assistant.api.router import PREFIX
from assistant.models.boundary import AuditRecord, InputPack, PackInput, RecordResult, WorkKind
from assistant.models.records import (
    ActionItem,
    ConfirmedStatement,
    FollowedProject,
    PieceOfWork,
    RequestState,
    StatementWording,
    WorkState,
)
from assistant.rules.input_pack import LABEL_LIMIT, PACK_LIMIT, fit_pack, pack_size
from assistant.rules.requests import RESUME_TO_UNDO
from assistant.rules.work import NOT_STARTED
from assistant.service.work_kinds import WorkKindRegistry
from standins.sample import LEGAL_LEAD, ORGANISATION_ID, sample_id
from tests.work_helpers import MANAGER, EpicThree, draft, item
from tests.world import World, build_world

VENDOR = sample_id("project/vendor_consolidation")


class EpicThreeRegressionsTest(EpicThree, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.w: World = await build_world()
        await self.forget_seeded_request()
        self.ops = self.w.seeded.assistant

    async def asyncTearDown(self) -> None:
        self.w.close()

    def _post(self, path: str, body: Any = None) -> Any:
        return self.w.call("POST", f"{PREFIX}{path}", MANAGER, body)

    async def _pieces(self) -> list[PieceOfWork]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            found = await tx.find(PieceOfWork, assistant_id=self.ops.id)
        return [p for p in found if p.id != self.w.seeded.work.id]

    async def test_a_long_project_name_does_not_jam_the_queue(self) -> None:
        self.w.standins.work_data.projects_by_id[VENDOR].name = "V" * 185
        now = await self.w.standins.clock.now()
        mine = {"organisation_id": ORGANISATION_ID, "assistant_id": self.ops.id, "created_at": now}
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            await tx.add(FollowedProject(**mine, project_id=VENDOR, confirmer_id=LEGAL_LEAD.id))
            await tx.add(
                ConfirmedStatement(
                    **mine, project_id=VENDOR, owner_id=LEGAL_LEAD.id, colour="green",
                    wording=StatementWording(reason="On plan."), source="Legal lead",
                    author_id=LEGAL_LEAD.id, mark="confirmed",
                )
            )  # fmt: skip
        body = self._post("/chat", {"text": "How is the vendor work going?"}).json()
        self.assertEqual(body["work"]["state"], "running")
        (order,) = self.w.standins.assistant.started
        self.assertTrue(all(len(i.label) <= LABEL_LIMIT for i in order.input_pack.inputs))

    async def test_an_unexpected_error_fails_the_piece_and_the_queue_moves_on(self) -> None:
        registry = WorkKindRegistry()

        async def broken(ctx: Any) -> list[PackInput]:
            raise ValueError("a bug in a provider")

        registry.register(WorkKind.REQUEST, broken)
        self.w.module.services.work.registry = registry
        first = self._post("/chat", {"text": "First"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(
            (first.json()["work"]["state"], first.json()["work"]["reason"]),
            ("failed", NOT_STARTED),
        )
        second = self._post("/chat", {"text": "Second"}).json()
        self.assertEqual(second["work"]["state"], "failed")
        self.assertEqual([p.state for p in await self._pieces()], [WorkState.FAILED] * 2)

    def test_refusals_cannot_push_a_pack_over_its_limit(self) -> None:
        now = self.w.seeded.assistant.created_at
        pack = fit_pack(
            InputPack(
                refused_last_time=[f"Tool request {n} was refused because " + "x" * 80
                                   for n in range(3_000)],  # fmt: skip
                inputs=[PackInput(label="Message from the manager", read_at=now, content="Hi")],
            )
        )
        self.assertLessEqual(pack_size(pack), PACK_LIMIT)
        self.assertEqual(len(pack.refused_last_time), 51)
        self.assertEqual(pack.refused_last_time[-1], "2,950 more tool requests were refused.")
        self.assertEqual([i.content for i in pack.inputs], ["Hi"])

    async def test_trying_again_after_a_lost_save_carries_out_once(self) -> None:
        """Flow's carry-out is idempotent per request id, so trying again after the module's
        own transaction was lost carries the request out once and adds the item once."""
        await self.run_tools([item()])
        (request,) = await self.requests()
        records = self.w.standins.records
        real = records.write
        failed: list[str] = []

        async def lose_one_save(record: AuditRecord) -> RecordResult:
            if record.name == "assistant.request_carried_out" and not failed:
                failed.append(record.name)
                return RecordResult(written=False, reason="The audit trail is not available.")
            return await real(record)

        records.write = lose_one_save  # type: ignore[method-assign]
        first = self._post(f"/requests/{request.id}/approve")
        self.assertEqual(first.status_code, 503)
        again = self._post(f"/requests/{request.id}/approve")
        self.assertEqual(again.json()["state"], "done")
        self.assertEqual(len(self.w.standins.approval.carried_out), 1)
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            items = await tx.find(ActionItem, assistant_id=self.ops.id)
        self.assertEqual(len([i for i in items if i.id != self.w.seeded.item.id]), 1)

    async def test_nothing_is_undone_while_paused(self) -> None:
        await self.run_tools([item()])
        (request,) = await self.requests()
        self._post(f"/requests/{request.id}/approve")
        self._post("/assistant/pause")
        response = self._post(f"/requests/{request.id}/undo")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["message"], RESUME_TO_UNDO)
        self.assertEqual(self.w.standins.approval.undone, set())

    async def test_todays_count_is_counted_on_the_day_of_approval(self) -> None:
        await self.run_tools([draft(text="One"), draft(text="Two")])
        one, two = await self.requests()
        await self.w.standins.clock.forward(timedelta(days=1))
        self._post(f"/requests/{one.id}/approve")
        self._post(f"/requests/{two.id}/approve")
        approvals = [r for r, _ in self.w.standins.approval.checked if r.request_id == two.id]
        self.assertEqual(approvals[-1].count_today, 1)
        self.assertEqual({r.state for r in await self.requests()}, {RequestState.DONE})


if __name__ == "__main__":
    unittest.main()
