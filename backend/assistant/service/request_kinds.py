"""Kinds of request, registered in one place (Story 3.3, Story 3.4).

Each kind says which approval level applies, whether the manager may edit its
text, whether it is a request to an owner (for "Approve all"), what the module
does as its own part when it is carried out, and whether and how that part is
undone. Flow carries out the rest, for example delivering the notice
(section 9.1). Part 2 and later epics add kinds with :func:`register`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from assistant.models.boundary import ActionKind
from assistant.models.records import (
    ACTION_ITEM_LIMIT,
    MESSAGE_LIMIT,
    ActionItem,
    AssistantRecord,
    DraftState,
    ItemEvent,
    ItemState,
    MutableRecord,
    PersonRequest,
    PersonRequestState,
    RequestRecord,
    StatementDraft,
)
from assistant.rules.working_time import answer_by
from assistant.service.actions import Action
from assistant.service.setup import settings_for
from assistant.store.interface import Transaction

CarryOut = Callable[[Action, AssistantRecord, RequestRecord, str | None], Awaitable[dict[str, Any]]]
"""The module's own part. Returns changes to keep on the request, e.g. its new subject."""
CanUndo = Callable[[Transaction | Action, RequestRecord], Awaitable[bool]]
Undo = Callable[[Action, RequestRecord], Awaitable[None]]

MESSAGE = "message"
ACTION_ITEM = "action_item"
ITEM_CHANGE = "action_item_change"
UPDATE_ASK = "update_ask"
STATEMENT_CONFIRM = "statement_confirm"

Asks = Literal["accept", "update", "status", "confirm"]


@dataclass(frozen=True)
class RequestKind:
    name: str
    action_kind: ActionKind
    label: str
    """What the manager's list calls it, e.g. "Message"."""
    carry_out: CarryOut
    editable: bool = True
    text_limit: int | None = None
    to_owner: bool = False
    """A request to an owner: "Approve all" covers it."""
    subject_type: type[MutableRecord] | None = None
    """The record the request is about; when it is done, closed or changed, the waiting
    request is withdrawn."""
    can_undo: CanUndo | None = None
    undo: Undo | None = None


REQUEST_KINDS: dict[str, RequestKind] = {}


def register(kind: RequestKind) -> RequestKind:
    if kind.name in REQUEST_KINDS:
        raise ValueError(f"request kind {kind.name!r} is registered twice")
    REQUEST_KINDS[kind.name] = kind
    return kind


def kind_of(request: RequestRecord) -> RequestKind:
    return REQUEST_KINDS[request.request_kind]


async def subject_has_moved_on(reader: Transaction | Action, request: RequestRecord) -> bool:
    """Whether the request's subject is gone, or done, closed or changed since it was asked."""
    kind = kind_of(request)
    subject_type = kind.subject_type
    if subject_type is None or request.subject_id is None:
        return False
    if request.subject_kind != subject_type.kind:
        return False  # e.g. an update asked about a project, which lives in Flow
    try:
        subject_id = UUID(request.subject_id)
    except ValueError:
        return True
    subject = await reader.get(subject_type, subject_id)
    return subject is None or subject.version != request.subject_version


# The module's own parts --------------------------------------------------------------


async def _ask(
    action: Action,
    assistant: AssistantRecord,
    person_id: str,
    asks: Asks,
    subject_kind: str,
    subject_id: str,
) -> PersonRequest:
    settings = await settings_for(action, assistant)
    return await action.add(
        "assistant.ask_sent",
        PersonRequest(
            organisation_id=assistant.organisation_id,
            assistant_id=assistant.id,
            created_at=action.now,
            person_id=person_id,
            asks=asks,
            subject_kind=subject_kind,
            subject_id=subject_id,
            answer_by=answer_by(action.now, settings),
            sent_at=action.now,
            state=PersonRequestState.SENT,
        ),
        details={"asks": asks, "subject_kind": subject_kind},
    )


async def _ask_of(reader: Transaction | Action, request: RequestRecord) -> PersonRequest | None:
    ask_id = request.payload.get("ask_id")
    return await reader.get(PersonRequest, UUID(ask_id)) if ask_id else None


