"""Story 2.3: what the colours mean."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any

from assistant.api.router import PREFIX
from assistant.models.records import ConfirmedStatement, OrganisationSettings, StatementWording
from assistant.rules.sentences import is_plain_sentence
from assistant.service.organisation import MeaningsChange, colour_choices
from standins.sample import (
    BACKEND_ENGINEER,
    DEFAULT_START,
    LEGAL_LEAD,
    OPERATIONS_DIRECTOR,
    ORGANISATION_ADMINISTRATOR,
    ORGANISATION_ID,
    SALES_DIRECTOR,
)
from tests.world import World, build_world_sync, viewer

ADMIN = ORGANISATION_ADMINISTRATOR.id
OWNER = LEGAL_LEAD.id
SETTINGS = f"{PREFIX}/organisation/settings"


class ColourMeaningsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.w: World = build_world_sync()

    def tearDown(self) -> None:
        self.w.close()

    def _write(self, meanings: dict[str, Any], person_id: str = ADMIN) -> Any:
        return self.w.call("PUT", SETTINGS, person_id, {"colour_meanings": meanings})

    def _colours(self, person_id: str = OWNER) -> dict[str, str | None]:
        response = self.w.call("GET", f"{PREFIX}/colours", person_id)
        self.assertEqual(response.status_code, 200)
        return {c["colour"]: c["meaning"] for c in response.json()}

    def test_meanings_are_shown_to_owners_beside_the_colour(self) -> None:
        """Story 2.3 - meanings shown to owners."""
        response = self._write({"green": "On plan", "red": "Needs a decision from you"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["colour_meanings"],
            {"green": "On plan", "amber": None, "red": "Needs a decision from you"},
        )
        expected = {"green": "On plan", "amber": None, "red": "Needs a decision from you"}
        self.assertEqual(self._colours(OWNER), expected)

        async def for_the_status_form() -> list[dict[str, Any]]:
            async with self.w.store.transaction(ORGANISATION_ID) as tx:
                return await colour_choices(tx, ORGANISATION_ID)

        choices = asyncio.run(for_the_status_form())
        self.assertEqual([c["colour"] for c in choices], ["green", "amber", "red"])
        self.assertEqual(choices[0]["meaning"], "On plan")

    def test_the_change_is_recorded_without_the_wording(self) -> None:
        self._write({"green": "On plan"})
        record = self.w.standins.records.written[-1]
        self.assertEqual(record.name, "assistant.colour_meanings_changed")
        self.assertEqual(record.who, ADMIN)
        self.assertEqual(record.details, {"colours_with_meaning": ["green"]})
        self.assertNotIn("On plan", record.model_dump_json())

    def test_not_saved_when_it_cannot_be_recorded(self) -> None:
        self.w.standins.records.failing = True
        self.assertEqual(self._write({"green": "On plan"}).status_code, 503)
        self.assertEqual(self._colours()["green"], None)

    def test_colours_can_be_chosen_without_a_meaning(self) -> None:
        """Story 2.3 - colours selectable without meaning."""
        self.assertEqual(self._colours(), {"green": None, "amber": None, "red": None})
        statement = ConfirmedStatement(
            organisation_id=ORGANISATION_ID,
            created_at=DEFAULT_START,
            project_id="p",
            owner_id=OWNER,
            colour="amber",
            wording=StatementWording(reason="Two contracts are open."),
            source="Legal lead",
            author_id=OWNER,
            mark="confirmed",
        )
        self.assertEqual(statement.colour, "amber")
        # writing nothing, or only spaces, clears a meaning again
        self._write({"green": "On plan"})
        self._write({"green": "   "})
        self.assertEqual(self._colours(), {"green": None, "amber": None, "red": None})

    def test_non_administrators_are_refused(self) -> None:
        """Story 2.3 - anyone other than an administrator is refused."""
        self._write({"amber": "At risk"})
        for person in (
            OPERATIONS_DIRECTOR.id,
            SALES_DIRECTOR.id,
            OWNER,
            BACKEND_ENGINEER.id,
        ):
            with self.subTest(person):
                self.assertEqual(self.w.call("GET", SETTINGS, person).status_code, 404)
                self.assertEqual(self._write({"amber": "Fine"}, person).status_code, 404)
        self.assertEqual(self._colours()["amber"], "At risk")
        admin_view = self.w.call("GET", SETTINGS, ADMIN)
        self.assertEqual(admin_view.json()["colour_meanings"]["amber"], "At risk")

    def test_nonsense_is_refused_with_one_sentence(self) -> None:
        for label, meanings in {
            "too long": {"green": "x" * 301},
            "unknown colour": {"blue": "Calm"},
        }.items():
            with self.subTest(label):
                response = self._write(meanings)
                self.assertEqual(response.status_code, 422)
                self.assertTrue(is_plain_sentence(response.json()["detail"]["message"]))

    def test_one_settings_record_however_many_first_writes(self) -> None:
        admin = viewer(ADMIN, administrator=True)
        organisation = self.w.module.services.organisation

        async def two_at_once() -> int:
            await asyncio.gather(
                organisation.change_meanings(admin, MeaningsChange(green="On plan")),
                organisation.change_meanings(admin, MeaningsChange(red="Stuck")),
            )
            async with self.w.store.transaction(ORGANISATION_ID) as tx:
                return len(await tx.find(OrganisationSettings))

        self.assertEqual(asyncio.run(two_at_once()), 1)


if __name__ == "__main__":
    unittest.main()
