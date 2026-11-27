"""Needs your attention: the entries and the manager's decisions (Story 4.4).

The decisions are the manager's own clicks, so none needs approval (BR-6):

* Extend: the new due date applies, the owner is told and the follow-ups are set again.
  "Use the new date" is Extend to the date the owner asked for; "Keep the first date"
  keeps it, and the owner is told either way.
* Reassign: the new owner is asked to accept and the previous owner is told.
* Take it up: the manager picks a person, edits the message the module prepared and
  sends it; the entry records the decision and to whom.
* Close: with or without a reason; the owner is told.

An entry the assistant raised shows what is known and its choices; the choice is
given to the assistant in a follow-on piece of work. Each decision marks the
entry decided, once: a second press finds it decided and changes nothing.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from assistant.boundary.interfaces import Boundary
from assistant.errors import AlreadyChanged, BoundaryUnavailable, NotAllowedValue, NotFound
from assistant.models.boundary import Notice, PackInput, Person, WorkKind
from assistant.models.records import (
    ActionItem,
    AssistantRecord,
    AttentionEntry,
    AttentionState,
    ItemEvent,
)
from assistant.rules import items as rules
from assistant.rules.items import Reason
from assistant.rules.visibility import Viewer
from assistant.service.actions import Action, perform
from assistant.service.assistants import my_assistant
from assistant.service.attention import ITEM_SUBJECT, mark_decided
from assistant.service.items import ItemService, Owner
from assistant.service.notices import Tell, tell
from assistant.service.visibility import find_visible, get_visible, parse_id
from assistant.service.work import WorkService
from assistant.store.interface import Store, Transaction

DECISION_LABEL = "The manager's decision"
DECISION_INPUT_KIND = "choice"
CHOOSE_ONE = "Choose one of the choices offered."
NEW_DATE_NEEDED = "Choose the new due date."
NEW_OWNER_NEEDED = "Choose the new owner."
PERSON_NEEDED = "Choose a person in the organisation to send the message to."
NOT_SENT = "The message could not be sent, so nothing was changed."


class DecisionService:
    def __init__(
        self, boundary: Boundary, store: Store, items: ItemService, work: WorkService
    ) -> None:
        self.boundary = boundary
        self.store = store
        self.items = items
        self.work = work

    # Reading -----------------------------------------------------------------------------

    async def _item_of(self, reader: Transaction, entry: AttentionEntry) -> ActionItem | None:
        if entry.subject_kind != ITEM_SUBJECT or entry.subject_id is None:
            return None
        return await reader.get(ActionItem, UUID(entry.subject_id))

    async def _view(
        self,
        reader: Transaction,
        viewer: Viewer,
        entry: AttentionEntry,
        names: dict[str, Person | None],
    ) -> dict[str, Any]:
        """The entry with the item, the reason, its owner, the last update and the choices;
        for an entry the assistant raised, what is known and its choices."""
        view: dict[str, Any] = {
            "id": str(entry.id),
            "state": entry.state.value,
            "subject_kind": entry.subject_kind,
            "reason": entry.reason,
            "reason_code": entry.reason_code,
            "label": None,
            "known": entry.known,
            "choices": list(entry.choices),
            "asked_date": entry.asked_date.isoformat() if entry.asked_date else None,
            "decision": entry.decision,
            "decision_target_id": entry.decision_target_id,
            "decided_at": entry.decided_at.isoformat() if entry.decided_at else None,
            "created_at": entry.created_at.isoformat(),
            "item": None,
            "owner": None,
            "last_update": None,
            "prepared_message": None,
        }
        item = await self._item_of(reader, entry)
        if item is None:
            return view
        shown = await self.items.view(reader, viewer, item, names)
        reason = Reason(entry.reason_code) if entry.reason_code else None
        view.update(
            label=rules.REASON_LABELS[reason] if reason else None,
            item={
                "id": shown["id"],
                "text": shown["text"],
                "state": shown["state"],
                "due_date": shown["due_date"],
                "answer_reason": shown["answer_reason"],
            },
            owner=shown["owner"],
            last_update=shown["last_update"],
        )
        if reason is not None and rules.TAKE_IT_UP in entry.choices:
            view["prepared_message"] = rules.prepared_message(
                item.text, shown["owner"]["display_name"] or "its owner", item.due_date, reason
            )
        return view

    async def entries(self, viewer: Viewer) -> dict[str, Any]:
        """Needs your attention: the open entries, oldest first, with the count."""
        async with self.store.transaction(viewer.organisation_id) as tx:
            assistant = await my_assistant(tx, viewer)
            found = await find_visible(
                tx, viewer, AttentionEntry, assistant_id=assistant.id, state=AttentionState.OPEN
            )
            names: dict[str, Person | None] = {}
            shown = [await self._view(tx, viewer, e, names) for e in found]
        return {"count": len(shown), "entries": shown}

    async def entry(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        async with self.store.transaction(viewer.organisation_id) as tx:
            entry = await get_visible(tx, viewer, AttentionEntry, parse_id(raw_id))
            return await self._view(tx, viewer, entry, {})

    # Deciding ------------------------------------------------------------------------------

    async def decide(
        self,
        viewer: Viewer,
        raw_id: str,
        *,
        choice: str,
        due_date: date | None = None,
        owner_id: str | None = None,
        assignment_id: str | None = None,
        person_id: str | None = None,
        message: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        entry_id = parse_id(raw_id)
        async with self.store.transaction(viewer.organisation_id) as tx:
            entry = await get_visible(tx, viewer, AttentionEntry, entry_id)
            await my_assistant(tx, viewer)
        label = rules.choice_named(entry.choices, choice)
        if label is None:
            raise NotAllowedValue(CHOOSE_ONE)
        owner: Owner | None = None
        recipient: Person | None = None
        if entry.subject_kind == ITEM_SUBJECT:
            if label == rules.EXTEND and due_date is None:
                raise NotAllowedValue(NEW_DATE_NEEDED)
            if label == rules.REASSIGN:
                if not owner_id:
                    raise NotAllowedValue(NEW_OWNER_NEEDED)
                owner = await self.items.resolve_owner(viewer, owner_id, assignment_id)
            if label == rules.TAKE_IT_UP:
                problem = rules.message_refusal(message)
                if problem:
                    raise NotAllowedValue(problem)
                recipient = await self.boundary.people.person(person_id) if person_id else None
                if (
                    recipient is None
                    or recipient.organisation_id != viewer.organisation_id
                    or recipient.kind != "person"
                ):
                    raise NotAllowedValue(PERSON_NEEDED)
            if label == rules.CLOSE:
                problem = rules.reason_refusal(reason, required=False)
                if problem:
                    raise NotAllowedValue(problem)

        async def work(action: Action) -> tuple[AssistantRecord, list[Tell], bool]:
            current = await get_visible(action.tx, viewer, AttentionEntry, entry_id)
            assistant = await my_assistant(action.tx, viewer)
            if current.assistant_id != assistant.id:
                raise NotFound()
            action.assistant_id = assistant.id
            if current.state is AttentionState.DECIDED and current.decision == label:
                return assistant, [], False  # pressed twice
            if current.state is not AttentionState.OPEN:
                raise AlreadyChanged()
            if current.subject_kind != ITEM_SUBJECT:
                await self._feed_back(action, assistant, current, label)
                return assistant, [], True
            item, _ = await self.items.manager_item(action, viewer, current.subject_id or "")
            tells: list[Tell] = []
            if label in (rules.EXTEND, rules.USE_NEW_DATE):
                new_date = due_date if label == rules.EXTEND else current.asked_date
                assert new_date is not None
                tells = await self.items.extend(action, item, assistant, new_date, label)
            elif label == rules.KEEP_DATE:
                tells = await self.items.keep_date(action, item, assistant, label)
            elif label == rules.REASSIGN:
                assert owner is not None
                tells = await self.items.reassign(action, item, assistant, owner, label)
            elif label == rules.CLOSE:
                tells = await self.items.close(action, item, assistant, reason, label)
            elif label == rules.TAKE_IT_UP:
                assert recipient is not None and message is not None
                await self._take_it_up(action, item, current, recipient, message)
            return assistant, tells, False

        assistant, tells, follow_on = await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )
        await tell(self.boundary, viewer.organisation_id, tells)
        if follow_on:
            await self.work.advance(assistant)
        return await self.entry(viewer, raw_id)

    async def _take_it_up(
        self,
        action: Action,
        item: ActionItem,
        entry: AttentionEntry,
        recipient: Person,
        message: str,
    ) -> None:
        """Send the manager's message, as they edited it, with their own click: no approval.
        The entry is decided with Take it up and the person it went to."""
        await action.change(
            "assistant.item_taken_up",
            item,
            item.evolve(
                history=[*item.history, ItemEvent(at=action.now, who=action.who, event="taken_up")]
            ),
            details={"person_id": recipient.id},
        )
        await mark_decided(action, entry, rules.TAKE_IT_UP, recipient.id)
        # Recorded first (BR-9); if Flow does not deliver it, nothing is kept.
        result = await self.boundary.notices.notify(
            Notice(
                organisation_id=item.organisation_id,
                person_id=recipient.id,
                subject_kind=ITEM_SUBJECT,
                subject_id=str(item.id),
                text=message,
            )
        )
        if not result.delivered:
            raise BoundaryUnavailable(NOT_SENT)

    async def _feed_back(
        self, action: Action, assistant: AssistantRecord, entry: AttentionEntry, label: str
    ) -> None:
        """The manager's choice on an entry the assistant raised goes to the assistant in a
        follow-on piece of work, with what was known, as data."""
        await mark_decided(action, entry, label)
        content = (
            f"You raised: {entry.reason}\n"
            f"What was known: {entry.known}\n"
            f"Choices offered: {'; '.join(entry.choices)}\n"
            f"The manager chose: {label}"
        )
        await self.work.enqueue(
            action,
            assistant,
            WorkKind.FOLLOW_ON,
            asked_by=action.who,
            given_inputs=[
                PackInput(
                    label=DECISION_LABEL,
                    read_at=action.now,
                    content=content,
                    kind=DECISION_INPUT_KIND,
                    subject_id=str(entry.id),
                )
            ],
        )
