"""Due steps for action items (Stories 4.1, 4.2, 4.4, 4.5).

Each kind is registered with the module's due-step runner, so each step is
claimed and happens once, however often, late or in how many places the
schedule runs (BR-13), and nothing is done for a new or paused assistant
(BR-14).

* ``send_asks``: asks that waited for working hours or the day's notices go
  out; those to one owner that are due together share one notice.
* ``follow_up``: at each follow-up time the owner is asked for an update
  through the approval check, unless they gave one in the last working day.
  When asking owners is not allowed, nothing is sent and the item says so.
  Outside working hours or over the day's notices, the follow-up waits.
* ``item_overdue`` and ``ask_unanswered``: raise the item's attention entry.
* ``owner_left``: raises the entry when an owner is no longer in the organisation.
* ``assignment_state``: reads the linked assignments on every run and shows a
  change on the item.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from assistant.errors import BoundaryUnavailable, ModuleError
from assistant.models.boundary import ActionKind, ApprovalLevel, Person
from assistant.models.records import (
    ActionItem,
    ItemEvent,
    ItemState,
    PersonRequest,
    PersonRequestState,
    RequestState,
    Update,
)
from assistant.rules import items as rules
from assistant.rules.follow_ups import (
    in_working_hours,
    next_working_start,
    one_working_day_before,
    overdue_at,
    sendable_at,
)
from assistant.rules.items import Reason
from assistant.rules.working_time import answer_by
from assistant.service.actions import Action
from assistant.service.approval_levels import level_for
from assistant.service.attention import raise_for_item
from assistant.service.due import DUE_STEPS, DueContext, DueStep
from assistant.service.items import SUBJECT, end_asks, mirror_assignment, notify_asks
from assistant.service.log import log_event
from assistant.service.notices import may_notify
from assistant.service.request_kinds import UPDATE_ASK
from assistant.service.requests import RequestService
from assistant.store.interface import Transaction

WAITING = (ItemState.WAITING_FOR_OWNER, ItemState.OPEN)


def _later(now: datetime, ctx: DueContext) -> datetime:
    """When something that may not go out now is tried again: the start of working hours,
    or the next working day when today's notices are used up."""
    if not in_working_hours(now, ctx.settings):
        return sendable_at(now, ctx.settings)
    return next_working_start(now, ctx.settings)


def _earliest_after(times: list[datetime], after: datetime) -> datetime | None:
    upcoming = [t for t in times if t > after]
    return min(upcoming) if upcoming else None


def the(person: Person | None) -> str:
    return f"the {person.display_name}" if person else "a person"


# Asks that waited ---------------------------------------------------------------------


async def _still_wanted(reader: Transaction | Action, ask: PersonRequest) -> bool:
    if ask.subject_kind != SUBJECT or ask.asks != "accept":
        return True
    item = await reader.get(ActionItem, UUID(ask.subject_id))
    return (
        item is not None
        and item.state is ItemState.WAITING_FOR_OWNER
        and item.owner_id == ask.person_id
    )


