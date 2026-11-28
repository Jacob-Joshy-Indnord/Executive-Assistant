"""Requests through Flow's approval check, and the manager's decisions (Stories 3.3, 3.4).

A request is checked by Flow as it is made, with the level, limits and today's
count that apply (BR-6). Flow's answer routes it: carried out, waiting under
"Needs your approval", or declined with Flow's reason.

The manager approves (once, however often it is pressed and in however many
windows: the change from waiting to approved is version-checked), edits and
approves (both texts are kept, the edited one is carried out), or declines
(with an optional reason). A request to confirm a statement shows the owner
and the project only, and cannot be edited (BR-4).

Carrying out: the module does its own part (for example adds the item), then
asks Flow to carry out the rest, in one transaction. When Flow cannot, the
module's part is rolled back and the request is failed with Flow's reason, to
be tried again. If the transaction is lost after Flow has carried the request
out (for example the audit record cannot be written), trying again is safe:
Flow's carry-out and undo are idempotent per request id (see
:class:`~assistant.boundary.interfaces.Approval`). Waiting requests whose
subject is done, closed or changed are withdrawn.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from assistant.boundary.interfaces import Boundary
from assistant.errors import AlreadyChanged, ModuleError, NotAllowedValue, NotFound
from assistant.models.boundary import ApprovalRequest, CheckOutcome
from assistant.models.records import (
    AssistantRecord,
    RequestRecord,
    RequestState,
    SignalKind,
)
from assistant.rules.requests import (
    CANNOT_EDIT,
    CANNOT_UNDO,
    CARRY_OUT_FAILED,
    DONE_FOR_YOU_STATES,
    FLOW_DECLINED,
    FLOW_STILL_ASKS,
    NOT_REACHED,
    NOT_RETRYABLE,
    OPEN_STATES,
    RESUME_FIRST,
    RESUME_TO_UNDO,
    UNDO_FAILED,
    WITHDRAWN,
    reason_refusal,
    short,
    text_refusal,
)
from assistant.rules.visibility import Viewer
from assistant.rules.work import one_sentence
from assistant.rules.working_time import local_date
from assistant.service.actions import Action, perform
from assistant.service.approval_levels import approval_request_for
from assistant.service.assistants import my_assistant
from assistant.service.closing import CLOSING_HANDLERS, Closing
from assistant.service.decision_papers import waiting_papers
from assistant.service.due import DUE_STEPS, DueContext, DueStep
from assistant.service.log import log_event
from assistant.service.pausing import running
from assistant.service.request_kinds import REQUEST_KINDS, kind_of, subject_has_moved_on
from assistant.service.setup import FLOW, settings_for
from assistant.service.signals import keep_signal
from assistant.service.visibility import find_visible, get_visible, parse_id
from assistant.store.interface import Store, Transaction


class _NotCarriedOut(Exception):
    """Flow did not carry the request out: roll back the module's own part."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class RequestService:
    def __init__(self, boundary: Boundary, store: Store) -> None:
        self.boundary = boundary
        self.store = store
        self.after_carry_out: list[Callable[[AssistantRecord], Awaitable[None]]] = []
        """Run once a request is carried out and saved, e.g. to start work it queued."""

    # Making a request ------------------------------------------------------------------

    async def propose(
        self,
        action: Action,
        assistant: AssistantRecord,
        *,
        request_kind: str,
        effect: str,
        subject_kind: str,
        target_id: str | None = None,
        text: str | None = None,
        subject_id: str | None = None,
        subject_version: int | None = None,
        payload: dict[str, Any] | None = None,
        work_id: UUID | None = None,
    ) -> RequestRecord:
        """A new request, checked by Flow at once and routed by its answer. Call
        :meth:`carry_out_approved` once the action is saved."""
        kind = REQUEST_KINDS[request_kind]
        request = RequestRecord(
            organisation_id=assistant.organisation_id,
            assistant_id=assistant.id,
            created_at=action.now,
            action_kind=kind.action_kind,
            request_kind=kind.name,
            subject_kind=subject_kind,
            subject_id=subject_id,
            subject_version=subject_version,
            target_id=target_id,
            work_id=work_id,
            text=text,
            payload=payload or {},
            effect=short(effect),
        )
        outcome = await self._check(
            action, assistant, request, approved_by_manager=False, now=action.now
        )
        if outcome.outcome == "carry_out":
            request = request.evolve(
                state=RequestState.APPROVED, allowed_by="without_approval", decided_at=action.now
            )
        elif outcome.outcome == "declined":
            request = request.evolve(
                state=RequestState.DECLINED,
                declined_by="flow",
                reason=one_sentence(outcome.reason, FLOW_DECLINED),
                decided_at=action.now,
            )
        return await action.add(
            "assistant.request_made",
            request,
            details={"request_kind": kind.name, "outcome": outcome.outcome},
        )

    async def _count_today(
        self,
        reader: Transaction | Action,
        assistant: AssistantRecord,
        request: RequestRecord,
        now: datetime,
    ) -> int:
        """Requests of the same kind of action carried out today, or allowed and about to
        be, in the manager's time zone; the request itself is not counted."""
        settings = await settings_for(reader, assistant)
        today = local_date(now, settings)

        def counts(r: RequestRecord) -> bool:
            if r.id == request.id:
                return False
            if r.done_at is not None:
                return local_date(r.done_at, settings) == today
            return (
                r.state is RequestState.APPROVED
                and local_date(r.decided_at or r.created_at, settings) == today
            )

        found = await reader.find(
            RequestRecord, assistant_id=assistant.id, action_kind=request.action_kind
        )
        return sum(1 for r in found if counts(r))

    async def _approval_request(
        self,
        reader: Transaction | Action,
        assistant: AssistantRecord,
        request: RequestRecord,
        approved_by_manager: bool,
        now: datetime,
    ) -> ApprovalRequest:
        return await approval_request_for(
            reader,
            assistant,
            request.action_kind,
            request_id=request.id,
            effect=short(request.effect or kind_of(request).label + "."),
            target_id=request.target_id,
            count_today=await self._count_today(reader, assistant, request, now),
            approved_by_manager=approved_by_manager,
            text=request.edited_text if request.edited_text is not None else request.text,
            project_ids=list(request.payload.get("project_ids", [])),
        )

    async def _check(
        self,
        reader: Transaction | Action,
        assistant: AssistantRecord,
        request: RequestRecord,
        approved_by_manager: bool,
        now: datetime,
    ) -> CheckOutcome:
        """Flow's approval check: its answer is final and may be stricter (BR-6)."""
        approval = await self._approval_request(
            reader, assistant, request, approved_by_manager, now
        )
        return await self.boundary.approval.check(approval)

    # Carrying out ----------------------------------------------------------------------

    async def carry_out(self, organisation_id: str, request_id: UUID, who: str = FLOW) -> None:
        """Carry out an approved request: the module's part, then Flow's, at most once."""
        carried: list[AssistantRecord] = []

        async def work(action: Action) -> None:
            request = await action.get(RequestRecord, request_id)
            if request is None or request.state is not RequestState.APPROVED:
                return  # already carried out, or no longer wanted
            assistant = await action.get(AssistantRecord, request.assistant_id or UUID(int=0))
            if assistant is None or not await running(action, assistant):
                return  # new, paused or ended: nothing is carried out (BR-14)
            action.assistant_id = assistant.id
            if await subject_has_moved_on(action, request):
                await self._withdraw(action, request, "subject_changed")
                return
            kind = kind_of(request)
            text = request.edited_text if request.edited_text is not None else request.text
            changes = await kind.carry_out(action, assistant, request, text)
            approval = await self._approval_request(
                action,
                assistant,
                request,
                approved_by_manager=request.allowed_by == "manager",
                now=action.now,
            )
            result = await self.boundary.approval.carry_out(approval)
            if not result.done:
                raise _NotCarriedOut(one_sentence(result.reason, CARRY_OUT_FAILED))
            await action.change(
                "assistant.request_carried_out",
                request,
                request.evolve(
                    **changes,
                    state=RequestState.DONE,
                    effect=short(result.effect or approval.effect, 500),
                    undoable=result.undoable and kind.undo is not None,
                    done_at=action.now,
                    reason=None,
                ),
                details={"request_kind": kind.name, "allowed_by": request.allowed_by or "none"},
            )
            carried.append(assistant)

        try:
            await perform(self.boundary, self.store, organisation_id, who, work)
        except AlreadyChanged:
            return  # carried out at the same moment elsewhere
        except _NotCarriedOut as failed:
            await self._fail(organisation_id, request_id, failed.reason, who)
        except ModuleError as error:
            if error.status != 503 or error.code == "not_saved":
                raise
            await self._fail(organisation_id, request_id, NOT_REACHED, who)
        for assistant in carried:
            for hook in self.after_carry_out:
                await hook(assistant)

    async def _fail(self, organisation_id: str, request_id: UUID, reason: str, who: str) -> None:
        async def work(action: Action) -> None:
            request = await action.get(RequestRecord, request_id)
            if request is not None and request.state is RequestState.APPROVED:
                await action.change(
                    "assistant.request_failed",
                    request,
                    request.evolve(state=RequestState.FAILED, reason=reason),
                )

        await perform(self.boundary, self.store, organisation_id, who, work)

    async def carry_out_approved(self, assistant: AssistantRecord) -> None:
        """Carry out what Flow allowed without approval, oldest first."""
        async with self.store.transaction(assistant.organisation_id) as tx:
            approved = await tx.find(
                RequestRecord, assistant_id=assistant.id, state=RequestState.APPROVED
            )
        for request in approved:
            try:
                await self.carry_out(assistant.organisation_id, request.id)
            except ModuleError as error:
                log_event(
                    "assistant.request_not_carried_out",
                    request_id=str(request.id),
                    code=error.code,
                )

    # Withdrawing -----------------------------------------------------------------------

    async def _withdraw(self, action: Action, request: RequestRecord, because: str) -> None:
        await action.change(
            "assistant.request_withdrawn",
            request,
            request.evolve(state=RequestState.WITHDRAWN, decided_at=action.now),
            details={"because": because},
        )

    async def withdraw_for_subject(
        self, action: Action, subject_kind: str, subject_id: str, because: str = "subject_changed"
    ) -> int:
        """Withdraw requests not yet carried out about this subject, e.g. when an owner's
        update makes an ask for an update unnecessary. Returns how many."""
        withdrawn = 0
        for request in await action.find(
            RequestRecord, subject_kind=subject_kind, subject_id=subject_id
        ):
            if request.state in OPEN_STATES:
                await self._withdraw(action, request, because)
                withdrawn += 1
        return withdrawn

    async def withdraw_stale(self, action: Action, assistant: AssistantRecord) -> int:
        """Withdraw every request not yet carried out whose subject has moved on."""
        withdrawn = 0
        for request in await action.find(RequestRecord, assistant_id=assistant.id):
            if request.state in OPEN_STATES and await subject_has_moved_on(action, request):
                await self._withdraw(action, request, "subject_changed")
                withdrawn += 1
        return withdrawn

    # The manager's decisions --------------------------------------------------------------

    async def _mine(
        self, action: Action, viewer: Viewer, request_id: UUID
    ) -> tuple[RequestRecord, AssistantRecord]:
        request = await get_visible(action.tx, viewer, RequestRecord, request_id)
        assistant = await my_assistant(action.tx, viewer)
        if request.assistant_id != assistant.id:
            raise NotFound()
        action.assistant_id = assistant.id
        return request, assistant

    async def approve(self, viewer: Viewer, raw_id: str, text: str | None = None) -> dict[str, Any]:
        """Approve, or edit and approve: carried out once, with the edited text if given."""
        request_id = parse_id(raw_id)

        async def work(action: Action) -> str:
            request, assistant = await self._mine(action, viewer, request_id)
            if request.allowed_by == "manager" and request.state in (
                RequestState.APPROVED,
                RequestState.DONE,
            ):
                return "approved"  # pressed twice, or in a second window
            if request.state is not RequestState.WAITING:
                raise AlreadyChanged()
            if not await running(action, assistant):
                raise NotAllowedValue(RESUME_FIRST)
            if await subject_has_moved_on(action, request):
                await self._withdraw(action, request, "subject_changed")
                return "withdrawn"
            kind = kind_of(request)
            edited: str | None = None
            if text is not None and text != request.text:
                if not kind.editable:
                    raise NotAllowedValue(CANNOT_EDIT)
                problem = text_refusal(text, kind.text_limit)
                if problem:
                    raise NotAllowedValue(problem)
                edited = text
            candidate = request.evolve(edited_text=edited)
            outcome = await self._check(
                action, assistant, candidate, approved_by_manager=True, now=action.now
            )
            if outcome.outcome == "ask_manager":
                raise NotAllowedValue(FLOW_STILL_ASKS)
            if outcome.outcome == "declined":
                await action.change(
                    "assistant.request_declined",
                    request,
                    candidate.evolve(
                        state=RequestState.DECLINED,
                        declined_by="flow",
                        reason=one_sentence(outcome.reason, FLOW_DECLINED),
                        decided_at=action.now,
                    ),
                    details={"declined_by": "flow"},
                )
                return "declined"
            await action.change(
                "assistant.request_approved",
                request,
                candidate.evolve(
                    state=RequestState.APPROVED, allowed_by="manager", decided_at=action.now
                ),
                details={"edited": edited is not None},
            )
            await keep_signal(
                action,
                assistant,
                SignalKind.DRAFT_EDITED if edited is not None else SignalKind.DRAFT_APPROVED,
                subject_kind="request",
                subject_id=str(request.id),
                action_kind=request.action_kind,
                before=request.text,
                after=edited if edited is not None else request.text,
            )
            return "approved"

        outcome = await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )
        if outcome == "withdrawn":
            raise AlreadyChanged(WITHDRAWN)
        if outcome == "approved":
            await self.carry_out(viewer.organisation_id, request_id, viewer.person_id)
        return await self.request(viewer, str(request_id))

    async def decline(
        self, viewer: Viewer, raw_id: str, reason: str | None = None
    ) -> dict[str, Any]:
        """Decline: nothing is carried out, and the reason is kept if one is given."""
        request_id = parse_id(raw_id)
        reason = reason.strip() if reason and reason.strip() else None
        problem = reason_refusal(reason)
        if problem:
            raise NotAllowedValue(problem)

        async def work(action: Action) -> None:
            request, assistant = await self._mine(action, viewer, request_id)
            if request.state is RequestState.DECLINED and request.declined_by == "manager":
                return  # pressed twice
            if request.state is not RequestState.WAITING:
                raise AlreadyChanged()
            await action.change(
                "assistant.request_declined",
                request,
                request.evolve(
                    state=RequestState.DECLINED,
                    declined_by="manager",
                    reason=reason,
                    decided_at=action.now,
                ),
                details={"declined_by": "manager", "with_reason": reason is not None},
            )
            await keep_signal(
                action,
                assistant,
                SignalKind.DRAFT_DECLINED,
                subject_kind="request",
                subject_id=str(request.id),
                action_kind=request.action_kind,
                before=request.text,
                said=reason,
            )

        await perform(self.boundary, self.store, viewer.organisation_id, viewer.person_id, work)
        return await self.request(viewer, str(request_id))

    async def approve_all(self, viewer: Viewer) -> dict[str, Any]:
        """Approve every waiting request to an owner, and nothing else."""
        async with self.store.transaction(viewer.organisation_id) as tx:
            assistant = await my_assistant(tx, viewer)
            if not await running(tx, assistant):
                raise NotAllowedValue(RESUME_FIRST)
            waiting = await tx.find(
                RequestRecord, assistant_id=assistant.id, state=RequestState.WAITING
            )
        approved = 0
        for request in waiting:
            if not kind_of(request).to_owner:
                continue
            try:
                view = await self.approve(viewer, str(request.id))
            except (AlreadyChanged, NotAllowedValue):
                continue  # withdrawn or decided meanwhile
            approved += view["state"] in ("approved", "done")
        return {"approved": approved, **await self.waiting(viewer)}

    async def retry(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        """Try again a request that could not be carried out."""
        request_id = parse_id(raw_id)

        async def work(action: Action) -> bool:
            request, assistant = await self._mine(action, viewer, request_id)
            if request.state is RequestState.APPROVED:
                return True  # pressed twice
            if request.state is not RequestState.FAILED:
                raise NotAllowedValue(NOT_RETRYABLE)
            if not await running(action, assistant):
                raise NotAllowedValue(RESUME_FIRST)
            await action.change(
                "assistant.request_retried",
                request,
                request.evolve(state=RequestState.APPROVED, reason=None),
            )
            return True

        await perform(self.boundary, self.store, viewer.organisation_id, viewer.person_id, work)
        await self.carry_out(viewer.organisation_id, request_id, viewer.person_id)
        return await self.request(viewer, str(request_id))

    async def undo(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        """Undo what was carried out, where that is still possible; the result is listed."""
        request_id = parse_id(raw_id)

        async def work(action: Action) -> None:
            request, assistant = await self._mine(action, viewer, request_id)
            if request.state is RequestState.UNDONE:
                return  # pressed twice
            if not await running(action, assistant):
                raise NotAllowedValue(RESUME_TO_UNDO)
            kind = kind_of(request)
            if (
                request.state is not RequestState.DONE
                or not request.undoable
                or kind.undo is None
                or kind.can_undo is None
                or not await kind.can_undo(action, request)
            ):
                raise NotAllowedValue(CANNOT_UNDO)
            await kind.undo(action, request)
            approval = await self._approval_request(action, assistant, request, True, action.now)
            result = await self.boundary.approval.undo(approval)
            if not result.done:
                raise _NotCarriedOut(one_sentence(result.reason, UNDO_FAILED))
            await action.change(
                "assistant.request_undone",
                request,
                request.evolve(state=RequestState.UNDONE, undone_at=action.now, undo_reason=None),
                details={"request_kind": kind.name},
            )

        try:
            await perform(self.boundary, self.store, viewer.organisation_id, viewer.person_id, work)
        except _NotCarriedOut as failed:
            await self._note_undo_failure(viewer, request_id, failed.reason)
        return await self.request(viewer, str(request_id))

    async def _note_undo_failure(self, viewer: Viewer, request_id: UUID, reason: str) -> None:
        async def work(action: Action) -> None:
            request, _ = await self._mine(action, viewer, request_id)
            await action.change(
                "assistant.request_undo_failed", request, request.evolve(undo_reason=reason)
            )

        await perform(self.boundary, self.store, viewer.organisation_id, viewer.person_id, work)

    # Reading ------------------------------------------------------------------------------

    async def _view(
        self, reader: Transaction, request: RequestRecord, names: dict[str, str | None]
    ) -> dict[str, Any]:
        kind = kind_of(request)
        target = None
        if request.target_id is not None:
            if request.target_id not in names:
                person = await self.boundary.people.person(request.target_id)
                names[request.target_id] = person.display_name if person else None
            target = {"id": request.target_id, "display_name": names[request.target_id]}
        project = None
        if "project_id" in request.payload:
            project = {
                "id": request.payload["project_id"],
                "name": request.payload.get("project_name"),
            }
        can_undo = (
            request.state is RequestState.DONE
            and request.undoable
            and kind.can_undo is not None
            and await kind.can_undo(reader, request)
        )
        approved_by = None
        if request.allowed_by == "manager":
            approved_by = "manager"
        elif request.allowed_by == "without_approval":
            approved_by = "no_approval_needed"
        return {
            "id": str(request.id),
            "kind": kind.name,
            "label": kind.label,
            "action_kind": request.action_kind.value,
            "state": request.state.value,
            "effect": request.effect,
            "target": target,
            "project": project,
            "text": request.edited_text if request.edited_text is not None else request.text,
            "original_text": request.text,
            "edited_text": request.edited_text,
            "editable": kind.editable and request.state is RequestState.WAITING,
            "to_owner": kind.to_owner,
            "reason": request.reason,
            "declined_by": request.declined_by,
            "approved_by": approved_by,
            "can_retry": request.state is RequestState.FAILED,
            "can_undo": can_undo,
            "undo_reason": request.undo_reason,
            "created_at": request.created_at.isoformat(),
            "decided_at": request.decided_at.isoformat() if request.decided_at else None,
            "done_at": request.done_at.isoformat() if request.done_at else None,
            "undone_at": request.undone_at.isoformat() if request.undone_at else None,
        }

    async def request(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        async with self.store.transaction(viewer.organisation_id) as tx:
            request = await get_visible(tx, viewer, RequestRecord, parse_id(raw_id))
            return await self._view(tx, request, {})

    async def waiting(self, viewer: Viewer) -> dict[str, Any]:
        """Needs your approval: waiting requests, in the order they were asked, and decision
        papers waiting for a decision, by due date and marked when late (part 2). A request
        whose subject has moved on is not shown; the due step withdraws it."""
        now = await self.boundary.clock.now()
        async with self.store.transaction(viewer.organisation_id) as tx:
            assistant = await my_assistant(tx, viewer)
            found = await find_visible(
                tx, viewer, RequestRecord, assistant_id=assistant.id, state=RequestState.WAITING
            )
            names: dict[str, str | None] = {}
            shown = [
                await self._view(tx, r, names)
                for r in found
                if not await subject_has_moved_on(tx, r)
            ]
            papers = await waiting_papers(tx, viewer, assistant, now)
        return {"count": len(shown) + len(papers), "requests": shown, "decision_papers": papers}

    async def done_for_you(self, viewer: Viewer) -> dict[str, Any]:
        """Done for you: what was carried out, undone, or could not be carried out, newest
        first, each with its effect, time and whether the manager approved it."""
        async with self.store.transaction(viewer.organisation_id) as tx:
            assistant = await my_assistant(tx, viewer)
            found = [
                r
                for r in await find_visible(tx, viewer, RequestRecord, assistant_id=assistant.id)
                if r.state in DONE_FOR_YOU_STATES
            ]
            order = sorted(
                enumerate(found),
                key=lambda pair: (pair[1].done_at or pair[1].decided_at or pair[1].created_at,
                                  pair[0]),  # fmt: skip
                reverse=True,
            )
            names: dict[str, str | None] = {}
            listed = [await self._view(tx, r, names) for _, r in order]
        return {"count": len(listed), "requests": listed}


# Running what is due, and closing ------------------------------------------------------


class WithdrawStaleRequests:
    """A due step for each request not yet carried out whose subject has moved on."""

    name = "withdraw_stale_requests"

    async def due(self, reader: Transaction, ctx: DueContext, until: Any) -> list[DueStep]:
        steps: list[DueStep] = []
        for request in await reader.find(RequestRecord, assistant_id=ctx.assistant.id):
            if request.state in OPEN_STATES and await subject_has_moved_on(reader, request):
                steps.append(DueStep(str(request.id), min(request.created_at, until)))
        return steps

    async def next_after(self, reader: Transaction, ctx: DueContext, after: Any) -> None:
        return None

    async def run(self, action: Action, ctx: DueContext, step: DueStep) -> None:
        request = await action.get(RequestRecord, UUID(step.subject))
        if (
            request is not None
            and request.state in OPEN_STATES
            and await subject_has_moved_on(action, request)
        ):
            await action.change(
                "assistant.request_withdrawn",
                request,
                request.evolve(state=RequestState.WITHDRAWN, decided_at=action.now),
                details={"because": "subject_changed"},
            )


DUE_STEPS.register(WithdrawStaleRequests())


@CLOSING_HANDLERS.register
async def remove_approved_requests(
    action: Action, assistant: AssistantRecord, closing: Closing
) -> None:
    """Approved requests not yet carried out go with the waiting ones: nothing more is
    carried out for an assistant that was handed over or ended."""
    for request in await action.find(
        RequestRecord, assistant_id=assistant.id, state=RequestState.APPROVED
    ):
        await action.remove(
            "assistant.waiting_request_removed", request, details={"because": closing.reason}
        )
