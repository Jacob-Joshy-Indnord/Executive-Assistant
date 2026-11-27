"""Kinds of action and their approval levels (BRD section 8.2), registered in one place.

Part 2 registers its kinds here with :func:`register`; nothing else changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from assistant.models.boundary import ActionKind, ApprovalLevel, ApprovalLimits

MAX_PER_DAY = 100
NEVER_ALLOWED = "Purchases, payments, bookings and agreements are never allowed."


@dataclass(frozen=True)
class KindSpec:
    kind: ActionKind
    label: str
    starts_at: ApprovalLevel
    locked: bool = False
    """A locked kind keeps its starting level; no one can change it."""
    people_limit: bool = False
    per_day_limit: bool = False
    project_limit: bool = False
    """The manager can limit it to some projects: outside them, it waits for approval."""
    default_per_day: int | None = None
    per_day_label: str = ""

    def default_limits(self) -> ApprovalLimits:
        return ApprovalLimits(per_day=self.default_per_day)


ACTION_KINDS: dict[ActionKind, KindSpec] = {}


def register(spec: KindSpec) -> KindSpec:
    if spec.kind in ACTION_KINDS:
        raise ValueError(f"action kind {spec.kind!r} is registered twice")
    ACTION_KINDS[spec.kind] = spec
    return spec


register(
    KindSpec(
        ActionKind.ASK_OWNER,
        "Ask an owner to accept an item, give an update, or give or confirm a status",
        ApprovalLevel.WITH_APPROVAL,
        people_limit=True,
        per_day_limit=True,
        per_day_label="notices a day",
    )
)
register(
    KindSpec(
        ActionKind.ADD_ACTION_ITEM,
        "Add an action item",
        ApprovalLevel.WITH_APPROVAL,
        people_limit=True,
        per_day_limit=True,
        default_per_day=10,
        per_day_label="items a day",
    )
)
register(
    KindSpec(
        ActionKind.SEND_MESSAGE,
        "Send a message to a person in the organisation",
        ApprovalLevel.WITH_APPROVAL,
        people_limit=True,
        per_day_limit=True,
        default_per_day=5,
        per_day_label="messages a day",
    )
)
register(
    KindSpec(
        ActionKind.PURCHASE,
        "Any purchase, payment, booking or agreement",
        ApprovalLevel.NOT_ALLOWED,
        locked=True,
    )
)
register(
    KindSpec(
        ActionKind.WRITE_MEETING_NOTES,
        "Write notes from a shared transcript",
        ApprovalLevel.WITH_APPROVAL,
        project_limit=True,
    )
)
register(
    KindSpec(
        ActionKind.ATTEND_MEETING,
        "Attend a meeting and take notes",
        ApprovalLevel.WITH_APPROVAL,
    )
)


def refusal(
    spec: KindSpec,
    level: ApprovalLevel,
    people: list[str] | None,
    per_day: int | None,
    projects: list[str] | None = None,
) -> str | None:
    """Why this change makes no sense, in one sentence; ``None`` when it may be made."""
    if spec.locked:
        return NEVER_ALLOWED
    if people is not None and not spec.people_limit:
        return "This kind of action has no list of people."
    if per_day is not None and not spec.per_day_limit:
        return "This kind of action has no daily number."
    if projects is not None and not spec.project_limit:
        return "This kind of action has no list of projects."
    if people is not None and not people:
        return "Name at least one person, or leave the list out."
    if projects is not None and not projects:
        return "Name at least one project, or leave the list out."
    if per_day is not None and per_day < 1:
        return "A daily number must be at least 1."
    if per_day is not None and per_day > MAX_PER_DAY:
        return f"A daily number must be at most {MAX_PER_DAY}."
    return None