class SendAsks:
    name = "send_asks"

    async def _waiting(self, reader: Transaction, ctx: DueContext) -> list[PersonRequest]:
        return await reader.find(
            PersonRequest, assistant_id=ctx.assistant.id, state=PersonRequestState.TO_BE_SENT
        )

    async def due(self, reader: Transaction, ctx: DueContext, until: datetime) -> list[DueStep]:
        steps = []
        for ask in await self._waiting(reader, ctx):
            at = ask.send_after or ask.created_at
            if at <= until:
                steps.append(DueStep(str(ask.id), at))
        return steps

    async def next_after(
        self, reader: Transaction, ctx: DueContext, after: datetime
    ) -> datetime | None:
        times = [a.send_after or a.created_at for a in await self._waiting(reader, ctx)]
        return _earliest_after(times, after)

    async def run(self, action: Action, ctx: DueContext, step: DueStep) -> None:
        ask = await action.get(PersonRequest, UUID(step.subject))
        if ask is None or ask.state is not PersonRequestState.TO_BE_SENT:
            return  # sent with another, answered or withdrawn meanwhile
        if not await _still_wanted(action, ask):
            await action.change(
                "assistant.ask_withdrawn",
                ask,
                ask.evolve(state=PersonRequestState.WITHDRAWN),
                details={"because": "subject_changed"},
            )
            return
        now = action.now
        if not await may_notify(action, ctx.assistant, ask.person_id, now, ctx.settings):
            await action.change(
                "assistant.ask_deferred", ask, ask.evolve(send_after=_later(now, ctx))
            )
            return
        together = [
            a
            for a in await action.find(
                PersonRequest,
                assistant_id=ctx.assistant.id,
                person_id=ask.person_id,
                state=PersonRequestState.TO_BE_SENT,
            )
            if (a.send_after or a.created_at) <= now and await _still_wanted(action, a)
        ]
        sent = await notify_asks(
            action, ctx.boundary, ctx.assistant, ask.person_id, len(together)
        )
        if not sent:
            raise BoundaryUnavailable()  # not sent: the step is tried again on the next run
        for waiting in together:
            await action.change(
                "assistant.ask_sent",
                waiting,
                waiting.evolve(
                    state=PersonRequestState.SENT,
                    sent_at=now,
                    send_after=None,
                    answer_by=answer_by(now, ctx.settings),
                ),
                details={"asks": waiting.asks, "together": len(together)},
            )


# Follow-ups ----------------------------------------------------------------------------


def _followed(item: ActionItem, ctx: DueContext) -> bool:
    """Follow-ups go to owners who are asked: not the manager, not a virtual employee."""
    return (
        item.state is ItemState.OPEN
        and item.owner_id != ctx.assistant.manager_id
        and item.assignment_id is None
    )


def _follow_up_times(item: ActionItem) -> list[datetime]:
    times = list(item.follow_up_times)
    if item.follow_up_waiting_until is not None:
        times.append(item.follow_up_waiting_until)
    return times


def follow_up_question(item: ActionItem) -> str:
    return (
        f'How is "{item.text}" going? Please give an update: On track, Done or Blocked, '
        "with a note if you like."
    )


class FollowUps:
    name = "follow_up"

    async def _items(self, reader: Transaction, ctx: DueContext) -> list[ActionItem]:
        found = await reader.find(ActionItem, assistant_id=ctx.assistant.id, state=ItemState.OPEN)
        return [i for i in found if _followed(i, ctx)]

    async def due(self, reader: Transaction, ctx: DueContext, until: datetime) -> list[DueStep]:
        steps = []
        for item in await self._items(reader, ctx):
            reached = [t for t in _follow_up_times(item) if t <= until]
            if reached:  # only the latest: missed follow-ups are sent once, not each
                steps.append(DueStep(str(item.id), max(reached)))
        return steps

    async def next_after(
        self, reader: Transaction, ctx: DueContext, after: datetime
    ) -> datetime | None:
        times = [t for i in await self._items(reader, ctx) for t in _follow_up_times(i)]
        return _earliest_after(times, after)

    async def run(self, action: Action, ctx: DueContext, step: DueStep) -> None:
        item = await action.get(ActionItem, UUID(step.subject))
        if item is None or not _followed(item, ctx):
            return  # done, closed or given to someone else meanwhile
        now = action.now
        since = one_working_day_before(now, ctx.settings)
        updates = await action.find(Update, item_id=item.id)
        if any(u.created_at >= since and u.author_id == item.owner_id for u in updates):
            await self._note(action, item, "follow_up_skipped", note=item.follow_up_note)
            return
        level, _ = await level_for(action, ctx.assistant, ActionKind.ASK_OWNER)
        if level is ApprovalLevel.NOT_ALLOWED:
            await self._note(action, item, "follow_up_not_allowed", note=rules.ASKING_NOT_ALLOWED)
            return
        if not await may_notify(action, ctx.assistant, item.owner_id, now, ctx.settings):
            await self._note(
                action, item, "follow_up_deferred", note=None, waiting=_later(now, ctx)
            )
            return
        item = await self._note(action, item, "follow_up", note=None)
        owner = await ctx.boundary.people.person(item.owner_id)
        request = await RequestService(ctx.boundary, ctx.store).propose(
            action,
            ctx.assistant,
            request_kind=UPDATE_ASK,
            effect=f"Ask {the(owner)} for an update on an action item.",
            subject_kind=SUBJECT,
            subject_id=str(item.id),
            subject_version=item.version,
            target_id=item.owner_id,
            text=follow_up_question(item),
            payload={"follow_up": True},
        )
        if request.state is RequestState.DECLINED:
            await self._note(action, item, "follow_up_declined", note=request.reason)

    @staticmethod
    async def _note(
        action: Action,
        item: ActionItem,
        name: str,
        note: str | None,
        waiting: datetime | None = None,
    ) -> ActionItem:
        """Keep on the item what happened to its follow-up, so the item says so."""
        return await action.change(
            f"assistant.item_{name}",
            item,
            item.evolve(
                follow_up_note=note,
                follow_up_waiting_until=waiting,
                history=[*item.history, ItemEvent(at=action.now, who=action.who, event=name)],
            ),
        )


