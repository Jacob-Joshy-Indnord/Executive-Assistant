"""Due-step kinds used only by the tests of the runner."""

from __future__ import annotations

import threading
from datetime import datetime, time, timedelta
from uuid import UUID

from assistant.errors import BoundaryUnavailable
from assistant.models.records import ActionItem, ItemEvent, ItemState
from assistant.service.actions import Action
from assistant.service.due import DueContext, DueStep
from assistant.store.interface import Transaction


class DailyAt:
    """Once a day at a fixed UTC time; missed days coalesce into the latest."""

    def __init__(self, name: str = "daily_check", at: time = time(9, 0)) -> None:
        self.name = name
        self.at = at
        self.ran: list[tuple[str, datetime, datetime]] = []
        self.fail_next = 0
        self._lock = threading.Lock()

    def _occurrence_on_or_before(self, when: datetime) -> datetime:
        today = datetime.combine(when.date(), self.at, tzinfo=when.tzinfo)
        return today if today <= when else today - timedelta(days=1)

    async def due(self, reader: Transaction, ctx: DueContext, until: datetime) -> list[DueStep]:
        latest = self._occurrence_on_or_before(until)
        since = ctx.assistant.manager_since or ctx.assistant.created_at
        return [DueStep("daily", latest)] if latest >= since else []

    async def next_after(
        self, reader: Transaction, ctx: DueContext, after: datetime
    ) -> datetime | None:
        return self._occurrence_on_or_before(after) + timedelta(days=1)

    async def run(self, action: Action, ctx: DueContext, step: DueStep) -> None:
        if self.fail_next:
            self.fail_next -= 1
            raise BoundaryUnavailable()
        with self._lock:
            self.ran.append((str(ctx.assistant.id), step.due_at, action.now))


class CheckOpenItems:
    """One step for each open action item, due when the item was made; checking an item
    adds a line to its history."""

    name = "check_open_items"

    async def due(self, reader: Transaction, ctx: DueContext, until: datetime) -> list[DueStep]:
        items = await reader.find(ActionItem, assistant_id=ctx.assistant.id, state=ItemState.OPEN)
        return [DueStep(str(i.id), i.created_at) for i in items if i.created_at <= until]

    async def next_after(
        self, reader: Transaction, ctx: DueContext, after: datetime
    ) -> datetime | None:
        return None

    async def run(self, action: Action, ctx: DueContext, step: DueStep) -> None:
        item = await action.get(ActionItem, UUID(step.subject))
        assert item is not None
        event = ItemEvent(at=action.now, who=action.who, event="checked")
        await action.change(
            "assistant.item_checked", item, item.evolve(history=[*item.history, event])
        )
