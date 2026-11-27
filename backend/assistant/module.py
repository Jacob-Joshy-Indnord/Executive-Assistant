"""The module factory: everything the module needs is passed in here."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter

from assistant.api.router import create_router
from assistant.boundary.interfaces import Boundary
from assistant.service.container import Services, build_services
from assistant.service.due import DueStepRegistry
from assistant.service.flow import FlowCalls
from assistant.service.guard import guard
from assistant.store.interface import Store


@dataclass(frozen=True)
class AssistantModule:
    """One running module: the injected boundary (guarded), the store, the API router,
    the services, and the calls Flow makes (section 9.2)."""

    boundary: Boundary
    store: Store
    router: APIRouter
    services: Services
    flow: FlowCalls


def create_module(
    boundary: Boundary, store: Store, *, due_steps: DueStepRegistry | None = None
) -> AssistantModule:
    """Create the module from one boundary bundle and one store.

    The module reaches people, work data, the assistant, approval, notices,
    records and the time only through ``boundary``, and keeps every record
    through ``store``. ``due_steps`` replaces the module's registry of due
    steps, for tests.
    """
    if not isinstance(boundary, Boundary):
        raise TypeError("create_module needs one Boundary bundle")
    guarded = guard(boundary)
    services = build_services(guarded, store, due_steps)
    return AssistantModule(
        boundary=guarded,
        store=store,
        router=create_router(guarded, store, services),
        services=services,
        flow=FlowCalls(services),
    )
