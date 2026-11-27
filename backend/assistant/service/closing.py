"""Hand-over and end (Story 2.4).

When an assistant is handed over or ended, even while paused, every handler
in :data:`CLOSING_HANDLERS` runs inside one audited transaction: items that
are not done are closed, what owners were asked is withdrawn, and working
data is removed. Confirmed statements and reports are never touched.

Each handler notes whom to tell. After the transaction commits, each of them
gets one notice that nothing more is needed. A notice that cannot be sent is
logged and not retried, so nobody is told twice.

Later services register their own handlers with :meth:`ClosingRegistry.register`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from assistant.boundary.interfaces import Boundary
from assistant.errors import ModuleError, NotAllowedValue, NotFound
from assistant.models.boundary import Notice
from assistant.models.records import (
    ActionItem,
    AssistantRecord,
    AssistantState,
    AttentionEntry,
    AttentionState,
    ChatMessage,
    FollowedProject,
    ItemEvent,
    ItemState,
    KeptResult,
    MutableRecord,
    PersonRequest,
    PersonRequestState,
    PieceOfWork,
    Preferences,
    RequestRecord,
    RequestState,
    StatementDraft,
    WorkState,
)
from assistant.service.actions import Action, perform
from assistant.service.approval_levels import ApprovalLevelService
from assistant.service.assistants import assistant_by_flow_id, current_assistant
from assistant.service.log import log_event
from assistant.service.setup import ALREADY_HAS_ONE, FLOW, SetupService
from assistant.store.interface import Store


@dataclass
class Closing:
    """What one hand-over or end is doing, shared by the handlers."""

    reason: str
    """``handed_over`` or ``ended``: recorded with each change."""
    tell: set[str] = field(default_factory=set)
    """People to tell, once each, that nothing more is needed from them."""


ClosingHandler = Callable[[Action, AssistantRecord, Closing], Awaitable[None]]


@dataclass
class ClosingRegistry:
    handlers: list[ClosingHandler] = field(default_factory=list)

    def register(self, handler: ClosingHandler) -> ClosingHandler:
        self.handlers.append(handler)
        return handler


CLOSING_HANDLERS = ClosingRegistry()


# Built-in handlers, for the records the module has today ------------------------


@CLOSING_HANDLERS.register
async def close_open_items(action: Action, assistant: AssistantRecord, closing: Closing) -> None:
    """Every item that is not done is closed; owners who were waited on are told."""
    for item in await action.find(ActionItem, assistant_id=assistant.id):
        if item.state in (ItemState.DONE, ItemState.CLOSED):
            continue
        if item.state in (ItemState.WAITING_FOR_OWNER, ItemState.OPEN):
            closing.tell.add(item.owner_id)
        event = ItemEvent(at=action.now, who=action.who, event=f"closed_{closing.reason}")
        await action.change(
            "assistant.item_closed",
            item,
            item.evolve(state=ItemState.CLOSED, history=[*item.history, event]),
            details={"because": closing.reason},
        )


@CLOSING_HANDLERS.register
async def withdraw_asks(action: Action, assistant: AssistantRecord, closing: Closing) -> None:
    """What owners were asked is withdrawn; those who had been asked are told."""
    for ask in await action.find(PersonRequest, assistant_id=assistant.id):
        if ask.state not in (PersonRequestState.TO_BE_SENT, PersonRequestState.SENT):
            continue
        if ask.state is PersonRequestState.SENT:
            closing.tell.add(ask.person_id)
        await action.change(
            "assistant.ask_withdrawn",
            ask,
            ask.evolve(state=PersonRequestState.WITHDRAWN),
            details={"because": closing.reason},
        )


@CLOSING_HANDLERS.register
async def cancel_work(action: Action, assistant: AssistantRecord, closing: Closing) -> None:
    for work in await action.find(PieceOfWork, assistant_id=assistant.id):
        if work.state in (WorkState.WAITING, WorkState.RUNNING):
            await action.change(
                "assistant.work_cancelled",
                work,
                work.evolve(state=WorkState.CANCELLED),
                details={"because": closing.reason},
            )


@CLOSING_HANDLERS.register
async def clear_attention(action: Action, assistant: AssistantRecord, closing: Closing) -> None:
    for entry in await action.find(AttentionEntry, assistant_id=assistant.id):
        if entry.state is AttentionState.OPEN:
            await action.change(
                "assistant.attention_cleared",
                entry,
                entry.evolve(state=AttentionState.CLEARED),
                details={"because": closing.reason},
            )


@CLOSING_HANDLERS.register
async def remove_working_data(action: Action, assistant: AssistantRecord, closing: Closing) -> None:
    """Preferences, followed projects, chat, waiting requests, unconfirmed statements and
    kept results go. Confirmed statements and reports stay: nothing here can remove them."""
    removable: list[tuple[str, type[MutableRecord]]] = [
        ("assistant.preferences_removed", Preferences),
        ("assistant.followed_project_removed", FollowedProject),
        ("assistant.chat_message_removed", ChatMessage),
        ("assistant.unconfirmed_statement_removed", StatementDraft),
        ("assistant.kept_result_removed", KeptResult),
    ]
    for name, record_type in removable:
        for record in await action.find(record_type, assistant_id=assistant.id):
            await action.remove(name, record, details={"because": closing.reason})
    for request in await action.find(RequestRecord, assistant_id=assistant.id):
        if request.state is RequestState.WAITING:
            await action.remove(
                "assistant.waiting_request_removed", request, details={"because": closing.reason}
            )


# The service ----------------------------------------------------------------------


@dataclass
class ClosingSummary:
    told: int = 0
    not_told: int = 0


class ClosingService:
    def __init__(
        self,
        boundary: Boundary,
        store: Store,
        setup: SetupService,
        levels: ApprovalLevelService,
        registry: ClosingRegistry = CLOSING_HANDLERS,
    ) -> None:
        self.boundary = boundary
        self.store = store
        self.setup = setup
        self.levels = levels
        self.registry = registry

    async def hand_over(
        self, organisation_id: str, flow_id: str, new_manager_id: str
    ) -> AssistantRecord:
        """Flow's Hand over: the assistant now works for another manager, and is new again.

        The current record ends (``handed_over_to``) and a new record with the same Flow
        id starts for the new manager, with the form and the starting levels. Returns it.
        """
        await self.setup.ensure_can_take_an_assistant(organisation_id, new_manager_id)
        closing = Closing("handed_over")

        async def work(action: Action) -> tuple[AssistantRecord, str | None]:
            assistant = await assistant_by_flow_id(action, flow_id)
            if assistant.manager_id == new_manager_id:
                return assistant, None  # the same call again
            theirs = await current_assistant(action, new_manager_id)
            if theirs is not None:
                raise NotAllowedValue(ALREADY_HAS_ONE)
            await self._close(action, assistant, closing)
            await action.change(
                "assistant.handed_over",
                assistant,
                assistant.evolve(state=AssistantState.ENDED, handed_over_to=new_manager_id),
                details={"previous_manager": assistant.manager_id, "new_manager": new_manager_id},
            )
            fresh = await self.setup.create(
                action, new_manager_id, assistant.display_name, assistant.flow_id
            )
            return fresh, assistant.manager_id

        handed, previous_manager = await perform(
            self.boundary, self.store, organisation_id, FLOW, work
        )
        if previous_manager is not None:
            await self._tell(handed, previous_manager, closing)
        return handed

    async def end(self, organisation_id: str, flow_id: str) -> AssistantRecord | None:
        """Flow's End: the assistant was ended, or its manager's account was closed."""
        closing = Closing("ended")

        async def work(action: Action) -> AssistantRecord | None:
            try:
                assistant = await assistant_by_flow_id(action, flow_id)
            except NotFound:
                return None  # already ended
            await self._close(action, assistant, closing)
            return await action.change(
                "assistant.ended", assistant, assistant.evolve(state=AssistantState.ENDED)
            )

        ended = await perform(self.boundary, self.store, organisation_id, FLOW, work)
        if ended is not None:
            await self._tell(ended, ended.manager_id, closing)
        return ended

    async def _close(self, action: Action, assistant: AssistantRecord, closing: Closing) -> None:
        action.assistant_id = assistant.id
        for handler in self.registry.handlers:
            await handler(action, assistant, closing)
        await self.levels.remove_all(action, assistant)

    async def _tell(
        self, assistant: AssistantRecord, manager_id: str, closing: Closing
    ) -> ClosingSummary:
        """One notice to each person who was waited on; never to virtual employees."""
        summary = ClosingSummary()
        manager = await self.boundary.people.person(manager_id)
        whose = f"the {manager.display_name}'s" if manager else "a manager's"
        text = (
            f"Nothing more is needed from you for {whose} assistant, "
            "so what you were asked has been withdrawn."
        )
        for person_id in sorted(closing.tell - {manager_id}):
            try:
                person = await self.boundary.people.person(person_id)
                if person is None or person.kind != "person":
                    continue
                result = await self.boundary.notices.notify(
                    Notice(
                        organisation_id=assistant.organisation_id,
                        person_id=person_id,
                        subject_kind="assistant",
                        subject_id=str(assistant.id),
                        text=text,
                    )
                )
                delivered = result.delivered
            except ModuleError:
                delivered = False
            if delivered:
                summary.told += 1
            else:
                summary.not_told += 1
                log_event(
                    "assistant.notice_not_delivered",
                    assistant_id=str(assistant.id),
                    person_id=person_id,
                    because=closing.reason,
                )
        return summary
