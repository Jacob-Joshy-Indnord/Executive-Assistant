"""Audit records before changes (BR-9) and what a record may hold (BR-10).

A person's change is recorded first. If the record cannot be written, the
change is not made and the person sees "Not saved. Try again." (503).

A record holds who, what (its name), which item, the state before and after,
and the time; only text, whole numbers, true or false, lists and objects; and
never the text of a message, statement, note or draft. Text values must be
ids, states or times: short and without spaces, so prose cannot slip in.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from assistant.boundary.interfaces import Records
from assistant.errors import ModuleError, NotSaved
from assistant.models.boundary import AuditRecord

T = TypeVar("T")

RECORD_NAME = re.compile(r"^assistant\.[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
REQUIRED = (
    "name", "organisation_id", "who", "item_kind", "item_id", "state_before", "state_after", "at"
)  # fmt: skip
FORBIDDEN_WORDS = frozenset(
    {"message", "statement", "note", "draft", "text", "wording", "body", "reply", "content",
     "comment", "question", "answer", "reason", "prompt"}
)  # fmt: skip
MAX_TOKEN_LENGTH = 200


class InvalidAuditRecord(ValueError):
    """A record that breaks BR-10 or the naming rule. A programming error, never shown."""


def is_token(value: str) -> bool:
    """Ids, states, names and times: short text without whitespace."""
    return len(value) <= MAX_TOKEN_LENGTH and not any(ch.isspace() for ch in value)


def _forbidden_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in FORBIDDEN_WORDS or any(lowered.endswith("_" + w) for w in FORBIDDEN_WORDS)


def check_plain_values(value: Any, path: str = "") -> None:
    """Only text tokens, whole numbers, true or false, lists and objects with text keys."""
    if isinstance(value, bool | int):
        return
    if isinstance(value, str):
        if not is_token(value):
            raise InvalidAuditRecord(f"{path or 'value'} holds free text")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            check_plain_values(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not is_token(key):
                raise InvalidAuditRecord(f"{path or 'object'} has a key that is not plain text")
            if _forbidden_key(key) and not isinstance(item, bool | int):
                raise InvalidAuditRecord(f"{path}.{key} would hold written text")
            check_plain_values(item, f"{path}.{key}" if path else key)
        return
    raise InvalidAuditRecord(f"{path or 'value'} is a {type(value).__name__}")


def audit_payload(record: AuditRecord) -> dict[str, Any]:
    """The record as it is written, validated against BR-10 and the naming rule."""
    if not RECORD_NAME.match(record.name):
        raise InvalidAuditRecord("record names are 'assistant.' and lower_snake_case words")
    check_plain_values(record.details, "details")  # as given, before JSON conversion
    payload = record.model_dump(mode="json", exclude_none=True)
    for key in REQUIRED:
        if not payload.get(key):
            raise InvalidAuditRecord(f"a record needs {key}")
    check_plain_values(payload)
    return payload


def validate_audit_record(record: AuditRecord) -> AuditRecord:
    audit_payload(record)
    return record


async def record_then_change(
    records: Records, record: AuditRecord, change: Callable[[], Awaitable[T]]
) -> T:
    """Write ``record`` first; only when it is written, make ``change``.

    Raises :class:`~assistant.errors.NotSaved` when the record is not written.
    Raised inside a store transaction, that rolls back everything the action
    did so far.
    """
    validate_audit_record(record)
    try:
        result = await records.write(record)
    except ModuleError as exc:
        raise NotSaved() from exc
    if not result.written:
        raise NotSaved()
    return await change()
