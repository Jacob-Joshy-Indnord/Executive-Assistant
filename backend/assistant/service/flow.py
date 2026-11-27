"""What Flow asks of the module (BRD section 9.2)."""

from __future__ import annotations

from datetime import datetime

from assistant.models.boundary import WorkResult
from assistant.models.records import AssistantRecord
from assistant.service.container import Services
from assistant.service.due import RunSummary
from assistant.service.setup import FLOW


class FlowCalls:
    def __init__(self, services: Services) -> None:
        self._services = services

    async def set_up(
        self,
        organisation_id: str,
        manager_id: str,
        display_name: str,
        flow_id: str,
        set_up_by: str = FLOW,
    ) -> AssistantRecord:
        """An assistant was set up, by its manager or by an administrator for them."""
        return await self._services.setup.set_up(
            organisation_id, manager_id, display_name, flow_id, set_up_by
        )

    async def run_due(self) -> RunSummary:
        """Every few minutes, possibly from more than one process."""
        return await self._services.due.run_due()

    async def next_due(self) -> datetime | None:
        """When something is next due; the stand-in clock uses it to run steps in order."""
        return await self._services.due.next_due()

    async def finish_work(self, organisation_id: str, flow_id: str, result: WorkResult) -> str:
        """The assistant's turn has ended. Returns "handled", "kept" or "ignored"."""
        return await self._services.control.finish_work(organisation_id, flow_id, result)

    async def pause_all(self, organisation_id: str) -> None:
        """The organisation paused all its virtual employees."""
        await self._services.control.set_organisation_paused(organisation_id, True)

    async def resume_all(self, organisation_id: str) -> None:
        """The organisation resumed all its virtual employees."""
        await self._services.control.set_organisation_paused(organisation_id, False)

    async def hand_over(
        self, organisation_id: str, flow_id: str, new_manager_id: str
    ) -> AssistantRecord:
        """The assistant now works for another manager."""
        return await self._services.closing.hand_over(organisation_id, flow_id, new_manager_id)

    async def end(self, organisation_id: str, flow_id: str) -> AssistantRecord | None:
        """The assistant was ended, or its manager's account was closed."""
        return await self._services.closing.end(organisation_id, flow_id)