# Attention triggers ---------------------------------------------------------------------


class Overdue:
    """An item that is not done at the end of its due date (section 8.1: or that many
    working days later) raises its attention entry."""

    name = "item_overdue"

    async def _items(self, reader: Transaction, ctx: DueContext) -> list[ActionItem]:
        found = await reader.find(ActionItem, assistant_id=ctx.assistant.id)
        return [i for i in found if i.state in WAITING]

    async def due(self, reader: Transaction, ctx: DueContext, until: datetime) -> list[DueStep]:
        steps = []
        for item in await self._items(reader, ctx):
            at = overdue_at(item.due_date, ctx.settings)
            if at <= until:
                steps.append(DueStep(str(item.id), at))
        return steps

    async def next_after(
        self, reader: Transaction, ctx: DueContext, after: datetime
    ) -> datetime | None:
        times = [overdue_at(i.due_date, ctx.settings) for i in await self._items(reader, ctx)]
        return _earliest_after(times, after)

    async def run(self, action: Action, ctx: DueContext, step: DueStep) -> None:
        item = await action.get(ActionItem, UUID(step.subject))
        if item is None or item.state not in WAITING:
            return
        if overdue_at(item.due_date, ctx.settings) != step.due_at:
            return  # extended meanwhile: the new date has its own step
        await raise_for_item(action, item, Reason.OVERDUE)


class Unanswered:
    """A request about an item that is not answered in time raises its attention entry."""

    name = "ask_unanswered"

    async def _asks(self, reader: Transaction, ctx: DueContext) -> list[PersonRequest]:
        return await reader.find(
            PersonRequest,
            assistant_id=ctx.assistant.id,
            subject_kind=SUBJECT,
            state=PersonRequestState.SENT,
        )

    async def due(self, reader: Transaction, ctx: DueContext, until: datetime) -> list[DueStep]:
        return [
            DueStep(str(a.id), a.answer_by)
            for a in await self._asks(reader, ctx)
            if a.answer_by <= until
        ]

    async def next_after(
        self, reader: Transaction, ctx: DueContext, after: datetime
    ) -> datetime | None:
        return _earliest_after([a.answer_by for a in await self._asks(reader, ctx)], after)

    async def run(self, action: Action, ctx: DueContext, step: DueStep) -> None:
        ask = await action.get(PersonRequest, UUID(step.subject))
        if ask is None or ask.state is not PersonRequestState.SENT:
            return
        item = await action.get(ActionItem, UUID(ask.subject_id))
        if item is None or item.state not in WAITING or item.owner_id != ask.person_id:
            return
        await raise_for_item(action, item, Reason.UNANSWERED)


