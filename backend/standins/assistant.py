"""Scripted assistant stand-in: one prepared answer for each kind of work.

Answers are built from the input pack. A faults switch adds faulty tool
requests (an unknown name, a missing source, text over the limit and an
unknown field) so that the module's checks can be shown and tested; for
meeting notes, it adds lines that name a part that does not exist or are too
long.

From a transcript it uses the lines that begin with "Decision:", "Action:",
"Open:" and "Status:", and no other line, and writes a summary from the first
lines. Asked about a past meeting, it answers from the notes in its pack and
names them.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from assistant.boundary.interfaces import Clock
from assistant.models.boundary import PackInput, StartResult, WorkKind, WorkOrder, WorkResult

MESSAGE_KIND = "message"
PROJECT_NEWS_KIND = "project_news"
CHOICE_KIND = "choice"
MEETING_KIND = "meeting"
PARTS_KIND = "transcript_parts"
NOTES_KIND = "meeting_notes"
FIGURES_KIND = "briefing_figures"
PAPER_KIND = "decision_paper"
PAPER_DAYS = 7
"""A decision paper is due this many days after it is written."""
SUMMARY_LINES = 2
"""The first lines of a transcript the summary is written from."""
MISSING_PART = 9999

ACTION_ITEM_TEXT_LIMIT = 300
UNKNOWN_NAME = "Nobody Of That Name"
MISSING_SOURCE = "A source that was never given"

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_ACTION = re.compile(
    r"^(?P<owner>.+?)\s+to\s+(?P<task>.+?)(?:\s+by\s+(?P<due>\d{4}-\d{2}-\d{2}|"
    + "|".join(_WEEKDAYS)
    + r"))?\.?$",
    re.IGNORECASE,
)
_MARKS = ("Decision:", "Action:", "Open:", "Status:")
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
            WorkKind.MEETING_NOTES: self._answer_meeting_notes,
            WorkKind.BRIEFING: self._answer_briefing,
        }

    async def start_work(self, order: WorkOrder) -> StartResult:
        if self.refusing:
            return StartResult(accepted=False, reason="The assistant is not available right now.")
        self.started.append(order)
        self._finished.append(await self.answer(order))
        return StartResult(accepted=True)

    @property
    def has_finished(self) -> bool:
        """Whether a turn has ended and its result waits to be handed to the module."""
        return bool(self._finished)

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
        if order.kind is WorkKind.MEETING_NOTES:
            return result.model_copy(update={"tool_requests": _faulty_notes(result.tool_requests)})
        if order.kind is WorkKind.BRIEFING:
            return result.model_copy(update={"deliverable": _unsourced(result.deliverable)})
        return result.model_copy(
            update={"tool_requests": [*result.tool_requests, *self._faulty(order, now)]}
        )

    # Prepared answers -------------------------------------------------------

    def _answer_request(self, order: WorkOrder, now: datetime) -> WorkResult:
        paper = _decision_paper(order, now)
        if paper is not None:
            return WorkResult(
                work_id=order.work_id, reply="I prepared a decision paper.", deliverable=paper
            )
        message = _first(order.input_pack.inputs, MESSAGE_KIND)
        match = _REMIND.match(message.content) if message else None
        notes = _first(order.input_pack.inputs, NOTES_KIND)
        if match is None and notes is not None:
            return WorkResult(work_id=order.work_id, reply=_from_notes(notes))
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

    def _answer_meeting_notes(self, order: WorkOrder, now: datetime) -> WorkResult:
        meeting = _first(order.input_pack.inputs, MEETING_KIND)
        if meeting is None:
            return WorkResult(work_id=order.work_id, reply="There was no transcript to read.")
        parts = [
            json.loads(line)
            for item in order.input_pack.inputs
            if item.kind == PARTS_KIND
            for line in item.content.splitlines()
            if line.strip()
        ]
        notes: dict[str, Any] = {
            "tool": "meeting_notes",
            "meeting": meeting.subject_id,
            "decisions": [],
            "actions": [],
            "open_points": [],
            "statements": [],
        }
        first = parts[:SUMMARY_LINES]
        if first:
            notes["summary"] = {
                "text": " ".join(p["text"] for p in first)[:600],
                "parts": [p["number"] for p in first],
            }
        for part in parts:
            text: str = part["text"]
            mark = next((m for m in _MARKS if text.startswith(m)), None)
            if mark is None:
                continue  # only the marked lines become notes
            rest = text[len(mark) :].strip()
            cited = [part["number"]]
            if mark == "Decision:":
                notes["decisions"].append({"text": rest, "parts": cited})
            elif mark == "Open:":
                notes["open_points"].append({"text": rest, "parts": cited})
            elif mark == "Action:":
                notes["actions"].append(_action(rest, cited, now))
            else:
                project, _, reason = rest.partition(":")
                notes["statements"].append(
                    {"project": project.strip(), "reason": reason.strip() or rest, "parts": cited}
                )
        return WorkResult(work_id=order.work_id, tool_requests=[notes])

    def _answer_briefing(self, order: WorkOrder, now: datetime) -> WorkResult:
        """A briefing note for each section of figures, and one for each kept meeting, each
        citing the input it was written from. Its figures are the module's, never its own."""
        inputs = order.input_pack.inputs
        figures = [i for i in inputs if i.kind == FIGURES_KIND]
        meetings = [i for i in inputs if i.kind == NOTES_KIND]
        if not figures and not meetings:
            reply = "There were no figures to write about."
            return WorkResult(work_id=order.work_id, reply=reply)
        sections: list[dict[str, Any]] = [
            {
                "section": item.subject_id,
                "notes": [{"text": _figures_note(item), "sources": [item.label]}],
            }
            for item in figures
        ]
        if meetings:
            sections.append(
                {
                    "section": "meetings",
                    "notes": [
                        {
                            "text": f"{_title_of(item.label)} was kept with its notes.",
                            "sources": [item.label],
                        }
                        for item in meetings
                    ],
                }
            )
        return WorkResult(
            work_id=order.work_id,
            reply=f"I wrote notes on {len(sections)} sections.",
            deliverable={"kind": "briefing", "sections": sections},
        )

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


