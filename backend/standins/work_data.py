"""Work data stand-in: three projects with budgets and spend, and four assignments."""

from __future__ import annotations

from assistant.boundary.interfaces import Clock
from assistant.models.boundary import AssignmentRecord, AssignmentState, ProjectRecord
from standins.sample import sample_assignments, sample_projects


class StandInWorkData:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self.projects_by_id = {p.id: p for p in sample_projects()}
        self.assignments_by_id = {a.id: a for a in sample_assignments()}

    async def projects(self, manager_id: str) -> list[ProjectRecord]:
        now = await self._clock.now()
        return [
            ProjectRecord(
                id=p.id,
                name=p.name,
                owner_id=p.owner.id,
                budget=p.budget,
                spend=p.spend,
                source_label=f"Flow project: {p.name}",
                read_at=now,
            )
            for p in self.projects_by_id.values()
            if manager_id in p.visible_to
        ]

    async def assignments(self, manager_id: str) -> list[AssignmentRecord]:
        now = await self._clock.now()
        return [
            AssignmentRecord(
                id=a.id,
                title=a.title,
                virtual_employee_id=a.virtual_employee.id,
                manager_id=a.manager.id,
                state=a.state,
                project_id=a.project_id,
                source_label=f"Flow assignment: {a.title}",
                read_at=now,
            )
            for a in self.assignments_by_id.values()
            if a.manager.id == manager_id
        ]

    # Demonstration controls -------------------------------------------------

    def set_assignment_state(self, assignment_id: str, state: AssignmentState) -> None:
        self.assignments_by_id[assignment_id].state = state

    def remove_assignment(self, assignment_id: str) -> None:
        del self.assignments_by_id[assignment_id]

    def set_spend(self, project_id: str, spend: str) -> None:
        self.projects_by_id[project_id].spend = spend

    def hide_project_from(self, project_id: str, manager_id: str) -> None:
        self.projects_by_id[project_id].visible_to.discard(manager_id)
