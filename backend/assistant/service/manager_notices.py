"""What waits for the manager (Story 6.2): the count for Flow's top bar, and notices.

When a report or an attention entry is ready, the manager gets a notice through the
Notices boundary. Those that arise together share one: everything made since the last
notice goes in the next, and the module looks after each run of what is due, each result
of the assistant, each resume, and each owner's answer. A report the manager made with
"Report now" is their own click and needs no notice. Nothing is sent while the assistant
is new or paused (BR-14); what arose meanwhile goes in the first notice after.

The notice names counts only, never what anyone wrote (BR-10), and is sent once the mark
is saved; one that cannot be delivered is logged, not retried.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from assistant.boundary.interfaces import Boundary
from assistant.errors import ModuleError
from assistant.models.records import (
    AssistantRecord,
    AssistantState,
    AttentionEntry,
    AttentionState,
    ManagerNoticeMark,
    Report,
)
from assistant.rules.visibility import Viewer
from assistant.service.actions import Action, perform
from assistant.service.assistants import my_assistant
from assistant.service.decisions import DecisionService
from assistant.service.log import log_event
from assistant.service.notices import Tell, tell
from assistant.service.pausing import running
from assistant.service.requests import RequestService
from assistant.store.interface import Store

SUBJECT = "assistant"
NOTICE_WHO = "assistant"
"""Who the mark's audit records name: the module, telling the manager."""

_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


def mark_id(assistant: AssistantRecord) -> UUID:
    return uuid5(NAMESPACE_URL, f"https://ea-001.invalid/manager-notice/{assistant.id}")


def notice_text(reports: int, entries: int) -> str:
    """One plain sentence with what is ready: counts only (BR-10)."""
    parts = []
    if reports:
        parts.append("a new status report" if reports == 1 else f"{reports} new status reports")
    if entries:
        parts.append(
            "one thing that needs your attention"
            if entries == 1
            else f"{entries} things that need your attention"
        )
    return f"Your Executive Assistant has {' and '.join(parts)}."


class ManagerNoticeService:
    def __init__(
        self,
        boundary: Boundary,
        store: Store,
        requests: RequestService,
        decisions: DecisionService,
    ) -> None:
        self.boundary = boundary
        self.store = store
        self.requests = requests
        self.decisions = decisions

    async def waiting_count(self, viewer: Viewer) -> dict[str, Any]:
        """For Flow's top bar: what waits for the manager, counted as on their page."""
        async with self.store.transaction(viewer.organisation_id) as tx:
            await my_assistant(tx, viewer)  # 404 for anyone who is not a manager
        approvals = (await self.requests.waiting(viewer))["count"]
        attention = (await self.decisions.entries(viewer))["count"]
        return {"count": approvals + attention, "approvals": approvals, "attention": attention}

    async def send_ready(self, assistant: AssistantRecord) -> bool:
        """Tell the manager, in one notice, about reports and attention entries made since
        the last notice. Returns whether a notice was delivered."""

        async def work(action: Action) -> tuple[AssistantRecord, int, int] | None:
            current = await action.get(AssistantRecord, assistant.id)
            if (
                current is None
                or current.state is not AssistantState.ACTIVE
                or not await running(action, current)
            ):
                return None
            mark = await action.get(ManagerNoticeMark, mark_id(current))
            since = mark.told_until if mark else _EPOCH
            already = set(mark.told_at_that_time) if mark else set()

            def new(record: Report | AttentionEntry) -> bool:
                return record.created_at > since or (
                    record.created_at == since and str(record.id) not in already
                )

            reports = [
                r
                for r in await action.find(Report, assistant_id=current.id)
                if r.made_by == "schedule" and new(r)
            ]
            entries = [
                e
                for e in await action.find(
                    AttentionEntry, assistant_id=current.id, state=AttentionState.OPEN
                )
                if new(e)
            ]
            if not reports and not entries:
                return None
            at_now = {str(r.id) for r in [*reports, *entries] if r.created_at == action.now}
            if since == action.now:
                at_now |= already
            told = sorted(at_now)
            details = {"reports": len(reports), "attention_entries": len(entries)}
            if mark is None:
                await action.add(
                    "assistant.manager_told",
                    ManagerNoticeMark(
                        id=mark_id(current),
                        organisation_id=current.organisation_id,
                        assistant_id=current.id,
                        created_at=action.now,
                        told_until=action.now,
                        told_at_that_time=told,
                    ),
                    details=details,
                )
            else:
                await action.change(
                    "assistant.manager_told",
                    mark,
                    mark.evolve(told_until=action.now, told_at_that_time=told),
                    details=details,
                )
            return current, len(reports), len(entries)

        try:
            found = await perform(
                self.boundary,
                self.store,
                assistant.organisation_id,
                NOTICE_WHO,
                work,
                assistant.id,
            )
        except ModuleError:  # another process told them first, or the mark was not saved
            log_event("assistant.manager_not_told", assistant_id=str(assistant.id))
            return False
        if found is None:
            return False
        current, reports, entries = found
        delivered = await tell(
            self.boundary,
            current.organisation_id,
            [Tell(current.manager_id, SUBJECT, str(current.id), notice_text(reports, entries))],
        )
        return delivered == 1

    async def after(self, assistant: AssistantRecord) -> None:
        """The hook run after each run of what is due, each result and each resume."""
        await self.send_ready(assistant)

    async def send_ready_in(self, organisation_id: str) -> None:
        """After an owner's answer: tell each manager of the organisation what became ready."""
        async with self.store.transaction(organisation_id) as tx:
            assistants = await tx.find(AssistantRecord, state=AssistantState.ACTIVE)
        for assistant in assistants:
            await self.send_ready(assistant)
