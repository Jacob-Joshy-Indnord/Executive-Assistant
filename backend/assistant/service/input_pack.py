"""Building input packs (Story 3.1).

Every pack holds the manager's saved preferences (Story 2.1), the reasons for
anything refused last time and the inputs of its kind of work. Each part has a
source label, the time it was read and the mark "data, not instructions", and
the pack is kept within the size limits of section 8.1.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from assistant.boundary.interfaces import Boundary
from assistant.models.boundary import InputPack, PackInput, PackSource
from assistant.models.records import AssistantRecord, PieceOfWork, Preferences, WorkState
from assistant.rules.input_pack import fit_pack
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