async def _ask_unanswered(reader: Transaction | Action, request: RequestRecord) -> bool:
    """Undo stays possible until the person has answered."""
    ask = await _ask_of(reader, request)
    return ask is not None and ask.state is PersonRequestState.SENT


async def _withdraw_ask(action: Action, request: RequestRecord) -> None:
    ask = await _ask_of(action, request)
    if ask is not None and ask.state is PersonRequestState.SENT:
        await action.change(
            "assistant.ask_withdrawn",
            ask,
            ask.evolve(state=PersonRequestState.WITHDRAWN),
            details={"because": "undone"},
        )


async def send_message(
    action: Action, assistant: AssistantRecord, request: RequestRecord, text: str | None
) -> dict[str, Any]:
    """Nothing to keep here: Flow delivers the message."""
    return {}


async def add_item(
    action: Action, assistant: AssistantRecord, request: RequestRecord, text: str | None
) -> dict[str, Any]:
    """Add the item, waiting for its owner, and ask the owner to accept it. An item for the
    manager themselves is open at once: no one is asked and it has no follow-ups."""
    payload = request.payload
    assert request.target_id is not None and text is not None
    own = request.target_id == assistant.manager_id
    chosen = "follow_up_times" in payload and not own
    item = await action.add(
        "assistant.item_added",
        ActionItem(
            organisation_id=assistant.organisation_id,
            assistant_id=assistant.id,
            created_at=action.now,
            text=text,
            owner_id=request.target_id,
            due_date=date.fromisoformat(payload["due_date"]),
            follow_up_times=(
                [datetime.fromisoformat(t) for t in payload["follow_up_times"]] if chosen else []
            ),
            follow_ups_set=chosen,
            origin="chat",
            accepted_at=action.now if own else None,
            state=ItemState.OPEN if own else ItemState.WAITING_FOR_OWNER,
            history=[ItemEvent(at=action.now, who=action.who, event="added")],
        ),
        details={"request_id": str(request.id)},
    )
    if own:
        return {
            "subject_kind": "action_item",
            "subject_id": str(item.id),
            "subject_version": item.version,
        }
    ask = await _ask(action, assistant, item.owner_id, "accept", "action_item", str(item.id))
    return {
        "subject_kind": "action_item",
        "subject_id": str(item.id),
        "subject_version": item.version,
        "payload": {**payload, "ask_id": str(ask.id)},
    }


async def _item_undoable(reader: Transaction | Action, request: RequestRecord) -> bool:
    """An added item can be undone until its owner has answered."""
    if request.subject_id is None:
        return False
    item = await reader.get(ActionItem, UUID(request.subject_id))
    if item is None or item.state is not ItemState.WAITING_FOR_OWNER:
        return False
    return await _ask_unanswered(reader, request)


async def _undo_item(action: Action, request: RequestRecord) -> None:
    assert request.subject_id is not None
    item = await action.get(ActionItem, UUID(request.subject_id))
    if item is not None:
        event = ItemEvent(at=action.now, who=action.who, event="undone")
        await action.change(
            "assistant.item_closed",
            item,
            item.evolve(state=ItemState.CLOSED, history=[*item.history, event]),
            details={"because": "undone"},
        )
    await _withdraw_ask(action, request)


async def change_item(
    action: Action, assistant: AssistantRecord, request: RequestRecord, text: str | None
) -> dict[str, Any]:
    """Change the item's text or follow-up times; never its date or owner (BR-1)."""
    assert request.subject_id is not None
    item = await action.get(ActionItem, UUID(request.subject_id))
    assert item is not None
    changes: dict[str, Any] = {}
    if text is not None:
        changes["text"] = text
    if "follow_up_times" in request.payload:
        changes["follow_up_times"] = [
            datetime.fromisoformat(t) for t in request.payload["follow_up_times"]
        ]
    event = ItemEvent(at=action.now, who=action.who, event="changed")
    changed = await action.change(
        "assistant.item_changed",
        item,
        item.evolve(**changes, history=[*item.history, event]),
        details={"request_id": str(request.id)},
    )
    return {"subject_version": changed.version}


