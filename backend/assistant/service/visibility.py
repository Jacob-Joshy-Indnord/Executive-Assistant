"""Apply BR-8 in the service layer. Not-theirs is answered exactly like not-found."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeVar
from uuid import UUID

from assistant.boundary.interfaces import Boundary
from assistant.errors import NotFound, NotSignedIn
from assistant.models.records import AssistantRecord, Record
from assistant.rules.visibility import Viewer, can_see
from assistant.store.interface import Transaction

R = TypeVar("R", bound=Record)


async def viewer_for(boundary: Boundary, headers: Mapping[str, str]) -> Viewer:
    """The person asking, as named by the boundary."""
    person = await boundary.people.person_asking(headers)
    if person is None:
        raise NotSignedIn()
    return Viewer(
        person_id=person.id,
        organisation_id=person.organisation_id,
        is_administrator=await boundary.people.is_administrator(person.id),
    )


def parse_id(raw: str) -> UUID:
    """An id from an address; a malformed one is simply not found."""
    try:
        return UUID(raw)
    except ValueError as exc:
        raise NotFound() from exc


async def is_visible(tx: Transaction, viewer: Viewer, record: Record) -> bool:
    if isinstance(record, AssistantRecord) or record.assistant_id is None:
        assistant = None
    else:
        assistant = await tx.get(AssistantRecord, record.assistant_id)
    return can_see(viewer, record, assistant)


async def get_visible(tx: Transaction, viewer: Viewer, record_type: type[R], record_id: UUID) -> R:
    """The record if it exists and is the viewer's to see; otherwise ``NotFound``."""
    if tx.organisation_id != viewer.organisation_id:
        raise NotFound()
    record = await tx.get(record_type, record_id)
    if record is None or not await is_visible(tx, viewer, record):
        raise NotFound()
    return record


async def find_visible(
    tx: Transaction, viewer: Viewer, record_type: type[R], **equals: object
) -> list[R]:
    if tx.organisation_id != viewer.organisation_id:
        return []
    return [r for r in await tx.find(record_type, **equals) if await is_visible(tx, viewer, r)]
