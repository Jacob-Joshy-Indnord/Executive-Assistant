"""Decision papers and decisions (part 2, Story 2.2).

The assistant returns a decision paper as the one deliverable of a finished piece
of work, e.g. when the manager's message asks "Should we". The module checks it
(two to five options, a recommendation, sources from the pack); a refused paper is
never kept or shown. The assistant is asked once more with the reasons, as for the
briefing, and when it is refused again the manager sees "The decision paper could
not be prepared." with Try again.

A paper that passed is listed under Needs your approval by due date and marked
when it is late. The manager picks an option, writes their own decision, or sends
the paper back with a comment, which returns a new version with both kept. A taken
decision is recorded before it takes effect, in a record that is never changed or
removed (BR-7), and a follow-on piece of work gives it to the assistant. A decided
paper cannot be sent back.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from assistant.boundary.interfaces import Boundary
from assistant.errors import NotAllowedValue
from assistant.models.boundary import PackInput, WorkKind
from assistant.models.records import (
    AssistantRecord,
    DecisionPaper,
    DecisionPaperState,
    PaperVersion,
    PieceOfWork,
    SignalKind,
    TakenDecision,
)
from assistant.rules import decision_papers as rules
from assistant.rules.visibility import Viewer
from assistant.service.actions import Action, perform
from assistant.service.assistants import my_assistant
from assistant.service.notices import Tell, tell
from assistant.service.setup import settings_for
from assistant.service.signals import keep_signal
from assistant.service.visibility import find_visible, get_visible, parse_id
from assistant.service.work import WorkService
from assistant.store.interface import Store, Transaction

SUBJECT = "decision_paper"
READY_NOTICE = "A decision paper is ready for you."
REFUSALS_INPUT_KIND = "refusals"
REFUSALS_LABEL = "Why the decision paper was refused"
COMMENT_INPUT_KIND = "comment"
COMMENT_LABEL = "The manager's comment"
PAPER_INPUT_KIND = "decision_paper"
PAPER_LABEL = "The decision paper sent back"
DECISION_INPUT_KIND = "choice"
DECISION_LABEL = "The manager's decision"
ASKED_AGAIN_KINDS = frozenset({REFUSALS_INPUT_KIND})
"""Given inputs that are not carried over when the assistant is asked once more."""


async def paper_of_work(reader: Transaction | Action, piece: PieceOfWork) -> DecisionPaper | None:
    found = await reader.find(DecisionPaper, assistant_id=piece.assistant_id, work_id=piece.id)
    return found[0] if found else None


def sent_back_inputs(version: PaperVersion, comment: str, now: datetime) -> list[PackInput]:
    """The manager's comment and the version they sent back, as data."""
    return [
        PackInput(label=COMMENT_LABEL, read_at=now, content=comment, kind=COMMENT_INPUT_KIND),
        PackInput(
            label=PAPER_LABEL,
            read_at=now,
            content=rules.paper_text(version),
            kind=PAPER_INPUT_KIND,
        ),
    ]


async def ask_for_paper(
    action: Action,
    assistant: AssistantRecord,
    paper: DecisionPaper,
    given: list[PackInput],
    asked_by: str | None,
    *,
    name: str,
    **changes: Any,
) -> DecisionPaper:
    """A new piece of work for the paper's next version, answering the same message."""
    piece = await action.add(
        "assistant.work_asked",
        PieceOfWork(
            organisation_id=assistant.organisation_id,
            assistant_id=assistant.id,
            created_at=action.now,
            work_kind=WorkKind.REQUEST,
            asked_by=asked_by,
            message_id=paper.message_id,
            given_inputs=given,
        ),
        details={"work_kind": WorkKind.REQUEST.value},
    )
    return await action.change(
        name, paper, paper.evolve(work_id=piece.id, **changes), details={"work_id": str(piece.id)}
    )


