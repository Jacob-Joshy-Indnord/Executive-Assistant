"""The module factory: everything the module needs is passed in here."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter

from assistant.api.router import create_router
from assistant.boundary.interfaces import Boundary
from assistant.service.guard import guard
from assistant.store.interface import Store


@dataclass(frozen=True)
class AssistantModule:
    """One running module: the injected boundary (guarded), the store and the API router."""

    boundary: Boundary
    store: Store
    router: APIRouter


def create_module(boundary: Boundary, store: Store) -> AssistantModule:
    """Create the module from one boundary bundle and one store.

    The module reaches people, work data, the assistant, approval, notices,
    records and the time only through ``boundary``, and keeps every record
    through ``store``.
    """
    if not isinstance(boundary, Boundary):
        raise TypeError("create_module needs one Boundary bundle")
    guarded = guard(boundary)
    return AssistantModule(boundary=guarded, store=store, router=create_router(guarded, store))
