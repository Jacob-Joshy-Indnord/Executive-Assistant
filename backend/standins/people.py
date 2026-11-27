"""People stand-in: the sample organisation. The demonstration bar names the person asking."""

from __future__ import annotations

from collections.abc import Mapping

from assistant.models.boundary import Person
from standins.sample import PEOPLE, SamplePerson

PERSON_HEADER = "x-demo-person"


class StandInPeople:
    def __init__(self, people: tuple[SamplePerson, ...] = PEOPLE) -> None:
        self._people = {p.id: p for p in people}

    @staticmethod
    def _shape(p: SamplePerson) -> Person:
        return Person(
            id=p.id, display_name=p.display_name, kind=p.kind, organisation_id=p.organisation_id
        )

    async def person_asking(self, headers: Mapping[str, str]) -> Person | None:
        person_id = next((v for k, v in headers.items() if k.lower() == PERSON_HEADER), None)
        return await self.person(person_id) if person_id else None

    async def person(self, person_id: str) -> Person | None:
        found = self._people.get(person_id)
        return self._shape(found) if found else None

    async def find_by_name(self, organisation_id: str, name: str) -> list[Person]:
        wanted = name.strip().casefold()
        return [
            self._shape(p)
            for p in self._people.values()
            if p.organisation_id == organisation_id and p.display_name.casefold() == wanted
        ]

    async def is_administrator(self, person_id: str) -> bool:
        found = self._people.get(person_id)
        return bool(found and found.administrator)
