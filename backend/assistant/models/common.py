"""Small shared types: UTC times, amounts and identifiers."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

from pydantic import AfterValidator, StringConstraints


def _must_be_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("time must be in UTC")
    return value


UtcDateTime = Annotated[datetime, AfterValidator(_must_be_utc)]
"""A timezone-aware time whose offset is zero."""

Amount = Annotated[str, StringConstraints(pattern=r"^-?\d+\.\d{2}$")]
"""Money as text with exactly two decimals, e.g. ``"125000.00"``."""

FlowId = Annotated[str, StringConstraints(min_length=1, max_length=200)]
"""An id issued by Flow (people, organisations, projects, assignments)."""