async def refuse_paper(
    action: Action,
    assistant: AssistantRecord,
    paper: DecisionPaper,
    piece: PieceOfWork,
    reasons: list[str],
) -> DecisionPaper:
    """Asked once more with the reasons; refused again, it could not be prepared."""
    refusals = paper.refusals + 1
    if refusals >= rules.REFUSALS_BEFORE_GIVING_UP:
        return await action.change(
            "assistant.decision_paper_not_prepared",
            paper,
            paper.evolve(state=DecisionPaperState.NOT_PREPARED, work_id=None, refusals=refusals),
            details={"refusals": refusals},
        )
    given = [i for i in piece.given_inputs if i.kind not in ASKED_AGAIN_KINDS]
    given.append(
        PackInput(
            label=REFUSALS_LABEL,
            read_at=action.now,
            content="\n".join(reasons),
            kind=REFUSALS_INPUT_KIND,
        )
    )
    return await ask_for_paper(
        action,
        assistant,
        paper,
        given,
        piece.asked_by,
        name="assistant.decision_paper_asked_again",
        refusals=refusals,
    )


async def take_paper(
    action: Action, assistant: AssistantRecord, piece: PieceOfWork, raw: dict[str, Any]
) -> list[str]:
    """The decision paper deliverable: kept as a version when it passes every check;
    otherwise never kept or shown, and the assistant is asked once more."""
    paper = await paper_of_work(action, piece)
    checked = rules.check_paper(raw, set(piece.input_labels))
    if checked.refused:
        if paper is None:
            paper = await action.add(
                "assistant.decision_paper_started",
                DecisionPaper(
                    organisation_id=assistant.organisation_id,
                    assistant_id=assistant.id,
                    created_at=action.now,
                    message_id=piece.message_id,
                    work_id=piece.id,
                ),
                details={"work_id": str(piece.id)},
            )
        await refuse_paper(action, assistant, paper, piece, checked.refused)
        return checked.refused
    version = PaperVersion(
        number=len(paper.versions) + 1 if paper else 1,
        arrived_at=action.now,
        **checked.fields,
    )
    details = {"version": version.number, "options": len(version.options)}
    if paper is None:
        ready = await action.add(
            "assistant.decision_paper_ready",
            DecisionPaper(
                organisation_id=assistant.organisation_id,
                assistant_id=assistant.id,
                created_at=action.now,
                message_id=piece.message_id,
                versions=[version],
                state=DecisionPaperState.READY,
            ),
            details=details,
        )
    else:
        ready = await action.change(
            "assistant.decision_paper_ready",
            paper,
            paper.evolve(
                versions=[*paper.versions, version],
                state=DecisionPaperState.READY,
                work_id=None,
                refusals=0,
            ),
            details=details,
        )
    await tell(
        action.boundary,
        ready.organisation_id,
        [Tell(assistant.manager_id, SUBJECT, str(ready.id), READY_NOTICE)],
    )
    return []


async def paper_work_finished(
    action: Action, assistant: AssistantRecord, piece: PieceOfWork
) -> None:
    """Work asked for a paper's next version that ended without one counts as a refusal."""
    paper = await paper_of_work(action, piece)
    if paper is None:
        return
    await refuse_paper(action, assistant, paper, piece, [piece.reason or rules.NO_PAPER_RETURNED])


# What the manager sees -----------------------------------------------------------------


def version_view(version: PaperVersion, today: date) -> dict[str, Any]:
    return {
        "number": version.number,
        "decision": version.decision,
        "background": version.background,
        "options": [{"number": n, "text": t} for n, t in enumerate(version.options, start=1)],
        "recommendation": version.recommendation,
        "due_date": version.due_date.isoformat(),
        "late": rules.is_late(version, today),
        "sources": list(version.sources),
        "arrived_at": version.arrived_at.isoformat(),
        "comment": version.comment,
        "sent_back_at": version.sent_back_at.isoformat() if version.sent_back_at else None,
    }


def paper_view(paper: DecisionPaper, taken: TakenDecision | None, today: date) -> dict[str, Any]:
    """The latest version that passed the checks, with every earlier one. Nothing of a
    refused paper is ever kept, so nothing of it is shown."""
    current = paper.versions[-1] if paper.versions else None
    view: dict[str, Any] = {
        "id": str(paper.id),
        "state": paper.state.value,
        "created_at": paper.created_at.isoformat(),
        "message": (
            rules.NOT_PREPARED if paper.state is DecisionPaperState.NOT_PREPARED else None
        ),
        "can_retry": paper.state is DecisionPaperState.NOT_PREPARED,
        "can_decide": rules.waiting(paper),
        "can_send_back": rules.waiting(paper),
        "version": current.number if current else None,
        "versions": [version_view(v, today) for v in paper.versions],
        "decision_taken": None,
    }
    if current is not None:
        view.update(version_view(current, today))
        view["version"] = current.number
    if taken is not None:
        view["decision_taken"] = {
            "option": taken.option,
            "text": taken.text,
            "version": taken.version,
            "decided_at": taken.created_at.isoformat(),
        }
    return view


