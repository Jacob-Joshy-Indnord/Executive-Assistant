"""Approval levels and limits (Story 2.2).

Every kind starts at its section 8.2 level when the assistant is set up. Only
the manager changes a level or limit, through their own page; nothing the
assistant returns can (no tool changes a level). A change is recorded before
it takes effect and applies to the next request.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from assistant.boundary.interfaces import Boundary
from assistant.errors import NotAllowedValue, NotFound
from assistant.models.boundary import ActionKind, ApprovalLevel, ApprovalLimits, ApprovalRequest
from assistant.models.records import ApprovalLevelRecord, AssistantRecord
from assistant.rules.approval_kinds import ACTION_KINDS, KindSpec, refusal
from assistant.rules.visibility import Viewer
from assistant.service.actions import Action, perform
from assistant.service.assistants import my_assistant
from assistant.store.interface import Store, Transaction


class LimitsChange(BaseModel):
    """Limits as the manager gives them: people by name."""

    model_config = ConfigDict(extra="forbid")

    people: list[str] | None = None
    per_day: int | None = None
    projects: list[str] | None = None
    """Project ids the kind may concern without approval; leave out for any project."""


class LevelChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: ApprovalLevel
    limits: LimitsChange = LimitsChange()


def spec_of(kind: str) -> KindSpec:
    try:
        return ACTION_KINDS[ActionKind(kind)]
    except (ValueError, KeyError) as exc:
        raise NotFound() from exc


async def level_for(
    reader: Transaction | Action, assistant: AssistantRecord, kind: ActionKind
) -> tuple[ApprovalLevel, ApprovalLimits]:
    """The level and limits that apply to the assistant's next request of this kind."""
    spec = ACTION_KINDS[kind]
    if spec.locked:
        return spec.starts_at, spec.default_limits()
    found = await reader.find(ApprovalLevelRecord, assistant_id=assistant.id, action_kind=kind)
    if not found:
        return spec.starts_at, spec.default_limits()
    return found[0].level, found[0].limits


async def approval_request_for(
    reader: Transaction | Action,
    assistant: AssistantRecord,
    kind: ActionKind,
    *,
    request_id: UUID,
    effect: str,
    target_id: str | None = None,
    count_today: int = 0,
    approved_by_manager: bool = False,
    text: str | None = None,
    project_ids: list[str] | None = None,
) -> ApprovalRequest:
    """What the module sends to Flow's check: the request with the level and limits that apply."""
    level, limits = await level_for(reader, assistant, kind)
    return ApprovalRequest(
        request_id=request_id,
        organisation_id=assistant.organisation_id,
        manager_id=assistant.manager_id,
        assistant_flow_id=assistant.flow_id,
        kind=kind,
        target_id=target_id,
        effect=effect,
        level=level,
        limits=limits,
        count_today=count_today,
        approved_by_manager=approved_by_manager,
        text=text,
        project_ids=project_ids or [],
    )


