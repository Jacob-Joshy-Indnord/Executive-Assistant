"""Whether an assistant may act now: its own state and its organisation's pause (BR-14)."""

from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

from assistant.models.records import AssistantRecord, OrganisationControl
from assistant.rules.lifecycle import is_running
from assistant.service.actions import Action
from assistant.store.interface import Transaction


def control_id(organisation_id: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"https://ea-001.invalid/organisation-control/{organisation_id}")


async def organisation_control(
    reader: Transaction | Action, organisation_id: str
) -> OrganisationControl | None:
    return await reader.get(OrganisationControl, control_id(organisation_id))


async def organisation_paused(reader: Transaction | Action, organisation_id: str) -> bool:
    control = await organisation_control(reader, organisation_id)
    return bool(control and control.all_paused)


async def running(reader: Transaction | Action, assistant: AssistantRecord) -> bool:
    """Whether anything may be started, sent or carried out for the assistant now."""
    return is_running(assistant, await organisation_paused(reader, assistant.organisation_id))
