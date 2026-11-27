"""Flow's schedule, stood in by the movable clock: moving it runs what is due, in order."""

from __future__ import annotations

from datetime import datetime

from assistant.module import AssistantModule
from standins.clock import MovableClock


def connect_schedule(clock: MovableClock, module: AssistantModule) -> None:
    """Call the module's "Run what is due" at each time something falls due."""

    async def run(at: datetime) -> None:
        await module.flow.run_due()

    clock.add_source("run what is due", module.flow.next_due, run)
