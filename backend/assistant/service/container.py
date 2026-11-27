"""All services of one module, built once from a boundary and a store."""

from __future__ import annotations

from dataclasses import dataclass

from assistant.boundary.interfaces import Boundary
from assistant.service.approval_levels import ApprovalLevelService
from assistant.service.closing import ClosingService
from assistant.service.control import ControlService
from assistant.service.due import DUE_STEPS, DueRunner, DueStepRegistry
from assistant.service.organisation import OrganisationService
from assistant.service.setup import SetupService
from assistant.store.interface import Store


@dataclass
class Services:
    boundary: Boundary
    store: Store
    setup: SetupService
    levels: ApprovalLevelService
    organisation: OrganisationService
    due: DueRunner
    control: ControlService
    closing: ClosingService


def build_services(
    boundary: Boundary, store: Store, due_steps: DueStepRegistry | None = None
) -> Services:
    setup = SetupService(boundary, store)
    levels = ApprovalLevelService(boundary, store)
    setup.on_set_up.append(levels.create_defaults)
    due = DueRunner(boundary, store, due_steps if due_steps is not None else DUE_STEPS)
    return Services(
        boundary=boundary,
        store=store,
        setup=setup,
        levels=levels,
        organisation=OrganisationService(boundary, store),
        due=due,
        control=ControlService(boundary, store, due),
        closing=ClosingService(boundary, store, setup, levels),
    )
