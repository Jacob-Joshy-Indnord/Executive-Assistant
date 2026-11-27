"""Building input packs. Every pack carries the manager's preferences (Story 2.1)."""

from __future__ import annotations

from collections.abc import Sequence

from assistant.models.boundary import InputPack, PackInput
from assistant.models.records import AssistantRecord, Preferences
from assistant.rules.settings import form_answers
from assistant.service.actions import Action
from assistant.store.interface import Transaction


async def preferences_of(
    reader: Transaction | Action, assistant: AssistantRecord
) -> Preferences | None:
    found = await reader.find(Preferences, assistant_id=assistant.id)
    return found[0] if found else None


async def input_pack_for(
    reader: Transaction | Action,
    assistant: AssistantRecord,
    inputs: Sequence[PackInput] = (),
    refused_last_time: Sequence[str] = (),
) -> InputPack:
    """A pack with the manager's saved answers, last time's refusals and the inputs."""
    preferences = await preferences_of(reader, assistant)
    answers = form_answers(preferences.form) if preferences and preferences.saved else {}
    return InputPack(
        preferences=answers,
        refused_last_time=list(refused_last_time),
        inputs=list(inputs),
    )
