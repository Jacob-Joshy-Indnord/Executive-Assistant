"""What the assistant's checked tool requests become (BRD section 11).

Plugged into the work service as its tool-request handler: every request is
checked first (:mod:`assistant.service.checks`); each one that passes becomes a
request through Flow's approval check, a statement draft for its confirmer, or
an attention entry. The assistant never moves a due date or gives an item to
someone else (BR-1), so a change that tries is refused.
"""

from __future__ import annotations

from typing import Any

from assistant.boundary.interfaces import Boundary
from assistant.models.boundary import Person, WorkKind
from assistant.models.records import (
    AssistantRecord,
    AttentionEntry,
    DraftState,
    FollowedProject,
    ItemState,
    PieceOfWork,
    StatementDraft,
    StatementWording,
)
from assistant.rules.tools import refusal
from assistant.service.actions import Action
from assistant.service.checks import CheckedRequest, ToolChecks
from assistant.service.request_kinds import (
    ACTION_ITEM,
    ITEM_CHANGE,
    MESSAGE,
    STATEMENT_CONFIRM,
    UPDATE_ASK,
)
from assistant.service.requests import RequestService


VIRTUAL_EMPLOYEE_ITEM = (
    "an item for a virtual employee follows one of its assignments, which the manager chooses"
)


def the(person: Person | None) -> str:
    return f"the {person.display_name}" if person else "a person"


