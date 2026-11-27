"""Wrap the boundary so that any failure surfaces as one plain sentence.

A guarded part raises :class:`~assistant.errors.BoundaryUnavailable` whatever
went wrong on Flow's side. Raised inside a store transaction, it rolls the
transaction back, so no record is left half-changed.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, cast

from assistant.boundary.interfaces import (
    Approval,
    Assistant,
    Boundary,
    Clock,
    Notices,
    People,
    Records,
    WorkData,
)
from assistant.errors import BoundaryUnavailable, ModuleError

PART_SENTENCES: dict[str, str] = {
    "people": "The people directory could not be reached, so nothing was changed.",
    "work_data": "Project and assignment data could not be reached, so nothing was changed.",
    "assistant": "The assistant could not be reached, so nothing was changed.",
    "approval": "The approval check could not be reached, so nothing was changed.",
    "notices": "Notices could not be sent, so nothing was changed.",
    "records": "The audit record could not be reached, so nothing was changed.",
    "clock": "The time could not be read, so nothing was changed.",
}


class _GuardedPart:
    """Proxy whose async methods turn any exception into ``BoundaryUnavailable``."""

    def __init__(self, inner: object, sentence: str) -> None:
        self._inner = inner
        self._sentence = sentence

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._inner, name)
        if not inspect.iscoroutinefunction(target):
            return target
        sentence = self._sentence

        @functools.wraps(target)
        async def guarded(*args: Any, **kwargs: Any) -> Any:
            try:
                return await cast(Callable[..., Awaitable[Any]], target)(*args, **kwargs)
            except ModuleError:
                raise
            except Exception as exc:  # any failure on Flow's side
                raise BoundaryUnavailable(sentence) from exc

        return guarded


class _GuardedClock(_GuardedPart):
    async def now(self) -> datetime:
        value = await self.__getattr__("now")()
        if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
            raise BoundaryUnavailable(self._sentence)
        return value


def guard(boundary: Boundary) -> Boundary:
    """Return a boundary whose every operation fails with one plain sentence."""
    return Boundary(
        people=cast(People, _GuardedPart(boundary.people, PART_SENTENCES["people"])),
        work_data=cast(WorkData, _GuardedPart(boundary.work_data, PART_SENTENCES["work_data"])),
        assistant=cast(Assistant, _GuardedPart(boundary.assistant, PART_SENTENCES["assistant"])),
        approval=cast(Approval, _GuardedPart(boundary.approval, PART_SENTENCES["approval"])),
        notices=cast(Notices, _GuardedPart(boundary.notices, PART_SENTENCES["notices"])),
        records=cast(Records, _GuardedPart(boundary.records, PART_SENTENCES["records"])),
        clock=cast(Clock, _GuardedClock(boundary.clock, PART_SENTENCES["clock"])),
    )
