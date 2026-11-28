"""The manager's notes: review, keeping actions, search, questions and sharing (part 2).

Notes are the manager's only. Each action has Keep, Edit and Remove: Edit
changes its text, owner and due date before it is kept (and is how an owner is
chosen for "Owner not found"); Keep makes it an action item with the origin
"meeting" and asks its owner to accept it, exactly as for an item the manager
gives by hand, and is available only when the action meets the same checks.
These are the manager's own decisions, so none needs approval. An action that
is removed or not yet kept never reaches its owner (BR-4).

A statement from the notes is shown only as "Awaiting its owner" until its owner
confirms it; then the confirmed words are shown (BR-4). A kept action's line
shows its item's outcome and date once the item is done, closed or raised to the
manager. Deleting the notes keeps the items already kept and the confirmed
statements. The manager searches kept notes, the assistant is given the notes a
question is about, and a note the manager shares is told to Flow through
Knowledge (and taken back when it is no longer shared or is deleted).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any
from uuid import UUID

from assistant.boundary.interfaces import Boundary
from assistant.errors import AlreadyChanged, NotAllowedValue, NotFound
from assistant.models.boundary import KnowledgeNote, PackInput, Person, WorkKind
from assistant.models.records import (
    MESSAGE_LIMIT,
    ActionItem,
    AssistantRecord,
    AssistantState,
    AttentionEntry,
    AttentionState,
    ChatMessage,
    ConfirmedStatement,
    ItemState,
    Meeting,
    MeetingNotes,
    NoteAction,
    NoteLine,
)
from assistant.rules import items as item_rules
from assistant.rules.input_pack import pack_label
from assistant.rules.meetings import part_place
from assistant.rules.settings import Settings
from assistant.rules.visibility import Viewer
from assistant.rules.working_time import local_date
from assistant.service.actions import Action, perform
from assistant.service.assistants import my_assistant
from assistant.service.items import ItemService, event
from assistant.service.setup import settings_for
from assistant.service.signals import notes_corrected
from assistant.service.visibility import find_visible, get_visible, parse_id
from assistant.service.work_kinds import WORK_KINDS, PackContext
from assistant.store.interface import Store, Transaction

OWNER_NOT_FOUND = "Owner not found"
CHOOSE_OWNER = "Choose an owner for this action before keeping it."
DUE_NEEDED = "Give this action a due date before keeping it."
NOT_PROPOSED = "This action was already kept or removed."
EDIT_TEXT_TOO_LONG = f"An action can be at most {MESSAGE_LIMIT:,} characters."
NOTHING_TO_EDIT = "Change the text, the owner or the due date."
OWNER_NOT_IN_ORGANISATION = "The owner must be a person or virtual employee in the organisation."
NOT_SHARED = "This note is not shared."
NOT_ADDED = "The note could not be shared."
NOT_TAKEN_BACK = "The note could not be taken back from the knowledge store."
AWAITING_OWNER = "awaiting_owner"
NOTES_INPUT_KIND = "meeting_notes"
MAX_NOTES_IN_PACK = 5

_WORDS = re.compile(r"[\w']+")
_COMMON = frozenset(
    {"the", "and", "for", "with", "what", "when", "were", "was", "did", "that", "this", "from",
     "about", "meeting", "meetings", "notes", "have", "which", "who", "our", "your"}
)  # fmt: skip


def words(text: str) -> list[str]:
    return [w.casefold() for w in _WORDS.findall(text)]


def keep_problem(action: NoteAction, today: date) -> str | None:
    """Why the action cannot be kept yet: the checks of an item the manager adds by hand."""
    if action.state != "proposed":
        return NOT_PROPOSED
    problem = item_rules.text_refusal(action.text)
    if problem:
        return problem
    if action.owner_id is None:
        return CHOOSE_OWNER
    if action.due_date is None:
        return DUE_NEEDED
    return item_rules.due_date_refusal(action.due_date, today)


def asked_about(message: str, notes: MeetingNotes) -> bool:
    """Whether the manager's message asks about this meeting: it names its title, or it
    speaks of a meeting and shares a word of the title."""
    said = message.casefold()
    title = notes.title.casefold().strip()
    if title and title in said:
        return True
    if "meeting" not in said:
        return False
    title_words = {w for w in words(notes.title) if len(w) >= 4 and w not in _COMMON}
    return bool(title_words & set(words(message)))


def _parts(parts: list[int]) -> str:
    return f"part {parts[0]}" if len(parts) == 1 else "parts " + ", ".join(map(str, parts))


def notes_text(notes: MeetingNotes, confirmed: dict[str, ConfirmedStatement]) -> str:
    """The notes as plain lines, each naming its transcript parts. Statements appear only
    once confirmed, in their confirmed words."""
    lines = [f"Meeting: {notes.title}", f"Date: {notes.meeting_date.isoformat()}"]
    if notes.summary is not None:
        lines.append(f"Summary: {notes.summary.text} ({_parts(notes.summary.parts)})")
    lines += [f"Decision: {d.text} ({_parts(d.parts)})" for d in notes.decisions]
    for a in notes.actions:
        if a.state == "removed":
            continue
        due = f", due {a.due_date.isoformat()}" if a.due_date else ""
        owner = a.owner_name or OWNER_NOT_FOUND
        lines.append(f"Action: {a.text} (owner {owner}{due}; {_parts(a.parts)})")
    lines += [f"Open: {o.text} ({_parts(o.parts)})" for o in notes.open_points]
    for s in notes.statements:
        statement = confirmed.get(s.project_id)
        if statement is not None:
            reason = statement.wording.reason
            lines.append(f"Status of {s.project_name}: {reason} ({_parts(s.parts)})")
    return "\n".join(lines)


async def confirmed_of(
    reader: Transaction | Action, notes: MeetingNotes
) -> dict[str, ConfirmedStatement]:
    """For each statement of the notes its owner confirmed, the latest confirmed version."""
    found: dict[str, ConfirmedStatement] = {}
    for statement in notes.statements:
        if statement.draft_id is None:
            continue
        versions = await reader.find(
            ConfirmedStatement, assistant_id=notes.assistant_id, project_id=statement.project_id
        )
        current = next((v for v in versions if v.draft_id == statement.draft_id), None)
        while current is not None:
            later = next((v for v in versions if v.previous_version_id == current.id), None)
            if later is None:
                break
            current = later
        if current is not None:
            found[statement.project_id] = current
    return found


async def notes_asked_about(ctx: PackContext) -> list[PackInput]:
    """When the manager asks about a past meeting, its kept notes, as data, so that the
    reply can cite them."""
    if ctx.work.message_id is None:
        return []
    message = await ctx.reader.get(ChatMessage, ctx.work.message_id)
    if message is None:
        return []
    kept = await ctx.reader.find(MeetingNotes, assistant_id=ctx.assistant.id)
    matching = [n for n in kept if asked_about(message.text, n)]
    matching.sort(key=lambda n: (n.meeting_date, n.created_at), reverse=True)
    inputs: list[PackInput] = []
    for notes in matching[:MAX_NOTES_IN_PACK]:
        inputs.append(
            PackInput(
                label=pack_label(
                    f"Meeting notes: {notes.title} ({notes.meeting_date.isoformat()})"
                ),
                read_at=ctx.now,
                content=notes_text(notes, await confirmed_of(ctx.reader, notes)),
                kind=NOTES_INPUT_KIND,
                subject_id=str(notes.id),
            )
        )
    return inputs


WORK_KINDS.add_inputs(WorkKind.REQUEST, notes_asked_about)


class NotesService:
    def __init__(self, boundary: Boundary, store: Store, items: ItemService) -> None:
        self.boundary = boundary
        self.store = store
        self.items = items

    # Reading ------------------------------------------------------------------------------

    async def _person(self, person_id: str, names: dict[str, Person | None]) -> Person | None:
        if person_id not in names:
            names[person_id] = await self.boundary.people.person(person_id)
        return names[person_id]

    async def _outcome(
        self, tx: Transaction, action: NoteAction, settings: Settings
    ) -> dict[str, Any] | None:
        """Once the item is done, closed or raised to the manager: the outcome and its date."""
        if action.item_id is None:
            return None
        item = await tx.get(ActionItem, action.item_id)
        if item is None:
            return None
        if item.state in (ItemState.DONE, ItemState.CLOSED):
            at = item.history[-1].at if item.history else item.created_at
            return {"outcome": item.state.value, "date": local_date(at, settings).isoformat()}
        entries = [
            e
            for e in await tx.find(
                AttentionEntry, subject_kind="action_item", subject_id=str(item.id)
            )
            if e.state is AttentionState.OPEN
        ]
        if entries:
            at = entries[-1].created_at
            return {"outcome": "raised", "date": local_date(at, settings).isoformat()}
        return None

    @staticmethod
    def _line(line: NoteLine) -> dict[str, Any]:
        return {"text": line.text, "parts": list(line.parts)}

    async def _view(
        self, tx: Transaction, notes: MeetingNotes, assistant: AssistantRecord
    ) -> dict[str, Any]:
        settings = await settings_for(tx, assistant)
        today = local_date(await self.boundary.clock.now(), settings)
        names: dict[str, Person | None] = {}
        organiser = await self._person(notes.organiser_id, names)
        actions = []
        for a in notes.actions:
            owner = await self._person(a.owner_id, names) if a.owner_id else None
            problem = keep_problem(a, today)
            actions.append(
                {
                    "id": str(a.id),
                    "text": a.text,
                    "owner": (
                        {"id": a.owner_id, "display_name": owner.display_name if owner else None}
                        if a.owner_id
                        else None
                    ),
                    "owner_name": a.owner_name,
                    "owner_label": (owner.display_name if owner else a.owner_name)
                    if a.owner_id
                    else OWNER_NOT_FOUND,
                    "due_date": a.due_date.isoformat() if a.due_date else None,
                    "parts": list(a.parts),
                    "state": a.state,
                    "can_keep": problem is None,
                    "keep_blocked": problem if a.state == "proposed" else None,
                    "can_edit": a.state == "proposed",
                    "can_remove": a.state == "proposed",
                    "item_id": str(a.item_id) if a.item_id else None,
                    "outcome": await self._outcome(tx, a, settings),
                }
            )
        confirmed = await confirmed_of(tx, notes)
        statements = []
        for s in notes.statements:
            statement = confirmed.get(s.project_id)
            statements.append(
                {
                    "project": {"id": s.project_id, "name": s.project_name},
                    "parts": list(s.parts),
                    "state": "confirmed" if statement else AWAITING_OWNER,
                    # BR-4: the words reach the manager only once its owner confirmed them.
                    "confirmed": (
                        {
                            "statement_id": str(statement.id),
                            "colour": statement.colour,
                            "reason": statement.wording.reason,
                            "what_changed": statement.wording.what_changed,
                            "what_comes_next": statement.wording.what_comes_next,
                            "mark": statement.mark,
                        }
                        if statement
                        else None
                    ),
                }
            )
        return {
            "id": str(notes.id),
            "meeting_id": str(notes.meeting_id),
            "title": notes.title,
            "date": notes.meeting_date.isoformat(),
            "origin": notes.origin,
            "organiser": {
                "id": notes.organiser_id,
                "display_name": organiser.display_name if organiser else None,
            },
            "manager_attended": notes.manager_attended,
            "summary": self._line(notes.summary) if notes.summary else None,
            "decisions": [self._line(d) for d in notes.decisions],
            "actions": actions,
            "open_points": [self._line(o) for o in notes.open_points],
            "statements": statements,
            "parts": [part_place(p) for p in notes.parts],
            "dropped_statements": notes.dropped_statements,
            "shared": notes.shared,
            "created_at": notes.created_at.isoformat(),
        }

    async def _mine(
        self, tx: Transaction, viewer: Viewer, notes_id: UUID
    ) -> tuple[MeetingNotes, AssistantRecord]:
        notes = await get_visible(tx, viewer, MeetingNotes, notes_id)
        assistant = await my_assistant(tx, viewer)
        if notes.assistant_id != assistant.id:
            raise NotFound()
        return notes, assistant

    async def notes(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        async with self.store.transaction(viewer.organisation_id) as tx:
            notes, assistant = await self._mine(tx, viewer, parse_id(raw_id))
            return await self._view(tx, notes, assistant)

    async def all_notes(self, viewer: Viewer) -> dict[str, Any]:
        """The kept notes, newest meeting first, each with what waits for the manager."""
        async with self.store.transaction(viewer.organisation_id) as tx:
            assistant = await my_assistant(tx, viewer)
            found = await find_visible(tx, viewer, MeetingNotes, assistant_id=assistant.id)
        found.sort(key=lambda n: (n.meeting_date, n.created_at), reverse=True)
        return {
            "count": len(found),
            "notes": [
                {
                    "id": str(n.id),
                    "title": n.title,
                    "date": n.meeting_date.isoformat(),
                    "origin": n.origin,
                    "actions_to_keep": sum(1 for a in n.actions if a.state == "proposed"),
                    "shared": n.shared,
                }
                for n in found
            ],
        }

    # The manager's decisions on actions ----------------------------------------------------------

    async def _change_action(
        self, viewer: Viewer, raw_id: str, action_id: str, name: str, change: Any
    ) -> dict[str, Any]:
        """Change one action of the notes with ``change(action, notes, line)``, which returns
        the new line; the notes are version-checked."""
        notes_id, line_id = parse_id(raw_id), parse_id(action_id)

        async def work(action: Action) -> None:
            notes, assistant = await self._mine(action.tx, viewer, notes_id)
            action.assistant_id = assistant.id
            index = next((i for i, a in enumerate(notes.actions) if a.id == line_id), None)
            if index is None:
                raise NotFound()
            line = notes.actions[index]
            changed = await change(action, assistant, notes, line)
            if changed is None:
                return
            lines = list(notes.actions)
            lines[index] = changed
            await action.change(
                name,
                notes,
                notes.evolve(actions=lines),
                details={"action_id": str(line_id), "action_state": changed.state},
            )

        await perform(self.boundary, self.store, viewer.organisation_id, viewer.person_id, work)
        return await self.notes(viewer, raw_id)

    async def keep(
        self, viewer: Viewer, raw_id: str, action_id: str, assignment_id: str | None = None
    ) -> dict[str, Any]:
        """Keep an action: it becomes an action item from the meeting, and its owner is
        asked to accept it. The manager's own decision: no approval."""
        notes_id, line_id = parse_id(raw_id), parse_id(action_id)
        async with self.store.transaction(viewer.organisation_id) as tx:
            notes, assistant = await self._mine(tx, viewer, notes_id)
            line = next((a for a in notes.actions if a.id == line_id), None)
            if line is None:
                raise NotFound()
            if line.state == "kept":
                return await self._view(tx, notes, assistant)  # pressed twice
            if assistant.state is AssistantState.NEW:
                raise NotAllowedValue(item_rules.FORM_FIRST)
            settings = await settings_for(tx, assistant)
        today = local_date(await self.boundary.clock.now(), settings)
        problem = keep_problem(line, today)
        if problem:
            raise NotAllowedValue(problem)
        assert line.owner_id is not None and line.due_date is not None
        owner = await self.items.resolve_owner(viewer, line.owner_id, assignment_id)

        async def keep_it(
            action: Action, assistant: AssistantRecord, notes: MeetingNotes, current: NoteAction
        ) -> NoteAction:
            if current != line:
                raise AlreadyChanged()
            settings = await settings_for(action, assistant)
            item = ActionItem(
                organisation_id=assistant.organisation_id,
                assistant_id=assistant.id,
                created_at=action.now,
                text=current.text,
                owner_id=owner.person.id,
                due_date=current.due_date or today,
                origin="meeting",
                project_id=notes.project_ids[0] if len(notes.project_ids) == 1 else None,
                history=[event(action, "added")],
            )
            item = await self.items._give_to(
                action, assistant, item, owner, settings, add=True, name="assistant.item_added"
            )
            return current.model_copy(update={"state": "kept", "item_id": item.id})

        return await self._change_action(
            viewer, raw_id, action_id, "assistant.meeting_action_kept", keep_it
        )

    async def edit(
        self,
        viewer: Viewer,
        raw_id: str,
        action_id: str,
        *,
        text: str | None = None,
        owner: str | None = None,
        owner_id: str | None = None,
        due_date: date | None = None,
    ) -> dict[str, Any]:
        """Change an action's text, owner or due date before it is kept. An owner named
        here replaces "Owner not found" when they match exactly one person or virtual
        employee; Keep stays unavailable until the action meets the checks."""
        if text is None and owner is None and owner_id is None and due_date is None:
            raise NotAllowedValue(NOTHING_TO_EDIT)
        changes: dict[str, Any] = {"edited": True}
        if text is not None:
            if not text.strip():
                raise NotAllowedValue(item_rules.TEXT_EMPTY)
            if len(text) > MESSAGE_LIMIT:
                raise NotAllowedValue(EDIT_TEXT_TOO_LONG)
            changes["text"] = text
        if owner_id is not None:
            person = await self.boundary.people.person(owner_id)
            if person is None or person.organisation_id != viewer.organisation_id:
                raise NotAllowedValue(OWNER_NOT_IN_ORGANISATION)
            changes.update(owner_id=person.id, owner_name=person.display_name)
        elif owner is not None:
            name = owner.strip()
            found = [
                p
                for p in await self.boundary.people.find_by_name(viewer.organisation_id, name)
                if p.organisation_id == viewer.organisation_id
            ]
            changes.update(
                owner_id=found[0].id if len(found) == 1 else None,
                owner_name=(found[0].display_name if len(found) == 1 else name[:200]) or None,
            )
        if due_date is not None:
            changes["due_date"] = due_date

        async def edit_it(
            action: Action, assistant: AssistantRecord, notes: MeetingNotes, current: NoteAction
        ) -> NoteAction:
            if current.state != "proposed":
                raise NotAllowedValue(NOT_PROPOSED)
            edited = NoteAction.model_validate({**current.model_dump(), **changes})
            await notes_corrected(action, assistant, notes, current, edited)
            return edited

        return await self._change_action(
            viewer, raw_id, action_id, "assistant.meeting_action_edited", edit_it
        )

    async def remove(self, viewer: Viewer, raw_id: str, action_id: str) -> dict[str, Any]:
        """Remove an action: its owner is never asked and no item is made for it."""

        async def remove_it(
            action: Action, assistant: AssistantRecord, notes: MeetingNotes, current: NoteAction
        ) -> NoteAction | None:
            if current.state == "removed":
                return None  # pressed twice
            if current.state != "proposed":
                raise NotAllowedValue(NOT_PROPOSED)
            await notes_corrected(action, assistant, notes, current, None)
            return current.model_copy(update={"state": "removed"})

        return await self._change_action(
            viewer, raw_id, action_id, "assistant.meeting_action_removed", remove_it
        )

    # Deleting and sharing ----------------------------------------------------------------

    async def delete(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        """Delete the notes. Items already kept and confirmed statements stay; a shared note
        is taken back from the knowledge store."""
        notes_id = parse_id(raw_id)

        async def work(action: Action) -> None:
            notes, assistant = await self._mine(action.tx, viewer, notes_id)
            action.assistant_id = assistant.id
            if notes.shared:
                await self._take_back(notes)
            meeting = await action.get(Meeting, notes.meeting_id)
            if meeting is not None and meeting.notes_id == notes.id:
                await action.change(
                    "assistant.meeting_notes_unlinked", meeting, meeting.evolve(notes_id=None)
                )
            await action.remove(
                "assistant.meeting_notes_deleted", notes, details={"shared": notes.shared}
            )

        await perform(self.boundary, self.store, viewer.organisation_id, viewer.person_id, work)
        return {"id": str(notes_id), "deleted": True}

    async def _take_back(self, notes: MeetingNotes) -> None:
        result = await self.boundary.knowledge.remove(notes.organisation_id, str(notes.id))
        if not result.done:
            raise NotAllowedValue(NOT_TAKEN_BACK)

    async def share(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        """The manager shares a kept note: Flow is told (Knowledge: add), so that it can add
        it to the organisation's knowledge store. Nothing is shared otherwise."""
        notes_id = parse_id(raw_id)

        async def work(action: Action) -> None:
            notes, assistant = await self._mine(action.tx, viewer, notes_id)
            if notes.shared:
                return  # pressed twice
            action.assistant_id = assistant.id
            changed = await action.change(
                "assistant.meeting_notes_shared",
                notes,
                notes.evolve(shared=True, shared_at=action.now),
            )
            result = await self.boundary.knowledge.add(
                KnowledgeNote(
                    note_id=str(changed.id),
                    organisation_id=changed.organisation_id,
                    manager_id=assistant.manager_id,
                    title=changed.title,
                    date=changed.meeting_date.isoformat(),
                    content=notes_text(changed, await confirmed_of(action, changed)),
                )
            )
            if not result.done:
                raise NotAllowedValue(NOT_ADDED)  # nothing is kept as shared

        await perform(self.boundary, self.store, viewer.organisation_id, viewer.person_id, work)
        return await self.notes(viewer, raw_id)

    async def unshare(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        """The manager stops sharing a note: Flow is told to take it back (Knowledge: remove)."""
        notes_id = parse_id(raw_id)

        async def work(action: Action) -> None:
            notes, assistant = await self._mine(action.tx, viewer, notes_id)
            if not notes.shared:
                return  # pressed twice
            action.assistant_id = assistant.id
            await action.change(
                "assistant.meeting_notes_unshared",
                notes,
                notes.evolve(shared=False, shared_at=None),
            )
            await self._take_back(notes)

        await perform(self.boundary, self.store, viewer.organisation_id, viewer.person_id, work)
        return await self.notes(viewer, raw_id)

    # Search -------------------------------------------------------------------------------

    async def search(
        self,
        viewer: Viewer,
        *,
        words_wanted: str = "",
        meeting: str = "",
        project: str = "",
        person: str = "",
    ) -> dict[str, Any]:
        """Search the kept notes by words, meeting, project or person. Each result opens the
        notes with their sources (``GET /meeting-notes/{id}``)."""
        async with self.store.transaction(viewer.organisation_id) as tx:
            assistant = await my_assistant(tx, viewer)
            found = await find_visible(tx, viewer, MeetingNotes, assistant_id=assistant.id)
            confirmed = {n.id: await confirmed_of(tx, n) for n in found}
        projects = {p.id: p.name for p in await self.boundary.work_data.projects(viewer.person_id)}
        names: dict[str, Person | None] = {}
        wanted = words(words_wanted)
        results = []
        for notes in sorted(found, key=lambda n: (n.meeting_date, n.created_at), reverse=True):
            if meeting.strip() and not (
                meeting.strip().casefold() in notes.title.casefold()
                or meeting.strip() in (str(notes.id), str(notes.meeting_id))
            ):
                continue
            if project.strip() and not self._about_project(notes, project.strip(), projects):
                continue
            if person.strip() and not await self._names_person(notes, person.strip(), names):
                continue
            lines = self._lines(notes, confirmed[notes.id])
            matching = [
                line for line in lines if all(w in set(words(line["text"])) for w in wanted)
            ] if wanted else []  # fmt: skip
            if wanted and not matching:
                everything = set(words(notes.title)).union(*(words(l["text"]) for l in lines))
                if not all(w in everything for w in wanted):
                    continue
            results.append(
                {
                    "notes_id": str(notes.id),
                    "title": notes.title,
                    "date": notes.meeting_date.isoformat(),
                    "lines": matching,
                }
            )
        return {"count": len(results), "results": results}

    @staticmethod
    def _about_project(notes: MeetingNotes, wanted: str, projects: dict[str, str]) -> bool:
        ids = set(notes.project_ids) | {s.project_id for s in notes.statements}
        key = wanted.casefold()
        return any(
            pid == wanted or key in (projects.get(pid) or "").casefold() for pid in ids
        ) or any(key in s.project_name.casefold() for s in notes.statements)

    async def _names_person(
        self, notes: MeetingNotes, wanted: str, names: dict[str, Person | None]
    ) -> bool:
        key = wanted.casefold()
        people = [notes.organiser_id] + [a.owner_id for a in notes.actions if a.owner_id]
        for person_id in people:
            found = await self._person(person_id, names)
            if person_id == wanted or (found and key in found.display_name.casefold()):
                return True
        return any(a.owner_name and key in a.owner_name.casefold() for a in notes.actions)

    @staticmethod
    def _lines(
        notes: MeetingNotes, confirmed: dict[str, ConfirmedStatement]
    ) -> list[dict[str, Any]]:
        lines: list[dict[str, Any]] = []
        if notes.summary is not None:
            summary = notes.summary
            lines.append({"kind": "summary", "text": summary.text, "parts": summary.parts})
        lines += [{"kind": "decision", "text": d.text, "parts": d.parts} for d in notes.decisions]
        lines += [
            {"kind": "action", "text": a.text, "parts": a.parts}
            for a in notes.actions
            if a.state != "removed"
        ]
        lines += [
            {"kind": "open_point", "text": o.text, "parts": o.parts} for o in notes.open_points
        ]
        for s in notes.statements:
            statement = confirmed.get(s.project_id)
            if statement is not None:  # never the unconfirmed words (BR-4)
                reason = statement.wording.reason
                lines.append({"kind": "statement", "text": reason, "parts": s.parts})
        return lines