class ApprovalLevelService:
    def __init__(self, boundary: Boundary, store: Store) -> None:
        self.boundary = boundary
        self.store = store

    async def create_defaults(self, action: Action, assistant: AssistantRecord) -> None:
        """Every kind at its starting level (a set-up hook)."""
        for spec in ACTION_KINDS.values():
            await action.add(
                "assistant.approval_level_set",
                ApprovalLevelRecord(
                    organisation_id=assistant.organisation_id,
                    assistant_id=assistant.id,
                    created_at=action.now,
                    action_kind=spec.kind,
                    level=spec.starts_at,
                    limits=spec.default_limits(),
                ),
                details={"action_kind": spec.kind.value},
            )

    async def remove_all(self, action: Action, assistant: AssistantRecord) -> None:
        """The levels go with the assistant record when it is handed over or ended."""
        for record in await action.find(ApprovalLevelRecord, assistant_id=assistant.id):
            await action.remove(
                "assistant.approval_level_removed",
                record,
                details={"action_kind": record.action_kind.value},
            )

    async def _view(
        self, spec: KindSpec, level: ApprovalLevel, limits: ApprovalLimits
    ) -> dict[str, Any]:
        people = None
        if limits.person_ids is not None:
            people = []
            for person_id in limits.person_ids:
                person = await self.boundary.people.person(person_id)
                people.append(
                    {"id": person_id, "display_name": person.display_name if person else None}
                )
        return {
            "kind": spec.kind.value,
            "label": spec.label,
            "level": level.value,
            "locked": spec.locked,
            "limits": {
                "people": people,
                "per_day": limits.per_day,
                "projects": limits.project_ids,
            },
            "allowed_limits": {
                "people": spec.people_limit,
                "per_day": spec.per_day_limit,
                "per_day_label": spec.per_day_label,
                "projects": spec.project_limit,
            },
        }

    async def levels(self, viewer: Viewer) -> list[dict[str, Any]]:
        async with self.store.transaction(viewer.organisation_id) as tx:
            assistant = await my_assistant(tx, viewer)
            current = [
                (spec, *await level_for(tx, assistant, spec.kind)) for spec in ACTION_KINDS.values()
            ]
        return [await self._view(spec, level, limits) for spec, level, limits in current]

    async def _resolve(self, organisation_id: str, names: list[str] | None) -> list[str] | None:
        """People named in a limit, each matching exactly one person or virtual employee."""
        if names is None:
            return None
        ids: list[str] = []
        for name in names:
            found = await self.boundary.people.find_by_name(organisation_id, name)
            if len(found) != 1:
                raise NotAllowedValue(
                    f"No one in the organisation is called {name.strip()}."
                    if not found
                    else f"More than one person is called {name.strip()}."
                )
            if found[0].id not in ids:
                ids.append(found[0].id)
        return ids

    async def _projects(self, viewer: Viewer, project_ids: list[str] | None) -> list[str] | None:
        """Projects named in a limit, each one the manager can see."""
        if project_ids is None:
            return None
        visible = {p.id for p in await self.boundary.work_data.projects(viewer.person_id)}
        ids: list[str] = []
        for project_id in project_ids:
            if project_id not in visible:
                raise NotAllowedValue("That project is not one you can see.")
            if project_id not in ids:
                ids.append(project_id)
        return ids

    async def change(self, viewer: Viewer, kind: str, change: LevelChange) -> dict[str, Any]:
        """The manager changes one level and its limits."""
        spec = spec_of(kind)
        async with self.store.transaction(viewer.organisation_id) as tx:
            await my_assistant(tx, viewer)  # only the manager gets past here
        sentence = refusal(
            spec,
            change.level,
            change.limits.people,
            change.limits.per_day,
            change.limits.projects,
        )
        if sentence:
            raise NotAllowedValue(sentence)
        person_ids = await self._resolve(viewer.organisation_id, change.limits.people)
        project_ids = await self._projects(viewer, change.limits.projects)
        limits = ApprovalLimits(
            person_ids=person_ids, per_day=change.limits.per_day, project_ids=project_ids
        )

        async def work(action: Action) -> tuple[ApprovalLevel, ApprovalLimits]:
            assistant = await my_assistant(action.tx, viewer)
            found = await action.find(
                ApprovalLevelRecord, assistant_id=assistant.id, action_kind=spec.kind
            )
            details: dict[str, Any] = {"action_kind": spec.kind.value}
            if limits.per_day is not None:
                details["per_day"] = limits.per_day
            if limits.person_ids is not None:
                details["people"] = limits.person_ids
            if limits.project_ids is not None:
                details["projects"] = limits.project_ids
            if found:
                await action.change(
                    "assistant.approval_level_changed",
                    found[0],
                    found[0].evolve(level=change.level, limits=limits),
                    details=details,
                )
            else:
                await action.add(
                    "assistant.approval_level_changed",
                    ApprovalLevelRecord(
                        organisation_id=assistant.organisation_id,
                        assistant_id=assistant.id,
                        created_at=action.now,
                        action_kind=spec.kind,
                        level=change.level,
                        limits=limits,
                    ),
                    details=details,
                )
            return change.level, limits

        level, saved = await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )
        return await self._view(spec, level, saved)
