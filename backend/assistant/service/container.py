"""All services of one module, built once from a boundary and a store."""

from __future__ import annotations

from dataclasses import dataclass

from assistant.boundary.interfaces import Boundary
from assistant.service import item_steps, status_steps  # noqa: F401  (register due steps)
from assistant.service.approval_levels import ApprovalLevelService
from assistant.service.checks import ToolChecks
from assistant.service.closing import ClosingService
from assistant.service.control import ControlService
from assistant.service.decisions import DecisionService
from assistant.service.due import DUE_STEPS, DueRunner, DueStepRegistry
from assistant.service.intake import ToolIntake
from assistant.service.items import ItemService
from assistant.service.manager_notices import ManagerNoticeService
from assistant.service.organisation import OrganisationService
from assistant.service.requests import RequestService
from assistant.service.setup import SetupService
from assistant.service.status import StatusService
from assistant.service.work import WorkService
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
    work: WorkService
    requests: RequestService
    items: ItemService
    decisions: DecisionService
    status: StatusService
    manager_notices: ManagerNoticeService


def build_services(
    boundary: Boundary, store: Store, due_steps: DueStepRegistry | None = None
) -> Services:
    setup = SetupService(boundary, store)
    levels = ApprovalLevelService(boundary, store)
    setup.on_set_up.append(levels.create_defaults)
    due = DueRunner(boundary, store, due_steps if due_steps is not None else DUE_STEPS)
    control = ControlService(boundary, store, due)
    work = WorkService(boundary, store)
    requests = RequestService(boundary, store)
    work.tool_requests = ToolIntake(boundary, ToolChecks(boundary), requests)
    control.result_handler = work.handle_result
    control.on_caught_up.append(requests.carry_out_approved)
    control.on_caught_up.append(work.advance)
    due.after_run.append(requests.carry_out_approved)
    due.after_run.append(work.advance)
    items = ItemService(boundary, store, requests)
    status = StatusService(boundary, store, requests)
    decisions = DecisionService(boundary, store, items, work, status)
    manager_notices = ManagerNoticeService(boundary, store, requests, decisions)
    due.after_run.append(manager_notices.after)
    control.after_result.append(manager_notices.after)
    control.on_caught_up.append(manager_notices.after)
    return Services(
        boundary=boundary,
        store=store,
        setup=setup,
        levels=levels,
        organisation=OrganisationService(boundary, store),
        due=due,
        control=control,
        closing=ClosingService(boundary, store, setup, levels),
        work=work,
        requests=requests,
        items=items,
        decisions=decisions,
        status=status,
        manager_notices=manager_notices,
    )
