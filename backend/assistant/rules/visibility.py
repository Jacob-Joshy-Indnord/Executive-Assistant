"""BR-8: who may see what. Pure rules, no reading or writing.

* An assistant's work is visible to its manager only.
* An owner sees only their own requests, items and statements.
* An administrator sees only the organisation settings, even for an
  assistant they set up. Being an administrator grants nothing else.

Every record kind has exactly one rule here, so a new kind cannot be added
without deciding who sees it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from assistant.models.records import (
    RECORD_TYPES,
    ActionItem,
    ApprovalSuggestion,
    AssistantRecord,
    Briefing,
    BriefingSettings,
    ConfirmedStatement,
    DecisionPaper,
    Lesson,
    Meeting,
    MeetingAttendance,
    MeetingNotes,
    PersonRequest,
    Record,
    RemovedLesson,
    Signal,
    StatementDraft,
    TakenDecision,
    Update,
)


@dataclass(frozen=True)
class Viewer:
    person_id: str
    organisation_id: str
    is_administrator: bool = False


Rule = Callable[[Viewer, Record, AssistantRecord | None], bool]


def is_its_manager(viewer: Viewer, record: Record, assistant: AssistantRecord | None) -> bool:
    """The viewer manages the record's assistant.

    A hand-over ends the assistant record and starts a new one for the new
    manager, so what was made before belongs to a record that is not theirs.
    """
    if isinstance(record, AssistantRecord):
        assistant = record
    if assistant is None or assistant.organisation_id != viewer.organisation_id:
        return False
    return assistant.manager_id == viewer.person_id


def _owned_by(owner_field: str, manager_too: bool) -> Rule:
    def rule(viewer: Viewer, record: Record, assistant: AssistantRecord | None) -> bool:
        if record.organisation_id != viewer.organisation_id:
            return False
        if getattr(record, owner_field) == viewer.person_id:
            return True
        return manager_too and is_its_manager(viewer, record, assistant)

    return rule


def administrators_only(viewer: Viewer, record: Record, assistant: AssistantRecord | None) -> bool:
    return viewer.is_administrator and record.organisation_id == viewer.organisation_id


MANAGER_ONLY: Rule = is_its_manager


def organiser_of_shared_or_manager(
    viewer: Viewer, record: Record, assistant: AssistantRecord | None
) -> bool:
    """A shared transcript is its organiser's to list and withdraw (BR-5: they are shown
    only its title and state); a meeting the assistant attended is its manager's only."""
    if record.organisation_id != viewer.organisation_id:
        return False
    if (
        isinstance(record, Meeting)
        and record.origin == "shared"
        and record.organiser_id == viewer.person_id
    ):
        return True
    return is_its_manager(viewer, record, assistant)

VISIBILITY: dict[str, Rule] = {
    "assistant": MANAGER_ONLY,
    "preferences": MANAGER_ONLY,
    "approval_level": MANAGER_ONLY,
    "piece_of_work": MANAGER_ONLY,
    "work_slot": MANAGER_ONLY,
    "chat_message": MANAGER_ONLY,
    "request": MANAGER_ONLY,
    "followed_project": MANAGER_ONLY,
    "report": MANAGER_ONLY,
    "attention_entry": MANAGER_ONLY,
    ActionItem.kind: _owned_by("owner_id", manager_too=True),
    Update.kind: _owned_by("author_id", manager_too=True),
    PersonRequest.kind: _owned_by("person_id", manager_too=True),
    ConfirmedStatement.kind: _owned_by("owner_id", manager_too=True),
    # BR-2/BR-4: an unconfirmed statement goes to its owner first, never to the manager.
    StatementDraft.kind: _owned_by("owner_id", manager_too=False),
    "organisation_settings": administrators_only,
    "organisation_control": administrators_only,
    "due_step_claim": MANAGER_ONLY,
    "kept_result": MANAGER_ONLY,
    "manager_notice_mark": MANAGER_ONLY,
    Meeting.kind: organiser_of_shared_or_manager,
    # Notes and bookings are the manager's only; an organiser sees only the state of the
    # request made to them (a person request), an owner only their own items and statements.
    MeetingNotes.kind: MANAGER_ONLY,
    MeetingAttendance.kind: MANAGER_ONLY,
    # The briefing, decision papers and the decisions taken on them are the manager's only.
    BriefingSettings.kind: MANAGER_ONLY,
    Briefing.kind: MANAGER_ONLY,
    DecisionPaper.kind: MANAGER_ONLY,
    TakenDecision.kind: MANAGER_ONLY,
    # What the assistant learned may hold the manager's wording: theirs only.
    Signal.kind: MANAGER_ONLY,
    Lesson.kind: MANAGER_ONLY,
    RemovedLesson.kind: MANAGER_ONLY,
    ApprovalSuggestion.kind: MANAGER_ONLY,
}


def can_see(viewer: Viewer, record: Record, assistant: AssistantRecord | None) -> bool:
    """Whether ``viewer`` may see ``record``; ``assistant`` is the record's assistant, if any."""
    rule = VISIBILITY.get(record.kind)
    if rule is None:
        raise LookupError(f"no visibility rule for {record.kind!r}")
    return rule(viewer, record, assistant)


def unruled_kinds() -> set[str]:
    """Record kinds without a rule; must always be empty."""
    return set(RECORD_TYPES) - set(VISIBILITY)
