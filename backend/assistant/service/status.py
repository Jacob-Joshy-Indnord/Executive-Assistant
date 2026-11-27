"""Project status (Epic 5): followed projects, owners' statements and reports.

The manager follows projects they can see; each project's owner confirms its
status unless the manager chooses someone else. "Since the last report" runs
from the last report, or from when the project was followed. A project's news
in that window is the updates on its action items, every change seen to an
assignment on the project, and its spend when it differs from the spend at the
start of the window.

Owners answer their status requests themselves: a draft from the assistant is
confirmed (unchanged) or corrected (both wordings kept, the assistant's never
shown, BR-5); a status they write themselves counts as confirmed (BR-16).
Confirmed statements are append-only: a later correction is a new version.

A report has one line for each followed project the manager can still see,
showing only confirmed words (BR-4), and is kept as it was made.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from assistant.boundary.interfaces import Boundary
from assistant.errors import AlreadyChanged, NotAllowedValue, NotFound
from assistant.models.boundary import ActionKind, AssignmentRecord, Person, ProjectRecord
from assistant.models.records import (
    MAX_ASSIGNMENT_CHANGES,
    ActionItem,
    AssignmentChange,
    AssistantRecord,
    AssistantState,
    ConfirmedStatement,
    DraftState,
    FollowedProject,
    PersonRequest,
    PersonRequestState,
    Report,
    ReportLine,
    RequestRecord,
    RequestState,
    StatementDraft,
    StatementWording,
    Update,
)
from assistant.rules import status as rules
from assistant.rules.items import WORD_LABELS
from assistant.rules.reports import (
    COLOUR_WORDS,
    MARK_WORDS,
    changed_points,
    changed_since,
    line_for,
    line_text,
    next_report_after,
)
from assistant.rules.requests import short
from assistant.rules.settings import Settings
from assistant.rules.visibility import Viewer
from assistant.rules.working_time import answer_by
from assistant.service.actions import Action, perform
from assistant.service.assistants import my_assistant
from assistant.service.attention import clear_about, mark_decided, open_entry_about, raise_entry
from assistant.service.organisation import colour_choices
from assistant.service.request_kinds import RequestKind, give_draft_to_owner, register
from assistant.service.requests import RequestService
from assistant.service.setup import settings_for
from assistant.service.visibility import find_visible, get_visible, parse_id
from assistant.store.interface import Store, Transaction

STATUS_ROUND = "status_round"
STATUS_ASKS = ("status", "confirm")
OPEN_ASKS = (PersonRequestState.TO_BE_SENT, PersonRequestState.SENT)
MISSING = "missing"


def anchor_of(assistant: AssistantRecord, settings: Settings) -> date:
    """A date in a week with a report, for reports every two weeks: the week the manager
    took the assistant on."""
    since = assistant.manager_since or assistant.created_at
    return since.astimezone(ZoneInfo(settings.time_zone)).date()


def _clean(text: str | None) -> str:
    return text.strip() if text else ""


# Reading the project of an ask ------------------------------------------------------------


async def project_of_ask(reader: Transaction | Action, ask: PersonRequest) -> str | None:
    if ask.asks == "status" and ask.subject_kind == "project":
        return ask.subject_id
    if ask.asks == "confirm" and ask.subject_kind == "statement_draft":
        draft = await reader.get(StatementDraft, UUID(ask.subject_id))
        return draft.project_id if draft else None
    return None


async def open_status_asks(
    reader: Transaction | Action,
    assistant: AssistantRecord,
    project_id: str,
    person_id: str | None = None,
) -> list[PersonRequest]:
    found = []
    for ask in await reader.find(PersonRequest, assistant_id=assistant.id):
        if ask.state not in OPEN_ASKS or ask.asks not in STATUS_ASKS:
            continue
        if person_id is not None and ask.person_id != person_id:
            continue
        if await project_of_ask(reader, ask) == project_id:
            found.append(ask)
    return found


async def end_status_asks(
    action: Action,
    assistant: AssistantRecord,
    project_id: str,
    to: PersonRequestState,
    because: str,
    person_id: str | None = None,
) -> None:
    for ask in await open_status_asks(action, assistant, project_id, person_id):
        name = (
            "assistant.ask_answered"
            if to is PersonRequestState.ANSWERED
            else ("assistant.ask_withdrawn")
        )
        await action.change(name, ask, ask.evolve(state=to), details={"because": because})


async def latest_statement(
    reader: Transaction | Action, assistant: AssistantRecord, project_id: str
) -> ConfirmedStatement | None:
    """The newest version: the one no later version points back to (times can be equal)."""
    found = await reader.find(ConfirmedStatement, assistant_id=assistant.id, project_id=project_id)
    corrected = {s.previous_version_id for s in found}
    heads = [s for s in found if s.id not in corrected] or found
    return max(heads, key=lambda s: s.created_at) if heads else None


async def followed_of(
    reader: Transaction | Action, assistant: AssistantRecord, project_id: str
) -> FollowedProject | None:
    found = await reader.find(FollowedProject, assistant_id=assistant.id, project_id=project_id)
    return found[0] if found else None


# Assignments and news -------------------------------------------------------------------


def assignment_states(assignments: list[AssignmentRecord], project_id: str) -> dict[str, str]:
    return {a.id: a.state for a in assignments if a.project_id == project_id}


async def note_assignment_changes(
    action: Action,
    followed: FollowedProject,
    assignments: list[AssignmentRecord],
) -> FollowedProject:
    """Keep each change seen to an assignment on the project since the window started."""
    now = assignment_states(assignments, followed.project_id)
    before = followed.assignment_states
    titles = {a.id: a.title for a in assignments}
    changes: list[AssignmentChange] = []
    for assignment_id in sorted(set(now) | set(before)):
        after = now.get(assignment_id, MISSING)
        earlier = before.get(assignment_id)
        if after == earlier or (earlier is None and after == MISSING):
            continue
        changes.append(
            AssignmentChange(
                assignment_id=assignment_id,
                title=titles.get(assignment_id, "An assignment")[:300],
                before=earlier,
                after=after,
                at=action.now,
            )
        )
    if not changes and now == before:
        return followed
    kept = [*followed.assignment_changes, *changes][-MAX_ASSIGNMENT_CHANGES:]
    states = {k: v for k, v in now.items()}
    return await action.change(
        "assistant.project_assignments_seen",
        followed,
        followed.evolve(assignment_states=states, assignment_changes=kept),
        details={"project_id": followed.project_id, "changes": len(changes)},
    )


STATE_WORDS = {
    "in_progress": "in progress",
    "blocked": "blocked",
    "waiting_for_review": "waiting for review",
    "approved": "approved",
    MISSING: "no longer found",
}


async def project_news(
    reader: Transaction | Action,
    assistant: AssistantRecord,
    followed: FollowedProject,
    project: ProjectRecord,
) -> list[str]:
    """The project's news since the last report, one line each; empty when there is none."""
    since = followed.since()
    lines: list[str] = []
    items = await reader.find(ActionItem, assistant_id=assistant.id, project_id=project.id)
    for item in items:
        for update in await reader.find(Update, item_id=item.id):
            if update.created_at < since:
                continue
            line = f'Update on the action item "{item.text}": {WORD_LABELS[update.word]}.'
            if update.source == "assignment":
                line += " (Shown from its assignment.)"
            if update.note:
                line += f" Note from its owner: {update.note}"
            if update.date_asked_for:
                line += f" Asked for {update.date_asked_for:%A %d %B}."
            lines.append(line)
    for change in followed.assignment_changes:
        if change.at < since:
            continue
        before = STATE_WORDS.get(change.before or "", "new on the project")
        lines.append(
            f'Assignment "{change.title}": {before}, now {STATE_WORDS.get(change.after, "")}.'
        )
    if followed.spend_at_start is not None and followed.spend_at_start != project.spend:
        lines.append(
            f"Spend: {followed.spend_at_start} at the start, {project.spend} now, "
            f"of a budget of {project.budget}."
        )
    return lines


