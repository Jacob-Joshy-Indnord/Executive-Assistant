"""Action items: giving, answering, updating and deciding (Stories 4.1, 4.3, 4.4, 4.5).

The manager gives an item with their own click, so asking its owner to accept
needs no approval (section 8.2); the ask goes out at once when it may (working
hours and the day's notices), otherwise it waits for the "send asks" step. An
item the manager gives themselves is open at once, with no follow-ups; one
given to a virtual employee is linked to the assignment the manager chooses and
follows it, so no one is asked.

Owners answer their own items only: accept, propose another date or decline,
then give updates in one step. Their words are shown word for word (BR-16).
The manager's controls (follow-up times, extend, reassign, close, mark their
own done) are their own decisions: no approval, an audit record before each
change, the owner told once the change is saved, and the item's open attention
entry marked decided.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from assistant.boundary.interfaces import Boundary
from assistant.errors import NotAllowedValue, NotFound
from assistant.models.boundary import AssignmentRecord, Notice, Person
from assistant.models.records import (
    ActionItem,
    AssistantRecord,
    AssistantState,
    ItemEvent,
    ItemState,
    PersonRequest,
    PersonRequestState,
    Update,
)
from assistant.rules import items as rules
from assistant.rules.follow_ups import follow_up_times, sendable_at
from assistant.rules.items import Reason
from assistant.rules.settings import Settings
from assistant.rules.visibility import Viewer
from assistant.rules.working_time import answer_by, local_date
from assistant.service.actions import Action, perform
from assistant.service.assistants import my_assistant
from assistant.service.attention import (
    clear_for_item,
    decide_for_item,
    open_entry,
    raise_for_item,
)
from assistant.service.notices import Tell, may_notify, tell
from assistant.service.pausing import running
from assistant.service.requests import RequestService
from assistant.service.setup import settings_for
from assistant.service.signals import item_ended
from assistant.service.visibility import find_visible, get_visible, parse_id
from assistant.store.interface import Store, Transaction

SUBJECT = "action_item"
OPEN_ASKS = (PersonRequestState.TO_BE_SENT, PersonRequestState.SENT)


def event(action: Action, name: str) -> ItemEvent:
    return ItemEvent(at=action.now, who=action.who, event=name)


def _clean(text: str | None) -> str | None:
    return text.strip() if text is not None and text.strip() else None


def _refuse(problem: str | None) -> None:
    if problem:
        raise NotAllowedValue(problem)


@dataclass(frozen=True)
class Owner:
    """Who an item is given to, checked against the boundary before the action."""

    person: Person
    assignment: AssignmentRecord | None = None


# Asks ---------------------------------------------------------------------------------


def ask_notice_text(manager: Person | None, count: int = 1) -> str:
    whose = f"The {manager.display_name}'s assistant" if manager else "An assistant"
    if count > 1:
        return f"{whose} has {count} requests for you. Open them to answer."
    return f"{whose} has a request for you. Open it to answer."


async def notify_asks(
    action: Action, boundary: Boundary, assistant: AssistantRecord, person_id: str, count: int
) -> bool:
    """One notice for the asks that go out together. ``False`` when Flow did not deliver it."""
    manager = await boundary.people.person(assistant.manager_id)
    result = await boundary.notices.notify(
        Notice(
            organisation_id=assistant.organisation_id,
            person_id=person_id,
            subject_kind="assistant",
            subject_id=str(assistant.id),
            text=ask_notice_text(manager, count),
        )
    )
    return result.delivered


async def ask_to_accept(
    action: Action,
    boundary: Boundary,
    assistant: AssistantRecord,
    item: ActionItem,
    settings: Settings,
) -> PersonRequest:
    """Ask the owner to accept, at once when it may go out now; otherwise it waits."""
    now = action.now
    sent = False
    if await running(action, assistant) and await may_notify(
        action, assistant, item.owner_id, now, settings
    ):
        sent = await notify_asks(action, boundary, assistant, item.owner_id, 1)
    send_after = now if sent or not await running(action, assistant) else sendable_at(now, settings)
    return await action.add(
        "assistant.ask_sent" if sent else "assistant.ask_waiting",
        PersonRequest(
            organisation_id=assistant.organisation_id,
            assistant_id=assistant.id,
            created_at=now,
            person_id=item.owner_id,
            asks="accept",
            subject_kind=SUBJECT,
            subject_id=str(item.id),
            answer_by=answer_by(send_after, settings),
            send_after=None if sent else send_after,
            sent_at=now if sent else None,
            state=PersonRequestState.SENT if sent else PersonRequestState.TO_BE_SENT,
        ),
        details={"asks": "accept", "subject_kind": SUBJECT},
    )


async def end_asks(
    action: Action,
    item: ActionItem,
    to: PersonRequestState,
    because: str,
    person_id: str | None = None,
) -> list[PersonRequest]:
    """Answer or withdraw what is still asked about the item (of one person, if given)."""
    ended: list[PersonRequest] = []
    for ask in await action.find(PersonRequest, subject_kind=SUBJECT, subject_id=str(item.id)):
        if ask.state not in OPEN_ASKS or (person_id is not None and ask.person_id != person_id):
            continue
        name = (
            "assistant.ask_answered"
            if to is PersonRequestState.ANSWERED
            else ("assistant.ask_withdrawn")
        )
        ended.append(
            await action.change(name, ask, ask.evolve(state=to), details={"because": because})
        )
    return ended


# Assignments ----------------------------------------------------------------------------


async def mirror_assignment(action: Action, item: ActionItem, state: str) -> ActionItem:
    """Show the assignment's state on its item, without confirmation (Story 4.5): in
    progress as On track, blocked as Blocked, approved as Done; waiting for review, or no
    longer found, raises the item's attention entry. Other states leave it unchanged."""
    if item.assignment_state == state:
        return item
    changes: dict[str, Any] = {"assignment_state": state}
    word = rules.ASSIGNMENT_WORDS.get(state)
    if word is not None:
        await action.add(
            "assistant.update_mirrored",
            Update(
                organisation_id=item.organisation_id,
                assistant_id=item.assistant_id,
                created_at=action.now,
                item_id=item.id,
                author_id=item.owner_id,
                word=word,
                source="assignment",
            ),
            details={"item_id": str(item.id), "word": word, "assignment_state": state},
        )
        if word == "done" and item.state is ItemState.OPEN:
            changes["state"] = ItemState.DONE
    item = await action.change(
        "assistant.item_follows_assignment",
        item,
        item.evolve(**changes, history=[*item.history, event(action, f"assignment_{state}")]),
        details={"assignment_state": state},
    )
    reason = rules.ASSIGNMENT_REASONS.get(state)
    if word == "blocked":
        await raise_for_item(action, item, Reason.BLOCKED)
    elif reason is not None:
        await raise_for_item(action, item, reason)
    elif word is not None:
        await clear_for_item(action, item, f"assignment_{state}", word)
    return item


