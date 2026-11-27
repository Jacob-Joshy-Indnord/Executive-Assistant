"""The module's only way to log: ids and states, never written text."""

from __future__ import annotations

import json
import logging

from assistant.service.audit import RECORD_NAME, check_plain_values

logger = logging.getLogger("assistant")


def log_event(event: str, **fields: str | int | bool | None) -> None:
    """Log one event named like a record, with id and state fields only.

    Raises ``InvalidAuditRecord`` if a field would carry free text, so a
    mistake fails in tests rather than leaking text into the logs.
    """
    if not RECORD_NAME.match(event):
        raise ValueError("log events are named like records: 'assistant.' and lower_snake_case")
    present = {key: value for key, value in fields.items() if value is not None}
    check_plain_values(present)
    logger.info("%s %s", event, json.dumps(present, sort_keys=True))