# Asking confirmers -----------------------------------------------------------------------


async def ask_for_status(
    action: Action, assistant: AssistantRecord, request: RequestRecord, text: str | None
) -> dict[str, Any]:
    """One request per confirmer: for each of their projects, the assistant's draft to
    confirm or correct, or else a request to give the status (from their last confirmed
    statement, or an empty form)."""
    assert request.target_id is not None
    settings = await settings_for(action, assistant)
    needed_by = next_report_after(action.now, settings, anchor_of(assistant, settings))
    ask_ids: list[str] = []
    for wanted in request.payload.get("projects", []):
        followed = await followed_of(action, assistant, wanted["project_id"])
        if followed is None or followed.confirmer_id != request.target_id:
            continue  # stopped following, or someone else confirms it now
        draft_id = wanted.get("draft_id")
        draft = await action.get(StatementDraft, UUID(draft_id)) if draft_id else None
        if draft is not None and draft.state is DraftState.DRAFT:
            draft = await give_draft_to_owner(action, draft)
            asks, subject_kind, subject_id = "confirm", "statement_draft", str(draft.id)
        else:
            asks, subject_kind, subject_id = "status", "project", followed.project_id
        ask = await action.add(
            "assistant.ask_sent",
            PersonRequest(
                organisation_id=assistant.organisation_id,
                assistant_id=assistant.id,
                created_at=action.now,
                person_id=request.target_id,
                asks=asks,
                subject_kind=subject_kind,
                subject_id=subject_id,
                answer_by=needed_by or answer_by(action.now, settings),
                sent_at=action.now,
                state=PersonRequestState.SENT,
            ),
            details={"asks": asks, "subject_kind": subject_kind},
        )
        ask_ids.append(str(ask.id))
        await action.change(
            "assistant.status_asked",
            followed,
            followed.evolve(asked_at=action.now),
            details={"project_id": followed.project_id},
        )
    return {"payload": {**request.payload, "ask_ids": ask_ids}}


