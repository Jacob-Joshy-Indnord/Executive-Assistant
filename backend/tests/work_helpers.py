"""Shared steps for the tests of Epic 3: give the assistant work and hand back its result."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from assistant.api.router import PREFIX
from assistant.models.boundary import WorkResult
from assistant.models.records import RequestRecord
from assistant.service.work_kinds import MESSAGE_LABEL
from standins.sample import OPERATIONS_DIRECTOR, ORGANISATION_ID
from tests.world import World

MANAGER = OPERATIONS_DIRECTOR.id
SOURCE = MESSAGE_LABEL
"""The source label every chat message gives its piece of work."""


class EpicThree:
    """Mixed into a test case that has ``self.w``, a seeded world."""

    w: World

    async def forget_seeded_request(self) -> None:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            seeded = await tx.get(RequestRecord, self.w.seeded.request.id)
            if seeded is not None:
                await tx.remove(seeded)

    async def run_tools(
        self, tool_requests: list[Any], text: str = "Please help", reply: str = "Done."
    ) -> UUID:
        """The manager asks; the assistant returns ``tool_requests``. Returns the work id."""
        response = self.w.call("POST", f"{PREFIX}/chat", MANAGER, {"text": text})
        assert response.status_code == 200, response.text
        (started,) = self.w.standins.assistant.take_finished()
        await self.w.module.flow.finish_work(
            ORGANISATION_ID,
            self.w.seeded.assistant.flow_id,
            WorkResult(work_id=started.work_id, reply=reply, tool_requests=tool_requests),
        )
        return started.work_id

    async def requests(self) -> list[RequestRecord]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return await tx.find(RequestRecord, assistant_id=self.w.seeded.assistant.id)

    def work_view(self, work_id: UUID) -> Any:
        return self.w.call("GET", f"{PREFIX}/work/{work_id}", MANAGER).json()

    def waiting(self) -> Any:
        return self.w.call("GET", f"{PREFIX}/requests", MANAGER).json()


def item(**changes: Any) -> dict[str, Any]:
    """A valid ``action_item`` tool request for the Legal lead, due next Friday."""
    return {
        "tool": "action_item",
        "text": "Finish the contract review",
        "owner": "Legal lead",
        "due_date": "2026-11-27",
        "source": SOURCE,
        **changes,
    }


def draft(**changes: Any) -> dict[str, Any]:
    return {"tool": "draft", "to": "Finance lead", "text": "Please send the figures.", **changes}
