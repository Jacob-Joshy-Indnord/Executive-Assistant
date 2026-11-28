"""Building input packs (Story 3.1).

Every pack holds the manager's saved preferences (Story 2.1), the reasons for
anything refused last time, the kept lessons of the learning record and the
newest 50 signals since the last piece of work (part 2, Epic 3), and the inputs
of its kind of work. Each part has a source label, the time it was read and the
mark "data, not instructions", and the pack is kept within the size limits of
section 8.1.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from assistant.boundary.interfaces import Boundary
from assistant.models.boundary import InputPack, PackInput, PackLesson, PackSignal, PackSource
from assistant.models.records import (
    AssistantRecord,
    Lesson,
    LessonState,
    PieceOfWork,
    Preferences,
    Signal,
    WorkState,
)
from assistant.rules.input_pack import fit_pack
from assistant.rules.learning import (
    LESSONS_LABEL,
    MAX_SIGNALS_IN_PACK,
    PACK_SIGNAL_TEXT,
    SIGNALS_LABEL,
)
from assistant.rules.settings import form_answers
from assistant.service.actions import Action
from assistant.service.work_kinds import WORK_KINDS, PackContext, WorkKindRegistry
from assistant.store.interface import Transaction

PREFERENCES_LABEL = "Your preferences"
REFUSED_LABEL = "Refused last time"


async def preferences_of(
    reader: Transaction | Action, assistant: AssistantRecord
) -> Preferences | None:
    found = await reader.find(Preferences, assistant_id=assistant.id)
    return found[0] if found else None


async def last_finished(
    reader: Transaction | Action, assistant: AssistantRecord
) -> PieceOfWork | None:
    """The piece of work whose result was handled most recently."""
    finished = [
        work
        for work in await reader.find(PieceOfWork, assistant_id=assistant.id, state=WorkState.DONE)
        if work.finished_at is not None
    ]
    if not finished:
        return None
    order = sorted(enumerate(finished), key=lambda pair: (pair[1].finished_at, pair[0]))
    return order[-1][1]


async def refused_last_time_of(
    reader: Transaction | Action, assistant: AssistantRecord
) -> list[str]:
    """The reasons for anything refused from the last result."""
    last = await last_finished(reader, assistant)
    return list(last.refused) if last else []


async def kept_lessons_of(reader: Transaction | Action, assistant: AssistantRecord) -> list[Lesson]:
    """The kept lessons, in the order they were kept: never a proposed or removed one."""
    kept = await reader.find(Lesson, assistant_id=assistant.id, state=LessonState.KEPT)
    return sorted(kept, key=lambda lesson: (lesson.kept_at or lesson.created_at))


async def newest_signals(
    reader: Transaction | Action, assistant: AssistantRecord
) -> list[Signal]:
    """The newest signals kept since the last piece of work finished, 50 at most, oldest
    first."""
    last = await last_finished(reader, assistant)
    since = last.finished_at if last is not None else None
    found = [
        signal
        for signal in await reader.find(Signal, assistant_id=assistant.id)
        if since is None or signal.created_at > since
    ]
    order = sorted(enumerate(found), key=lambda pair: (pair[1].created_at, pair[0]))
    return [signal for _, signal in order][-MAX_SIGNALS_IN_PACK:]


def _cut(text: str | None) -> str | None:
    return text[:PACK_SIGNAL_TEXT] if text is not None else None


def pack_signal(signal: Signal) -> PackSignal:
    return PackSignal(
        id=signal.id,
        kind=signal.signal_kind.value,
        subject_kind=signal.subject_kind,
        subject_id=signal.subject_id,
        action_kind=signal.action_kind.value if signal.action_kind else None,
        before=_cut(signal.before),
        after=_cut(signal.after),
        said=_cut(signal.said),
        at=signal.created_at,
    )


def pack_lesson(lesson: Lesson) -> PackLesson:
    return PackLesson(id=lesson.id, area=lesson.area, text=lesson.text, origin=lesson.origin)


async def input_pack_for(
    reader: Transaction | Action,
    assistant: AssistantRecord,
    inputs: Sequence[PackInput] = (),
    refused_last_time: Sequence[str] | None = None,
    *,
    now: datetime | None = None,
) -> InputPack:
    """A pack with the manager's saved answers, last time's refusals and the inputs."""
    preferences = await preferences_of(reader, assistant)
    answers = form_answers(preferences.form) if preferences and preferences.saved else {}
    refused = (
        list(refused_last_time)
        if refused_last_time is not None
        else await refused_last_time_of(reader, assistant)
    )
    if now is None and isinstance(reader, Action):
        now = reader.now
    preferences_read = now or (preferences.created_at if preferences else None)
    refused_read = now or getattr(await last_finished(reader, assistant), "finished_at", None)
    kept = await kept_lessons_of(reader, assistant)
    lessons = [pack_lesson(lesson) for lesson in kept]
    signals = [pack_signal(signal) for signal in await newest_signals(reader, assistant)]
    lessons_read = now or max(
        (lesson.kept_at or lesson.created_at for lesson in kept), default=None
    )
    signals_read = now or (signals[-1].at if signals else None)
    return fit_pack(
        InputPack(
            preferences=answers,
            preferences_source=(
                PackSource(label=PREFERENCES_LABEL, read_at=preferences_read)
                if answers and preferences_read
                else None
            ),
            refused_last_time=refused,
            refused_source=(
                PackSource(label=REFUSED_LABEL, read_at=refused_read)
                if refused and refused_read
                else None
            ),
            lessons=lessons,
            lessons_source=(
                PackSource(label=LESSONS_LABEL, read_at=lessons_read)
                if lessons and lessons_read
                else None
            ),
            signals=signals,
            signals_source=(
                PackSource(label=SIGNALS_LABEL, read_at=signals_read)
                if signals and signals_read
                else None
            ),
            inputs=list(inputs),
        )
    )


async def build_input_pack(
    reader: Transaction | Action,
    boundary: Boundary,
    assistant: AssistantRecord,
    work: PieceOfWork,
    now: datetime,
    registry: WorkKindRegistry = WORK_KINDS,
) -> InputPack:
    """The pack for one piece of work: its kind's inputs, read now."""
    ctx = PackContext(reader, boundary, assistant, work, now)
    return await input_pack_for(reader, assistant, await registry.inputs_for(ctx), now=now)