async def _asks_unanswered(reader: Transaction | Action, request: RequestRecord) -> bool:
    ids = request.payload.get("ask_ids") or []
    for ask_id in ids:
        ask = await reader.get(PersonRequest, UUID(ask_id))
        if ask is None or ask.state is not PersonRequestState.SENT:
            return False
    return bool(ids)


async def _take_back(action: Action, request: RequestRecord) -> None:
    for ask_id in request.payload.get("ask_ids") or []:
        ask = await action.get(PersonRequest, UUID(ask_id))
        if ask is None or ask.state is not PersonRequestState.SENT:
            continue
        await action.change(
            "assistant.ask_withdrawn",
            ask,
            ask.evolve(state=PersonRequestState.WITHDRAWN),
            details={"because": "undone"},
        )
        if ask.asks == "confirm":
            draft = await action.get(StatementDraft, UUID(ask.subject_id))
            if draft is not None and draft.state is DraftState.WITH_OWNER:
                await action.change(
                    "assistant.statement_taken_back", draft, draft.evolve(state=DraftState.DRAFT)
                )


register(
    RequestKind(
        STATUS_ROUND,
        ActionKind.ASK_OWNER,
        "Status",
        ask_for_status,
        editable=False,  # carries no wording: the owner reads any draft in the module (BR-4)
        to_owner=True,
        can_undo=_asks_unanswered,
        undo=_take_back,
    )
)


async def withdraw_stale_rounds(action: Action, assistant: AssistantRecord) -> int:
    """Withdraw status requests not yet carried out none of whose projects is still followed
    and confirmed by the person it asks, e.g. after a change of who confirms."""
    withdrawn = 0
    for request in await action.find(
        RequestRecord, assistant_id=assistant.id, request_kind=STATUS_ROUND
    ):
        if request.state not in (RequestState.WAITING, RequestState.APPROVED):
            continue
        still = False
        for wanted in request.payload.get("projects", []):
            followed = await followed_of(action, assistant, wanted["project_id"])
            still = still or (followed is not None and followed.confirmer_id == request.target_id)
        if not still:
            await action.change(
                "assistant.request_withdrawn",
                request,
                request.evolve(state=RequestState.WITHDRAWN, decided_at=action.now),
                details={"because": "subject_changed"},
            )
            withdrawn += 1
    return withdrawn


def the(person: Person | None) -> str:
    return f"the {person.display_name}" if person else "a person"


async def propose_status_round(
    action: Action,
    boundary: Boundary,
    requests: RequestService,
    assistant: AssistantRecord,
    confirmer_id: str,
    followed: list[FollowedProject],
) -> RequestRecord:
    """Ask one confirmer for the status of their projects, through the approval check."""
    projects = []
    for entry in followed:
        drafts = [
            d
            for d in await action.find(
                StatementDraft, assistant_id=assistant.id, project_id=entry.project_id
            )
            if d.state is DraftState.DRAFT and d.owner_id == confirmer_id
        ]
        projects.append(
            {
                "project_id": entry.project_id,
                "project_name": entry.project_name,
                "draft_id": str(drafts[-1].id) if drafts else None,
            }
        )
    person = await boundary.people.person(confirmer_id)
    names = ", ".join(p["project_name"] or "a project" for p in projects)
    return await requests.propose(
        action,
        assistant,
        request_kind=STATUS_ROUND,
        effect=short(f"Ask {the(person)} for the status of {names}."),
        subject_kind="person",
        subject_id=confirmer_id,
        target_id=confirmer_id,
        payload={"projects": projects},
    )


# The service -------------------------------------------------------------------------------


