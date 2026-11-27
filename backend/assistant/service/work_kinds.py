"""Kinds of work and the inputs each is given, registered in one place.

Each kind of work (request, status drafts, follow-on) has a list of input
providers. A provider reads what the kind needs and returns labelled inputs.
Later epics add providers with :meth:`WorkKindRegistry.add_inputs`; part 2
adds kinds with :meth:`WorkKindRegistry.register`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from assistant.boundary.interfaces import Boundary
from assistant.models.boundary import PackInput, WorkKind
from assistant.models.records import (
    AssistantRecord,
    ChatMessage,
    ConfirmedStatement,
    FollowedProject,
    PieceOfWork,
)
from assistant.rules.input_pack import pack_label, status_content
from assistant.service.actions import Action
from assistant.store.interface import Transaction

MESSAGE_INPUT_KIND = "message"
MESSAGE_LABEL = "Message from the manager"
STATUS_INPUT_KIND = "project_status"


@dataclass(frozen=True)
class PackContext:
    """What a provider may use to make its inputs."""

    reader: Transaction | Action
    boundary: Boundary
    assistant: AssistantRecord
    work: PieceOfWork
    now: datetime


InputProvider = Callable[[PackContext], Awaitable[list[PackInput]]]


@dataclass
class WorkKindSpec:
    kind: WorkKind
    providers: list[InputProvider] = field(default_factory=list)


@dataclass
class WorkKindRegistry:
    kinds: dict[WorkKind, WorkKindSpec] = field(default_factory=dict)

    def register(self, kind: WorkKind, *providers: InputProvider) -> WorkKindSpec:
        if kind in self.kinds:
            raise ValueError(f"work kind {kind.value!r} is registered twice")
        spec = WorkKindSpec(kind, list(providers))
        self.kinds[kind] = spec
        return spec

    def add_inputs(self, kind: WorkKind, provider: InputProvider) -> InputProvider:
        self.kinds[kind].providers.append(provider)
        return provider

    async def inputs_for(self, ctx: PackContext) -> list[PackInput]:
        inputs: list[PackInput] = []
        for provider in self.kinds[ctx.work.work_kind].providers:
            inputs.extend(await provider(ctx))
        return inputs


# Providers --------------------------------------------------------------------


async def manager_message(ctx: PackContext) -> list[PackInput]:
    """The manager's message, word for word, as data."""
    if ctx.work.message_id is None:
        return []
    message = await ctx.reader.get(ChatMessage, ctx.work.message_id)
    if message is None:
        return []
    return [
        PackInput(
            label=MESSAGE_LABEL,
            read_at=ctx.now,
            content=message.text,
            kind=MESSAGE_INPUT_KIND,
            subject_id=str(message.id),
        )
    ]


async def given_inputs(ctx: PackContext) -> list[PackInput]:
    """What the asker gave with the piece of work, e.g. the manager's choice."""
    return list(ctx.work.given_inputs)


async def confirmed_status(ctx: PackContext) -> list[PackInput]:
    """The latest confirmed statement of each followed project. Never a draft (BR-4)."""
    followed = await ctx.reader.find(FollowedProject, assistant_id=ctx.assistant.id)
    if not followed:
        return []
    projects = {p.id: p for p in await ctx.boundary.work_data.projects(ctx.assistant.manager_id)}
    inputs: list[PackInput] = []
    for entry in followed:
        project = projects.get(entry.project_id)
        if project is None:
            continue  # no longer visible to the manager
        versions = await ctx.reader.find(
            ConfirmedStatement, assistant_id=ctx.assistant.id, project_id=entry.project_id
        )
        if not versions:
            continue
        inputs.append(
            PackInput(
                label=pack_label(f"Confirmed status: {project.name}"),
                read_at=ctx.now,
                content=status_content(versions[-1]),
                kind=STATUS_INPUT_KIND,
                subject_id=project.id,
            )
        )
    return inputs


WORK_KINDS = WorkKindRegistry()
WORK_KINDS.register(WorkKind.REQUEST, manager_message, given_inputs, confirmed_status)
WORK_KINDS.register(WorkKind.STATUS_DRAFTS, given_inputs, confirmed_status)
WORK_KINDS.register(WorkKind.FOLLOW_ON, given_inputs)
