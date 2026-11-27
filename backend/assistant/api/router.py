"""The foundation's addresses under ``/api/assistant``.

Each address reads one thing and applies BR-8 through the visibility service,
so anything that is not the person's own answers exactly like something that
does not exist. Later epics add their addresses here, each with an
expectation in the visibility test.
"""

# No ``from __future__ import annotations`` here: FastAPI must resolve the
# dependency annotations of the routes defined inside ``create_router``.
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Depends, Request

from assistant.api.errors import ModuleErrorRoute
from assistant.boundary.interfaces import Boundary
from assistant.errors import NotFound
from assistant.models.records import (
    ActionItem,
    AssistantRecord,
    AssistantState,
    ConfirmedStatement,
    OrganisationSettings,
    PersonRequest,
    PieceOfWork,
    Record,
)
from assistant.rules.visibility import Viewer
from assistant.service.visibility import find_visible, get_visible, parse_id, viewer_for
from assistant.store.interface import Store

PREFIX = "/api/assistant"

R = TypeVar("R", bound=Record)

# Fields that are kept in the record but never shown through the API.
NEVER_SHOWN: dict[type[Record], set[str]] = {
    ConfirmedStatement: {"unconfirmed_wording"},  # BR-5
}


def shown(record: Record) -> dict[str, Any]:
    hidden = NEVER_SHOWN.get(type(record), set())
    return record.model_dump(mode="json", exclude=hidden)


def create_router(boundary: Boundary, store: Store) -> APIRouter:
    router = APIRouter(prefix=PREFIX, route_class=ModuleErrorRoute, tags=["assistant"])

    async def current_viewer(request: Request) -> Viewer:
        return await viewer_for(boundary, request.headers)

    ViewerDep = Annotated[Viewer, Depends(current_viewer)]

    async def read_one(viewer: Viewer, record_type: type[R], raw_id: str) -> dict[str, Any]:
        record_id = parse_id(raw_id)
        async with store.transaction(viewer.organisation_id) as tx:
            return shown(await get_visible(tx, viewer, record_type, record_id))

    @router.get("/assistant")
    async def my_assistant(viewer: ViewerDep) -> dict[str, Any]:
        """The assistant of the person asking, if they are its manager."""
        async with store.transaction(viewer.organisation_id) as tx:
            mine = await find_visible(tx, viewer, AssistantRecord, manager_id=viewer.person_id)
        current = [a for a in mine if a.state is not AssistantState.ENDED]
        if not current:
            raise NotFound()
        return shown(current[0])

    @router.get("/assistants/{assistant_id}")
    async def assistant(assistant_id: str, viewer: ViewerDep) -> dict[str, Any]:
        return await read_one(viewer, AssistantRecord, assistant_id)

    @router.get("/work/{work_id}")
    async def piece_of_work(work_id: str, viewer: ViewerDep) -> dict[str, Any]:
        return await read_one(viewer, PieceOfWork, work_id)

    @router.get("/action-items/{item_id}")
    async def action_item(item_id: str, viewer: ViewerDep) -> dict[str, Any]:
        return await read_one(viewer, ActionItem, item_id)

    @router.get("/person-requests/{request_id}")
    async def person_request(request_id: str, viewer: ViewerDep) -> dict[str, Any]:
        return await read_one(viewer, PersonRequest, request_id)

    @router.get("/statements/{statement_id}")
    async def statement(statement_id: str, viewer: ViewerDep) -> dict[str, Any]:
        return await read_one(viewer, ConfirmedStatement, statement_id)

    @router.get("/organisation/settings")
    async def organisation_settings(viewer: ViewerDep) -> dict[str, Any]:
        if not viewer.is_administrator:
            raise NotFound()
        async with store.transaction(viewer.organisation_id) as tx:
            found = await find_visible(tx, viewer, OrganisationSettings)
        if found:
            return shown(found[0])
        return {"organisation_id": viewer.organisation_id, "colour_meanings": {}}

    return router
