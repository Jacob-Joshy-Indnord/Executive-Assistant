"""Clock stand-in that can be moved forward.

Moving it runs everything due on the way, in time order: each step sees
``now()`` equal to the time it was due. Two kinds of thing can be due:

* steps scheduled for a time with :meth:`MovableClock.at`, and
* sources registered with :meth:`MovableClock.add_source`, such as the
  module's "run what is due", which say when they are next due.
"""

from __future__ import annotations

import heapq
import itertools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pydantic import TypeAdapter

from assistant.models.common import UtcDateTime

Step = Callable[[], Awaitable[None]]
NextDue = Callable[[], Awaitable[datetime | None]]
RunDue = Callable[[datetime], Awaitable[None]]

MAX_STEPS_PER_MOVE = 10_000

_utc = TypeAdapter(UtcDateTime)


@dataclass(order=True)
class _Scheduled:
    due: datetime
    seq: int
    name: str = field(compare=False)
    step: Step = field(compare=False)


@dataclass(frozen=True)
class _Source:
    name: str
    next_due: NextDue
    run_due: RunDue


@dataclass(frozen=True)
class Ran:
    """One step that ran while the clock moved."""

    at: datetime
    name: str


class MovableClock:
    def __init__(self, start: datetime, max_steps_per_move: int = MAX_STEPS_PER_MOVE) -> None:
        self._now = _utc.validate_python(start)
        self._max_steps = max_steps_per_move
        self._timeline: list[_Scheduled] = []
        self._seq = itertools.count()
        self._sources: list[_Source] = []

    async def now(self) -> datetime:
        return self._now

    # Scheduling -------------------------------------------------------------

    def at(self, due: datetime, step: Step, name: str = "step") -> None:
        scheduled = _Scheduled(_utc.validate_python(due), next(self._seq), name, step)
        heapq.heappush(self._timeline, scheduled)

    def add_source(self, name: str, next_due: NextDue, run_due: RunDue) -> None:
        self._sources.append(_Source(name, next_due, run_due))

    # Moving -----------------------------------------------------------------

    async def forward_hour(self) -> list[Ran]:
        return await self.forward(timedelta(hours=1))

    async def forward_day(self) -> list[Ran]:
        return await self.forward(timedelta(days=1))

    async def forward(self, delta: timedelta) -> list[Ran]:
        if delta < timedelta(0):
            raise ValueError("the clock only moves forward")
        return await self.move_to(self._now + delta)

    async def to_next_due(self) -> list[Ran]:
        """Move to the next time anything is due and run what is due then."""
        nxt = await self._earliest()
        if nxt is None:
            return []
        return await self.move_to(max(nxt[0], self._now))

    async def move_to(self, target: datetime) -> list[Ran]:
        target = _utc.validate_python(target)
        if target < self._now:
            raise ValueError("the clock only moves forward")
        ran: list[Ran] = []
        for _ in range(self._max_steps):
            nxt = await self._earliest()
            if nxt is None or nxt[0] > target:
                break
            due, run, name = nxt
            self._now = max(self._now, due)
            await run()
            ran.append(Ran(self._now, name))
        else:
            raise RuntimeError("a due step keeps falling due again")
        self._now = target
        return ran

    async def _earliest(self) -> tuple[datetime, Step, str] | None:
        """The earliest due thing; on a tie, scheduled steps first, in the order added."""
        best: tuple[datetime, Step, str] | None = None
        if self._timeline:
            head = self._timeline[0]

            async def run_head() -> None:
                heapq.heappop(self._timeline)
                await head.step()

            best = (head.due, run_head, head.name)
        for source in self._sources:
            due = await source.next_due()
            if due is None:
                continue
            due = _utc.validate_python(due)
            if best is None or due < best[0]:
                best = (due, _bind(source.run_due, max(due, self._now)), source.name)
        return best


def _bind(run_due: RunDue, at: datetime) -> Step:
    async def step() -> None:
        await run_due(at)

    return step
