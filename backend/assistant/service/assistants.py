"""Finding a manager's assistant."""

from __future__ import annotations

from assistant.errors import NotFound
from assistant.models.records import AssistantRecord, AssistantState
from assistant.rules.visibility import Viewer
from assistant.service.actions import Action
from assistant.service.visibility import find_visible
from assistant.store.interface import Transaction


async def current_assistant(
    reader: Transaction | Action, manager_id: str
) -> AssistantRecord | None:
    """The manager's assistant that has not ended, if any. A manager has at most one."""
    found = await reader.find(AssistantRecord, manager_id=manager_id)
    current = [a for a in found if a.state is not AssistantState.ENDED]
    return current[0] if current else None


async def my_assistant(tx: Transaction, viewer: Viewer) -> AssistantRecord:
    """The asking manager's own assistant; anyone else gets ``NotFound``."""
    mine = await find_visible(tx, viewer, AssistantRecord, manager_id=viewer.person_id)
    current = [a for a in mine if a.state is not AssistantState.ENDED]
    if not current:
        raise NotFound()
    return current[0]


async def assistant_by_flow_id(reader: Transaction | Action, flow_id: str) -> AssistantRecord:
    """The assistant Flow knows by ``flow_id``; ``NotFound`` if there is none."""
    found = [
        a
        for a in await reader.find(AssistantRecord, flow_id=flow_id)
        if a.state is not AssistantState.ENDED
    ]
    if not found:
        raise NotFound()
    return found[0]
