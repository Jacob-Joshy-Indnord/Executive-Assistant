"""The demonstration (Story 7.1): the module wired to the stand-ins, with the bar's addresses.

Start it with ``python -m standins.demo``. It serves the module's addresses under
``/api/assistant`` exactly as Flow would mount them, and the demonstration bar's own
addresses under ``/api/demo``. Those belong to the stand-ins, never to the module: the bar
plays Flow's part (who is asking, the schedule, set up, hand over, end, pause everything)
and calls the module only through ``module.flow`` and its router.

Everything lives in memory. "Start again" builds a fresh sample organisation, so nothing
needs an account, a key or a connection.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel, ConfigDict
from starlette.types import Receive, Scope, Send

from assistant.api.errors import ModuleErrorRoute
from assistant.errors import NotFound
from assistant.models.boundary import AssignmentState
from assistant.models.records import AssistantRecord, AssistantState
from assistant.module import AssistantModule, create_module
from assistant.service.pausing import organisation_paused
from assistant.store.sqlite import SqliteStore
from standins.bundle import StandIns, build_standins
from standins.clock import Ran
from standins.sample import (
    FINANCE_LEAD,
    LEGAL_LEAD,
    OPERATIONS_DIRECTOR,
    ORGANISATION_ADMINISTRATOR,
    ORGANISATION_ID,
    PROGRAMME_LEAD,
    SALES_DIRECTOR,
    SamplePerson,
)
from standins.schedule import connect_schedule

DEMO_PREFIX = "/api/demo"
ASSISTANT_NAME = "Executive Assistant"
FIRST_FLOW_ID = "flow-assistant-operations_director"

# The people the bar offers, in the order shown, with what they are in the demonstration.
BAR_PEOPLE: tuple[tuple[SamplePerson, str], ...] = (
    (OPERATIONS_DIRECTOR, "Manager"),
    (LEGAL_LEAD, "Owner"),
    (FINANCE_LEAD, "Owner"),
    (PROGRAMME_LEAD, "Owner"),
    (SALES_DIRECTOR, "Another manager"),
    (ORGANISATION_ADMINISTRATOR, "Administrator"),
)

Fault = Literal["assistant_faults", "assistant_refusing", "approval", "notices", "records"]
FAULTS: tuple[Fault, ...] = (
    "assistant_faults",
    "assistant_refusing",
    "approval",
    "notices",
    "records",
)


def flow_id_for(person: SamplePerson) -> str:
    return f"flow-assistant-{person.key}"


class Demonstration:
    """One running demonstration: the stand-ins, a store and the module made from them."""

    def __init__(self) -> None:
        self.standins: StandIns
        self.store: SqliteStore
        self.module: AssistantModule
        self.app: FastAPI
        self._build()

    def _build(self) -> None:
        self.standins = build_standins()
        self.store = SqliteStore()
        self.module = create_module(self.standins.boundary, self.store)
        # Flow hands each ended turn back to the module before anything else falls due.
        self.standins.clock.add_source("the assistant's turn ended", self._turn_ended, self._finish)
        connect_schedule(self.standins.clock, self.module)
        self.app = FastAPI(title="Executive Assistant")
        self.app.include_router(self.module.router)

    async def start(self) -> None:
        """Start from the sample data: an assistant just set up for the Operations director."""
        self._build()
        await self.module.flow.set_up(
            ORGANISATION_ID, OPERATIONS_DIRECTOR.id, ASSISTANT_NAME, FIRST_FLOW_ID
        )

    # Flow's side of a piece of work ---------------------------------------------------

    async def _turn_ended(self) -> datetime | None:
        return await self.standins.clock.now() if self.standins.assistant.has_finished else None

    async def _finish(self, at: datetime) -> None:
        await self.finish_turns()

    async def finish_turns(self) -> None:
        """Hand every ended turn to the module, as Flow does at the end of a turn."""
        while self.standins.assistant.has_finished:
            for result in self.standins.assistant.take_finished():
                flow_id = await self._flow_id_of_work(result.work_id)
                if flow_id is not None:
                    await self.module.flow.finish_work(ORGANISATION_ID, flow_id, result)

    async def _flow_id_of_work(self, work_id: UUID) -> str | None:
        order = next((o for o in self.standins.assistant.started if o.work_id == work_id), None)
        return order.assistant_flow_id if order else None

    # The bar ---------------------------------------------------------------------------

    async def assistants(self) -> list[AssistantRecord]:
        async with self.store.transaction(ORGANISATION_ID) as tx:
            found = await tx.find(AssistantRecord)
        return sorted(
            (a for a in found if a.state is not AssistantState.ENDED),
            key=lambda a: a.created_at,
        )

    async def assistant_by_flow_id(self, flow_id: str) -> AssistantRecord | None:
        return next((a for a in await self.assistants() if a.flow_id == flow_id), None)

    async def all_paused(self) -> bool:
        async with self.store.transaction(ORGANISATION_ID) as tx:
            return await organisation_paused(tx, ORGANISATION_ID)

    def faults(self) -> dict[str, bool]:
        s = self.standins
        return {
            "assistant_faults": s.assistant.faults,
            "assistant_refusing": s.assistant.refusing,
            "approval": s.approval.failing,
            "notices": s.notices.failing,
            "records": s.records.failing,
        }

    def set_fault(self, fault: Fault, on: bool) -> None:
        s = self.standins
        if fault == "assistant_faults":
            s.assistant.faults = on
        elif fault == "assistant_refusing":
            s.assistant.refusing = on
        elif fault == "approval":
            s.approval.failing = on
        elif fault == "notices":
            s.notices.failing = on
        else:
            s.records.failing = on

    async def move_clock(self, move: Literal["hour", "day", "next"]) -> list[Ran]:
        clock = self.standins.clock
        if move == "hour":
            return await clock.forward_hour()
        if move == "day":
            return await clock.forward_day()
        return await clock.to_next_due()

    async def state(self) -> dict[str, Any]:
        """What the bar shows: the time, the people, the assistants, the switches and the
        notices that were sent (the stand-ins keep them in lists)."""
        names = {p.id: p.display_name for p, _ in BAR_PEOPLE}
        return {
            "now": (await self.standins.clock.now()).isoformat(),
            "people": [
                {"id": p.id, "name": p.display_name, "role": role} for p, role in BAR_PEOPLE
            ],
            "assistants": [
                {
                    "flow_id": a.flow_id,
                    "manager_id": a.manager_id,
                    "manager_name": names.get(a.manager_id, "Someone else"),
                    "state": a.state.value,
                }
                for a in await self.assistants()
            ],
            "all_paused": await self.all_paused(),
            "faults": self.faults(),
            "notices": [
                {"to": names.get(n.person_id, "Someone else"), "text": n.text}
                for n in self.standins.notices.sent
            ],
            "assignments": [
                {"id": a.id, "title": a.title, "state": a.state}
                for a in self.standins.work_data.assignments_by_id.values()
            ],
            "carried_out": len(self.standins.approval.carried_out),
            "records": len(self.standins.records.written),
        }


# --- Addresses -----------------------------------------------------------------------


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClockBody(_Body):
    move: Literal["hour", "day", "next"]


class FaultBody(_Body):
    fault: Fault
    on: bool


class SetUpBody(_Body):
    manager_id: str


class HandOverBody(_Body):
    flow_id: str
    new_manager_id: str


class EndBody(_Body):
    flow_id: str


class PauseAllBody(_Body):
    paused: bool


class AssignmentBody(_Body):
    assignment_id: str
    state: AssignmentState


def create_demo_router(demo: Demonstration) -> APIRouter:
    """The bar's addresses. Each call to the module goes through ``module.flow``."""
    router = APIRouter(prefix=DEMO_PREFIX, route_class=ModuleErrorRoute, tags=["demonstration"])

    async def known_flow_id(flow_id: str) -> str:
        if await demo.assistant_by_flow_id(flow_id) is None:
            raise NotFound()
        return flow_id

    async def known_person(person_id: str) -> SamplePerson:
        found = next((p for p, _ in BAR_PEOPLE if p.id == person_id), None)
        if found is None:
            raise NotFound()
        return found

    @router.get("")
    async def state() -> dict[str, Any]:
        return await demo.state()

    @router.post("/clock")
    async def move_clock(body: ClockBody) -> dict[str, Any]:
        ran = await demo.move_clock(body.move)
        return {
            **await demo.state(),
            "ran": [{"at": r.at.isoformat(), "name": r.name} for r in ran],
        }

    @router.put("/faults")
    async def set_fault(body: FaultBody) -> dict[str, Any]:
        demo.set_fault(body.fault, body.on)
        return await demo.state()

    @router.post("/reset")
    async def reset() -> dict[str, Any]:
        await demo.start()
        return await demo.state()

    @router.post("/set-up")
    async def set_up(body: SetUpBody) -> dict[str, Any]:
        person = await known_person(body.manager_id)
        await demo.module.flow.set_up(
            ORGANISATION_ID, person.id, ASSISTANT_NAME, flow_id_for(person)
        )
        return await demo.state()

    @router.post("/hand-over")
    async def hand_over(body: HandOverBody) -> dict[str, Any]:
        person = await known_person(body.new_manager_id)
        flow_id = await known_flow_id(body.flow_id)
        await demo.module.flow.hand_over(ORGANISATION_ID, flow_id, person.id)
        return await demo.state()

    @router.post("/end")
    async def end(body: EndBody) -> dict[str, Any]:
        await demo.module.flow.end(ORGANISATION_ID, await known_flow_id(body.flow_id))
        return await demo.state()

    @router.post("/assignment")
    async def change_assignment(body: AssignmentBody) -> dict[str, Any]:
        """Work data changes in Flow: the next run of what is due sees it as news."""
        if body.assignment_id not in demo.standins.work_data.assignments_by_id:
            raise NotFound()
        demo.standins.work_data.set_assignment_state(body.assignment_id, body.state)
        return await demo.state()

    @router.post("/pause-all")
    async def pause_all(body: PauseAllBody) -> dict[str, Any]:
        if body.paused:
            await demo.module.flow.pause_all(ORGANISATION_ID)
        else:
            await demo.module.flow.resume_all(ORGANISATION_ID)
        await demo.finish_turns()
        return await demo.state()

    return router


