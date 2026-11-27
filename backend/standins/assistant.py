"""Scripted assistant stand-in: one prepared answer for each kind of work.

Answers are built from the input pack. A faults switch adds faulty tool
requests (an unknown name, a missing source, text over the limit and an
unknown field) so that the module's checks can be shown and tested.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from assistant.boundary.interfaces import Clock
from assistant.models.boundary import PackInput, StartResult, WorkKind, WorkOrder, WorkResult

MESSAGE_KIND = "message"
PROJECT_NEWS_KIND = "project_news"
CHOICE_KIND = "choice"

ACTION_ITEM_TEXT_LIMIT = 300
UNKNOWN_NAME = "Nobody Of That Name"
MISSING_SOURCE = "A source that was never given"

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_REMIND = re.compile(
    r"^\s*remind\s+(?:the\s+)?(?P<owner>.+?)\s+to\s+(?P<task>.+?)\s+by\s+(?P<day>"
    + "|".join(_WEEKDAYS)
    + r")\b",
    re.IGNORECASE,
)


def next_weekday(today: date, weekday_name: str) -> date:
    """The first date on or after ``today`` that falls on ``weekday_name``."""
    target = _WEEKDAYS.index(weekday_name.lower())
    return today + timedelta(days=(target - today.weekday()) % 7)


class ScriptedAssistant:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self.faults = False
        self.refusing = False
        self.started: list[WorkOrder] = []
        self._finished: list[WorkResult] = []
        self._answers: dict[WorkKind, Callable[[WorkOrder, datetime], WorkResult]] = {
            WorkKind.REQUEST: self._answer_request,
            WorkKind.STATUS_DRAFTS: self._answer_status_drafts,
            WorkKind.FOLLOW_ON: self._answer_follow_on,
        }

    async def start_work(self, order: WorkOrder) -> StartResult:
        if self.refusing:
            return StartResult(accepted=False, reason="The assistant is not available right now.")
        self.started.append(order)
        self._finished.append(await self.answer(order))
        return StartResult(accepted=True)

    def take_finished(self) -> list[WorkResult]:
        """Results waiting to be handed to the module, as Flow would at the end of a turn."""
        finished, self._finished = self._finished, []
        return finished

    async def answer(self, order: WorkOrder) -> WorkResult:
        """The prepared answer for this order, with faults added when the switch is on."""
        now = await self._clock.now()
        result = self._answers[order.kind](order, now)
        if not self.faults:
            return result
        return result.model_copy(
            update={"tool_requests": [*result.tool_requests, *self._faulty(order, now)]}
        )

    # Prepared answers -------------------------------------------------------

    def _answer_request(self, order: WorkOrder, now: datetime) -> WorkResult:
        message = _first(order.input_pack.inputs, MESSAGE_KIND)
        match = _REMIND.match(message.content) if message else None
        if message is None or match is None:
            return WorkResult(work_id=order.work_id, reply="I have noted this.")
        task = match["task"].strip().rstrip(".")
        owner = match["owner"].strip()
        due = next_weekday(now.date(), match["day"])
        return WorkResult(
            work_id=order.work_id,
            reply=f"I propose an action item for the {owner}, due {due:%A %d %B}.",
            tool_requests=[
                {
                    "tool": "action_item",
                    "text": task[:1].upper() + task[1:],
                    "owner": owner,
                    "due_date": due.isoformat(),
                    "source": message.label,
                }
            ],
        )

    def _answer_status_drafts(self, order: WorkOrder, now: datetime) -> WorkResult:
        drafts = [
            {
                "tool": "status_statement",
                "project": item.subject_id or item.label,
                "reason": "Drafted from the news since the last report.",
                "what_changed": item.content[:300],
                "what_comes_next": "Confirm the next step with the owner.",
                "source": item.label,
            }
            for item in order.input_pack.inputs
            if item.kind == PROJECT_NEWS_KIND and item.content.strip()
        ]
        reply = f"I drafted {len(drafts)} statements." if drafts else "There is no news to draft."
        return WorkResult(work_id=order.work_id, reply=reply, tool_requests=drafts)

    def _answer_follow_on(self, order: WorkOrder, now: datetime) -> WorkResult:
        choice = _first(order.input_pack.inputs, CHOICE_KIND)
        reply = "I have noted your decision." if choice else "There was no decision to follow."
        return WorkResult(work_id=order.work_id, reply=reply)

    # Faults -----------------------------------------------------------------

    @staticmethod
    def _faulty(order: WorkOrder, now: datetime) -> list[dict[str, Any]]:
        inputs = order.input_pack.inputs
        source = inputs[0].label if inputs else MISSING_SOURCE
        due = (now.date() + timedelta(days=7)).isoformat()

        def item(**changes: Any) -> dict[str, Any]:
            base = {
                "tool": "action_item",
                "text": "Send the signed copy",
                "owner": "Legal lead",
                "due_date": due,
                "source": source,
            }
            return {**base, **changes}

        return [
            item(owner=UNKNOWN_NAME),
            item(source=MISSING_SOURCE),
            item(text="x" * (ACTION_ITEM_TEXT_LIMIT + 1)),
            item(priority="high"),
        ]


def _first(inputs: list[PackInput], kind: str) -> PackInput | None:
    return next((item for item in inputs if item.kind == kind), None)
