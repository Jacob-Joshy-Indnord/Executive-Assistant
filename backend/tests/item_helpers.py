"""Shared steps for the tests of Epic 4: give items, answer them and run what is due."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from assistant.api.router import PREFIX
from assistant.models.records import (
    ActionItem,
    AttentionEntry,
    AttentionState,
    PersonRequest,
    RequestRecord,
)
from assistant.service.approval_levels import LevelChange
from standins.sample import LEGAL_LEAD, OPERATIONS_DIRECTOR, ORGANISATION_ID
from tests.world import World, viewer

MANAGER = OPERATIONS_DIRECTOR.id
OWNER = LEGAL_LEAD.id
FRIDAY = "2026-11-27"


class EpicFour:
    """Mixed into a test case that has ``self.w``, a seeded world. The clock starts on
    Monday 23 November 2026 at 08:00 UTC; the managers skipped the form, so their working
    hours are 09:00 to 18:00 UTC, Monday to Friday."""

    w: World

    async def at(self, when: datetime) -> None:
        """Move the clock; nothing runs unless :meth:`run_due` is called."""
        await self.w.standins.clock.move_to(when)

    async def later(self, **delta: float) -> None:
        await self.w.standins.clock.forward(timedelta(**delta))

    async def run_due(self) -> Any:
        return await self.w.module.flow.run_due()

    def call(self, method: str, path: str, person: str, body: Any = None) -> Any:
        return self.w.call(method, f"{PREFIX}{path}", person, body)

    def ok(self, method: str, path: str, person: str, body: Any = None) -> Any:
        response = self.call(method, path, person, body)
        assert response.status_code == 200, response.text
        return response.json()

    def refused(self, method: str, path: str, person: str, body: Any = None) -> str:
        response = self.call(method, path, person, body)
        assert response.status_code == 422, response.text
        message: str = response.json()["detail"]["message"]
        return message

    def give(self, owner: str = OWNER, due: str = FRIDAY, **changes: Any) -> Any:
        """The manager gives an item."""
        body = {"text": "Review the vendor contracts", "owner_id": owner, "due_date": due}
        return self.ok("POST", "/action-items", MANAGER, {**body, **changes})

    def accept(self, item: Any, person: str = OWNER) -> Any:
        return self.ok("POST", f"/action-items/{item['id']}/accept", person)

    def update(self, item: Any, word: str, person: str = OWNER, **more: Any) -> Any:
        return self.ok(
            "POST", f"/action-items/{item['id']}/updates", person, {"word": word, **more}
        )

    def view(self, item: Any, person: str = MANAGER) -> Any:
        return self.ok("GET", f"/action-items/{item['id']}", person)

    async def set_level(self, kind: str, level: str) -> None:
        await self.w.module.services.levels.change(
            viewer(MANAGER), kind, LevelChange.model_validate({"level": level})
        )

    async def stored(self, item: Any) -> ActionItem:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            found = await tx.find(ActionItem, assistant_id=self.w.seeded.assistant.id)
        return next(i for i in found if str(i.id) == item["id"])

    async def asks(self, item: Any) -> list[PersonRequest]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return await tx.find(PersonRequest, subject_kind="action_item", subject_id=item["id"])

    async def requests_about(self, item: Any) -> list[RequestRecord]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return await tx.find(RequestRecord, subject_kind="action_item", subject_id=item["id"])

    async def entries(self, item: Any) -> list[AttentionEntry]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return await tx.find(
                AttentionEntry, subject_kind="action_item", subject_id=item["id"]
            )

    async def open_entries(self, item: Any) -> list[AttentionEntry]:
        return [e for e in await self.entries(item) if e.state is AttentionState.OPEN]

    def notices_to(self, person: str) -> list[str]:
        return [n.text for n in self.w.standins.notices.sent if n.person_id == person]

    def audited(self, name: str) -> list[Any]:
        return [r for r in self.w.standins.records.written if r.name == name]
