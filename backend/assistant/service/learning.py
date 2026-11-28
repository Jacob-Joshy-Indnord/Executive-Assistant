"""Learning (part 2, Epic 3): signals, the learning record, suggested levels and improvement.

The assistant learns from what happens, shows what it learned, and never widens its own
authority.

* **Signals** are kept by the services that see each moment (:mod:`assistant.service.signals`);
  here the manager reads them, and only the manager.
* **Lessons**: the ``propose_learning`` tool keeps a lesson as proposed when its evidence
  names signals kept for this assistant, and as stated (kept at once, in the manager's own
  words) when its evidence quotes, word for word, a message the manager sent to this
  assistant; anything else is refused. The manager types a lesson on the page, or saves
  part 1's preferences form, and it is kept at once. Only kept lessons reach an input pack.
* **The learning record**: the manager lists, keeps, edits, adds and removes lessons, or
  clears the record; each change is recorded before it takes effect. A removed lesson is not
  proposed again in the same words, and no more than 200 lessons are kept.
* **Suggested levels**: the ``suggest_approval_change`` tool; the module counts the track
  record itself. Only the manager's acceptance changes a level.
* **Improvement**: this month's figures beside last month's, for the manager only.

Everything here goes when the assistant is handed over or ended.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from assistant.boundary.interfaces import Boundary
from assistant.errors import NotAllowedValue, NotFound
from assistant.models.boundary import ActionKind, ApprovalLevel
from assistant.models.preferences import PreferencesForm
from assistant.models.records import (
    ApprovalSuggestion,
    AssistantRecord,
    ChatMessage,
    ConfirmedStatement,
    Lesson,
    LessonState,
    MutableRecord,
    PieceOfWork,
    RemovedLesson,
    Report,
    RequestRecord,
    Signal,
    SignalKind,
    SuggestionState,
)
from assistant.rules import learning as rules
from assistant.rules.approval_kinds import ACTION_KINDS
from assistant.rules.settings import form_answers
from assistant.rules.tools import refusal
from assistant.rules.visibility import Viewer
from assistant.service.actions import Action, perform
from assistant.service.approval_levels import change_level_keeping_limits, level_for
from assistant.service.assistants import my_assistant
from assistant.service.checks import CheckedRequest
from assistant.service.closing import CLOSING_HANDLERS, Closing
from assistant.service.input_pack import kept_lessons_of
from assistant.service.setup import settings_for
from assistant.service.visibility import find_visible, get_visible, parse_id
from assistant.store.interface import Store, Transaction

# Views --------------------------------------------------------------------------------------


def signal_view(signal: Signal) -> dict[str, Any]:
    return {
        "id": str(signal.id),
        "kind": signal.signal_kind.value,
        "subject_kind": signal.subject_kind,
        "subject_id": signal.subject_id,
        "action_kind": signal.action_kind.value if signal.action_kind else None,
        "before": signal.before,
        "after": signal.after,
        "said": signal.said,
        "created_at": signal.created_at.isoformat(),
    }


def lesson_view(lesson: Lesson, message_date: date | None = None) -> dict[str, Any]:
    """A lesson with its text, its evidence in one line, and where it came from."""
    return {
        "id": str(lesson.id),
        "area": lesson.area,
        "text": lesson.text,
        "evidence": rules.evidence_line(
            lesson.origin,
            signal_kinds=lesson.signal_kinds,
            message_date=message_date,
            form_key=lesson.form_key,
            added_on=lesson.created_at.date(),
        ),
        "origin": lesson.origin,
        "origin_label": rules.ORIGIN_WORDS[lesson.origin],
        "state": lesson.state.value,
        "edited": lesson.edited,
        "created_at": lesson.created_at.isoformat(),
        "kept_at": lesson.kept_at.isoformat() if lesson.kept_at else None,
    }


def suggestion_view(suggestion: ApprovalSuggestion) -> dict[str, Any]:
    spec = ACTION_KINDS[suggestion.action_kind]
    return {
        "id": str(suggestion.id),
        "kind": suggestion.action_kind.value,
        "label": spec.label,
        "level_before": suggestion.level_before.value,
        "proposed_level": suggestion.proposed_level.value,
        "track_record": {
            "considered": suggestion.considered,
            "approved_unchanged": suggestion.approved_unchanged,
        },
        "state": suggestion.state.value,
        "created_at": suggestion.created_at.isoformat(),
        "decided_at": suggestion.decided_at.isoformat() if suggestion.decided_at else None,
    }


# Reading and writing the record ---------------------------------------------------------------


async def lessons_of(reader: Transaction | Action, assistant: AssistantRecord) -> list[Lesson]:
    return await reader.find(Lesson, assistant_id=assistant.id)


async def kept_count(reader: Transaction | Action, assistant: AssistantRecord) -> int:
    """Kept lessons of every origin; proposed ones do not count towards the limit."""
    return len(await kept_lessons_of(reader, assistant))


async def was_removed(reader: Transaction | Action, assistant: AssistantRecord, text: str) -> bool:
    digest = rules.words_digest(text)
    return bool(await reader.find(RemovedLesson, assistant_id=assistant.id, digest=digest))


async def in_record(reader: Transaction | Action, assistant: AssistantRecord, text: str) -> bool:
    words = rules.same_words(text)
    lessons = await lessons_of(reader, assistant)
    return any(rules.same_words(lesson.text) == words for lesson in lessons)


async def remove_lesson(
    action: Action, assistant: AssistantRecord, lesson: Lesson, because: str
) -> None:
    """Remove a lesson, recording first; its words are remembered only as a digest, so that
    the assistant does not propose them again."""
    digest = rules.words_digest(lesson.text)
    if not await action.find(RemovedLesson, assistant_id=assistant.id, digest=digest):
        await action.add(
            "assistant.lesson_words_remembered",
            RemovedLesson(
                organisation_id=assistant.organisation_id,
                assistant_id=assistant.id,
                created_at=action.now,
                digest=digest,
            ),
            details={"lesson_id": str(lesson.id)},
        )
    await action.remove(
        "assistant.lesson_removed",
        lesson,
        details={"origin": lesson.origin, "because": because},
    )


async def form_saved(
    action: Action,
    assistant: AssistantRecord,
    before: PreferencesForm | None,
    after: PreferencesForm,
) -> None:
    """Part 1's preferences form was saved in Settings: each non-empty answer is one kept
    lesson of area preference with the origin 'form'. A changed answer replaces the lesson
    from the same answer before, an emptied answer removes its lesson, and an answer whose
    lesson is missing gets it again, so the record always matches the form."""
    was = form_answers(before)
    now = form_answers(after)
    by_key = {
        lesson.form_key: lesson
        for lesson in await lessons_of(action, assistant)
        if lesson.origin == "form" and lesson.form_key
    }
    for key, lesson in by_key.items():
        if key not in now:
            await action.remove(
                "assistant.lesson_removed",
                lesson,
                details={"origin": "form", "because": "answer_emptied", "form_field": key},
            )
    kept = await kept_count(action, assistant)
    for key, answer in now.items():
        name = rules.FORM_ANSWER_NAMES.get(key, key.replace("_", " "))
        text = f"{name[:1].upper()}{name[1:]}: {answer}"[: rules.LESSON_LIMIT]
        current = by_key.get(key)
        if current is not None:
            if was.get(key) == answer:
                continue  # the same answer: the lesson stays as it is
            await action.change(
                "assistant.lesson_replaced",
                current,
                current.evolve(text=text, edited=False, kept_at=action.now),
                details={"origin": "form", "form_field": key},
            )
            continue
        if kept >= rules.MAX_KEPT_LESSONS:
            raise NotAllowedValue(rules.FORM_LIMIT_REACHED)
        await action.add(
            "assistant.lesson_kept",
            Lesson(
                organisation_id=assistant.organisation_id,
                assistant_id=assistant.id,
                created_at=action.now,
                area=rules.PREFERENCE_AREA,
                text=text,
                origin="form",
                form_key=key,
                kept_at=action.now,
                state=LessonState.KEPT,
            ),
            details={"origin": "form", "form_field": key},
        )
        kept += 1


# Improvement figures --------------------------------------------------------------------------

FIGURES = (
    "items_closed_by_due_date",
    "statements_confirmed_before_report",
    "drafts_approved_unchanged",
    "corrections_by_owners",
)


def _within(moment: datetime, bounds: tuple[datetime, datetime]) -> bool:
    return bounds[0] <= moment < bounds[1]


async def figures_for(
    reader: Transaction, assistant: AssistantRecord, bounds: tuple[datetime, datetime]
) -> dict[str, int]:
    """The four figures for one calendar month, counted by the module from part 1's records
    and the kept signals."""
    signals = await reader.find(Signal, assistant_id=assistant.id)
    on_time = sum(
        1
        for s in signals
        if s.signal_kind is SignalKind.ITEM_ON_TIME and _within(s.created_at, bounds)
    )
    unchanged = sum(
        1
        for s in signals
        if s.signal_kind is SignalKind.DRAFT_APPROVED and _within(s.created_at, bounds)
    )
    statements = await reader.find(ConfirmedStatement, assistant_id=assistant.id)
    corrections = sum(
        1
        for s in statements
        if s.mark == "corrected" and s.author_id == s.owner_id and _within(s.created_at, bounds)
    )
    by_id = {s.id: s for s in statements}
    # 'The report' is part 1's regular report: the scheduled one. A statement counts when it
    # was confirmed after the regular report before it and in time for this one.
    regular = sorted(
        (
            r
            for r in await reader.find(Report, assistant_id=assistant.id)
            if r.made_by == "schedule"
        ),
        key=lambda r: r.created_at,
    )
    before_report = 0
    previous: datetime | None = None
    for report in regular:
        if _within(report.created_at, bounds):
            for line in report.lines:
                statement = by_id.get(line.statement_id) if line.statement_id else None
                if (
                    statement is not None
                    and statement.created_at <= report.created_at
                    and (previous is None or statement.created_at > previous)
                ):
                    before_report += 1
        previous = report.created_at
    return {
        "items_closed_by_due_date": on_time,
        "statements_confirmed_before_report": before_report,
        "drafts_approved_unchanged": unchanged,
        "corrections_by_owners": corrections,
    }


# Closing --------------------------------------------------------------------------------------


@CLOSING_HANDLERS.register
async def forget_learning(action: Action, assistant: AssistantRecord, closing: Closing) -> None:
    """Signals, lessons (and the words of removed ones) and suggestions go with a hand-over or
    end. Removing a suggestion never changes a level."""
    removable: list[tuple[str, type[MutableRecord]]] = [
        ("assistant.signal_removed", Signal),
        ("assistant.lesson_removed", Lesson),
        ("assistant.removed_lesson_forgotten", RemovedLesson),
        ("assistant.approval_suggestion_removed", ApprovalSuggestion),
    ]
    for name, record_type in removable:
        for record in await action.find(record_type, assistant_id=assistant.id):
            await action.remove(name, record, details={"because": closing.reason})


# The service ----------------------------------------------------------------------------------


class LearningService:
    def __init__(self, boundary: Boundary, store: Store) -> None:
        self.boundary = boundary
        self.store = store

    async def _perform(self, viewer: Viewer, work: Any) -> Any:
        return await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )

    # Signals ------------------------------------------------------------------------------

    async def signals(self, viewer: Viewer) -> dict[str, Any]:
        """The manager's signals, newest first."""
        async with self.store.transaction(viewer.organisation_id) as tx:
            assistant = await my_assistant(tx, viewer)
            found = await find_visible(tx, viewer, Signal, assistant_id=assistant.id)
        found.sort(key=lambda s: s.created_at, reverse=True)
        return {"count": len(found), "signals": [signal_view(s) for s in found]}

    async def signal(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        async with self.store.transaction(viewer.organisation_id) as tx:
            return signal_view(await get_visible(tx, viewer, Signal, parse_id(raw_id)))

    # The learning record --------------------------------------------------------------------

    async def _views(self, tx: Transaction, lessons: list[Lesson]) -> list[dict[str, Any]]:
        views = []
        for lesson in lessons:
            message_date = None
            if lesson.message_id is not None:
                message = await tx.get(ChatMessage, lesson.message_id)
                message_date = message.created_at.date() if message else None
            views.append(lesson_view(lesson, message_date))
        return views

    async def lessons(self, viewer: Viewer) -> dict[str, Any]:
        """Every lesson, proposed ones first, each with its text, evidence and origin."""
        async with self.store.transaction(viewer.organisation_id) as tx:
            assistant = await my_assistant(tx, viewer)
            found = await find_visible(tx, viewer, Lesson, assistant_id=assistant.id)
            found.sort(
                key=lambda lesson: (
                    lesson.state is LessonState.KEPT,
                    lesson.kept_at or lesson.created_at,
                )
            )
            views = await self._views(tx, found)
        kept = sum(1 for lesson in found if lesson.state is LessonState.KEPT)
        return {"kept": kept, "limit": rules.MAX_KEPT_LESSONS, "lessons": views}

    async def lesson(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        async with self.store.transaction(viewer.organisation_id) as tx:
            found = await get_visible(tx, viewer, Lesson, parse_id(raw_id))
            return (await self._views(tx, [found]))[0]

    async def _mine(
        self, action: Action, viewer: Viewer, raw_id: str
    ) -> tuple[Lesson, AssistantRecord]:
        lesson = await get_visible(action.tx, viewer, Lesson, parse_id(raw_id))
        assistant = await my_assistant(action.tx, viewer)
        if lesson.assistant_id != assistant.id:
            raise NotFound()
        action.assistant_id = assistant.id
        return lesson, assistant

    async def add(
        self, viewer: Viewer, text: str | None, area: str | None = None
    ) -> dict[str, Any]:
        """The manager types a lesson: it is stated, and applies at once."""
        problem = rules.lesson_refusal(text)
        if problem:
            raise NotAllowedValue(problem)
        assert text is not None
        area = (area or "").strip() or rules.MANAGER_AREA
        if len(area) > rules.AREA_LIMIT:
            raise NotAllowedValue(rules.AREA_TOO_LONG)

        async def work(action: Action) -> Lesson:
            assistant = await my_assistant(action.tx, viewer)
            action.assistant_id = assistant.id
            if await kept_count(action, assistant) >= rules.MAX_KEPT_LESSONS:
                raise NotAllowedValue(rules.LIMIT_REACHED)
            return await action.add(
                "assistant.lesson_kept",
                Lesson(
                    organisation_id=assistant.organisation_id,
                    assistant_id=assistant.id,
                    created_at=action.now,
                    area=area,
                    text=text.strip(),
                    origin="typed",
                    kept_at=action.now,
                    state=LessonState.KEPT,
                ),
                details={"origin": "typed"},
            )

        added = await self._perform(viewer, work)
        return await self.lesson(viewer, str(added.id))

    async def keep(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        """Keep a proposed lesson as it is; it applies to the next piece of work."""

        async def work(action: Action) -> None:
            lesson, assistant = await self._mine(action, viewer, raw_id)
            if lesson.state is LessonState.KEPT:
                return  # pressed twice
            if await kept_count(action, assistant) >= rules.MAX_KEPT_LESSONS:
                raise NotAllowedValue(rules.LIMIT_REACHED)
            await action.change(
                "assistant.lesson_kept",
                lesson,
                lesson.evolve(state=LessonState.KEPT, kept_at=action.now),
                details={"origin": lesson.origin},
            )

        await self._perform(viewer, work)
        return await self.lesson(viewer, raw_id)

    async def edit(self, viewer: Viewer, raw_id: str, text: str | None) -> dict[str, Any]:
        """Edit a proposed or kept lesson: it is kept in the manager's wording and applies at
        once (BR-8)."""
        problem = rules.lesson_refusal(text)
        if problem:
            raise NotAllowedValue(problem)
        assert text is not None

        async def work(action: Action) -> None:
            lesson, assistant = await self._mine(action, viewer, raw_id)
            if (
                lesson.state is LessonState.PROPOSED
                and await kept_count(action, assistant) >= rules.MAX_KEPT_LESSONS
            ):
                raise NotAllowedValue(rules.LIMIT_REACHED)
            await action.change(
                "assistant.lesson_edited",
                lesson,
                lesson.evolve(
                    text=text.strip(),
                    edited=True,
                    state=LessonState.KEPT,
                    kept_at=action.now if lesson.state is LessonState.PROPOSED else lesson.kept_at,
                ),
                details={"origin": lesson.origin, "was": lesson.state.value},
            )

        await self._perform(viewer, work)
        return await self.lesson(viewer, raw_id)

    async def remove(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        """Remove a lesson: it no longer reaches the assistant, and is not proposed again in
        the same words."""

        async def work(action: Action) -> None:
            lesson, assistant = await self._mine(action, viewer, raw_id)
            await remove_lesson(action, assistant, lesson, "manager")

        await self._perform(viewer, work)
        return await self.lessons(viewer)

    async def clear(self, viewer: Viewer) -> dict[str, Any]:
        """Clear the record: every kept and proposed lesson is removed, each counting as
        removed."""

        async def work(action: Action) -> None:
            assistant = await my_assistant(action.tx, viewer)
            action.assistant_id = assistant.id
            for lesson in await lessons_of(action, assistant):
                await remove_lesson(action, assistant, lesson, "cleared")

        await self._perform(viewer, work)
        return await self.lessons(viewer)

    # The assistant's proposals (the propose_learning tool) --------------------------------------

    async def propose(
        self, action: Action, assistant: AssistantRecord, work: PieceOfWork, c: CheckedRequest
    ) -> str | None:
        """Kept as proposed when its evidence names signals kept for this assistant; stated
        (kept at once, the manager's own words being the entry) when its evidence quotes, word
        for word, a message the manager sent to this assistant; refused otherwise."""
        area = c.values["area"].strip()
        text = c.values["lesson"].strip()
        evidence = c.values["evidence"]
        if isinstance(evidence, str):
            try:
                evidence = [str(UUID(evidence.strip()))]
            except ValueError:
                return await self._state(action, assistant, c, area, evidence)
        signals: list[Signal] = []
        for raw in evidence:
            try:
                signal = await action.get(Signal, UUID(raw))
            except ValueError:
                signal = None
            if signal is None or signal.assistant_id != assistant.id:
                return refusal(c.index, rules.NO_EVIDENCE)
            if signal not in signals:
                signals.append(signal)
        if await was_removed(action, assistant, text):
            return refusal(c.index, rules.REMOVED_BEFORE)
        if await in_record(action, assistant, text):
            return refusal(c.index, rules.ALREADY_IN_RECORD)
        await action.add(
            "assistant.lesson_proposed",
            Lesson(
                organisation_id=assistant.organisation_id,
                assistant_id=assistant.id,
                created_at=action.now,
                area=area,
                text=text,
                origin="assistant",
                signal_ids=[s.id for s in signals],
                signal_kinds=[s.signal_kind for s in signals],
            ),
            details={"origin": "assistant", "signals": len(signals), "work_id": str(work.id)},
        )
        return None

    async def _state(
        self,
        action: Action,
        assistant: AssistantRecord,
        c: CheckedRequest,
        area: str,
        quote: str,
    ) -> str | None:
        """A lesson whose evidence quotes the manager: stated only on a word-for-word match."""
        messages = await action.find(ChatMessage, assistant_id=assistant.id, author="manager")
        source = next((m for m in messages if rules.quotes(m.text, quote)), None)
        if source is None:
            return refusal(c.index, rules.NO_EVIDENCE)
        entry = quote.strip()
        if len(entry) > rules.LESSON_LIMIT:
            return refusal(c.index, f"its quote is over {rules.LESSON_LIMIT:,} characters")
        if await was_removed(action, assistant, entry):
            return refusal(c.index, rules.REMOVED_BEFORE)
        if await in_record(action, assistant, entry):
            return refusal(c.index, rules.ALREADY_IN_RECORD)
        if await kept_count(action, assistant) >= rules.MAX_KEPT_LESSONS:
            return refusal(c.index, rules.RECORD_FULL)
        await action.add(
            "assistant.lesson_stated",
            Lesson(
                organisation_id=assistant.organisation_id,
                assistant_id=assistant.id,
                created_at=action.now,
                area=area,
                text=entry,
                origin="your_words",
                message_id=source.id,
                kept_at=action.now,
                state=LessonState.KEPT,
            ),
            details={"origin": "your_words", "message_id": str(source.id)},
        )
        return None

    # Suggested levels (the suggest_approval_change tool) -----------------------------------------

    async def suggest(
        self, action: Action, assistant: AssistantRecord, work: PieceOfWork, c: CheckedRequest
    ) -> str | None:
        """Kept as an open suggestion, with its track record, only when the module counts the
        last 20 requests of that kind as approved unchanged. It changes no level."""
        try:
            kind = ActionKind(c.values["kind"].strip())
            spec = ACTION_KINDS[kind]
        except (ValueError, KeyError):
            return refusal(c.index, rules.NOT_A_KIND)
        try:
            proposed = ApprovalLevel(c.values["level"].strip())
        except ValueError:
            return refusal(c.index, rules.NOT_A_LEVEL)
        if spec.locked:
            return refusal(c.index, rules.KIND_LOCKED)
        current, _ = await level_for(action, assistant, kind)
        if not rules.wider(proposed, current):
            return refusal(c.index, rules.NOT_WIDER)
        earlier = await action.find(ApprovalSuggestion, assistant_id=assistant.id, action_kind=kind)
        if any(s.state is SuggestionState.OPEN for s in earlier):
            return refusal(c.index, rules.ALREADY_OPEN)
        window = timedelta(days=rules.DECLINED_FOR_DAYS)
        if any(
            s.state is SuggestionState.DECLINED
            and s.proposed_level is proposed
            and s.decided_at is not None
            and action.now - s.decided_at < window
            for s in earlier
        ):
            return refusal(c.index, rules.DECLINED_RECENTLY)
        record = rules.track_record(
            await action.find(RequestRecord, assistant_id=assistant.id, action_kind=kind)
        )
        if not rules.supports(record):
            return refusal(c.index, rules.TRACK_RECORD_SHORT)
        await action.add(
            "assistant.approval_suggestion_added",
            ApprovalSuggestion(
                organisation_id=assistant.organisation_id,
                assistant_id=assistant.id,
                created_at=action.now,
                action_kind=kind,
                level_before=current,
                proposed_level=proposed,
                considered=len(record),
                approved_unchanged=sum(1 for r in record if rules.approved_unchanged(r)),
                request_ids=[r.id for r in record],
            ),
            details={
                "action_kind": kind.value,
                "proposed_level": proposed.value,
                "work_id": str(work.id),
            },
        )
        return None

    async def suggestions(self, viewer: Viewer) -> dict[str, Any]:
        async with self.store.transaction(viewer.organisation_id) as tx:
            assistant = await my_assistant(tx, viewer)
            found = await find_visible(tx, viewer, ApprovalSuggestion, assistant_id=assistant.id)
        found.sort(key=lambda s: s.created_at, reverse=True)
        open_ = [s for s in found if s.state is SuggestionState.OPEN]
        return {"open": len(open_), "suggestions": [suggestion_view(s) for s in found]}

    async def suggestion(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        async with self.store.transaction(viewer.organisation_id) as tx:
            found = await get_visible(tx, viewer, ApprovalSuggestion, parse_id(raw_id))
        return suggestion_view(found)

    async def _decide(self, viewer: Viewer, raw_id: str, accept: bool) -> dict[str, Any]:
        suggestion_id = parse_id(raw_id)

        async def work(action: Action) -> None:
            suggestion = await get_visible(action.tx, viewer, ApprovalSuggestion, suggestion_id)
            assistant = await my_assistant(action.tx, viewer)
            if suggestion.assistant_id != assistant.id:
                raise NotFound()
            action.assistant_id = assistant.id
            wanted = SuggestionState.ACCEPTED if accept else SuggestionState.DECLINED
            if suggestion.state is wanted:
                return  # pressed twice
            if suggestion.state is not SuggestionState.OPEN:
                raise NotAllowedValue(rules.SUGGESTION_DECIDED)
            kind = suggestion.action_kind
            if accept:
                if ACTION_KINDS[kind].locked:
                    raise NotAllowedValue(rules.LEVEL_LOCKED)
                await change_level_keeping_limits(
                    action,
                    assistant,
                    kind,
                    suggestion.proposed_level,
                    {
                        "action_kind": kind.value,
                        "level": suggestion.proposed_level.value,
                        "suggestion_id": str(suggestion.id),
                    },
                )
            await action.change(
                "assistant.approval_suggestion_accepted"
                if accept
                else "assistant.approval_suggestion_declined",
                suggestion,
                suggestion.evolve(state=wanted, decided_by=viewer.person_id, decided_at=action.now),
                details={"action_kind": kind.value},
            )

        await self._perform(viewer, work)
        return await self.suggestion(viewer, raw_id)

    async def accept(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        """The approval level changes to the proposed level, recorded; the suggestion is
        accepted."""
        return await self._decide(viewer, raw_id, accept=True)

    async def decline(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        """Declined: the same suggestion is refused for 30 days."""
        return await self._decide(viewer, raw_id, accept=False)

    # What I've learned: improvement -------------------------------------------------------------

    async def improvement(self, viewer: Viewer) -> dict[str, Any]:
        """This month's figures beside last month's, by calendar month in the manager's time
        zone. In the assistant's first month, last month's figures are none, not zero."""
        now = await self.boundary.clock.now()
        async with self.store.transaction(viewer.organisation_id) as tx:
            assistant = await my_assistant(tx, viewer)
            zone = (await settings_for(tx, assistant)).time_zone
            this_month = rules.month_of(now, zone)
            last_month = rules.previous_month(this_month)
            this = await figures_for(tx, assistant, rules.month_bounds(this_month, zone))
            first_month = rules.month_of(assistant.created_at, zone) >= this_month
            last: dict[str, int | None] = (
                dict.fromkeys(FIGURES)
                if first_month
                else dict(await figures_for(tx, assistant, rules.month_bounds(last_month, zone)))
            )
        return {
            "time_zone": zone,
            "this_month": {"month": this_month.strftime("%Y-%m"), **this},
            "last_month": {"month": last_month.strftime("%Y-%m"), **last},
            "figures": [
                {"figure": name, "this_month": this[name], "last_month": last[name]}
                for name in FIGURES
            ],
        }

    async def learned(self, viewer: Viewer) -> dict[str, Any]:
        """What I've learned: the record, open suggestions and this month's improvement."""
        return {
            **await self.lessons(viewer),
            "suggestions": (await self.suggestions(viewer))["suggestions"],
            "improvement": await self.improvement(viewer),
        }
