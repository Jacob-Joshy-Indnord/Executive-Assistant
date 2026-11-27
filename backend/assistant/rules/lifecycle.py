"""When an assistant may act (BR-14)."""

from __future__ import annotations

from assistant.models.records import AssistantRecord, AssistantState


def is_running(assistant: AssistantRecord, organisation_paused: bool = False) -> bool:
    """Only an active assistant in an organisation that has not paused everything may act.

    While an assistant is new or paused, nothing is started, sent or carried out for it.
    """
    return assistant.state is AssistantState.ACTIVE and not organisation_paused
