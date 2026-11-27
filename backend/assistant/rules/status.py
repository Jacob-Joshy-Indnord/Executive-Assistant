"""Followed projects and statements (Epic 5): sentences, checks and attention. Pure rules."""

from __future__ import annotations

from typing import Any

from assistant.models.records import REASON_LIMIT, STATEMENT_FIELD_LIMIT

COLOURS = ("green", "amber", "red")

NOT_A_CONFIRMER = "The person who confirms a status must be a person in the organisation."
NOT_FOLLOWED = "You do not follow this project."
CHOOSE_COLOUR = "Choose a colour: green, amber or red."
REASON_NEEDED = "Give a reason."
REASON_TOO_LONG = f"A reason can be at most {REASON_LIMIT} characters."
FIELD_TOO_LONG = (
    f"What changed and what comes next can be at most {STATEMENT_FIELD_LIMIT} characters."
)
ONLY_LATEST = "Only the latest version of a statement can be corrected."
ALREADY_ANSWERED = "This request has already been answered."
FORM_FIRST = "Save or skip the first-time form before following projects."

PROJECT_SUBJECT = "followed_project"
CONFIRMER_LEFT = "confirmer_left"
MISSED_REPORTS = "missed_reports"
MISSED_IN_A_ROW = 2
"""Reports in a row without a confirmation before the manager is asked to decide."""

ASK_AGAIN = "Ask again"
CHANGE_CONFIRMER = "Change who confirms"
STOP_FOLLOWING = "Stop following"

REASONS: dict[str, tuple[str, list[str]]] = {
    CONFIRMER_LEFT: (
        "The person who confirms its status has left the organisation.",
        [CHANGE_CONFIRMER, STOP_FOLLOWING],
    ),
    MISSED_REPORTS: (
        "Two reports in a row had no confirmed status for it.",
        [ASK_AGAIN, CHANGE_CONFIRMER, STOP_FOLLOWING],
    ),
}
REASON_LABELS = {CONFIRMER_LEFT: "Confirmer left", MISSED_REPORTS: "No confirmation"}


def statement_refusal(colour: Any, reason: str | None, *fields: str | None) -> str | None:
    """Why a statement cannot be kept, in one sentence; ``None`` when it can."""
    if colour not in COLOURS:
        return CHOOSE_COLOUR
    if reason is None or not reason.strip():
        return REASON_NEEDED
    if len(reason) > REASON_LIMIT:
        return REASON_TOO_LONG
    if any(f is not None and len(f) > STATEMENT_FIELD_LIMIT for f in fields):
        return FIELD_TOO_LONG
    return None


def dropped_notice(project_name: str) -> str:
    name = project_name or "A project you followed"
    return f"{name} is no longer visible to you, so it has left your followed projects."
