"""The first-time form (Story 2.1). Every part, and every field in a part, is optional.

What the manager leaves out falls back to the defaults of section 8.1
(see :mod:`assistant.rules.settings`).
"""

from __future__ import annotations

from datetime import time as TimeOfDay
from enum import StrEnum
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Weekday(StrEnum):
    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"

    @property
    def index(self) -> int:
        """Monday is 0, as in :meth:`datetime.date.weekday`."""
        return list(Weekday).index(self)


def valid_time_zone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("This is not a time zone we know.") from exc
    return name


class _Part(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class WorkingTime(_Part):
    days: list[Weekday] | None = Field(default=None, min_length=1, max_length=7)
    starts: TimeOfDay | None = None
    ends: TimeOfDay | None = None
    time_zone: str | None = None

    @field_validator("days")
    @classmethod
    def _unique_days(cls, days: list[Weekday] | None) -> list[Weekday] | None:
        if days is not None and len(set(days)) != len(days):
            raise ValueError("Each working day can be chosen once.")
        return sorted(days, key=lambda d: d.index) if days else days

    @field_validator("time_zone")
    @classmethod
    def _known_time_zone(cls, name: str | None) -> str | None:
        return valid_time_zone(name) if name is not None else None

    @model_validator(mode="after")
    def _starts_before_ends(self) -> Self:
        if self.starts is not None and self.ends is not None and self.starts >= self.ends:
            raise ValueError("The working day must start before it ends.")
        return self


class ToneAndSignOff(_Part):
    tone: str | None = Field(default=None, max_length=300)
    sign_off: str | None = Field(default=None, max_length=300)


class PrioritiesAndPeople(_Part):
    priorities: list[str] | None = Field(default=None, max_length=10)
    key_people: list[str] | None = Field(default=None, max_length=20)

    @field_validator("priorities", "key_people")
    @classmethod
    def _short_entries(cls, entries: list[str] | None) -> list[str] | None:
        if entries is not None and any(not e.strip() or len(e) > 300 for e in entries):
            raise ValueError("Each entry must be between 1 and 300 characters.")
        return entries


class FollowUps(_Part):
    follow_ups: int | None = Field(default=None, ge=0, le=5)
    """How many follow-ups an item gets; ``None`` keeps the section 8.1 schedule."""
    answer_within_working_days: int | None = Field(default=None, ge=1, le=5)
    late_after_working_days: int | None = Field(default=None, ge=0, le=10)
    """An item counts as late this many working days after its due date."""
    notices_per_owner_per_day: int | None = Field(default=None, ge=1, le=10)


ReportFrequency = Literal["weekly", "every_two_weeks", "monthly", "off"]


class ReportPreferences(_Part):
    frequency: ReportFrequency | None = None
    day: Weekday | None = None
    time: TimeOfDay | None = None
    ask_owners_working_days_before: int | None = Field(default=None, ge=1, le=5)
    includes: str | None = Field(default=None, max_length=500)
    leaves_out: str | None = Field(default=None, max_length=500)


class PreferencesForm(_Part):
    """The form's five optional parts."""

    working_time: WorkingTime | None = None
    tone: ToneAndSignOff | None = None
    priorities: PrioritiesAndPeople | None = None
    follow_ups: FollowUps | None = None
    report: ReportPreferences | None = None