class OwnersLeft:
    """An item whose owner is no longer in the organisation raises its attention entry,
    once for each owner, and what they were asked is withdrawn."""

    name = "owner_left"

    async def due(self, reader: Transaction, ctx: DueContext, until: datetime) -> list[DueStep]:
        steps = []
        known: dict[str, bool] = {}
        for item in await reader.find(ActionItem, assistant_id=ctx.assistant.id):
            if item.state not in rules.UNFINISHED or item.owner_id == ctx.assistant.manager_id:
                continue
            if item.owner_id not in known:
                try:
                    known[item.owner_id] = await self._left(ctx, item.owner_id)
                except ModuleError:
                    return []  # Flow could not be reached: looked at again on the next run
            if known[item.owner_id]:
                steps.append(DueStep(f"{item.id}/{item.owner_id}", item.created_at))
        return steps

    @staticmethod
    async def _left(ctx: DueContext, person_id: str) -> bool:
        person = await ctx.boundary.people.person(person_id)
        return person is None or person.organisation_id != ctx.assistant.organisation_id

    async def next_after(
        self, reader: Transaction, ctx: DueContext, after: datetime
    ) -> datetime | None:
        return None

    async def run(self, action: Action, ctx: DueContext, step: DueStep) -> None:
        item_id, owner_id = step.subject.split("/", 1)
        item = await action.get(ActionItem, UUID(item_id))
        if item is None or item.state not in rules.UNFINISHED or item.owner_id != owner_id:
            return
        if not await self._left(ctx, owner_id):
            return
        await end_asks(action, item, PersonRequestState.WITHDRAWN, "owner_left")
        await raise_for_item(action, item, Reason.OWNER_LEFT)


class FollowAssignments:
    """Every run reads the manager's assignments; a linked item whose assignment changed, or
    can no longer be found, gets one step that shows it (Story 4.5)."""

    name = "assignment_state"

    async def _states(self, ctx: DueContext) -> dict[str, str]:
        found = await ctx.boundary.work_data.assignments(ctx.assistant.manager_id)
        return {a.id: a.state for a in found}

    async def due(self, reader: Transaction, ctx: DueContext, until: datetime) -> list[DueStep]:
        found = await reader.find(ActionItem, assistant_id=ctx.assistant.id, state=ItemState.OPEN)
        linked = [i for i in found if i.assignment_id is not None]
        if not linked:
            return []
        try:
            states = await self._states(ctx)
        except ModuleError:
            log_event("assistant.assignments_not_read", assistant_id=str(ctx.assistant.id))
            return []
        steps = []
        for item in linked:
            state = states.get(item.assignment_id or "", rules.MISSING)
            if state != item.assignment_state:
                # The item's version makes each change its own step, claimed once.
                steps.append(DueStep(f"{item.id}/{item.version}/{state}", item.created_at))
        return steps

    async def next_after(
        self, reader: Transaction, ctx: DueContext, after: datetime
    ) -> datetime | None:
        return None

    async def run(self, action: Action, ctx: DueContext, step: DueStep) -> None:
        item_id, version, _ = step.subject.split("/", 2)
        item = await action.get(ActionItem, UUID(item_id))
        if item is None or item.state is not ItemState.OPEN or str(item.version) != version:
            return
        state = (await self._states(ctx)).get(item.assignment_id or "", rules.MISSING)
        await mirror_assignment(action, item, state)


DUE_STEPS.register(SendAsks())
DUE_STEPS.register(FollowUps())
DUE_STEPS.register(Overdue())
DUE_STEPS.register(Unanswered())
DUE_STEPS.register(OwnersLeft())
DUE_STEPS.register(FollowAssignments())
