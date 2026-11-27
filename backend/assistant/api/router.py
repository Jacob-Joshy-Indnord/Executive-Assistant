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
from pydantic import BaseModel, ConfigDict

from assistant.api.errors import ModuleErrorRoute
from assistant.boundary.interfaces import Boundary
from assistant.models.preferences import PreferencesForm
from assistant.models.records import (
    ActionItem,
    AssistantRecord,
    ConfirmedStatement,
    PersonRequest,
    PieceOfWork,
    Record,
)
from assistant.rules.visibility import Viewer
from assistant.service.approval_levels import LevelChange
from assistant.service.assistants import my_assistant as find_my_assistant
from assistant.service.container import Services, build_services
from assistant.service.organisation import MeaningsChange
from assistant.service.setup import page_view
from assistant.service.visibility import get_visible, parse_id, viewer_for
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


class OrganisationSettingsChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    colour_meanings: MeaningsChange


class OpenedBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time_zone: str | None = None


def create_router(
    boundary: Boundary, store: Store, services: Services | None = None
) -> APIRouter:
    services = services or build_services(boundary, store)
    router = APIRouter(prefix=PREFIX, route_class=ModuleErrorRoute, tags=["assistant"])

    async def current_viewer(request: Request) -> Viewer:
        return await viewer_for(boundary, request.headers)

    ViewerDep = Annotated[Viewer, Depends(current_viewer)]

    async def page(assistant: AssistantRecord) -> dict[str, Any]:
        return page_view(assistant, running=await services.control.page_running(assistant))

    async def read_one(viewer: Viewer, record_type: type[R], raw_id: str) -> dict[str, Any]:
        record_id = parse_id(raw_id)
        async with store.transaction(viewer.organisation_id) as tx:
            return shown(await get_visible(tx, viewer, record_type, record_id))

    @router.get("/assistant")
    async def my_assistant(viewer: ViewerDep) -> dict[str, Any]:
        """The assistant of the person asking, if they are its manager."""
        async with store.transaction(viewer.organisation_id) as tx:
            assistant = await find_my_assistant(tx, viewer)
        return await page(assistant)

    @router.post("/assistant/opened")
    async def assistant_opened(body: OpenedBody, viewer: ViewerDep) -> dict[str, Any]:
        """The manager opened the page; the first time, their browser's time zone is kept."""
        return await page(await services.setup.first_opened(viewer, body.time_zone))

    @router.post("/assistant/pause")
    async def pause(viewer: ViewerDep) -> dict[str, Any]:
        """Nothing is started, sent or carried out until the manager resumes."""
        return await page(await services.control.pause(viewer))

    @router.post("/assistant/resume")
    async def resume(viewer: ViewerDep) -> dict[str, Any]:
        """Kept results are handled and each step that fell due meanwhile is done once."""
        return await page(await services.control.resume(viewer))

    @router.get("/preferences")
    async def get_preferences(viewer: ViewerDep) -> dict[str, Any]:
        return await services.setup.preferences(viewer)

    @router.put("/preferences")
    async def save_preferences(form: PreferencesForm, viewer: ViewerDep) -> dict[str, Any]:
        return await services.setup.save(viewer, form)

    @router.post("/preferences/skip")
    async def skip_preferences(viewer: ViewerDep) -> dict[str, Any]:
        return await services.setup.skip(viewer)

    @router.get("/approval-levels")
    async def approval_levels(viewer: ViewerDep) -> list[dict[str, Any]]:
        return await services.levels.levels(viewer)

    @router.put("/approval-levels/{kind}")
    async def change_approval_level(
        kind: str, change: LevelChange, viewer: ViewerDep
    ) -> dict[str, Any]:
        """Only the manager, from their own page, changes a level (BR-7)."""
        return await services.levels.change(viewer, kind, change)

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
        """Administrators only (BR-8)."""
        return await services.organisation.settings(viewer)

    @router.put("/organisation/settings")
    async def change_organisation_settings(
        change: OrganisationSettingsChange, viewer: ViewerDep
    ) -> dict[str, Any]:
        """Administrators only: what each status colour means."""
        return await services.organisation.change_meanings(viewer, change.colour_meanings)

    @router.get("/colours")
    async def colours(viewer: ViewerDep) -> list[dict[str, Any]]:
        """Each colour with its meaning, for the person giving or confirming a status."""
        return await services.organisation.colours(viewer)

    return router
