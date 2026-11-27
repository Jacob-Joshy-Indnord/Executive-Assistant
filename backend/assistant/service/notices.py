"""Notices to owners: asks within working hours and the daily number, and telling.

Section 8.1: at most ``notices_per_owner_per_day`` notices to one owner a day,
in the manager's working hours; what goes out together shares one notice and
the rest waits for the next working day. Asks that go out together are sent at
the same moment, so a notice is counted once for each distinct minute in which
asks were sent to the owner today. Follow-ups proposed today and not yet
carried out are counted too, so a day's follow-ups never exceed the number.

Telling an owner that something changed does not count, and is sent once the
change is saved: a notice that cannot be sent is logged, not retried.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from assistant.boundary.interfaces import Boundary
from assistant.errors import ModuleError
from assistant.models.boundary import Notice
from assistant.models.records import (
    AssistantRecord,
    PersonRequest,
    RequestRecord,
    RequestState,
)
from assistant.rules.follow_ups import in_working_hours
from assistant.rules.settings import Settings
from assistant.rules.working_time import local_date
from assistant.service.actions import Action
from assistant.service.log import log_event
from assistant.store.interface import Transaction

FOLLOW_UP_REQUEST = "update_ask"


def _slot(when: datetime) -> datetime:
    return when.replace(second=0, microsecond=0)


async def notices_today(
    reader: Transaction | Action,
    assistant: AssistantRecord,
    person_id: str,
    now: datetime,
    settings: Settings,
) -> int:
    """Notices the owner has had today from this assistant, or that wait to go out."""
    today = local_date(now, settings)
    slots: set[datetime] = set()
    for ask in await reader.find(PersonRequest, assistant_id=assistant.id, person_id=person_id):
        if ask.sent_at is not None and local_date(ask.sent_at, settings) == today:
            slots.add(_slot(ask.sent_at))
    for request in await reader.find(
        RequestRecord, assistant_id=assistant.id, request_kind=FOLLOW_UP_REQUEST
    ):
        if (
            request.target_id == person_id
            and request.state in (RequestState.WAITING, RequestState.APPROVED)
            and request.payload.get("follow_up")
            and local_date(request.created_at, settings) == today
        ):
            slots.add(_slot(request.created_at))
    return len(slots)


async def may_notify(
    reader: Transaction | Action,
    assistant: AssistantRecord,
    person_id: str,
    now: datetime,
    settings: Settings,
) -> bool:
    """Whether an ask may go to the owner now: in working hours and under the day's number.
    Asks sent this very minute share their notice, so they never use up the number."""
    if not in_working_hours(now, settings):
        return False
    for ask in await reader.find(PersonRequest, assistant_id=assistant.id, person_id=person_id):
        if ask.sent_at is not None and _slot(ask.sent_at) == _slot(now):
            return True
    count = await notices_today(reader, assistant, person_id, now, settings)
    return count < settings.notices_per_owner_per_day


@dataclass(frozen=True)
class Tell:
    """One notice telling a person that something changed."""

    person_id: str
    subject_kind: str
    subject_id: str
    text: str


async def tell(boundary: Boundary, organisation_id: str, tells: Iterable[Tell]) -> int:
    """Send each notice once the change is saved; never to virtual employees. Returns how
    many were delivered; one that cannot be sent is logged and not retried."""
    delivered = 0
    for item in tells:
        try:
            person = await boundary.people.person(item.person_id)
            if person is None or person.kind != "person":
                continue
            result = await boundary.notices.notify(
                Notice(
                    organisation_id=organisation_id,
                    person_id=item.person_id,
                    subject_kind=item.subject_kind,
                    subject_id=item.subject_id,
                    text=item.text,
                )
            )
            ok = result.delivered
        except ModuleError:
            ok = False
        if ok:
            delivered += 1
        else:
            log_event(
                "assistant.notice_not_delivered",
                person_id=item.person_id,
                subject_kind=item.subject_kind,
                subject_id=item.subject_id,
            )
    return delivered
