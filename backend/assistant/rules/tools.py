"""The tools the assistant may ask for (BRD section 11) and the shape of each request.

Pure rules: what fields each tool has, which are required, their limits in
characters (section 8.1), and the reason, in one sentence, when a request does
not fit. Names, sources, projects and items are resolved by
:mod:`assistant.service.checks`. Part 2 registers its tools with :func:`register`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from assistant.models.records import (
    ACTION_ITEM_LIMIT,
    MESSAGE_LIMIT,
    REASON_LIMIT,
    STATEMENT_FIELD_LIMIT,
)

MAX_TOOL_REQUESTS = 50
MAX_FOLLOW_UPS = 5
CHOICE_LIMIT = 100
SHOWN_LIMIT = 60

FieldKind = Literal[
    "text",
    "name",
    "source",
    "date",
    "times",
    "choices",
    "item",
    "project",
    "line",
    "lines",
    "evidence",
]
MAX_NOTE_LINES = 200
"""Lines of one kind (decisions, actions...) handled in one set of notes."""
MAX_EVIDENCE = 50
"""Signals one lesson can name as its evidence."""


@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: FieldKind
    limit: int | None = None
    required: bool = True


@dataclass(frozen=True)
class ToolSpec:
    name: str
    fields: tuple[FieldSpec, ...]
    exactly_one_of: tuple[str, ...] = ()
    """Fields of which exactly one must be given, e.g. an item or a project."""

    def field(self, name: str) -> FieldSpec | None:
        return next((f for f in self.fields if f.name == name), None)


TOOLS: dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> ToolSpec:
    if spec.name in TOOLS:
        raise ValueError(f"tool {spec.name!r} is registered twice")
    TOOLS[spec.name] = spec
    return spec


F = FieldSpec
register(
    ToolSpec(
        "draft",
        (F("to", "name"), F("text", "text", MESSAGE_LIMIT), F("source", "source", required=False)),
    )
)
register(
    ToolSpec(
        "action_item",
        (
            F("text", "text", ACTION_ITEM_LIMIT),
            F("owner", "name"),
            F("due_date", "date"),
            F("follow_up_times", "times", required=False),
            F("item", "item", required=False),
            F("source", "source"),
        ),
    )
)
register(
    ToolSpec(
        "ask_for_update",
        (
            F("item", "item", required=False),
            F("project", "project", required=False),
            F("owner", "name"),
            F("question", "text", MESSAGE_LIMIT),
            F("source", "source", required=False),
        ),
        exactly_one_of=("item", "project"),
    )
)
register(
    ToolSpec(
        "status_statement",
        (
            F("project", "project"),
            F("reason", "text", REASON_LIMIT),
            F("what_changed", "text", STATEMENT_FIELD_LIMIT, required=False),
            F("what_comes_next", "text", STATEMENT_FIELD_LIMIT, required=False),
            F("source", "source"),
        ),
    )
)
register(
    ToolSpec(
        "meeting_notes",
        (
            F("meeting", "text", 200),
            F("summary", "line", required=False),
            F("decisions", "lines", required=False),
            F("actions", "lines", required=False),
            F("open_points", "lines", required=False),
            F("statements", "lines", required=False),
        ),
    )
)
register(
    ToolSpec(
        "attend_meeting",
        (F("meeting", "text", 200), F("reason", "text", REASON_LIMIT), F("source", "source")),
    )
)
register(
    ToolSpec(
        "raise_attention",
        (
            F("subject", "text", ACTION_ITEM_LIMIT),
            F("known", "text", MESSAGE_LIMIT),
            F("choices", "choices"),
            F("source", "source"),
        ),
    )
)


# Reasons ------------------------------------------------------------------------------

_SPACE = re.compile(r"\s+")


def shown(value: Any) -> str:
    """A value from the assistant, made safe to quote in a reason: one short line, no markup."""
    text = _SPACE.sub(" ", str(value)).replace("<", "").replace(">", "").strip()
    return text if len(text) <= SHOWN_LIMIT else text[: SHOWN_LIMIT - 3].rstrip() + "..."


def refusal(index: int, because: str) -> str:
    """The reason a tool request was refused, in one sentence that says which one."""
    return f"Tool request {index} was refused because {because}."


def over_the_cap(index: int) -> str:
    return refusal(
        index, f"only {MAX_TOOL_REQUESTS} tool requests are handled for one piece of work"
    )


# Shapes -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Shaped:
    """A request whose shape fits its tool: values parsed, still to be resolved."""

    index: int
    tool: ToolSpec
    values: dict[str, Any] = field(default_factory=dict)


def _parse(spec: FieldSpec, value: Any) -> tuple[Any, str | None]:
    """The parsed value, or why it does not fit."""
    name = spec.name.replace("_", " ")
    if spec.kind in ("text", "name", "source", "project"):
        if not isinstance(value, str) or not value.strip():
            return None, f"its {name} is empty or not text"
        if spec.limit is not None and len(value) > spec.limit:
            return None, f"its {name} is over {spec.limit:,} characters"
        return value, None
    if spec.kind == "evidence":
        # A word-for-word quote (text), or the ids of the signals it rests on (a list).
        if isinstance(value, str):
            if not value.strip():
                return None, f"its {name} is empty"
            if spec.limit is not None and len(value) > spec.limit:
                return None, f"its {name} is over {spec.limit:,} characters"
            return value, None
        if (
            not isinstance(value, list)
            or not value
            or len(value) > MAX_EVIDENCE
            or not all(isinstance(v, str) and v.strip() for v in value)
        ):
            return None, f"its {name} is not a quote or a list of at most {MAX_EVIDENCE} signals"
        return [v.strip() for v in value], None
    if spec.kind == "date":
        try:
            return date.fromisoformat(str(value)), None
        except ValueError:
            return None, f"its {name} is not a date"
    if spec.kind == "line":
        if not isinstance(value, dict):
            return None, f"its {name} is not a line"
        return value, None
    if spec.kind == "lines":
        if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
            return None, f"its {name} are not a list of lines"
        if len(value) > MAX_NOTE_LINES:
            return None, f"it has more than {MAX_NOTE_LINES} {name}"
        return list(value), None
    if spec.kind == "item":
        try:
            return UUID(str(value)), None
        except ValueError:
            return None, f"its {name} is not an item"
    if spec.kind == "times":
        if not isinstance(value, list) or len(value) > MAX_FOLLOW_UPS:
            return None, f"its {name} is not a list of at most {MAX_FOLLOW_UPS} times"
        times: list[datetime] = []
        for raw in value:
            try:
                when = datetime.fromisoformat(str(raw))
            except ValueError:
                return None, f"its {name} holds something that is not a time"
            if when.tzinfo is None:
                return None, f"its {name} holds a time without a time zone"
            times.append(when.astimezone(UTC))
        return times, None
    # choices
    if (
        not isinstance(value, list)
        or not 2 <= len(value) <= 4
        or not all(isinstance(c, str) and c.strip() for c in value)
    ):
        return None, f"its {name} are not two to four choices"
    if any(len(c) > CHOICE_LIMIT for c in value):
        return None, f"one of its {name} is over {CHOICE_LIMIT:,} characters"
    return list(value), None


def check_shape(index: int, raw: Any) -> tuple[Shaped | None, str | None]:
    """The request parsed against its tool, or the reason it is refused."""
    if not isinstance(raw, dict):
        return None, refusal(index, "it is not a tool request")
    tool_name = raw.get("tool")
    if not isinstance(tool_name, str) or not tool_name.strip():
        return None, refusal(index, "it names no tool")
    tool = TOOLS.get(tool_name)
    if tool is None:
        return None, refusal(index, f"there is no tool called {shown(tool_name)}")
    unknown = sorted(str(k) for k in raw if k != "tool" and tool.field(str(k)) is None)
    if unknown:
        return None, refusal(index, f"{shown(unknown[0])} is not a field of {tool.name}")
    for spec in tool.fields:
        if spec.required and raw.get(spec.name) is None:
            return None, refusal(index, f"it has no {spec.name.replace('_', ' ')}")
    if tool.exactly_one_of:
        given = [name for name in tool.exactly_one_of if raw.get(name) is not None]
        if len(given) != 1:
            options = " or ".join(n.replace("_", " ") for n in tool.exactly_one_of)
            return None, refusal(index, f"it needs one {options}, not {len(given)}")
    values: dict[str, Any] = {}
    for spec in tool.fields:
        if raw.get(spec.name) is None:
            continue
        value, problem = _parse(spec, raw[spec.name])
        if problem:
            return None, refusal(index, problem)
        values[spec.name] = value
    return Shaped(index, tool, values), None


def due_date_refusal(index: int, due: date, today: date) -> str | None:
    if due < today:
        return refusal(index, "its due date is in the past")
    if due > today + timedelta(days=366 * 2):
        return refusal(index, "its due date is more than two years away")
    return None
