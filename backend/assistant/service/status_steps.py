"""Due steps for project status (Stories 5.1, 5.3).

* ``project_visibility``: each run reads the projects the manager can see; a
  followed project they can no longer see leaves the list, and the manager is
  told once.
* ``confirmer_left``: a confirmer no longer in the organisation raises the
  project's attention entry, and what they were asked is withdrawn.
* ``project_assignments``: each run reads the manager's assignments and keeps
  every change to an assignment on a followed project, as its news.
* ``status_drafts``: at the time to draft, the assistant is given the news of
  each followed project that has some, in one piece of work.
* ``status_asks``: at the time to ask, each confirmer is asked once, through
  the approval check, for the projects they have not confirmed since the last
  report.
* ``status_report``: the report, on the manager's schedule.

Drafts, asks and reports recur: only the latest due one is done, so what was
missed while paused is done once.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from assistant.errors import BoundaryUnavailable, ModuleError
from assistant.models.boundary import Notice, PackInput, WorkKind
from assistant.models.records import (
    ConfirmedStatement,
    FollowedProject,
    PersonRequestState,
    PieceOfWork,
)
from assistant.rules import status as rules
from assistant.rules.input_pack import pack_label
from assistant.rules.reports import latest_due, next_step_after, step_time
from assistant.service.actions import Action
from assistant.service.attention import raise_entry
from assistant.service.due import DUE_STEPS, DueContext, DueStep
from assistant.service.log import log_event
from assistant.service.requests import RequestService
from assistant.service.status import (
    StatusService,
    anchor_of,
    end_status_asks,
    note_assignment_changes,
    project_news,
    propose_status_round,
    withdraw_stale_rounds,
)
from assistant.store.interface import Transaction

PROJECT_NEWS_KIND = "project_news"


async def _followed(reader: Transaction | Action, ctx: DueContext) -> list[FollowedProject]:
    return await reader.find(FollowedProject, assistant_id=ctx.assistant.id)


def _status(ctx: DueContext) -> StatusService:
    return StatusService(ctx.boundary, ctx.store, RequestService(ctx.boundary, ctx.store))


# Read on every run ----------------------------------------------------------------------


class ProjectVisibility:
    name = "project_visibility"

    async def due(self, reader: Transaction, ctx: DueContext, until: datetime) -> list[DueStep]:
        followed = await _followed(reader, ctx)
        if not followed:
            return []
        try:
            projects = await ctx.boundary.work_data.projects(ctx.assistant.manager_id)
        except ModuleError:
            return []  # looked at again on the next run
        visible = {p.id for p in projects}
        return [DueStep(str(f.id), f.created_at) for f in followed if f.project_id not in visible]

    async def next_after(
        self, reader: Transaction, ctx: DueContext, after: datetime
    ) -> datetime | None:
        return None

    async def run(self, action: Action, ctx: DueContext, step: DueStep) -> None:
        followed = await action.get(FollowedProject, UUID(step.subject))
        if followed is None:
            return
        projects = await ctx.boundary.work_data.projects(ctx.assistant.manager_id)
        if followed.project_id in {p.id for p in projects}:
            return
        await end_status_asks(
            action, ctx.assistant, followed.project_id, PersonRequestState.WITHDRAWN, "not_visible"
        )
        await action.remove(
            "assistant.project_dropped", followed, details={"project_id": followed.project_id}
        )
        await withdraw_stale_rounds(action, ctx.assistant)
        result = await ctx.boundary.notices.notify(
            Notice(
                organisation_id=ctx.assistant.organisation_id,
                person_id=ctx.assistant.manager_id,
                subject_kind="followed_project",
                subject_id=str(followed.id),
                text=rules.dropped_notice(followed.project_name),
            )
        )
        if not result.delivered:
            raise BoundaryUnavailable()  # nothing is kept: tried again on the next run


class ConfirmerLeft:
    name = "confirmer_left"

    async def due(self, reader: Transaction, ctx: DueContext, until: datetime) -> list[DueStep]:
        steps = []
        known: dict[str, bool] = {}
        for followed in await _followed(reader, ctx):
            who = followed.confirmer_id
            if who not in known:
                try:
                    known[who] = await self._left(ctx, who)
                except ModuleError:
                    return []
            if known[who]:
                steps.append(DueStep(f"{followed.id}/{who}", followed.created_at))
        return steps

    @staticmethod
    async def _left(ctx: DueContext, person_id: str) -> bool:
        person = await ctx.boundary.people.person(person_id)
        return person is None or person.organisation_id != ctx.assistant.organisation_id

    async def next_after(
        self, reader: Transaction, ctx: DueContext, after: datetime
    ) -> datetime | None:
        return None

    async def run(self, action: Action, ctx: DueContext, step: DueStep) -> None:
        followed_id, confirmer_id = step.subject.split("/", 1)
        followed = await action.get(FollowedProject, UUID(followed_id))
        if followed is None or followed.confirmer_id != confirmer_id:
            return
        if not await self._left(ctx, confirmer_id):
            return
        await end_status_asks(
            action, ctx.assistant, followed.project_id, PersonRequestState.WITHDRAWN,
            "confirmer_left", confirmer_id,
        )  # fmt: skip
        sentence, choices = rules.REASONS[rules.CONFIRMER_LEFT]
        await raise_entry(
            action,
            subject_kind=rules.PROJECT_SUBJECT,
            subject_id=str(followed.id),
            organisation_id=followed.organisation_id,
            assistant_id=followed.assistant_id,
            reason_code=rules.CONFIRMER_LEFT,
            reason=sentence,
            choices=choices,
        )


class ProjectAssignments:
    name = "project_assignments"

    async def due(self, reader: Transaction, ctx: DueContext, until: datetime) -> list[DueStep]:
        followed = await _followed(reader, ctx)
        if not followed:
            return []
        try:
            assignments = await ctx.boundary.work_data.assignments(ctx.assistant.manager_id)
        except ModuleError:
            log_event("assistant.assignments_not_read", assistant_id=str(ctx.assistant.id))
            return []
        steps = []
        for entry in followed:
            states = {a.id: a.state for a in assignments if a.project_id == entry.project_id}
            if states != entry.assignment_states:
                # The record's version makes each change its own step, claimed once.
                steps.append(DueStep(f"{entry.id}/{entry.version}", entry.created_at))
        return steps

    async def next_after(
        self, reader: Transaction, ctx: DueContext, after: datetime
    ) -> datetime | None:
        return None

    async def run(self, action: Action, ctx: DueContext, step: DueStep) -> None:
        followed_id, version = step.subject.split("/", 1)
        followed = await action.get(FollowedProject, UUID(followed_id))
        if followed is None or str(followed.version) != version:
            return
        assignments = await ctx.boundary.work_data.assignments(ctx.assistant.manager_id)
        await note_assignment_changes(action, followed, assignments)


# On the manager's schedule -----------------------------------------------------------------


class _Scheduled:
    """A step done for each report: at the time to draft, to ask, or the report itself."""

    name = ""
    step = ""

    async def due(self, reader: Transaction, ctx: DueContext, until: datetime) -> list[DueStep]:
        since = ctx.assistant.manager_since or ctx.assistant.created_at
        report_at = latest_due(
            until, since, ctx.settings, anchor_of(ctx.assistant, ctx.settings), self.step
        )
        if report_at is None:
            return []
        return [DueStep(report_at.isoformat(), step_time(report_at, ctx.settings, self.step))]

    async def next_after(
        self, reader: Transaction, ctx: DueContext, after: datetime
    ) -> datetime | None:
        return next_step_after(
            after, ctx.settings, anchor_of(ctx.assistant, ctx.settings), self.step
        )

    async def run(self, action: Action, ctx: DueContext, step: DueStep) -> None:
        raise NotImplementedError


class StatusDrafts(_Scheduled):
    name = "status_drafts"
    step = "draft"

    async def run(self, action: Action, ctx: DueContext, step: DueStep) -> None:
        """The assistant is given the news of each followed project that has some."""
        followed = await _followed(action, ctx)
        if not followed:
            return
        manager_id = ctx.assistant.manager_id
        projects = {p.id: p for p in await ctx.boundary.work_data.projects(manager_id)}
        assignments = await ctx.boundary.work_data.assignments(ctx.assistant.manager_id)
        inputs: list[PackInput] = []
        for entry in followed:
            project = projects.get(entry.project_id)
            if project is None:
                continue
            entry = await note_assignment_changes(action, entry, assignments)
            news = await project_news(action, ctx.assistant, entry, project)
            if not news:
                continue  # no news: no draft; its confirmer starts from their last statement
            inputs.append(
                PackInput(
                    label=pack_label(f"News since the last report: {project.name}"),
                    read_at=action.now,
                    content="\n".join(news),
                    kind=PROJECT_NEWS_KIND,
                    subject_id=project.id,
                )
            )
        if not inputs:
            return
        await action.add(
            "assistant.work_asked",
            PieceOfWork(
                organisation_id=ctx.assistant.organisation_id,
                assistant_id=ctx.assistant.id,
                created_at=action.now,
                work_kind=WorkKind.STATUS_DRAFTS,
                given_inputs=inputs,
            ),
            details={"work_kind": WorkKind.STATUS_DRAFTS.value, "projects": len(inputs)},
        )


class StatusAsks(_Scheduled):
    name = "status_asks"
    step = "ask"

    async def run(self, action: Action, ctx: DueContext, step: DueStep) -> None:
        """Each confirmer once, for the projects they have not confirmed since the last
        report, through the approval check."""
        by_confirmer: dict[str, list[FollowedProject]] = {}
        for entry in await _followed(action, ctx):
            statements = await action.find(
                ConfirmedStatement, assistant_id=ctx.assistant.id, project_id=entry.project_id
            )
            if any(
                s.owner_id == entry.confirmer_id and s.created_at >= entry.since()
                for s in statements
            ):
                continue  # confirmed one since the last report
            by_confirmer.setdefault(entry.confirmer_id, []).append(entry)
        requests = RequestService(ctx.boundary, ctx.store)
        for confirmer_id, entries in by_confirmer.items():
            person = await ctx.boundary.people.person(confirmer_id)
            if person is None or person.organisation_id != ctx.assistant.organisation_id:
                continue  # the confirmer left: their project's entry says so
            await propose_status_round(
                action, ctx.boundary, requests, ctx.assistant, confirmer_id, entries
            )


class StatusReport(_Scheduled):
    name = "status_report"
    step = "report"

    async def run(self, action: Action, ctx: DueContext, step: DueStep) -> None:
        await _status(ctx).make_report(
            action, ctx.assistant, ctx.settings, "schedule", datetime.fromisoformat(step.subject)
        )


DUE_STEPS.register(ProjectVisibility())
DUE_STEPS.register(ConfirmerLeft())
DUE_STEPS.register(ProjectAssignments())
DUE_STEPS.register(StatusDrafts())
DUE_STEPS.register(StatusAsks())
DUE_STEPS.register(StatusReport())