class StatusService:
    def __init__(self, boundary: Boundary, store: Store, requests: RequestService) -> None:
        self.boundary = boundary
        self.store = store
        self.requests = requests

    async def _manager(self, viewer: Viewer) -> AssistantRecord:
        async with self.store.transaction(viewer.organisation_id) as tx:
            return await my_assistant(tx, viewer)

    async def visible_projects(self, manager_id: str) -> dict[str, ProjectRecord]:
        return {p.id: p for p in await self.boundary.work_data.projects(manager_id)}

    async def confirmer(self, viewer: Viewer, person_id: str) -> Person:
        person = await self.boundary.people.person(person_id)
        if (
            person is None
            or person.organisation_id != viewer.organisation_id
            or (person.kind != "person")
        ):
            raise NotAllowedValue(rules.NOT_A_CONFIRMER)
        return person

    # Following ---------------------------------------------------------------------------

    async def projects(self, viewer: Viewer) -> dict[str, Any]:
        """The projects the manager can see, with whether each is followed and who confirms
        it. A followed project they can no longer see is not listed."""
        assistant = await self._manager(viewer)
        visible = await self.visible_projects(viewer.person_id)
        async with self.store.transaction(viewer.organisation_id) as tx:
            followed = {
                f.project_id: f for f in await tx.find(FollowedProject, assistant_id=assistant.id)
            }
        listed = []
        for project in visible.values():
            entry = followed.get(project.id)
            confirmer = await self.boundary.people.person(entry.confirmer_id) if entry else None
            listed.append(
                {
                    "id": project.id,
                    "name": project.name,
                    "owner_id": project.owner_id,
                    "budget": project.budget,
                    "spend": project.spend,
                    "source_label": project.source_label,
                    "read_at": project.read_at.isoformat(),
                    "followed": entry is not None,
                    "confirmer": (
                        {
                            "id": entry.confirmer_id,
                            "display_name": confirmer.display_name if confirmer else None,
                        }
                        if entry
                        else None
                    ),
                }
            )
        return {"projects": listed}

    async def follow(
        self, viewer: Viewer, project_id: str, confirmer_id: str | None = None
    ) -> dict[str, Any]:
        """Follow a project the manager can see: its owner confirms its status unless the
        manager chooses someone else."""
        await self._manager(viewer)
        project = (await self.visible_projects(viewer.person_id)).get(project_id)
        if project is None:
            raise NotFound()
        confirmer = await self.confirmer(viewer, confirmer_id or project.owner_id)
        assignments = await self.boundary.work_data.assignments(viewer.person_id)

        async def work(action: Action) -> None:
            assistant = await my_assistant(action.tx, viewer)
            if assistant.state is AssistantState.NEW:
                raise NotAllowedValue(rules.FORM_FIRST)
            action.assistant_id = assistant.id
            current = await followed_of(action, assistant, project_id)
            if current is not None:
                if confirmer_id is not None and current.confirmer_id != confirmer.id:
                    await self._change_confirmer(action, assistant, current, confirmer.id)
                return
            await action.add(
                "assistant.project_followed",
                FollowedProject(
                    organisation_id=assistant.organisation_id,
                    assistant_id=assistant.id,
                    created_at=action.now,
                    project_id=project.id,
                    confirmer_id=confirmer.id,
                    project_name=project.name,
                    window_start=action.now,
                    spend_at_start=project.spend,
                    assignment_states=assignment_states(assignments, project.id),
                ),
                details={"project_id": project.id, "confirmer_id": confirmer.id},
            )

        await perform(self.boundary, self.store, viewer.organisation_id, viewer.person_id, work)
        return await self.projects(viewer)

    async def _change_confirmer(
        self,
        action: Action,
        assistant: AssistantRecord,
        followed: FollowedProject,
        person_id: str,
    ) -> FollowedProject:
        """Someone else confirms the project's status from now on: what the previous person
        was asked about it is withdrawn and their drafts are replaced."""
        previous = followed.confirmer_id
        await end_status_asks(
            action, assistant, followed.project_id, PersonRequestState.WITHDRAWN,
            "confirmer_changed", previous,
        )  # fmt: skip
        for draft in await action.find(
            StatementDraft, assistant_id=assistant.id, project_id=followed.project_id
        ):
            if draft.owner_id == previous and draft.state in (
                DraftState.DRAFT,
                DraftState.WITH_OWNER,
            ):
                await action.change(
                    "assistant.statement_draft_replaced",
                    draft,
                    draft.evolve(state=DraftState.REPLACED),
                )
        changed = await action.change(
            "assistant.confirmer_changed",
            followed,
            followed.evolve(confirmer_id=person_id),
            details={"project_id": followed.project_id, "confirmer_id": person_id},
        )
        await withdraw_stale_rounds(action, assistant)
        entry = await open_entry_about(action, rules.PROJECT_SUBJECT, str(followed.id))
        if entry is not None:
            await mark_decided(action, entry, rules.CHANGE_CONFIRMER, person_id)
        return changed

    async def _stop_following(
        self, action: Action, assistant: AssistantRecord, followed: FollowedProject
    ) -> None:
        await end_status_asks(
            action, assistant, followed.project_id, PersonRequestState.WITHDRAWN, "unfollowed"
        )
        entry = await open_entry_about(action, rules.PROJECT_SUBJECT, str(followed.id))
        if entry is not None:
            await mark_decided(action, entry, rules.STOP_FOLLOWING)
        await action.remove(
            "assistant.project_unfollowed", followed, details={"project_id": followed.project_id}
        )
        await withdraw_stale_rounds(action, assistant)

    async def _followed_for_manager(
        self, action: Action, viewer: Viewer, project_id: str
    ) -> tuple[AssistantRecord, FollowedProject]:
        assistant = await my_assistant(action.tx, viewer)
        action.assistant_id = assistant.id
        followed = await followed_of(action, assistant, project_id)
        if followed is None:
            raise NotFound()
        return assistant, followed

    async def change_confirmer(
        self, viewer: Viewer, project_id: str, person_id: str
    ) -> dict[str, Any]:
        await self._manager(viewer)
        person = await self.confirmer(viewer, person_id)

        async def work(action: Action) -> None:
            assistant, followed = await self._followed_for_manager(action, viewer, project_id)
            if followed.confirmer_id != person.id:
                await self._change_confirmer(action, assistant, followed, person.id)

        await perform(self.boundary, self.store, viewer.organisation_id, viewer.person_id, work)
        return await self.projects(viewer)

    async def unfollow(self, viewer: Viewer, project_id: str) -> dict[str, Any]:
        async def work(action: Action) -> None:
            assistant, followed = await self._followed_for_manager(action, viewer, project_id)
            await self._stop_following(action, assistant, followed)

        await perform(self.boundary, self.store, viewer.organisation_id, viewer.person_id, work)
        return await self.projects(viewer)

    # Decisions on a followed project's attention entry -----------------------------------

    async def decide(
        self,
        action: Action,
        viewer: Viewer,
        assistant: AssistantRecord,
        followed_id: str,
        label: str,
        person: Person | None,
    ) -> None:
        followed = await action.get(FollowedProject, UUID(followed_id))
        entry = await open_entry_about(action, rules.PROJECT_SUBJECT, followed_id)
        if followed is None or followed.assistant_id != assistant.id or entry is None:
            raise AlreadyChanged()
        if label == rules.STOP_FOLLOWING:
            await self._stop_following(action, assistant, followed)
        elif label == rules.CHANGE_CONFIRMER:
            assert person is not None
            await self._change_confirmer(action, assistant, followed, person.id)
        elif label == rules.ASK_AGAIN:
            await propose_status_round(
                action, self.boundary, self.requests, assistant, followed.confirmer_id,
                [followed],
            )  # fmt: skip
            await mark_decided(action, entry, rules.ASK_AGAIN, followed.confirmer_id)

    # Owners: status requests and statements ---------------------------------------------

    async def _ask_for_owner(
        self, reader: Transaction | Action, viewer: Viewer, raw_id: str
    ) -> tuple[PersonRequest, AssistantRecord, FollowedProject | None]:
        """A status request, for the person asked only: it may hold a draft (BR-4)."""
        tx = reader.tx if isinstance(reader, Action) else reader
        ask = await get_visible(tx, viewer, PersonRequest, parse_id(raw_id))
        if ask.person_id != viewer.person_id or ask.asks not in STATUS_ASKS:
            raise NotFound()
        assistant = await reader.get(AssistantRecord, ask.assistant_id or UUID(int=0))
        if assistant is None or assistant.state is AssistantState.ENDED:
            raise NotFound()
        project_id = await project_of_ask(reader, ask)
        followed = await followed_of(reader, assistant, project_id) if project_id else None
        return ask, assistant, followed

    async def _request_view(
        self,
        reader: Transaction,
        ask: PersonRequest,
        assistant: AssistantRecord,
        followed: FollowedProject | None,
    ) -> dict[str, Any]:
        """Who it is for, the project, the wording to start from, its source label and when
        it is needed: the assistant's draft (no colour chosen), else the owner's last
        confirmed statement, else an empty form."""
        manager = await self.boundary.people.person(assistant.manager_id)
        project_id = await project_of_ask(reader, ask)
        start, wording, colour, source = "empty", None, None, None
        if ask.asks == "confirm":
            draft = await reader.get(StatementDraft, UUID(ask.subject_id))
            if draft is not None:
                start, wording, source = "draft", draft.wording, draft.source
        elif project_id is not None:
            last = await latest_statement(reader, assistant, project_id)
            if last is not None:
                start, wording, colour, source = (
                    "last_confirmed", last.wording, last.colour, last.source,
                )  # fmt: skip
        return {
            "id": str(ask.id),
            "asks": ask.asks,
            "state": ask.state.value,
            "for": {
                "id": assistant.manager_id,
                "display_name": manager.display_name if manager else None,
            },
            "project": {
                "id": project_id,
                "name": followed.project_name if followed else None,
            },
            "start": start,
            "wording": wording.model_dump() if wording else None,
            "colour": colour,
            "source": source,
            "needed_by": ask.answer_by.isoformat(),
            "colours": await colour_choices(reader, assistant.organisation_id),
        }

    async def status_requests(self, viewer: Viewer) -> dict[str, Any]:
        """What the person asking is asked to confirm or give, oldest first."""
        async with self.store.transaction(viewer.organisation_id) as tx:
            found = await find_visible(tx, viewer, PersonRequest, person_id=viewer.person_id)
            shown = []
            for ask in found:
                if ask.asks not in STATUS_ASKS or ask.state not in OPEN_ASKS:
                    continue
                try:
                    _, assistant, followed = await self._ask_for_owner(tx, viewer, str(ask.id))
                except NotFound:
                    continue
                shown.append(await self._request_view(tx, ask, assistant, followed))
        return {"count": len(shown), "requests": shown}

    async def status_request(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        async with self.store.transaction(viewer.organisation_id) as tx:
            ask, assistant, followed = await self._ask_for_owner(tx, viewer, raw_id)
            return await self._request_view(tx, ask, assistant, followed)

    async def answer(
        self,
        viewer: Viewer,
        raw_id: str,
        *,
        colour: str | None,
        reason: str | None,
        what_changed: str | None = None,
        what_comes_next: str | None = None,
    ) -> dict[str, Any]:
        """The owner confirms or corrects the draft, or gives the status themselves."""
        problem = rules.statement_refusal(colour, reason, what_changed, what_comes_next)
        if problem:
            raise NotAllowedValue(problem)
        wording = StatementWording(
            reason=_clean(reason),
            what_changed=_clean(what_changed),
            what_comes_next=_clean(what_comes_next),
        )
        owner = await self.boundary.people.person(viewer.person_id)

        async def work(action: Action) -> ConfirmedStatement:
            ask, assistant, _ = await self._ask_for_owner(action, viewer, raw_id)
            action.assistant_id = assistant.id
            if ask.state not in OPEN_ASKS:
                raise NotAllowedValue(rules.ALREADY_ANSWERED)
            project_id = await project_of_ask(action, ask)
            if project_id is None:
                raise NotFound()
            draft = None
            if ask.asks == "confirm":
                draft = await action.get(StatementDraft, UUID(ask.subject_id))
            previous = await latest_statement(action, assistant, project_id)
            fields: dict[str, Any] = {
                "author_id": viewer.person_id,
                "mark": "confirmed",
                "source": owner.display_name if owner else "Its owner",
            }
            if draft is not None:
                fields["source"] = draft.source
                fields["draft_id"] = draft.id
                if wording == draft.wording:
                    fields["author_id"] = draft.author_id  # the assistant wrote it
                else:
                    fields.update(
                        mark="corrected",
                        unconfirmed_wording=draft.wording,
                        changed_points=changed_points(wording, str(colour), draft.wording, None),
                    )
                await action.change(
                    "assistant.statement_answered", draft, draft.evolve(state=DraftState.ANSWERED)
                )
            statement = await action.add(
                "assistant.statement_confirmed",
                ConfirmedStatement(
                    organisation_id=assistant.organisation_id,
                    assistant_id=assistant.id,
                    created_at=action.now,
                    project_id=project_id,
                    owner_id=viewer.person_id,
                    colour=colour,  # checked above
                    wording=wording,
                    previous_version_id=previous.id if previous else None,
                    ask_id=ask.id,
                    **fields,
                ),
                details={"project_id": project_id, "mark": fields["mark"]},
            )
            await end_status_asks(
                action, assistant, project_id, PersonRequestState.ANSWERED, "confirmed",
                viewer.person_id,
            )  # fmt: skip
            followed = await followed_of(action, assistant, project_id)
            if followed is not None:
                await clear_about(action, rules.PROJECT_SUBJECT, str(followed.id), "confirmed")
            return statement

        statement = await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )
        return await self.statement(viewer, str(statement.id))

    async def correct(
        self,
        viewer: Viewer,
        raw_id: str,
        *,
        colour: str | None,
        reason: str | None,
        what_changed: str | None = None,
        what_comes_next: str | None = None,
    ) -> dict[str, Any]:
        """The owner adds a correction at any time: a new version; the earlier one is kept."""
        problem = rules.statement_refusal(colour, reason, what_changed, what_comes_next)
        if problem:
            raise NotAllowedValue(problem)
        wording = StatementWording(
            reason=_clean(reason),
            what_changed=_clean(what_changed),
            what_comes_next=_clean(what_comes_next),
        )

        async def work(action: Action) -> ConfirmedStatement:
            earlier = await get_visible(action.tx, viewer, ConfirmedStatement, parse_id(raw_id))
            if earlier.owner_id != viewer.person_id:
                raise NotFound()
            assistant = await action.get(AssistantRecord, earlier.assistant_id or UUID(int=0))
            if assistant is None:
                raise NotFound()
            action.assistant_id = assistant.id
            latest = await latest_statement(action, assistant, earlier.project_id)
            if latest is None or latest.id != earlier.id:
                raise NotAllowedValue(rules.ONLY_LATEST)
            return await action.add(
                "assistant.statement_corrected",
                ConfirmedStatement(
                    organisation_id=assistant.organisation_id,
                    assistant_id=assistant.id,
                    created_at=action.now,
                    project_id=earlier.project_id,
                    owner_id=viewer.person_id,
                    colour=colour,  # checked above
                    wording=wording,
                    source=earlier.source,
                    author_id=viewer.person_id,
                    mark="corrected",
                    previous_version_id=earlier.id,
                    changed_points=changed_points(
                        wording, str(colour), earlier.wording, earlier.colour
                    ),
                ),
                details={"project_id": earlier.project_id, "mark": "corrected"},
            )

        statement = await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )
        return await self.statement(viewer, str(statement.id))

    async def my_statements(self, viewer: Viewer) -> dict[str, Any]:
        """The latest confirmed version of each statement the person asking owns, for each
        manager who follows the project, so that they can add a correction."""
        async with self.store.transaction(viewer.organisation_id) as tx:
            found = await find_visible(tx, viewer, ConfirmedStatement, owner_id=viewer.person_id)
            heads: list[tuple[ConfirmedStatement, AssistantRecord, str | None]] = []
            for key in sorted({(s.assistant_id, s.project_id) for s in found}, key=str):
                assistant = await tx.get(AssistantRecord, key[0] or UUID(int=0))
                if assistant is None or assistant.state is AssistantState.ENDED:
                    continue
                latest = await latest_statement(tx, assistant, key[1])
                if latest is None or latest.owner_id != viewer.person_id:
                    continue
                followed = await followed_of(tx, assistant, key[1])
                heads.append((latest, assistant, followed.project_name if followed else None))
        shown = []
        for latest, assistant, project_name in sorted(
            heads, key=lambda h: h[0].created_at, reverse=True
        ):
            manager = await self.boundary.people.person(assistant.manager_id)
            view = await self.statement(viewer, str(latest.id))
            view["project_name"] = project_name
            view["for"] = {
                "id": assistant.manager_id,
                "display_name": manager.display_name if manager else None,
            }
            shown.append(view)
        return {"count": len(shown), "statements": shown}

    async def statement(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        """A confirmed statement for its owner or the manager: for a correction, who made
        it, when and which points changed; never the wording the owner did not confirm."""
        async with self.store.transaction(viewer.organisation_id) as tx:
            statement = await get_visible(tx, viewer, ConfirmedStatement, parse_id(raw_id))
            draft = await tx.get(StatementDraft, statement.draft_id) if statement.draft_id else None
        author = await self.boundary.people.person(statement.author_id)
        return {
            "id": str(statement.id),
            "project_id": statement.project_id,
            "owner_id": statement.owner_id,
            "colour": statement.colour,
            "colour_words": COLOUR_WORDS[statement.colour],
            "wording": statement.wording.model_dump(),
            "source": statement.source,
            "author": {
                "id": statement.author_id,
                "display_name": author.display_name if author else None,
            },
            "mark": statement.mark,
            "mark_words": MARK_WORDS[statement.mark],
            "created_at": statement.created_at.isoformat(),
            "previous_version_id": (
                str(statement.previous_version_id) if statement.previous_version_id else None
            ),
            "correction": (
                {
                    "by": {
                        "id": statement.author_id,
                        "display_name": author.display_name if author else None,
                    },
                    "at": statement.created_at.isoformat(),
                    "points": list(statement.changed_points),
                    "of": "draft" if statement.draft_id else "earlier_version",
                    "drafted_at": draft.created_at.isoformat() if draft else None,
                }
                if statement.mark == "corrected"
                else None
            ),
        }

    # Reports ------------------------------------------------------------------------------

    async def lines(
        self,
        reader: Transaction | Action,
        assistant: AssistantRecord,
        visible: dict[str, ProjectRecord],
        settings: Settings,
    ) -> list[tuple[FollowedProject, ReportLine]]:
        """One line for each followed project the manager can still see."""
        lines = []
        for followed in await reader.find(FollowedProject, assistant_id=assistant.id):
            project = visible.get(followed.project_id)
            if project is None:
                continue  # it leaves the list and the report
            latest = await latest_statement(reader, assistant, project.id)
            awaiting = (
                latest is not None
                and followed.asked_at is not None
                and latest.created_at < followed.asked_at
            )
            lines.append((followed, line_for(project.id, project.name, latest, awaiting, settings)))
        return lines

    async def make_report(
        self,
        action: Action,
        assistant: AssistantRecord,
        settings: Settings,
        made_by: str,
        due_at: datetime | None = None,
    ) -> Report:
        """Make and keep a report; "since the last report" starts again for each project, and
        two reports in a row without a confirmation raise the project's attention entry."""
        visible = await self.visible_projects(assistant.manager_id)
        lines = await self.lines(action, assistant, visible, settings)
        reports = await action.find(Report, assistant_id=assistant.id)
        previous = max(reports, key=lambda r: r.created_at) if reports else None
        report_lines = [line for _, line in lines]
        report = await action.add(
            "assistant.report_made",
            Report(
                organisation_id=assistant.organisation_id,
                assistant_id=assistant.id,
                created_at=action.now,
                lines=report_lines,
                changed_since_last=changed_since(
                    report_lines, previous.lines if previous else None
                ),
                made_by=made_by,
                due_at=due_at,
            ),
            details={"lines": len(report_lines), "made_by": made_by},
        )
        for followed, _ in lines:
            await self._after_report(action, assistant, followed, visible[followed.project_id])
        return report

    async def _after_report(
        self,
        action: Action,
        assistant: AssistantRecord,
        followed: FollowedProject,
        project: ProjectRecord,
    ) -> None:
        statements = await action.find(
            ConfirmedStatement, assistant_id=assistant.id, project_id=followed.project_id
        )
        confirmed = any(s.created_at >= followed.since() for s in statements)
        missed = 0 if confirmed else followed.missed_reports + 1
        followed = await action.change(
            "assistant.project_window_started",
            followed,
            followed.evolve(
                window_start=action.now,
                spend_at_start=project.spend,
                assignment_changes=[],
                missed_reports=missed,
                project_name=project.name,
            ),
            details={"project_id": followed.project_id, "missed_reports": missed},
        )
        if missed >= rules.MISSED_IN_A_ROW:
            sentence, choices = rules.REASONS[rules.MISSED_REPORTS]
            await raise_entry(
                action,
                subject_kind=rules.PROJECT_SUBJECT,
                subject_id=str(followed.id),
                organisation_id=followed.organisation_id,
                assistant_id=followed.assistant_id,
                reason_code=rules.MISSED_REPORTS,
                reason=sentence,
                choices=choices,
            )

    def _report_view(self, report: Report) -> dict[str, Any]:
        names = {line.project_id: line.project_name for line in report.lines}
        return {
            "id": str(report.id),
            "created_at": report.created_at.isoformat(),
            "made_by": report.made_by,
            "due_at": report.due_at.isoformat() if report.due_at else None,
            "lines": [self._line_view(line, report.changed_since_last) for line in report.lines],
            "changed_since_last": [
                {"project_id": p, "project_name": names.get(p)} for p in report.changed_since_last
            ],
        }

    @staticmethod
    def _line_view(line: ReportLine, changed: list[str]) -> dict[str, Any]:
        return {
            "project_id": line.project_id,
            "project_name": line.project_name,
            "colour": line.colour,
            "colour_words": COLOUR_WORDS[line.colour] if line.colour else None,
            "reason": line.wording.reason if line.wording else None,
            "what_changed": line.wording.what_changed if line.wording else None,
            "what_comes_next": line.wording.what_comes_next if line.wording else None,
            "source": line.source,
            "date": line.statement_date.isoformat() if line.statement_date else None,
            "statement_id": str(line.statement_id) if line.statement_id else None,
            "mark": line.mark,
            "mark_words": MARK_WORDS[line.mark],
            "text": line_text(line),
            "changed_since_last": line.project_id in changed,
        }

    async def report_now(self, viewer: Viewer) -> dict[str, Any]:
        """ "Report now": a report is made at once."""

        async def work(action: Action) -> Report:
            assistant = await my_assistant(action.tx, viewer)
            if assistant.state is AssistantState.NEW:
                raise NotAllowedValue(rules.FORM_FIRST)
            action.assistant_id = assistant.id
            settings = await settings_for(action, assistant)
            return await self.make_report(action, assistant, settings, "manager")

        report = await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )
        return self._report_view(report)

    async def reports(self, viewer: Viewer) -> dict[str, Any]:
        """The reports made, newest first."""
        async with self.store.transaction(viewer.organisation_id) as tx:
            assistant = await my_assistant(tx, viewer)
            found = await find_visible(tx, viewer, Report, assistant_id=assistant.id)
        ordered = sorted(found, key=lambda r: r.created_at, reverse=True)
        return {"count": len(ordered), "reports": [self._report_view(r) for r in ordered]}

    async def report(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        async with self.store.transaction(viewer.organisation_id) as tx:
            return self._report_view(await get_visible(tx, viewer, Report, parse_id(raw_id)))

    async def page(self, viewer: Viewer) -> dict[str, Any]:
        """Project status as it stands now: a later confirmation shows here at once, and in
        the next report. Only confirmed words are shown (BR-4)."""
        assistant = await self._manager(viewer)
        visible = await self.visible_projects(viewer.person_id)
        async with self.store.transaction(viewer.organisation_id) as tx:
            settings = await settings_for(tx, assistant)
            lines = await self.lines(tx, assistant, visible, settings)
        marks = [line.mark for _, line in lines]
        next_at = next_report_after(
            await self.boundary.clock.now(), settings, anchor_of(assistant, settings)
        )
        return {
            "lines": [self._line_view(line, []) for _, line in lines],
            "confirmed": sum(m in ("confirmed", "corrected") for m in marks),
            "awaiting_confirmation": marks.count("awaiting_confirmation"),
            "next_report_at": next_at.isoformat() if next_at else None,
        }