class DemonstrationApp:
    """The demonstration's one ASGI app.

    ``/api/demo`` goes to the bar's addresses; everything else goes to the module's app,
    which "Start again" replaces. Calls are taken one at a time, so moving the clock never
    overlaps a page's call, and after each call the ended turns are handed back as Flow
    would hand them back.
    """

    def __init__(self, demo: Demonstration) -> None:
        self.demo = demo
        self._bar = FastAPI(title="Demonstration bar")
        self._bar.include_router(create_demo_router(demo))
        self._lock = asyncio.Lock()
        self._started = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        async with self._lock:
            if not self._started:
                await self.demo.start()
                self._started = True
            path: str = scope.get("path", "")
            target: Callable[[Scope, Receive, Send], Awaitable[None]]
            target = self._bar if path.startswith(DEMO_PREFIX) else self.demo.app
            await target(scope, receive, send)
            await self.demo.finish_turns()

    @staticmethod
    async def _lifespan(receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return


@dataclass(frozen=True)
class Address:
    host: str
    port: int


def address_from_env() -> Address:
    return Address(
        host=os.environ.get("EA_DEMO_HOST", "127.0.0.1"),
        port=int(os.environ.get("EA_DEMO_PORT", "8000")),
    )


def create_app() -> DemonstrationApp:
    return DemonstrationApp(Demonstration())


def main() -> None:  # pragma: no cover - starts a server
    import uvicorn

    address = address_from_env()
    uvicorn.run(create_app(), host=address.host, port=address.port, log_level="warning")


if __name__ == "__main__":  # pragma: no cover
    main()