# The service ---------------------------------------------------------------------------------


class ItemService:
    def __init__(self, boundary: Boundary, store: Store, requests: RequestService) -> None:
        self.boundary = boundary
        self.store = store
        self.requests = requests

    # Checking what the manager chose, before the action ----------------------------------

    async def resolve_owner(
        self, viewer: Viewer, owner_id: str, assignment_id: str | None
    ) -> Owner:
        person = await self.boundary.people.person(owner_id)
        if person is None or person.organisation_id != viewer.organisation_id:
            raise NotAllowedValue(rules.NOT_AN_OWNER)
        if person.kind != "virtual_employee":
            if assignment_id:
                raise NotAllowedValue(rules.ASSIGNMENT_ONLY_FOR_VIRTUAL)
            return Owner(person)
        if not assignment_id:
            raise NotAllowedValue(rules.ASSIGNMENT_NEEDED)
        found = [
            a
            for a in await self.boundary.work_data.assignments(viewer.person_id)
            if a.id == assignment_id and a.virtual_employee_id == person.id
        ]
        if not found:
            raise NotAllowedValue(rules.ASSIGNMENT_NOT_THEIRS)
        return Owner(person, found[0])

    async def _check_manager(self, viewer: Viewer) -> None:
        """Only a manager with an assistant gets past here, before the boundary is asked."""
        async with self.store.transaction(viewer.organisation_id) as tx:
            await my_assistant(tx, viewer)

    # Giving an item ------------------------------------------------------------------------

    async def _give_to(
        self,
        action: Action,
        assistant: AssistantRecord,
        item: ActionItem,
        owner: Owner,
        settings: Settings,
        *,
        add: bool,
        name: str,
    ) -> ActionItem:
        """Put the item in the hands of ``owner``: open at once for the manager themselves or
        a virtual employee; otherwise waiting, with its owner asked to accept."""
        is_manager = owner.person.id == assistant.manager_id
        linked = owner.assignment is not None
        item = item.evolve(
            owner_id=owner.person.id,
            state=ItemState.OPEN if is_manager or linked else ItemState.WAITING_FOR_OWNER,
            accepted_at=action.now if is_manager or linked else None,
            assignment_id=owner.assignment.id if owner.assignment else None,
            assignment_state=None,
            follow_up_times=item.follow_up_times if item.follow_ups_set else [],
            follow_up_waiting_until=None,
            follow_up_note=None,
            answer_reason=None,
            date_asked_for=None,
        )
        if is_manager or linked:
            item = item.evolve(follow_up_times=[], follow_ups_set=False)
        details = {"owner_id": owner.person.id, "state": item.state.value}
        if add:
            item = await action.add(name, item, details=details)
        else:
            current = await action.get(ActionItem, item.id)
            assert current is not None
            item = await action.change(name, current, item, details=details)
        if owner.assignment is not None:
            item = await mirror_assignment(action, item, owner.assignment.state)
        elif not is_manager:
            await ask_to_accept(action, self.boundary, assistant, item, settings)
        return item

    async def add(
        self,
        viewer: Viewer,
        *,
        text: str,
        owner_id: str,
        due_date: date,
        project_id: str | None = None,
        assignment_id: str | None = None,
        follow_up_times: list[datetime] | None = None,
    ) -> dict[str, Any]:
        """The manager gives an item with their own click (Story 4.1)."""
        await self._check_manager(viewer)
        _refuse(rules.text_refusal(text))
        times = self._times_refusal_or_utc(follow_up_times, await self.boundary.clock.now())
        owner = await self.resolve_owner(viewer, owner_id, assignment_id)
        if project_id is not None:
            projects = await self.boundary.work_data.projects(viewer.person_id)
            if project_id not in {p.id for p in projects}:
                raise NotAllowedValue(rules.UNKNOWN_PROJECT)

        async def work(action: Action) -> ActionItem:
            assistant = await my_assistant(action.tx, viewer)
            if assistant.state is AssistantState.NEW:
                raise NotAllowedValue(rules.FORM_FIRST)
            action.assistant_id = assistant.id
            settings = await settings_for(action, assistant)
            _refuse(rules.due_date_refusal(due_date, local_date(action.now, settings)))
            item = ActionItem(
                organisation_id=assistant.organisation_id,
                assistant_id=assistant.id,
                created_at=action.now,
                text=text,
                owner_id=owner.person.id,
                due_date=due_date,
                follow_up_times=times or [],
                follow_ups_set=times is not None,
                origin="manager",
                project_id=project_id,
                history=[event(action, "added")],
            )
            return await self._give_to(
                action, assistant, item, owner, settings, add=True, name="assistant.item_added"
            )

        item = await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )
        return await self.item(viewer, str(item.id))

    # Owners -------------------------------------------------------------------------------

    async def _owner_item(
        self, action: Action, viewer: Viewer, raw_id: str
    ) -> tuple[ActionItem, AssistantRecord]:
        item = await get_visible(action.tx, viewer, ActionItem, parse_id(raw_id))
        assistant = await action.get(AssistantRecord, item.assistant_id or UUID(int=0))
        if assistant is None or assistant.state is AssistantState.ENDED:
            raise NotFound()
        if item.owner_id != viewer.person_id:
            raise NotAllowedValue(rules.ONLY_THE_OWNER)
        action.assistant_id = assistant.id
        return item, assistant

    async def _accept(
        self,
        action: Action,
        item: ActionItem,
        assistant: AssistantRecord,
        name: str,
        **changes: Any,
    ) -> ActionItem:
        settings = await settings_for(action, assistant)
        times = (
            item.follow_up_times
            if item.follow_ups_set
            else follow_up_times(action.now, item.due_date, settings)
        )
        item = await action.change(
            "assistant.item_accepted",
            item,
            item.evolve(
                state=ItemState.OPEN,
                accepted_at=action.now,
                follow_up_times=[t for t in times if t > action.now],
                history=[*item.history, event(action, name)],
                **changes,
            ),
            details={"follow_ups": len(times)},
        )
        await end_asks(action, item, PersonRequestState.ANSWERED, name, item.owner_id)
        return item

    async def accept(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        """The owner accepts: the item is open and its follow-ups are set."""

        async def work(action: Action) -> None:
            item, assistant = await self._owner_item(action, viewer, raw_id)
            if item.state is ItemState.OPEN:
                return  # pressed twice
            if item.state is not ItemState.WAITING_FOR_OWNER:
                raise NotAllowedValue(rules.NOT_WAITING)
            await self._accept(action, item, assistant, "accepted")

        await perform(self.boundary, self.store, viewer.organisation_id, viewer.person_id, work)
        return await self.item(viewer, raw_id)

    async def propose_date(
        self, viewer: Viewer, raw_id: str, due_date: date, reason: str | None
    ) -> dict[str, Any]:
        """The owner proposes another date with a reason: the item is open on its date and an
        attention entry offers the manager that date or the first one."""
        _refuse(rules.reason_refusal(reason, required=True))

        async def work(action: Action) -> None:
            item, assistant = await self._owner_item(action, viewer, raw_id)
            if item.state is not ItemState.WAITING_FOR_OWNER:
                raise NotAllowedValue(rules.NOT_WAITING)
            settings = await settings_for(action, assistant)
            _refuse(rules.due_date_refusal(due_date, local_date(action.now, settings)))
            if due_date == item.due_date:
                raise NotAllowedValue(rules.SAME_DATE)
            item = await self._accept(
                action,
                item,
                assistant,
                "other_date_asked",
                answer_reason=_clean(reason),
                date_asked_for=due_date,
            )
            await raise_for_item(action, item, Reason.OTHER_DATE, due_date)

        await perform(self.boundary, self.store, viewer.organisation_id, viewer.person_id, work)
        return await self.item(viewer, raw_id)

    async def decline(self, viewer: Viewer, raw_id: str, reason: str | None) -> dict[str, Any]:
        """The owner declines with a reason: an attention entry is made."""
        _refuse(rules.reason_refusal(reason, required=True))

        async def work(action: Action) -> None:
            item, _ = await self._owner_item(action, viewer, raw_id)
            if item.state is ItemState.DECLINED:
                return  # pressed twice
            if item.state is not ItemState.WAITING_FOR_OWNER:
                raise NotAllowedValue(rules.NOT_WAITING)
            item = await action.change(
                "assistant.item_declined",
                item,
                item.evolve(
                    state=ItemState.DECLINED,
                    answer_reason=_clean(reason),
                    history=[*item.history, event(action, "declined")],
                ),
            )
            await end_asks(action, item, PersonRequestState.ANSWERED, "declined", item.owner_id)
            await raise_for_item(action, item, Reason.DECLINED)

        await perform(self.boundary, self.store, viewer.organisation_id, viewer.person_id, work)
        return await self.item(viewer, raw_id)

    async def give_update(
        self,
        viewer: Viewer,
        raw_id: str,
        *,
        word: str,
        note: str | None = None,
        date_asked_for: date | None = None,
    ) -> dict[str, Any]:
        """The owner's update in one step (Story 4.3): kept word for word, waiting requests
        about the item withdrawn, Done ends the item and its follow-ups, Blocked or another
        date raises the attention entry, and an update that settles its reason clears it."""
        if word not in rules.WORDS:
            raise NotAllowedValue(rules.UNKNOWN_WORD)
        _refuse(rules.note_refusal(note))
        note = _clean(note)

        async def work(action: Action) -> None:
            item, assistant = await self._owner_item(action, viewer, raw_id)
            if item.state is ItemState.WAITING_FOR_OWNER:
                raise NotAllowedValue(rules.ACCEPT_FIRST)
            if item.state is not ItemState.OPEN:
                raise NotAllowedValue(rules.ENDED)
            if date_asked_for is not None:
                settings = await settings_for(action, assistant)
                _refuse(rules.due_date_refusal(date_asked_for, local_date(action.now, settings)))
                if date_asked_for == item.due_date:
                    raise NotAllowedValue(rules.SAME_DATE)
            await action.add(
                "assistant.update_given",
                Update(
                    organisation_id=item.organisation_id,
                    assistant_id=item.assistant_id,
                    created_at=action.now,
                    item_id=item.id,
                    author_id=viewer.person_id,
                    word=word,  # checked against WORDS above
                    note=note,
                    date_asked_for=date_asked_for,
                ),
                details={"item_id": str(item.id), "word": word, "with_note": note is not None},
            )
            await end_asks(action, item, PersonRequestState.ANSWERED, "updated", item.owner_id)
            await self.requests.withdraw_for_subject(action, SUBJECT, str(item.id), "updated")
            changes: dict[str, Any] = {}
            if word == "done":
                changes.update(state=ItemState.DONE, follow_up_waiting_until=None)
            if date_asked_for is not None:
                changes["date_asked_for"] = date_asked_for
            item = await action.change(
                "assistant.item_updated",
                item,
                item.evolve(**changes, history=[*item.history, event(action, f"update_{word}")]),
                details={"word": word},
            )
            if word == "done":
                await item_ended(action, assistant, item)
            if word == "blocked":
                await raise_for_item(action, item, Reason.BLOCKED, date_asked_for)
            elif date_asked_for is not None and word != "done":
                await raise_for_item(action, item, Reason.OTHER_DATE, date_asked_for)
            else:
                await clear_for_item(action, item, f"update_{word}", word)

        await perform(self.boundary, self.store, viewer.organisation_id, viewer.person_id, work)
        return await self.item(viewer, raw_id)

    # The manager's controls ---------------------------------------------------------------

    async def manager_item(
        self, action: Action, viewer: Viewer, raw_id: str
    ) -> tuple[ActionItem, AssistantRecord]:
        item = await get_visible(action.tx, viewer, ActionItem, parse_id(raw_id))
        assistant = await my_assistant(action.tx, viewer)
        if item.assistant_id != assistant.id:
            raise NotFound()
        action.assistant_id = assistant.id
        return item, assistant

    @staticmethod
    def _times_refusal_or_utc(times: list[datetime] | None, now: datetime) -> list[datetime] | None:
        if times is None:
            return None
        if len(times) > rules.MAX_FOLLOW_UPS:
            raise NotAllowedValue(rules.TOO_MANY_FOLLOW_UPS)
        if any(t.tzinfo is None for t in times):
            raise NotAllowedValue(rules.FOLLOW_UP_NO_ZONE)
        utc = sorted({t.astimezone(UTC) for t in times})
        if any(t <= now for t in utc):
            raise NotAllowedValue(rules.FOLLOW_UP_PAST)
        return utc

    async def set_follow_ups(
        self, viewer: Viewer, raw_id: str, times: list[datetime]
    ) -> dict[str, Any]:
        """The manager sets this item's follow-up times: up to five, or none."""
        chosen = self._times_refusal_or_utc(times, await self.boundary.clock.now()) or []

        async def work(action: Action) -> None:
            item, assistant = await self.manager_item(action, viewer, raw_id)
            if item.state not in (ItemState.WAITING_FOR_OWNER, ItemState.OPEN):
                raise NotAllowedValue(rules.NOT_OPEN)
            if item.owner_id == assistant.manager_id or item.assignment_id is not None:
                raise NotAllowedValue(rules.NO_FOLLOW_UPS)
            await action.change(
                "assistant.item_follow_ups_set",
                item,
                item.evolve(
                    follow_up_times=chosen,
                    follow_ups_set=True,
                    follow_up_waiting_until=None,
                    follow_up_note=None,
                    history=[*item.history, event(action, "follow_ups_set")],
                ),
                details={"follow_ups": len(chosen)},
            )

        await perform(self.boundary, self.store, viewer.organisation_id, viewer.person_id, work)
        return await self.item(viewer, raw_id)

    def _tell_owner(self, assistant: AssistantRecord, item: ActionItem, text: str) -> list[Tell]:
        if item.owner_id == assistant.manager_id or item.assignment_id is not None:
            return []
        return [Tell(item.owner_id, SUBJECT, str(item.id), text)]

    async def extend(
        self,
        action: Action,
        item: ActionItem,
        assistant: AssistantRecord,
        due_date: date,
        decision: str = rules.EXTEND,
    ) -> list[Tell]:
        """The new due date applies, the owner is told, and the follow-ups are set again."""
        if item.state not in (ItemState.WAITING_FOR_OWNER, ItemState.OPEN):
            raise NotAllowedValue(rules.NOT_OPEN)
        settings = await settings_for(action, assistant)
        _refuse(rules.due_date_refusal(due_date, local_date(action.now, settings)))
        changes: dict[str, Any] = {"due_date": due_date, "date_asked_for": None}
        if (
            item.state is ItemState.OPEN
            and item.owner_id != assistant.manager_id
            and item.assignment_id is None
        ):
            changes.update(
                follow_up_times=follow_up_times(action.now, due_date, settings),
                follow_ups_set=False,
                follow_up_waiting_until=None,
                follow_up_note=None,
            )
        item = await action.change(
            "assistant.item_extended",
            item,
            item.evolve(**changes, history=[*item.history, event(action, "extended")]),
        )
        await decide_for_item(action, item, decision)
        return self._tell_owner(
            assistant,
            item,
            f"The due date of an action item you own is now {due_date:%A %d %B}.",
        )

    async def keep_date(
        self, action: Action, item: ActionItem, assistant: AssistantRecord, decision: str
    ) -> list[Tell]:
        """The manager keeps the first date the owner was given; the owner is told."""
        if item.state not in rules.UNFINISHED:
            raise NotAllowedValue(rules.ENDED)
        item = await action.change(
            "assistant.item_date_kept",
            item,
            item.evolve(date_asked_for=None, history=[*item.history, event(action, "date_kept")]),
        )
        await decide_for_item(action, item, decision)
        return self._tell_owner(
            assistant,
            item,
            f"The due date of an action item you own stays {item.due_date:%A %d %B}.",
        )

    async def reassign(
        self,
        action: Action,
        item: ActionItem,
        assistant: AssistantRecord,
        owner: Owner,
        decision: str = rules.REASSIGN,
    ) -> list[Tell]:
        """The new owner is asked to accept, and the previous owner is told."""
        if item.state not in rules.UNFINISHED:
            raise NotAllowedValue(rules.ENDED)
        if owner.person.id == item.owner_id and owner.assignment is None:
            raise NotAllowedValue(rules.SAME_OWNER)
        previous = item
        await end_asks(action, item, PersonRequestState.WITHDRAWN, "reassigned")
        await self.requests.withdraw_for_subject(action, SUBJECT, str(item.id), "reassigned")
        settings = await settings_for(action, assistant)
        item = item.evolve(
            follow_up_times=[],
            follow_ups_set=False,
            history=[*item.history, event(action, "reassigned")],
        )
        item = await self._give_to(
            action, assistant, item, owner, settings, add=False, name="assistant.item_reassigned"
        )
        await decide_for_item(action, item, decision, owner.person.id)
        if previous.owner_id == owner.person.id:
            return []
        return self._tell_owner(
            assistant,
            previous,
            "An action item you owned has been given to someone else, "
            "so nothing more is needed from you.",
        )

    async def close(
        self,
        action: Action,
        item: ActionItem,
        assistant: AssistantRecord,
        reason: str | None,
        decision: str = rules.CLOSE,
    ) -> list[Tell]:
        """The item is closed, with or without a reason, and the owner is told."""
        if item.state not in rules.UNFINISHED:
            raise NotAllowedValue(rules.ENDED)
        reason = _clean(reason)
        await end_asks(action, item, PersonRequestState.WITHDRAWN, "closed")
        await self.requests.withdraw_for_subject(action, SUBJECT, str(item.id), "closed")
        item = await action.change(
            "assistant.item_closed",
            item,
            item.evolve(
                state=ItemState.CLOSED,
                close_reason=reason,
                follow_up_waiting_until=None,
                history=[*item.history, event(action, "closed")],
            ),
            details={"because": "manager", "with_reason": reason is not None},
        )
        await item_ended(action, assistant, item)
        await decide_for_item(action, item, decision)
        text = "An action item you own has been closed, so nothing more is needed from you."
        if reason:
            text += f" The reason given: {reason}"
        return self._tell_owner(assistant, item, text)

    async def _decide(
        self,
        viewer: Viewer,
        raw_id: str,
        decide: Any,
    ) -> dict[str, Any]:
        async def work(action: Action) -> list[Tell]:
            item, assistant = await self.manager_item(action, viewer, raw_id)
            tells: list[Tell] = await decide(action, item, assistant)
            return tells

        tells = await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )
        await tell(self.boundary, viewer.organisation_id, tells)
        return await self.item(viewer, raw_id)

    async def extend_item(self, viewer: Viewer, raw_id: str, due_date: date) -> dict[str, Any]:
        return await self._decide(viewer, raw_id, lambda a, i, s: self.extend(a, i, s, due_date))

    async def reassign_item(
        self, viewer: Viewer, raw_id: str, owner_id: str, assignment_id: str | None
    ) -> dict[str, Any]:
        await self._check_manager(viewer)
        owner = await self.resolve_owner(viewer, owner_id, assignment_id)
        return await self._decide(viewer, raw_id, lambda a, i, s: self.reassign(a, i, s, owner))

    async def close_item(self, viewer: Viewer, raw_id: str, reason: str | None) -> dict[str, Any]:
        _refuse(rules.reason_refusal(reason, required=False))
        return await self._decide(viewer, raw_id, lambda a, i, s: self.close(a, i, s, reason))

    async def mark_done(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        """The manager marks an item they gave themselves as done."""

        async def done(action: Action, item: ActionItem, assistant: AssistantRecord) -> list[Tell]:
            if item.owner_id != assistant.manager_id:
                raise NotAllowedValue(rules.ONLY_OWN_DONE)
            if item.state is ItemState.DONE:
                return []  # pressed twice
            if item.state is not ItemState.OPEN:
                raise NotAllowedValue(rules.NOT_OPEN)
            item = await action.change(
                "assistant.item_done",
                item,
                item.evolve(state=ItemState.DONE, history=[*item.history, event(action, "done")]),
            )
            await item_ended(action, assistant, item)
            await clear_for_item(action, item, "done")
            return []

        return await self._decide(viewer, raw_id, done)

    # Reading ----------------------------------------------------------------------------

    async def _name(self, person_id: str, names: dict[str, Person | None]) -> Person | None:
        if person_id not in names:
            names[person_id] = await self.boundary.people.person(person_id)
        return names[person_id]

    async def view(
        self,
        reader: Transaction,
        viewer: Viewer,
        item: ActionItem,
        names: dict[str, Person | None],
    ) -> dict[str, Any]:
        """An item as the viewer may see it: its owner's words word for word with their
        time (BR-16), and what the viewer can do with it."""
        assistant = await reader.get(AssistantRecord, item.assistant_id or UUID(int=0))
        is_manager = assistant is not None and assistant.manager_id == viewer.person_id
        is_owner = item.owner_id == viewer.person_id
        owner = await self._name(item.owner_id, names)
        updates = await reader.find(Update, item_id=item.id)
        shown_updates = [
            {
                "id": str(u.id),
                "word": u.word,
                "label": rules.WORD_LABELS[u.word],
                "note": u.note,
                "date_asked_for": u.date_asked_for.isoformat() if u.date_asked_for else None,
                "author_id": u.author_id,
                "source": u.source,
                "confirmed": not rules.needs_confirmation(u.author_id, item.owner_id),
                "at": u.created_at.isoformat(),
            }
            for u in updates
        ]
        view: dict[str, Any] = {
            "id": str(item.id),
            "text": item.text,
            "state": item.state.value,
            "due_date": item.due_date.isoformat(),
            "origin": item.origin,
            "project_id": item.project_id,
            "owner": {
                "id": item.owner_id,
                "display_name": owner.display_name if owner else None,
                "kind": owner.kind if owner else None,
            },
            "assignment": (
                {"id": item.assignment_id, "state": item.assignment_state}
                if item.assignment_id
                else None
            ),
            "follow_up_times": [t.isoformat() for t in item.follow_up_times],
            "follow_ups_set": item.follow_ups_set,
            "follow_up_waiting_until": (
                item.follow_up_waiting_until.isoformat() if item.follow_up_waiting_until else None
            ),
            "follow_up_note": item.follow_up_note,
            "answer_reason": item.answer_reason,
            "date_asked_for": item.date_asked_for.isoformat() if item.date_asked_for else None,
            "close_reason": item.close_reason,
            "accepted_at": item.accepted_at.isoformat() if item.accepted_at else None,
            "created_at": item.created_at.isoformat(),
            "updates": shown_updates,
            "last_update": shown_updates[-1] if shown_updates else None,
            "can": self._can(item, assistant, is_manager, is_owner),
        }
        if is_manager:
            entry = await open_entry(reader, item)
            view["attention"] = (
                {
                    "id": str(entry.id),
                    "reason": entry.reason,
                    "reason_code": entry.reason_code,
                    "choices": entry.choices,
                }
                if entry
                else None
            )
            view["history"] = [e.model_dump(mode="json") for e in item.history]
        return view

    @staticmethod
    def _can(
        item: ActionItem, assistant: AssistantRecord | None, is_manager: bool, is_owner: bool
    ) -> list[str]:
        can: list[str] = []
        if is_owner and item.state is ItemState.WAITING_FOR_OWNER:
            can += ["accept", "propose_date", "decline"]
        if is_owner and item.state is ItemState.OPEN and item.assignment_id is None:
            can.append("update")
        if not is_manager or assistant is None:
            return can
        own = item.owner_id == assistant.manager_id
        if item.state in (ItemState.WAITING_FOR_OWNER, ItemState.OPEN):
            if not own and item.assignment_id is None:
                can.append("follow_ups")
            can.append("extend")
        if item.state in rules.UNFINISHED:
            can += ["reassign", "close"]
        if own and item.state is ItemState.OPEN:
            can.append("done")
        return can

    async def item(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        async with self.store.transaction(viewer.organisation_id) as tx:
            item = await get_visible(tx, viewer, ActionItem, parse_id(raw_id))
            return await self.view(tx, viewer, item, {})

    async def items(self, viewer: Viewer) -> dict[str, Any]:
        """The manager's action items, by due date."""
        async with self.store.transaction(viewer.organisation_id) as tx:
            assistant = await my_assistant(tx, viewer)
            found = await find_visible(tx, viewer, ActionItem, assistant_id=assistant.id)
            names: dict[str, Person | None] = {}
            shown = [
                await self.view(tx, viewer, i, names)
                for i in sorted(found, key=lambda i: (i.due_date, i.created_at))
            ]
        open_count = sum(1 for i in found if i.state is ItemState.OPEN)
        return {"count": len(shown), "open": open_count, "items": shown}

    async def my_items(self, viewer: Viewer) -> dict[str, Any]:
        """The items the person asking owns, whoever gave them, with what waits for them:
        who asks (the manager) and, for an ask that was sent, by when."""
        async with self.store.transaction(viewer.organisation_id) as tx:
            found = await find_visible(tx, viewer, ActionItem, owner_id=viewer.person_id)
            asks = await tx.find(PersonRequest, person_id=viewer.person_id)
            names: dict[str, Person | None] = {}
            shown = []
            for item in sorted(found, key=lambda i: (i.due_date, i.created_at)):
                assistant = await tx.get(AssistantRecord, item.assistant_id or UUID(int=0))
                if assistant is None or assistant.state is AssistantState.ENDED:
                    continue
                view = await self.view(tx, viewer, item, names)
                manager = await self._name(assistant.manager_id, names)
                view["for"] = {
                    "id": assistant.manager_id,
                    "display_name": manager.display_name if manager else None,
                }
                sent = [
                    a
                    for a in asks
                    if a.subject_id == str(item.id)
                    and a.state is PersonRequestState.SENT
                    and a.asks in ("accept", "update")
                ]
                latest = max(sent, key=lambda a: a.answer_by) if sent else None
                view["ask"] = (
                    {"asks": latest.asks, "answer_by": latest.answer_by.isoformat()}
                    if latest
                    else None
                )
                shown.append(view)
        return {"count": len(shown), "items": shown}

    async def assignments(self, viewer: Viewer) -> dict[str, Any]:
        """The assignments of the manager's virtual employees, to link an item to one."""
        await self._check_manager(viewer)
        found = await self.boundary.work_data.assignments(viewer.person_id)
        return {
            "assignments": [
                {
                    "id": a.id,
                    "title": a.title,
                    "virtual_employee_id": a.virtual_employee_id,
                    "state": a.state,
                    "source_label": a.source_label,
                    "read_at": a.read_at.isoformat(),
                }
                for a in found
            ]
        }
