"""Shared steps for the tests of Epic 5: follow projects, draft, ask and report."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from assistant.models.boundary import WorkResult
from assistant.models.records import (
    AttentionEntry,
    ConfirmedStatement,
    FollowedProject,
    PersonRequest,
    RequestRecord,
    StatementDraft,
)
from standins.sample import ORGANISATION_ID, sample_id
from tests.item_helpers import MANAGER, EpicFour

VENDOR = sample_id("project/vendor_consolidation")
HIRING = sample_id("project/hiring_plan")
CRM = sample_id("project/crm_rollout")
VENDOR_EXPORT = sample_id("assignment/vendor_export")

MONDAY_10 = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)
DRAFT_TIME = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)
"""For the report on Monday 30 November 08:00: owners are asked two working days before
(Thursday 26, 09:00) and statements drafted one working day before that."""
ASK_TIME = datetime(2026, 11, 26, 9, 0, tzinfo=UTC)
REPORT_TIME = datetime(2026, 11, 30, 8, 0, tzinfo=UTC)


class EpicFive(EpicFour):
    """Mixed into a test case that has ``self.w``, a seeded world. The seeded manager
    already follows the CRM rollout, confirmed by the Legal lead."""

    def follow(self, project: str, confirmer: str | None = None) -> Any:
        body = {"confirmer_id": confirmer} if confirmer else None
        return self.ok("POST", f"/projects/{project}/follow", MANAGER, body)

    def project(self, project: str) -> Any:
        listed = self.ok("GET", "/projects", MANAGER)["projects"]
        return next((p for p in listed if p["id"] == project), None)

    async def followed(self, project: str) -> FollowedProject | None:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            found = await tx.find(
                FollowedProject, assistant_id=self.w.seeded.assistant.id, project_id=project
            )
        return found[0] if found else None

    async def drafts(self, project: str) -> list[StatementDraft]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return await tx.find(StatementDraft, project_id=project)

    async def statements(self, project: str) -> list[ConfirmedStatement]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return await tx.find(ConfirmedStatement, project_id=project)

    async def status_rounds(self) -> list[RequestRecord]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            found = await tx.find(RequestRecord, assistant_id=self.w.seeded.assistant.id)
        return [r for r in found if r.request_kind == "status_round"]

    async def status_asks(self, person: str) -> list[PersonRequest]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            found = await tx.find(PersonRequest, person_id=person)
        return [a for a in found if a.asks in ("status", "confirm")]

    async def project_entries(self, project: str) -> list[AttentionEntry]:
        followed = await self.followed(project)
        assert followed is not None
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return await tx.find(
                AttentionEntry, subject_kind="followed_project", subject_id=str(followed.id)
            )

    async def finish_started_work(self) -> None:
        """Hand back what the scripted assistant answered, as Flow would."""
        for result in self.w.standins.assistant.take_finished():
            await self.w.module.flow.finish_work(
                ORGANISATION_ID, self.w.seeded.assistant.flow_id, result
            )

    async def finish_with(self, tool_requests: list[Any]) -> None:
        (order,) = self.w.standins.assistant.started[-1:]
        self.w.standins.assistant.take_finished()
        await self.w.module.flow.finish_work(
            ORGANISATION_ID,
            self.w.seeded.assistant.flow_id,
            WorkResult(work_id=order.work_id, reply="Drafted.", tool_requests=tool_requests),
        )

    def answer(self, request_id: str, person: str, **statement: Any) -> Any:
        return self.ok("POST", f"/status-requests/{request_id}/answer", person, statement)
