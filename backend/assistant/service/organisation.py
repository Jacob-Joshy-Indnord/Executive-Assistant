"""What the colours mean (Story 2.3), and whether meeting notes are allowed (part 2).

Only an administrator opens or changes the organisation settings. Owners see
each colour with its meaning, if one has been written, when they give or
confirm a status; a colour without a meaning can still be chosen. Meeting
notes are not allowed until an administrator allows them; the choice applies
to the next transcript shared, and notes that already exist stay.
"""

from __future__ import annotations

from typing import Any, get_args
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from assistant.boundary.interfaces import Boundary
from assistant.errors import NotFound
from assistant.models.records import Colour, OrganisationSettings
from assistant.rules.visibility import Viewer
from assistant.service.actions import Action, perform
from assistant.store.interface import Store, Transaction

COLOURS: tuple[Colour, ...] = get_args(Colour)
MEANING_LIMIT = 300


class MeaningsChange(BaseModel):
    """The meaning of each colour; leaving one out, or giving it empty, clears it."""

    model_config = ConfigDict(extra="forbid")

    green: str | None = Field(default=None, max_length=MEANING_LIMIT)
    amber: str | None = Field(default=None, max_length=MEANING_LIMIT)
    red: str | None = Field(default=None, max_length=MEANING_LIMIT)

    def meanings(self) -> dict[Colour, str]:
        given: dict[Colour, str | None] = {
            "green": self.green,
            "amber": self.amber,
            "red": self.red,
        }
        return {colour: text.strip() for colour, text in given.items() if text and text.strip()}


def settings_id(organisation_id: str) -> UUID:
    """One settings record per organisation, so two first writes cannot make two."""
    return uuid5(NAMESPACE_URL, f"https://ea-001.invalid/organisation-settings/{organisation_id}")


async def settings_of(
    reader: Transaction | Action, organisation_id: str
) -> OrganisationSettings | None:
    return await reader.get(OrganisationSettings, settings_id(organisation_id))


async def meeting_notes_allowed(reader: Transaction | Action, organisation_id: str) -> bool:
    """Whether transcripts may be used for notes in the organisation (default No)."""
    settings = await settings_of(reader, organisation_id)
    return bool(settings and settings.meeting_notes_allowed)


async def colour_choices(
    reader: Transaction | Action, organisation_id: str
) -> list[dict[str, Any]]:
    """Every colour an owner can choose, with its meaning beside it (``None`` when unwritten)."""
    settings = await settings_of(reader, organisation_id)
    meanings = settings.colour_meanings if settings else {}
    return [{"colour": colour, "meaning": meanings.get(colour)} for colour in COLOURS]


def settings_view(organisation_id: str, settings: OrganisationSettings | None) -> dict[str, Any]:
    meanings = settings.colour_meanings if settings else {}
    return {
        "organisation_id": organisation_id,
        "colour_meanings": {colour: meanings.get(colour) for colour in COLOURS},
        "meeting_notes_allowed": bool(settings and settings.meeting_notes_allowed),
    }


class OrganisationService:
    def __init__(self, boundary: Boundary, store: Store) -> None:
        self.boundary = boundary
        self.store = store

    @staticmethod
    def _administrators_only(viewer: Viewer) -> None:
        if not viewer.is_administrator:
            raise NotFound()

    async def settings(self, viewer: Viewer) -> dict[str, Any]:
        self._administrators_only(viewer)
        async with self.store.transaction(viewer.organisation_id) as tx:
            current = await settings_of(tx, viewer.organisation_id)
        return settings_view(viewer.organisation_id, current)

    async def change_meanings(self, viewer: Viewer, change: MeaningsChange) -> dict[str, Any]:
        return await self.change(viewer, meanings=change)

    async def change(
        self,
        viewer: Viewer,
        *,
        meanings: MeaningsChange | None = None,
        meeting_notes_allowed: bool | None = None,
    ) -> dict[str, Any]:
        """An administrator changes the colour meanings, whether meeting notes are allowed,
        or both. What is left out stays as it was."""
        self._administrators_only(viewer)
        org = viewer.organisation_id
        changes: dict[str, Any] = {}
        details: dict[str, Any] = {}
        name = "assistant.organisation_settings_changed"
        if meanings is not None:
            changes["colour_meanings"] = meanings.meanings()
            details["colours_with_meaning"] = sorted(changes["colour_meanings"])
            name = "assistant.colour_meanings_changed"
        if meeting_notes_allowed is not None:
            changes["meeting_notes_allowed"] = meeting_notes_allowed
            details["meeting_notes_allowed"] = meeting_notes_allowed
            if meanings is None:
                name = "assistant.meeting_notes_setting_changed"

        async def work(action: Action) -> OrganisationSettings | None:
            current = await settings_of(action, org)
            if current is None:
                return await action.add(
                    name,
                    OrganisationSettings(
                        id=settings_id(org), organisation_id=org, created_at=action.now, **changes
                    ),
                    details=details,
                )
            if not changes:
                return current
            return await action.change(name, current, current.evolve(**changes), details=details)

        saved = await perform(self.boundary, self.store, org, viewer.person_id, work)
        return settings_view(org, saved)

    async def colours(self, viewer: Viewer) -> list[dict[str, Any]]:
        async with self.store.transaction(viewer.organisation_id) as tx:
            return await colour_choices(tx, viewer.organisation_id)