async def ask_for_update(
    action: Action, assistant: AssistantRecord, request: RequestRecord, text: str | None
) -> dict[str, Any]:
    """Ask the owner for an update on an item, or for a project's status."""
    assert request.target_id is not None and request.subject_id is not None
    asks: Asks = "update" if request.subject_kind == "action_item" else "status"
    ask = await _ask(
        action, assistant, request.target_id, asks, request.subject_kind, request.subject_id
    )
    return {"payload": {**request.payload, "ask_id": str(ask.id)}}


async def give_draft_to_owner(action: Action, draft: StatementDraft) -> StatementDraft:
    """The draft goes to its owner. An older one of the project still unanswered is marked
    replaced, and what its owner was asked about it is withdrawn (Story 5.2)."""
    for older in await action.find(
        StatementDraft, assistant_id=draft.assistant_id, project_id=draft.project_id
    ):
        if older.id == draft.id or older.state is not DraftState.WITH_OWNER:
            continue
        await action.change(
            "assistant.statement_draft_replaced", older, older.evolve(state=DraftState.REPLACED)
        )
        for ask in await action.find(
            PersonRequest, subject_kind="statement_draft", subject_id=str(older.id)
        ):
            if ask.state in (PersonRequestState.TO_BE_SENT, PersonRequestState.SENT):
                await action.change(
                    "assistant.ask_withdrawn",
                    ask,
                    ask.evolve(state=PersonRequestState.WITHDRAWN),
                    details={"because": "replaced"},
                )
    return await action.change(
        "assistant.statement_given_to_owner", draft, draft.evolve(state=DraftState.WITH_OWNER)
    )


async def ask_to_confirm(
    action: Action, assistant: AssistantRecord, request: RequestRecord, text: str | None
) -> dict[str, Any]:
    """Give the draft to its owner and ask them to confirm or correct it (BR-2)."""
    assert request.target_id is not None and request.subject_id is not None
    draft = await action.get(StatementDraft, UUID(request.subject_id))
    assert draft is not None
    await give_draft_to_owner(action, draft)
    ask = await _ask(
        action, assistant, request.target_id, "confirm", "statement_draft", request.subject_id
    )
    return {"payload": {**request.payload, "ask_id": str(ask.id)}}


async def _undo_confirm(action: Action, request: RequestRecord) -> None:
    assert request.subject_id is not None
    draft = await action.get(StatementDraft, UUID(request.subject_id))
    if draft is not None and draft.state is DraftState.WITH_OWNER:
        await action.change(
            "assistant.statement_taken_back", draft, draft.evolve(state=DraftState.DRAFT)
        )
    await _withdraw_ask(action, request)


register(
    RequestKind(MESSAGE, ActionKind.SEND_MESSAGE, "Message", send_message, text_limit=MESSAGE_LIMIT)
)
register(
    RequestKind(
        ACTION_ITEM,
        ActionKind.ADD_ACTION_ITEM,
        "Item",
        add_item,
        text_limit=ACTION_ITEM_LIMIT,
        can_undo=_item_undoable,
        undo=_undo_item,
    )
)
register(
    RequestKind(
        ITEM_CHANGE,
        ActionKind.ADD_ACTION_ITEM,
        "Item change",
        change_item,
        text_limit=ACTION_ITEM_LIMIT,
        subject_type=ActionItem,
    )
)
register(
    RequestKind(
        UPDATE_ASK,
        ActionKind.ASK_OWNER,
        "Update",
        ask_for_update,
        text_limit=MESSAGE_LIMIT,
        to_owner=True,
        subject_type=ActionItem,
        can_undo=_ask_unanswered,
        undo=_withdraw_ask,
    )
)
register(
    RequestKind(
        STATEMENT_CONFIRM,
        ActionKind.ASK_OWNER,
        "Confirm status",
        ask_to_confirm,
        editable=False,
        to_owner=True,
        subject_type=StatementDraft,
        can_undo=_ask_unanswered,
        undo=_undo_confirm,
    )
)
