"""Run what is due (BRD section 9.2, BR-13, BR-14).

Kinds of due step are registered in one place (:class:`DueStepRegistry`).
Flow calls "Run what is due" every few minutes, possibly from more than one
process. For each running assistant, each kind says which steps are due; each
step then runs in its own transaction that first *claims* it by adding a
:class:`DueStepClaim` whose id is derived from the step. The store refuses a
second claim of the same step, so a step happens once however often, however
late and in however many places the schedule runs. A step that fails rolls
back with its claim and is tried again on the next run.

A kind that recurs returns only its latest occurrence that is due, so steps
missed while an assistant was paused are done once, not once per missed run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from assistant.boundary.interfaces import Boundary
from assistant.errors import AlreadyChanged, ModuleError
from assistant.models.records import AssistantRecord, AssistantState, DueStepClaim
from assistant.rules.settings import Settings
from assistant.service.actions import Action, perform
from assistant.service.log import log_event
from assistant.service.pausing import organisation_paused, running
from assistant.service.setup import FLOW, settings_for
from assistant.store.interface import Store, Transaction


@dataclass(frozen=True)
class DueContext:
    assistant: AssistantRecord
    settings: Settings


@dataclass(frozen=True)
class DueStep:
    subject: str
    """What the step is about, as an id or token, e.g. an action item id."""
    due_at: datetime


class DueStepKind(Protocol):
    name: str

    async def due(self, reader: Transaction, ctx: DueContext, until: datetime) -> list[DueStep]:
        """Steps due at or before ``until``; for each subject, only the latest occurrence."""
        ...

    async def next_after(
        self, reader: Transaction, ctx: DueContext, after: datetime
    ) -> datetime | None:
        """When this kind is next due after ``after``, if ever."""
        ...

    async def run(self, action: Action, ctx: DueContext, step: DueStep) -> None:
        """Do the step. Writes go through ``action``, so they are audited and all-or-nothing."""
        ...


@dataclass
class DueStepRegistry:
    kinds: dict[str, DueStepKind] = field(default_factory=dict)

    def register(self, kind: DueStepKind) -> DueStepKind:
        if kind.name in self.kinds:
            raise ValueError(f"due step kind {kind.name!r} is registered twice")
        self.kinds[kind.name] = kind
        return kind

    def all(self) -> list[DueStepKind]:
        return list(self.kinds.values())


DUE_STEPS = DueStepRegistry()
"""The module's due steps. Later services register their kinds here."""


def claim_id(assistant: AssistantRecord, kind: str, step: DueStep) -> UUID:
    key = f"{assistant.organisation_id}/{assistant.id}/{kind}/{step.subject}"
    return uuid5(NAMESPACE_URL, f"https://ea-001.invalid/due/{key}/{step.due_at.isoformat()}")


@dataclass
class RunSummary:
    done: int = 0
    already_done: int = 0
    failed: int = 0


class DueRunner:
    def __init__(self, boundary: Boundary, store: Store, registry: DueStepRegistry) -> None:
        self.boundary = boundary
        self.store = store
        self.registry = registry

    async def _running_assistants(self, organisation_id: str) -> list[AssistantRecord]:
        async with self.store.transaction(organisation_id) as tx:
            if await organisation_paused(tx, organisation_id):
                return []
            return await tx.find(AssistantRecord, state=AssistantState.ACTIVE)

    async def run_due(self) -> RunSummary:
        """Flow's "Run what is due": every step due now, for every running assistant, once."""
        now = await self.boundary.clock.now()
        summary = RunSummary()
        for organisation_id in await self.store.organisation_ids():
            for assistant in await self._running_assistants(organisation_id):
                await self.run_for(assistant, now, summary)
        return summary

    async def run_for(
        self, assistant: AssistantRecord, now: datetime, summary: RunSummary | None = None
    ) -> RunSummary:
        summary = summary or RunSummary()
        async with self.store.transaction(assistant.organisation_id) as tx:
            ctx = DueContext(assistant, await settings_for(tx, assistant))
            pending: list[tuple[str, DueStep]] = []
            for kind in self.registry.all():
                for step in await kind.due(tx, ctx, now):
                    if await tx.get(DueStepClaim, claim_id(assistant, kind.name, step)) is None:
                        pending.append((kind.name, step))
                    else:
                        summary.already_done += 1
        pending.sort(key=lambda item: item[1].due_at)
        for kind_name, step in pending:
            await self._run_step(assistant, kind_name, step, summary)
        return summary

    async def _run_step(
        self, assistant: AssistantRecord, kind_name: str, step: DueStep, summary: RunSummary
    ) -> None:
        kind = self.registry.kinds[kind_name]

        async def work(action: Action) -> bool:
            current = await action.get(AssistantRecord, assistant.id)
            if current is None or not await running(action, current):
                return False  # paused or ended since the run began
            await action.add(
                "assistant.due_step_claimed",
                DueStepClaim(
                    id=claim_id(current, kind_name, step),
                    organisation_id=current.organisation_id,
                    assistant_id=current.id,
                    created_at=action.now,
                    step_kind=kind_name,
                    subject=step.subject,
                    due_at=step.due_at,
                ),
                details={"step_kind": kind_name},
            )
            ctx = DueContext(current, await settings_for(action, current))
            await kind.run(action, ctx, step)
            return True

        try:
            ran = await perform(
                self.boundary,
                self.store,
                assistant.organisation_id,
                FLOW,
                work,
                assistant_id=assistant.id,
            )
        except AlreadyChanged:  # claimed by another run in the meantime
            summary.already_done += 1
            return
        except ModuleError as error:  # tried again next time
            summary.failed += 1
            log_event(
                "assistant.due_step_failed",
                assistant_id=str(assistant.id),
                step_kind=kind_name,
                code=error.code,
            )
            return
        if ran:
            summary.done += 1

    async def next_due(self) -> datetime | None:
        """When something is next due for a running assistant: overdue steps first."""
        now = await self.boundary.clock.now()
        earliest: datetime | None = None
        for organisation_id in await self.store.organisation_ids():
            for assistant in await self._running_assistants(organisation_id):
                async with self.store.transaction(organisation_id) as tx:
                    ctx = DueContext(assistant, await settings_for(tx, assistant))
                    for kind in self.registry.all():
                        candidates = [
                            step.due_at
                            for step in await kind.due(tx, ctx, now)
                            if await tx.get(DueStepClaim, claim_id(assistant, kind.name, step))
                            is None
                        ]
                        upcoming = await kind.next_after(tx, ctx, now)
                        if upcoming is not None:
                            candidates.append(upcoming)
                        for when in candidates:
                            if earliest is None or when < earliest:
                                earliest = when
        return earliest
