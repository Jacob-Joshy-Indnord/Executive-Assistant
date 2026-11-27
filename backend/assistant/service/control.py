"""Pause and resume (Story 2.4), and the results Flow hands back.

While an assistant is paused, by its manager or by its organisation, nothing
is started, sent or carried out, and results of work that was running are
kept aside unopened. On resume, kept results are handled in the order they
arrived and each step that fell due meanwhile is done once.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import NAMESPACE_URL, UUID, uuid5

from assistant.boundary.interfaces import Boundary
from assistant.errors import AlreadyChanged, NotAllowedValue, NotFound
from assistant.models.boundary import WorkResult
from assistant.models.records import (
    AssistantRecord,
    AssistantState,
    KeptResult,
    OrganisationControl,
)
from assistant.rules.visibility import Viewer
from assistant.service.actions import Action, perform
from assistant.service.assistants import assistant_by_flow_id, my_assistant
from assistant.service.due import DueRunner
from assistant.service.log import log_event
from assistant.service.pausing import control_id, organisation_control, running
from assistant.service.setup import FLOW
from assistant.store.interface import Store

ResultHandler = Callable[[AssistantRecord, WorkResult], Awaitable[None]]
CaughtUpHook = Callable[[AssistantRecord], Awaitable[None]]

FORM_FIRST = "Save or skip the first-time form before pausing or resuming your assistant."


async def note_result(assistant: AssistantRecord, result: WorkResult) -> None:
    """Placeholder until the work service (Epic 3) handles results: ids only."""
    log_event(
        "assistant.result_received", assistant_id=str(assistant.id), work_id=str(result.work_id)
    )


def kept_id(organisation_id: str, work_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"https://ea-001.invalid/kept/{organisation_id}/{work_id}")


class ControlService:
    def __init__(self, boundary: Boundary, store: Store, due: DueRunner) -> None:
        self.boundary = boundary
        self.store = store
        self.due = due
        self.result_handler: ResultHandler = note_result
        self.on_caught_up: list[CaughtUpHook] = []
        """Run after a resume has caught up, e.g. to start work that waited meanwhile."""

    # The manager -------------------------------------------------------------

    async def pause(self, viewer: Viewer) -> AssistantRecord:
        async def work(action: Action) -> AssistantRecord:
            assistant = await my_assistant(action.tx, viewer)
            if assistant.state is AssistantState.NEW:
                raise NotAllowedValue(FORM_FIRST)
            if assistant.state is AssistantState.PAUSED:
                return assistant
            return await action.change(
                "assistant.assistant_paused",
                assistant,
                assistant.evolve(state=AssistantState.PAUSED),
            )

        return await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )

    async def resume(self, viewer: Viewer) -> AssistantRecord:
        async def work(action: Action) -> AssistantRecord:
            assistant = await my_assistant(action.tx, viewer)
            if assistant.state is AssistantState.NEW:
                raise NotAllowedValue(FORM_FIRST)
            if assistant.state is AssistantState.ACTIVE:
                return assistant
            return await action.change(
                "assistant.assistant_resumed",
                assistant,
                assistant.evolve(state=AssistantState.ACTIVE),
            )

        assistant = await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )
        await self.catch_up(assistant)
        return assistant

    # The organisation (Flow) --------------------------------------------------

    async def set_organisation_paused(self, organisation_id: str, paused: bool) -> None:
        async def work(action: Action) -> None:
            current = await organisation_control(action, organisation_id)
            if current is None:
                await action.add(
                    "assistant.organisation_paused" if paused else "assistant.organisation_resumed",
                    OrganisationControl(
                        id=control_id(organisation_id),
                        organisation_id=organisation_id,
                        created_at=action.now,
                        all_paused=paused,
                    ),
                )
            elif current.all_paused != paused:
                await action.change(
                    "assistant.organisation_paused" if paused else "assistant.organisation_resumed",
                    current,
                    current.evolve(all_paused=paused),
                )

        await perform(self.boundary, self.store, organisation_id, FLOW, work)
        if not paused:
            async with self.store.transaction(organisation_id) as tx:
                assistants = await tx.find(AssistantRecord, state=AssistantState.ACTIVE)
            for assistant in assistants:
                await self.catch_up(assistant)

    # Results and catching up ---------------------------------------------------

    async def finish_work(self, organisation_id: str, flow_id: str, result: WorkResult) -> str:
        """Flow's "Finish a piece of work". Kept aside unopened while the assistant is paused."""

        async def keep_or_handle(action: Action) -> tuple[str, AssistantRecord | None]:
            try:
                assistant = await assistant_by_flow_id(action, flow_id)
            except NotFound:
                return "ignored", None  # ended: nothing more is done for it
            if await running(action, assistant):
                return "handled", assistant
            await action.add(
                "assistant.result_kept",
                KeptResult(
                    id=kept_id(organisation_id, result.work_id),
                    organisation_id=organisation_id,
                    assistant_id=assistant.id,
                    created_at=action.now,
                    work_id=result.work_id,
                    payload=result.model_dump(mode="json"),
                ),
                details={"work_id": str(result.work_id)},
            )
            return "kept", assistant

        try:
            outcome, assistant = await perform(
                self.boundary, self.store, organisation_id, FLOW, keep_or_handle
            )
        except AlreadyChanged:
            return "ignored"  # the same result again, already kept
        if outcome == "handled" and assistant is not None:
            await self.result_handler(assistant, result)
        return outcome

    async def catch_up(self, assistant: AssistantRecord) -> None:
        """Handle what was kept, oldest first, then do once each step that fell due."""
        async with self.store.transaction(assistant.organisation_id) as tx:
            current = await tx.get(AssistantRecord, assistant.id)
            if current is None or not await running(tx, current):
                return
            kept = await tx.find(KeptResult, assistant_id=assistant.id)
        for item in kept:
            await self.result_handler(current, WorkResult.model_validate(item.payload))
            await self._forget(item)
        await self.due.run_for(current, await self.boundary.clock.now())
        for hook in self.on_caught_up:
            await hook(current)

    async def _forget(self, item: KeptResult) -> None:
        async def work(action: Action) -> None:
            stored = await action.get(KeptResult, item.id)
            if stored is not None:
                await action.remove(
                    "assistant.kept_result_handled", stored, details={"work_id": str(item.work_id)}
                )

        await perform(self.boundary, self.store, item.organisation_id, FLOW, work)

    async def page_running(self, assistant: AssistantRecord) -> bool:
        async with self.store.transaction(assistant.organisation_id) as tx:
            return await running(tx, assistant)
