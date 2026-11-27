"""A seeded module behind a test client: the sample organisation, two managers with assistants.

The assistants are made the way Flow makes them (``flow.set_up``) and their
managers skip the form, so both are active with their starting levels.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from assistant.models.records import (
    ActionItem,
    AssistantRecord,
    ConfirmedStatement,
    PersonRequest,
    PieceOfWork,
    StatementWording,
    WorkKind,
)
from assistant.module import AssistantModule, create_module
from assistant.rules.visibility import Viewer
from assistant.store.sqlite import SqliteStore
from standins import StandIns, build_standins
from standins.people import PERSON_HEADER
from standins.sample import (
    LEGAL_LEAD,
    OPERATIONS_DIRECTOR,
    ORGANISATION_ADMINISTRATOR,
    ORGANISATION_ID,
    SALES_DIRECTOR,
)

PERSONAS = {
    "manager": OPERATIONS_DIRECTOR.id,
    "other_manager": SALES_DIRECTOR.id,
    "owner": LEGAL_LEAD.id,
    "administrator": ORGANISATION_ADMINISTRATOR.id,
}
UNSHOWN_WORDING = "The assistant's wording the owner did not confirm"


def viewer(person_id: str, administrator: bool = False) -> Viewer:
    return Viewer(person_id, ORGANISATION_ID, administrator)


@dataclass
class Seeded:
    assistant: AssistantRecord
    other_assistant: AssistantRecord
    work: PieceOfWork
    item: ActionItem
    person_request: PersonRequest
    statement: ConfirmedStatement


@dataclass
class World:
    standins: StandIns
    store: SqliteStore
    module: AssistantModule
    app: FastAPI
    client: TestClient
    seeded: Seeded

    def call(
        self, method: str, path: str, person_id: str | None, json: Any = None
    ) -> Response:
        headers = {PERSON_HEADER: person_id} if person_id else {}
        return self.client.request(method, path, headers=headers, json=json)

    def close(self) -> None:
        self.client.close()


async def set_up_active(module: AssistantModule, manager_id: str, flow_id: str) -> AssistantRecord:
    await module.flow.set_up(ORGANISATION_ID, manager_id, "Executive Assistant", flow_id)
    await module.services.setup.skip(viewer(manager_id))
    async with module.store.transaction(ORGANISATION_ID) as tx:
        found = await tx.find(AssistantRecord, flow_id=flow_id)
    return found[0]


async def seed(module: AssistantModule) -> Seeded:
    t = await module.boundary.clock.now()
    org = ORGANISATION_ID
    assistant = await set_up_active(module, OPERATIONS_DIRECTOR.id, "flow-assistant-ops")
    other = await set_up_active(module, SALES_DIRECTOR.id, "flow-assistant-sales")
    async with module.store.transaction(org) as tx:
        mine = {"organisation_id": org, "assistant_id": assistant.id, "created_at": t}
        work = await tx.add(
            PieceOfWork(**mine, work_kind=WorkKind.REQUEST, asked_by=OPERATIONS_DIRECTOR.id)
        )
        item = await tx.add(
            ActionItem(
                **mine,
                text="Finish the review",
                owner_id=LEGAL_LEAD.id,
                due_date=date(2026, 11, 27),
                origin="manager",
            )
        )
        person_request = await tx.add(
            PersonRequest(
                **mine,
                person_id=LEGAL_LEAD.id,
                asks="accept",
                subject_kind="action_item",
                subject_id=str(item.id),
                answer_by=t + timedelta(days=1),
            )
        )
        statement = await tx.add(
            ConfirmedStatement(
                **mine,
                project_id="vendor-consolidation",
                owner_id=LEGAL_LEAD.id,
                colour="amber",
                wording=StatementWording(reason="Two contracts are still open."),
                source="Legal lead",
                author_id=LEGAL_LEAD.id,
                mark="corrected",
                unconfirmed_wording=StatementWording(reason=UNSHOWN_WORDING),
            )
        )
    return Seeded(assistant, other, work, item, person_request, statement)


async def build_world(**module_options: Any) -> World:
    standins = build_standins()
    store = SqliteStore()
    module = create_module(standins.boundary, store, **module_options)
    seeded = await seed(module)
    app = FastAPI()
    app.include_router(module.router)
    return World(standins, store, module, app, TestClient(app), seeded)


def build_world_sync(**module_options: Any) -> World:
    """For tests that are not async; the test client runs its own event loop."""
    return asyncio.run(build_world(**module_options))


@dataclass
class Bare:
    """A module with the stand-ins and an empty store: no assistant set up yet."""

    standins: StandIns
    store: SqliteStore
    module: AssistantModule
    client: TestClient

    def call(
        self, method: str, path: str, person_id: str | None, json: Any = None
    ) -> Response:
        headers = {PERSON_HEADER: person_id} if person_id else {}
        return self.client.request(method, path, headers=headers, json=json)


def bare(**module_options: Any) -> Bare:
    standins = build_standins()
    store = SqliteStore()
    module = create_module(standins.boundary, store, **module_options)
    app = FastAPI()
    app.include_router(module.router)
    return Bare(standins, store, module, TestClient(app))