def _action(rest: str, parts: list[int], now: datetime) -> dict[str, Any]:
    """ "<owner> to <task> by <date or weekday>": an action with its owner and due date."""
    match = _ACTION.match(rest)
    if match is None:
        return {"text": rest, "parts": parts}
    task = match["task"].strip()
    action: dict[str, Any] = {
        "text": task[:1].upper() + task[1:],
        "owner": match["owner"].strip(),
        "parts": parts,
    }
    due = match["due"]
    if due:
        action["due_date"] = (
            due if due[0].isdigit() else next_weekday(now.date(), due).isoformat()
        )
    return action


def _faulty_notes(tool_requests: list[Any]) -> list[Any]:
    """Notes with a decision naming a part that does not exist and an open point that is
    too long, beside the good lines."""
    faulty: list[Any] = []
    for request in tool_requests:
        if isinstance(request, dict) and request.get("tool") == "meeting_notes":
            request = {
                **request,
                "decisions": [
                    *request.get("decisions", []),
                    {"text": "A decision from a part that is not there", "parts": [MISSING_PART]},
                ],
                "open_points": [
                    *request.get("open_points", []),
                    {"text": "x" * (ACTION_ITEM_TEXT_LIMIT + 1), "parts": [1]},
                ],
            }
        faulty.append(request)
    return faulty


def _from_notes(notes: PackInput) -> str:
    """An answer from kept notes that names them, so the manager can open them."""
    lines = [
        line
        for line in notes.content.splitlines()
        if line.startswith(("Summary:", "Decision:", "Open:", "Action:"))
    ]
    said = " ".join(lines[:4]) or "There is nothing more in them."
    return f"From {notes.label}: {said}"[:4000]


def _figures_note(item: PackInput) -> str:
    """One sentence on a section's figures, from the figures as given."""
    title = item.label.removeprefix("Figures: ")
    try:
        figures = json.loads(item.content)
    except ValueError:
        figures = None
    if isinstance(figures, dict):
        said = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in sorted(figures.items()))
        return f"{title}: {said}."[:500]
    if isinstance(figures, list):
        return f"{title}: {len(figures)} lines to look at."
    return f"{title}: see the figures."


def _title_of(label: str) -> str:
    return label.removeprefix("Meeting notes: ")


def _unsourced(deliverable: dict[str, Any] | None) -> dict[str, Any] | None:
    """A briefing with a note that cites nothing, beside the good notes."""
    if not deliverable or not deliverable.get("sections"):
        return deliverable
    first, *rest = deliverable["sections"]
    unsourced = {"text": "A note without a source.", "sources": []}
    first = {**first, "notes": [*first["notes"], unsourced]}
    return {**deliverable, "sections": [first, *rest]}


def _decision_paper(order: WorkOrder, now: datetime) -> dict[str, Any] | None:
    """A decision paper when the manager's message asks "Should we", or for a paper sent
    back: two options, a recommendation, due in a week, citing what it was written from."""
    inputs = order.input_pack.inputs
    message = _first(inputs, MESSAGE_KIND)
    asked = message if message and "should we" in message.content.casefold() else None
    previous = _first(inputs, PAPER_KIND)
    source = asked or previous
    if source is None:
        return None
    if asked is not None:
        question = asked.content.strip()
    else:
        first = source.content.splitlines()[0] if source.content else ""
        question = first.removeprefix("Decision: ").strip() or "The decision sent back"
    return {
        "kind": "decision_paper",
        "decision": question[:300],
        "background": f"Written from {source.label}.",
        "options": ["Yes, go ahead", "No, not now"],
        "recommendation": "Yes, go ahead",
        "due_date": (now.date() + timedelta(days=PAPER_DAYS)).isoformat(),
        "sources": [source.label],
    }