def listed(paper: DecisionPaper, today: date) -> dict[str, Any]:
    """A paper under Needs your approval: what is to be decided, by when, and whether late."""
    version = paper.versions[-1]
    return {
        "id": str(paper.id),
        "kind": SUBJECT,
        "decision": version.decision,
        "due_date": version.due_date.isoformat(),
        "late": rules.is_late(version, today),
        "version": version.number,
    }


async def waiting_papers(
    reader: Transaction, viewer: Viewer, assistant: AssistantRecord, now: datetime
) -> list[dict[str, Any]]:
    """Papers waiting for the manager's decision, by due date, each marked when late."""
    settings = await settings_for(reader, assistant)
    today = now.astimezone(ZoneInfo(settings.time_zone)).date()
    found = [
        p
        for p in await find_visible(reader, viewer, DecisionPaper, assistant_id=assistant.id)
        if rules.waiting(p)
    ]
    found.sort(key=lambda p: (p.versions[-1].due_date, p.created_at))
    return [listed(p, today) for p in found]


class DecisionPaperService:
    def __init__(self, boundary: Boundary, store: Store, work: WorkService) -> None:
        self.boundary = boundary
        self.store = store
        self.work = work
        work.deliverables[SUBJECT] = take_paper
        work.on_finished.append(paper_work_finished)

    async def _today(self, reader: Transaction | Action, assistant: AssistantRecord) -> date:
        settings = await settings_for(reader, assistant)
        now = await self.boundary.clock.now()
        return now.astimezone(ZoneInfo(settings.time_zone)).date()

    async def _mine(self, tx: Transaction, viewer: Viewer, paper_id: UUID) -> DecisionPaper:
        paper = await get_visible(tx, viewer, DecisionPaper, paper_id)
        await my_assistant(tx, viewer)
        return paper

    async def _view(
        self, tx: Transaction, assistant: AssistantRecord, paper: DecisionPaper
    ) -> dict[str, Any]:
        taken = await tx.get(TakenDecision, paper.decision_id) if paper.decision_id else None
        return paper_view(paper, taken, await self._today(tx, assistant))

    async def papers(self, viewer: Viewer) -> dict[str, Any]:
        """Every paper the manager can see, by due date: those waiting, sent back, decided,
        and those that could not be prepared. One still being prepared for the first time
        has nothing to show."""
        async with self.store.transaction(viewer.organisation_id) as tx:
            assistant = await my_assistant(tx, viewer)
            found = await find_visible(tx, viewer, DecisionPaper, assistant_id=assistant.id)
            shown = [
                p for p in found if p.versions or p.state is DecisionPaperState.NOT_PREPARED
            ]
            shown.sort(
                key=lambda p: (
                    p.versions[-1].due_date.isoformat() if p.versions else "",
                    p.created_at,
                )
            )
            views = [await self._view(tx, assistant, p) for p in shown]
        return {"count": len(views), "decision_papers": views}

    async def paper(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        async with self.store.transaction(viewer.organisation_id) as tx:
            paper = await self._mine(tx, viewer, parse_id(raw_id))
            return await self._view(tx, await my_assistant(tx, viewer), paper)

    async def decide(
        self, viewer: Viewer, raw_id: str, option: int | None, own: str | None
    ) -> dict[str, Any]:
        """The manager picks an option or writes their own decision. It is recorded before
        it takes effect, cannot be changed, and a follow-on piece of work gives it to the
        assistant."""
        paper_id = parse_id(raw_id)

        async def work(action: Action) -> AssistantRecord:
            paper = await self._mine(action.tx, viewer, paper_id)
            assistant = await my_assistant(action.tx, viewer)
            action.assistant_id = assistant.id
            if paper.state is DecisionPaperState.DECIDED:
                raise NotAllowedValue(rules.DECIDED)  # BR-7: a later decision is a new one
            if not rules.waiting(paper):
                raise NotAllowedValue(rules.NOT_WAITING)
            version = paper.versions[-1]
            refusal = rules.answer_refusal(option, own, version)
            if refusal:
                raise NotAllowedValue(refusal)
            text = version.options[option - 1] if option is not None else (own or "").strip()
            taken = await action.add(
                "assistant.decision_taken",
                TakenDecision(
                    organisation_id=paper.organisation_id,
                    assistant_id=assistant.id,
                    created_at=action.now,
                    paper_id=paper.id,
                    version=version.number,
                    option=option,
                    text=text,
                    decided_by=viewer.person_id,
                ),
                details={
                    "paper_id": str(paper.id),
                    "version": version.number,
                    "option": option or 0,
                },
            )
            how = f"option {option}" if option is not None else "their own decision"
            follow_on = await self.work.enqueue(
                action,
                assistant,
                WorkKind.FOLLOW_ON,
                asked_by=viewer.person_id,
                given_inputs=[
                    PackInput(
                        label=DECISION_LABEL,
                        read_at=action.now,
                        content=(
                            f"{rules.paper_text(version)}\n"
                            f"The manager decided ({how}): {text}"
                        ),
                        kind=DECISION_INPUT_KIND,
                        subject_id=str(paper.id),
                    )
                ],
            )
            await action.change(
                "assistant.decision_paper_decided",
                paper,
                paper.evolve(
                    state=DecisionPaperState.DECIDED,
                    decision_id=taken.id,
                    follow_on_work_id=follow_on.id,
                ),
                details={"decision_id": str(taken.id), "work_id": str(follow_on.id)},
            )
            return assistant

        assistant = await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )
        await self.work.advance(assistant)
        return await self.paper(viewer, raw_id)

    async def send_back(self, viewer: Viewer, raw_id: str, comment: str | None) -> dict[str, Any]:
        """Send the paper back with a comment: a new version is prepared and both are kept.
        A decided paper cannot be sent back (BR-7)."""
        paper_id = parse_id(raw_id)

        async def work(action: Action) -> AssistantRecord:
            paper = await self._mine(action.tx, viewer, paper_id)
            assistant = await my_assistant(action.tx, viewer)
            action.assistant_id = assistant.id
            if paper.state is DecisionPaperState.DECIDED:
                raise NotAllowedValue(rules.DECIDED_NOT_SENT_BACK)
            if paper.state is DecisionPaperState.SENT_BACK:
                raise NotAllowedValue(rules.BEING_PREPARED)
            if not rules.waiting(paper):
                raise NotAllowedValue(rules.NOT_WAITING)
            refusal = rules.comment_refusal(comment)
            if refusal or comment is None:
                raise NotAllowedValue(refusal or rules.COMMENT_NEEDED)
            said = comment.strip()
            last = paper.versions[-1]
            sent_back = paper.evolve(
                versions=[
                    *paper.versions[:-1],
                    last.model_copy(update={"comment": said, "sent_back_at": action.now}),
                ],
                state=DecisionPaperState.SENT_BACK,
                refusals=0,
            )
            # The record names the version only: never the comment (BR-10).
            await ask_for_paper(
                action,
                assistant,
                sent_back,
                sent_back_inputs(last, said, action.now),
                viewer.person_id,
                name="assistant.decision_paper_sent_back",
            )
            await keep_signal(
                action,
                assistant,
                SignalKind.PAPER_SENT_BACK,
                subject_kind="decision_paper",
                subject_id=str(paper.id),
                said=said,
            )
            return assistant

        assistant = await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )
        await self.work.advance(assistant)
        return await self.paper(viewer, raw_id)

    async def retry(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        """Try again a paper that could not be prepared: the assistant is asked afresh."""
        paper_id = parse_id(raw_id)

        async def work(action: Action) -> AssistantRecord:
            paper = await self._mine(action.tx, viewer, paper_id)
            assistant = await my_assistant(action.tx, viewer)
            action.assistant_id = assistant.id
            if paper.state is DecisionPaperState.PREPARING:
                return assistant  # pressed twice
            if paper.state is not DecisionPaperState.NOT_PREPARED:
                raise NotAllowedValue(rules.NOT_RETRYABLE)
            last = paper.versions[-1] if paper.versions else None
            given = (
                sent_back_inputs(last, last.comment, action.now)
                if last is not None and last.comment
                else []
            )
            await ask_for_paper(
                action,
                assistant,
                paper,
                given,
                viewer.person_id,
                name="assistant.decision_paper_tried_again",
                state=DecisionPaperState.PREPARING,
                refusals=0,
            )
            return assistant

        assistant = await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )
        await self.work.advance(assistant)
        return await self.paper(viewer, raw_id)
