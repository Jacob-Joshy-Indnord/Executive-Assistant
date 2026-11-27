"""Check what the assistant returns (Story 3.2, BR-11, BR-12).

Every tool request is checked on its own: its shape against the tool registry
and the limits of section 8.1, each name against the People boundary (exactly
one person or virtual employee, ignoring case), its source against the labels
of the input pack, and any project or item against what the manager has. A
request that fails is refused with a reason and the others are still handled.
Only the first 50 requests of one piece of work are handled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from assistant.boundary.interfaces import Boundary
from assistant.models.boundary import Person, ProjectRecord
from assistant.models.records import ActionItem, AssistantRecord, PieceOfWork
from assistant.rules.tools import (
    MAX_TOOL_REQUESTS,
    Shaped,
    check_shape,
    due_date_refusal,
    over_the_cap,
    refusal,
    shown,
)
from assistant.service.actions import Action
from assistant.service.setup import settings_for
from assistant.store.interface import Transaction


@dataclass(frozen=True)
class CheckedRequest:
    """A tool request that passed every check, with its names and subjects resolved."""

    index: int
    tool: str
    values: dict[str, Any]
    people: dict[str, Person] = field(default_factory=dict)
    project: ProjectRecord | None = None
    item: ActionItem | None = None


@dataclass
class CheckResult:
    accepted: list[CheckedRequest] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)


class ToolChecks:
    def __init__(self, boundary: Boundary) -> None:
        self.boundary = boundary

    async def check_all(
        self,
        reader: Transaction | Action,
        assistant: AssistantRecord,
        work: PieceOfWork,
        tool_requests: list[Any],
        now: datetime,
    ) -> CheckResult:
        result = CheckResult()
        settings = await settings_for(reader, assistant)
        today = now.astimezone(ZoneInfo(settings.time_zone)).date()
        resolver = _Resolver(self.boundary, reader, assistant, set(work.input_labels))
        for index, raw in enumerate(tool_requests, start=1):
            if index > MAX_TOOL_REQUESTS:
                result.refused.append(over_the_cap(index))
                continue
            shaped, reason = check_shape(index, raw)
            if shaped is None:
                result.refused.append(reason or refusal(index, "it could not be read"))
                continue
            if "due_date" in shaped.values:
                reason = due_date_refusal(index, shaped.values["due_date"], today)
                if reason:
                    result.refused.append(reason)
                    continue
            checked, reason = await resolver.resolve(shaped)
            if checked is None:
                result.refused.append(reason or refusal(index, "it could not be checked"))
            else:
                result.accepted.append(checked)
        return result


class _Resolver:
    """Resolves names, sources, projects and items, asking the boundary once for each."""

    def __init__(
        self,
        boundary: Boundary,
        reader: Transaction | Action,
        assistant: AssistantRecord,
        labels: set[str],
    ) -> None:
        self.boundary = boundary
        self.reader = reader
        self.assistant = assistant
        self.labels = labels
        self._names: dict[str, list[Person]] = {}
        self._projects: list[ProjectRecord] | None = None

    async def _people_called(self, name: str) -> list[Person]:
        key = name.strip().casefold()
        if key not in self._names:
            found = await self.boundary.people.find_by_name(self.assistant.organisation_id, name)
            self._names[key] = [
                p
                for p in found
                if p.organisation_id == self.assistant.organisation_id
                and p.display_name.casefold() == key
            ]
        return self._names[key]

    async def _project(self, wanted: str) -> list[ProjectRecord]:
        if self._projects is None:
            self._projects = await self.boundary.work_data.projects(self.assistant.manager_id)
        key = wanted.strip().casefold()
        return [p for p in self._projects if p.id == wanted.strip() or p.name.casefold() == key]

    async def resolve(self, shaped: Shaped) -> tuple[CheckedRequest | None, str | None]:
        index, values = shaped.index, shaped.values
        people: dict[str, Person] = {}
        project: ProjectRecord | None = None
        item: ActionItem | None = None
        for spec in shaped.tool.fields:
            if spec.name not in values:
                continue
            value = values[spec.name]
            if spec.kind == "source" and value not in self.labels:
                return None, refusal(index, f"its source {shown(value)} was not in the input pack")
            if spec.kind == "name":
                found = await self._people_called(value)
                if not found:
                    return None, refusal(
                        index, f"no one in the organisation is called {shown(value)}"
                    )
                if len(found) > 1:
                    return None, refusal(index, f"more than one person is called {shown(value)}")
                people[spec.name] = found[0]
            if spec.kind == "project":
                projects = await self._project(value)
                if len(projects) != 1:
                    return None, refusal(
                        index, f"no project the manager can see is called {shown(value)}"
                    )
                project = projects[0]
            if spec.kind == "item":
                item = await self.reader.get(ActionItem, value)
                if item is None or item.assistant_id != self.assistant.id:
                    return None, refusal(index, "the item it names was not found")
        return (
            CheckedRequest(index, shaped.tool.name, values, people, project, item),
            None,
        )