class ToolIntake:
    def __init__(self, boundary: Boundary, checks: ToolChecks, requests: RequestService) -> None:
        self.boundary = boundary
        self.checks = checks
        self.requests = requests

    async def take(
        self,
        action: Action,
        assistant: AssistantRecord,
        work: PieceOfWork,
        tool_requests: list[Any],
    ) -> list[str]:
        result = await self.checks.check_all(action, assistant, work, tool_requests, action.now)
        refused = list(result.refused)
        for checked in result.accepted:
            reason = await self._use(action, assistant, work, checked)
            if reason:
                refused.append(reason)
        return refused

    async def after(self, assistant: AssistantRecord) -> None:
        await self.requests.carry_out_approved(assistant)

    async def _use(
        self, action: Action, assistant: AssistantRecord, work: PieceOfWork, c: CheckedRequest
    ) -> str | None:
        use = {
            "draft": self._draft,
            "action_item": self._action_item,
            "ask_for_update": self._ask_for_update,
            "status_statement": self._status_statement,
            "raise_attention": self._raise_attention,
        }[c.tool]
        return await use(action, assistant, work, c)

    async def _draft(
        self, action: Action, assistant: AssistantRecord, work: PieceOfWork, c: CheckedRequest
    ) -> str | None:
        to = c.people["to"]
        await self.requests.propose(
            action,
            assistant,
            request_kind=MESSAGE,
            effect=f"Send a message to {the(to)}.",
            subject_kind="person",
            subject_id=to.id,
            target_id=to.id,
            text=c.values["text"],
            work_id=work.id,
        )
        return None

    async def _action_item(
        self, action: Action, assistant: AssistantRecord, work: PieceOfWork, c: CheckedRequest
    ) -> str | None:
        owner = c.people["owner"]
        due = c.values["due_date"]
        times = [t.isoformat() for t in c.values.get("follow_up_times", [])]
        item = c.item
        if item is None:
            if owner.kind == "virtual_employee":
                return refusal(c.index, VIRTUAL_EMPLOYEE_ITEM)
            payload: dict[str, Any] = {"due_date": due.isoformat()}
            if "follow_up_times" in c.values:
                payload["follow_up_times"] = times
            await self.requests.propose(
                action,
                assistant,
                request_kind=ACTION_ITEM,
                effect=f"Add an action item for {the(owner)}, due {due:%A %d %B}.",
                subject_kind="action_item",
                target_id=owner.id,
                text=c.values["text"],
                payload=payload,
                work_id=work.id,
            )
            return None
        if item.state in (ItemState.DONE, ItemState.CLOSED):
            return refusal(c.index, "the item it names is already done or closed")
        if due != item.due_date:
            return refusal(c.index, "the assistant cannot move a due date")
        if owner.id != item.owner_id:
            return refusal(c.index, "the assistant cannot give an item to someone else")
        change: dict[str, Any] = {}
        if "follow_up_times" in c.values:
            change["follow_up_times"] = times
        await self.requests.propose(
            action,
            assistant,
            request_kind=ITEM_CHANGE,
            effect=f"Change an action item of {the(owner)}.",
            subject_kind="action_item",
            subject_id=str(item.id),
            subject_version=item.version,
            target_id=owner.id,
            text=c.values["text"],
            payload=change,
            work_id=work.id,
        )
        return None

    async def _ask_for_update(
        self, action: Action, assistant: AssistantRecord, work: PieceOfWork, c: CheckedRequest
    ) -> str | None:
        owner = c.people["owner"]
        if c.item is not None:
            if c.item.state in (ItemState.DONE, ItemState.CLOSED):
                return refusal(c.index, "the item it names is already done or closed")
            await self.requests.propose(
                action,
                assistant,
                request_kind=UPDATE_ASK,
                effect=f"Ask {the(owner)} for an update on an action item.",
                subject_kind="action_item",
                subject_id=str(c.item.id),
                subject_version=c.item.version,
                target_id=owner.id,
                text=c.values["question"],
                work_id=work.id,
            )
            return None
        assert c.project is not None
        await self.requests.propose(
            action,
            assistant,
            request_kind=UPDATE_ASK,
            effect=f"Ask {the(owner)} for the status of {c.project.name}.",
            subject_kind="project",
            subject_id=c.project.id,
            target_id=owner.id,
            text=c.values["question"],
            payload={"project_id": c.project.id, "project_name": c.project.name},
            work_id=work.id,
        )
        return None

    async def _status_statement(
        self, action: Action, assistant: AssistantRecord, work: PieceOfWork, c: CheckedRequest
    ) -> str | None:
        """Kept as a draft for the person who confirms the project's status. They get it
        when they are next asked, or at once, through the approval check, when the manager's
        own message asked for it. It has no colour: the owner chooses it."""
        project = c.project
        assert project is not None
        followed = await action.find(
            FollowedProject, assistant_id=assistant.id, project_id=project.id
        )
        confirmer_id = followed[0].confirmer_id if followed else project.owner_id
        for older in await action.find(
            StatementDraft, assistant_id=assistant.id, project_id=project.id
        ):
            if older.state is DraftState.DRAFT:
                await action.change(
                    "assistant.statement_draft_replaced",
                    older,
                    older.evolve(state=DraftState.REPLACED),
                )
        draft = await action.add(
            "assistant.statement_drafted",
            StatementDraft(
                organisation_id=assistant.organisation_id,
                assistant_id=assistant.id,
                created_at=action.now,
                project_id=project.id,
                owner_id=confirmer_id,
                wording=StatementWording(
                    reason=c.values["reason"],
                    what_changed=c.values.get("what_changed", ""),
                    what_comes_next=c.values.get("what_comes_next", ""),
                ),
                source=c.values["source"][:200],
                author_id=assistant.flow_id,
            ),
            details={"project_id": project.id},
        )
        if work.work_kind is WorkKind.REQUEST:
            confirmer = await self.boundary.people.person(confirmer_id)
            await self.requests.propose(
                action,
                assistant,
                request_kind=STATEMENT_CONFIRM,
                effect=f"Ask {the(confirmer)} to confirm the status of {project.name}.",
                subject_kind="statement_draft",
                subject_id=str(draft.id),
                subject_version=draft.version,
                target_id=confirmer_id,
                payload={"project_id": project.id, "project_name": project.name},
                work_id=work.id,
            )
        return None

    async def _raise_attention(
        self, action: Action, assistant: AssistantRecord, work: PieceOfWork, c: CheckedRequest
    ) -> str | None:
        await action.add(
            "assistant.attention_raised",
            AttentionEntry(
                organisation_id=assistant.organisation_id,
                assistant_id=assistant.id,
                created_at=action.now,
                subject_kind="assistant",
                subject_id=str(work.id),
                reason=c.values["subject"],
                known=c.values["known"],
                choices=c.values["choices"],
            ),
            details={"work_id": str(work.id)},
        )
        return None
