"""Pieces of work and the chat (Story 3.1).

The manager's message becomes a piece of work. Pieces wait in the order they
were asked; one runs at a time per assistant, guarded by a :class:`WorkSlot`
whose id is derived from the assistant, so two starts at the same moment
cannot both succeed. Starting a piece makes its input pack and hands it to
Flow through the Assistant boundary.

Flow's "Finish a piece of work" stores the reply in the chat and hands the tool
requests to the checks. A result is used once: a second copy finds the piece
no longer running and is ignored. Refused or failed work shows its reason in
one sentence and can be tried again, as a new piece at the end of the queue.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal, Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from assistant.boundary.interfaces import Boundary
from assistant.errors import AlreadyChanged, ModuleError, NotAllowedValue
from assistant.models.boundary import PackInput, WorkKind, WorkOrder, WorkResult
from assistant.models.records import (
    AssistantRecord,
    AssistantState,
    ChatMessage,
    PieceOfWork,
    WorkSlot,
    WorkState,
)
from assistant.rules.visibility import Viewer
from assistant.rules.work import (
    FLOW_REFUSED,
    FORM_FIRST,
    NOT_RETRYABLE,
    NOT_STARTED,
    REPLY_TOO_LONG,
    RETRYABLE,
    WORK_FAILED,
    chat_refusal,
    one_sentence,
)
from assistant.service.actions import Action, perform
from assistant.service.assistants import my_assistant
from assistant.service.closing import CLOSING_HANDLERS, Closing
from assistant.service.input_pack import build_input_pack
from assistant.service.log import log_event
from assistant.service.pausing import running
from assistant.service.setup import FLOW
from assistant.service.visibility import find_visible, get_visible, parse_id
from assistant.service.work_kinds import WORK_KINDS, WorkKindRegistry
from assistant.store.interface import Store

MAX_STARTS_AT_ONCE = 100
"""How many refused pieces one advance may pass over before it stops."""

StartOutcome = Literal["idle", "busy", "started", "refused", "failed"]

FinishHook = Callable[[Action, AssistantRecord, PieceOfWork], Awaitable[None]]
"""Run inside the action in which a piece of work is done, refused or failed."""


class ToolRequestHandler(Protocol):
    """What the checks (Story 3.2) and the approval flow (Story 3.3) plug in."""

    async def take(
        self,
        action: Action,
        assistant: AssistantRecord,
        work: PieceOfWork,
        tool_requests: list[Any],
    ) -> list[str]:
        """Use the tool requests inside the finishing action; returns the refusal reasons."""
        ...

    async def after(self, assistant: AssistantRecord) -> None:
        """After the finishing action is saved, e.g. carry out what needed no approval."""
        ...


class RefuseToolRequests:
    """Until a handler is plugged in, every tool request is refused with a reason."""

    async def take(
        self,
        action: Action,
        assistant: AssistantRecord,
        work: PieceOfWork,
        tool_requests: list[Any],
    ) -> list[str]:
        return ["Tool requests cannot be handled yet, so this one was refused."] * len(
            tool_requests
        )

    async def after(self, assistant: AssistantRecord) -> None:
        return None


def slot_id(assistant: AssistantRecord) -> UUID:
    return uuid5(NAMESPACE_URL, f"https://ea-001.invalid/work-slot/{assistant.id}")


def work_summary(work: PieceOfWork) -> dict[str, Any]:
    """A piece of work as the manager sees it: its state, its reply, any reason, and what was
    refused."""
    return {
        "id": str(work.id),
        "kind": work.work_kind.value,
        "state": work.state.value,
        "reason": work.reason,
        "can_retry": work.state in RETRYABLE,
        "refused_count": len(work.refused),
        "refused": list(work.refused),
        "reply": work.reply,
        "created_at": work.created_at.isoformat(),
        "finished_at": work.finished_at.isoformat() if work.finished_at else None,
    }


def message_view(message: ChatMessage, work: PieceOfWork | None) -> dict[str, Any]:
    """One chat message. The text is returned exactly as written, as plain text."""
    return {
        "id": str(message.id),
        "author": message.author,
        "text": message.text,
        "created_at": message.created_at.isoformat(),
        "work": work_summary(work) if work else None,
    }


class WorkService:
    def __init__(
        self, boundary: Boundary, store: Store, registry: WorkKindRegistry = WORK_KINDS
    ) -> None:
        self.boundary = boundary
        self.store = store
        self.registry = registry
        self.tool_requests: ToolRequestHandler = RefuseToolRequests()
        self.on_finished: list[FinishHook] = []
        """E.g. the meeting whose notes a piece of work was writing fails when it does."""

    async def _finished(
        self, action: Action, assistant: AssistantRecord, piece: PieceOfWork
    ) -> None:
        for hook in self.on_finished:
            await hook(action, assistant, piece)

    # Asking for work ------------------------------------------------------------

    async def enqueue(
        self,
        action: Action,
        assistant: AssistantRecord,
        kind: WorkKind,
        *,
        asked_by: str | None = None,
        message_id: UUID | None = None,
        given_inputs: Sequence[PackInput] = (),
        retry_of: UUID | None = None,
    ) -> PieceOfWork:
        """A new piece of work at the end of the assistant's queue. Call :meth:`advance`
        once the action is saved."""
        return await action.add(
            "assistant.work_asked",
            PieceOfWork(
                organisation_id=assistant.organisation_id,
                assistant_id=assistant.id,
                created_at=action.now,
                work_kind=kind,
                asked_by=asked_by,
                message_id=message_id,
                given_inputs=list(given_inputs),
                retry_of=retry_of,
            ),
            details={"work_kind": kind.value},
        )

    async def send(self, viewer: Viewer, text: str) -> dict[str, Any]:
        """The manager's chat message: kept in the chat and asked of the assistant."""
        refusal = chat_refusal(text)
        if refusal:
            raise NotAllowedValue(refusal)

        async def work(action: Action) -> tuple[AssistantRecord, ChatMessage]:
            assistant = await my_assistant(action.tx, viewer)
            if assistant.state is AssistantState.NEW:
                raise NotAllowedValue(FORM_FIRST)
            action.assistant_id = assistant.id
            message_id = uuid4()
            piece = await self.enqueue(
                action,
                assistant,
                WorkKind.REQUEST,
                asked_by=viewer.person_id,
                message_id=message_id,
            )
            message = await action.add(
                "assistant.chat_message_added",
                ChatMessage(
                    id=message_id,
                    organisation_id=assistant.organisation_id,
                    assistant_id=assistant.id,
                    created_at=action.now,
                    author="manager",
                    text=text,
                    work_id=piece.id,
                ),
                details={"work_id": str(piece.id)},
            )
            return assistant, message

        assistant, message = await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )
        await self.advance(assistant)
        async with self.store.transaction(viewer.organisation_id) as tx:
            piece = await tx.get(PieceOfWork, message.work_id) if message.work_id else None
        return message_view(message, piece)

    async def retry(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        """Try refused or failed work again: a new piece, at the end of the queue."""
        work_id = parse_id(raw_id)

        async def work(action: Action) -> tuple[AssistantRecord, PieceOfWork]:
            piece = await get_visible(action.tx, viewer, PieceOfWork, work_id)
            assistant = await my_assistant(action.tx, viewer)
            if piece.assistant_id != assistant.id:
                raise NotAllowedValue(NOT_RETRYABLE)
            if piece.state not in RETRYABLE:
                raise NotAllowedValue(NOT_RETRYABLE)
            action.assistant_id = assistant.id
            again = await action.find(PieceOfWork, assistant_id=assistant.id, retry_of=piece.id)
            if again:
                return assistant, again[0]  # pressed twice: tried again once
            fresh = await self.enqueue(
                action,
                assistant,
                piece.work_kind,
                asked_by=piece.asked_by,
                message_id=piece.message_id,
                given_inputs=piece.given_inputs,
                retry_of=piece.id,
            )
            message = await action.get(ChatMessage, piece.message_id) if piece.message_id else None
            if message is not None:
                await action.change(
                    "assistant.chat_message_retried",
                    message,
                    message.evolve(work_id=fresh.id),
                    details={"work_id": str(fresh.id)},
                )
            return assistant, fresh

        assistant, fresh = await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )
        await self.advance(assistant)
        async with self.store.transaction(viewer.organisation_id) as tx:
            current = await tx.get(PieceOfWork, fresh.id)
        return work_summary(current or fresh)

    # Reading -----------------------------------------------------------------------

    async def chat(self, viewer: Viewer) -> dict[str, Any]:
        """The manager's chat, oldest first, each message with the state of its work."""
        async with self.store.transaction(viewer.organisation_id) as tx:
            assistant = await my_assistant(tx, viewer)
            messages = await find_visible(tx, viewer, ChatMessage, assistant_id=assistant.id)
            pieces = {w.id: w for w in await tx.find(PieceOfWork, assistant_id=assistant.id)}
        return {
            "messages": [
                message_view(m, pieces.get(m.work_id) if m.work_id else None) for m in messages
            ]
        }

    async def work(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        async with self.store.transaction(viewer.organisation_id) as tx:
            piece = await get_visible(tx, viewer, PieceOfWork, parse_id(raw_id))
        return work_summary(piece)

    # Starting work -------------------------------------------------------------------

    async def advance(self, assistant: AssistantRecord) -> None:
        """Start the oldest waiting piece if nothing is running. Never raises: what could
        not be started stays waiting, or is failed with a reason that can be tried again."""
        for _ in range(MAX_STARTS_AT_ONCE):
            try:
                outcome = await self._start_next(assistant)
            except ModuleError as error:
                log_event(
                    "assistant.work_not_started", assistant_id=str(assistant.id), code=error.code
                )
                return
            if outcome != "refused":
                return

    async def _slot(self, action: Action, assistant: AssistantRecord) -> WorkSlot:
        slot = await action.get(WorkSlot, slot_id(assistant))
        if slot is not None:
            return slot
        return await action.add(
            "assistant.work_slot_made",
            WorkSlot(
                id=slot_id(assistant),
                organisation_id=assistant.organisation_id,
                assistant_id=assistant.id,
                created_at=action.now,
            ),
        )

    async def _start_next(self, assistant: AssistantRecord) -> StartOutcome:
        picked: list[PieceOfWork] = []

        async def start(action: Action) -> StartOutcome:
            current = await action.get(AssistantRecord, assistant.id)
            if current is None or not await running(action, current):
                return "idle"  # new, paused or ended: nothing is started (BR-14)
            waiting = await action.find(
                PieceOfWork, assistant_id=current.id, state=WorkState.WAITING
            )
            if not waiting:
                return "idle"
            slot = await self._slot(action, current)
            if slot.running_work_id is not None:
                busy = await action.get(PieceOfWork, slot.running_work_id)
                if busy is not None and busy.state is WorkState.RUNNING:
                    return "busy"
            piece = waiting[0]
            picked.append(piece)
            pack = await build_input_pack(
                action, self.boundary, current, piece, action.now, self.registry
            )
            started = await action.change(
                "assistant.work_started",
                piece,
                piece.evolve(
                    state=WorkState.RUNNING,
                    input_labels=sorted(pack.labels()),
                    started_at=action.now,
                ),
            )
            taken = await action.change(
                "assistant.work_slot_taken",
                slot,
                slot.evolve(running_work_id=piece.id),
                details={"work_id": str(piece.id)},
            )
            answer = await self.boundary.assistant.start_work(
                WorkOrder(
                    work_id=piece.id,
                    organisation_id=current.organisation_id,
                    assistant_flow_id=current.flow_id,
                    kind=piece.work_kind,
                    input_pack=pack,
                )
            )
            if answer.accepted:
                return "started"
            refused = await action.change(
                "assistant.work_refused",
                started,
                started.evolve(
                    state=WorkState.REFUSED,
                    reason=one_sentence(answer.reason, FLOW_REFUSED),
                    finished_at=action.now,
                ),
            )
            await self._finished(action, current, refused)
            await action.change(
                "assistant.work_slot_freed", taken, taken.evolve(running_work_id=None)
            )
            return "refused"

        try:
            return await perform(
                self.boundary, self.store, assistant.organisation_id, FLOW, start, assistant.id
            )
        except AlreadyChanged:
            return "busy"  # started elsewhere at the same moment
        except Exception as error:
            # Whatever went wrong, the piece fails with a reason and can be tried again, so
            # the queue behind it never jams.
            if not picked:
                raise
            if not isinstance(error, ModuleError):
                log_event(
                    "assistant.work_start_error",
                    assistant_id=str(assistant.id),
                    work_id=str(picked[-1].id),
                    error=type(error).__name__,
                )
            await self._fail_waiting(picked[-1], NOT_STARTED)
            return "failed"

    async def _fail_waiting(self, piece: PieceOfWork, reason: str) -> None:
        async def work(action: Action) -> None:
            current = await action.get(PieceOfWork, piece.id)
            if current is not None and current.state is WorkState.WAITING:
                failed = await action.change(
                    "assistant.work_failed",
                    current,
                    current.evolve(state=WorkState.FAILED, reason=reason, finished_at=action.now),
                )
                assistant = await action.get(AssistantRecord, piece.assistant_id or uuid4())
                if assistant is not None:
                    await self._finished(action, assistant, failed)

        await perform(self.boundary, self.store, piece.organisation_id, FLOW, work)

    # Finishing work --------------------------------------------------------------------

    async def handle_result(self, assistant: AssistantRecord, result: WorkResult) -> None:
        """Flow's "Finish a piece of work" for a running assistant. Used once."""

        async def finish(action: Action) -> bool:
            piece = await action.get(PieceOfWork, result.work_id)
            if piece is None or piece.assistant_id != assistant.id:
                return False
            if piece.state is not WorkState.RUNNING:
                return False  # the same result again, or work that was cancelled
            action.assistant_id = assistant.id
            if result.failure is not None:
                finished = await action.change(
                    "assistant.work_failed",
                    piece,
                    piece.evolve(
                        state=WorkState.FAILED,
                        reason=one_sentence(result.failure, WORK_FAILED),
                        finished_at=action.now,
                    ),
                )
            else:
                finished = await self._use_result(action, assistant, piece, result)
            await self._finished(action, assistant, finished)
            await self._free_slot(action, assistant, piece.id)
            return True

        try:
            used = await perform(
                self.boundary, self.store, assistant.organisation_id, FLOW, finish, assistant.id
            )
        except AlreadyChanged:
            used = False  # the same result, handled at the same moment elsewhere
        if not used:
            log_event(
                "assistant.result_ignored",
                assistant_id=str(assistant.id),
                work_id=str(result.work_id),
            )
            return
        await self.tool_requests.after(assistant)
        await self.advance(assistant)

    async def _use_result(
        self, action: Action, assistant: AssistantRecord, piece: PieceOfWork, result: WorkResult
    ) -> PieceOfWork:
        refused: list[str] = []
        reply: str | None = None
        if result.reply is not None and result.reply.strip():
            if chat_refusal(result.reply):
                refused.append(REPLY_TOO_LONG)
            else:
                reply = result.reply
                await action.add(
                    "assistant.chat_reply_added",
                    ChatMessage(
                        organisation_id=assistant.organisation_id,
                        assistant_id=assistant.id,
                        created_at=action.now,
                        author="assistant",
                        text=reply,
                        work_id=piece.id,
                    ),
                    details={"work_id": str(piece.id)},
                )
        refused.extend(
            await self.tool_requests.take(action, assistant, piece, list(result.tool_requests))
        )
        return await action.change(
            "assistant.work_done",
            piece,
            piece.evolve(
                state=WorkState.DONE, reply=reply, refused=refused, finished_at=action.now
            ),
            details={"refused_count": len(refused)},
        )

    async def _free_slot(self, action: Action, assistant: AssistantRecord, work_id: UUID) -> None:
        slot = await action.get(WorkSlot, slot_id(assistant))
        if slot is not None and slot.running_work_id == work_id:
            await action.change(
                "assistant.work_slot_freed", slot, slot.evolve(running_work_id=None)
            )


@CLOSING_HANDLERS.register
async def forget_chat_in_work(action: Action, assistant: AssistantRecord, closing: Closing) -> None:
    """The chat goes with a hand-over or end: replies and given inputs kept on pieces of
    work are cleared too, and the work slot is removed."""
    for piece in await action.find(PieceOfWork, assistant_id=assistant.id):
        if piece.reply is not None or piece.given_inputs:
            await action.change(
                "assistant.work_chat_removed",
                piece,
                piece.evolve(reply=None, given_inputs=[]),
                details={"because": closing.reason},
            )
    for slot in await action.find(WorkSlot, assistant_id=assistant.id):
        await action.remove(
            "assistant.work_slot_removed", slot, details={"because": closing.reason}
        )
