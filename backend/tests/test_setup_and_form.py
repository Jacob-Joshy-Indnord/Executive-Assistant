"""Story 2.1: set up and the first-time form."""

from __future__ import annotations

import asyncio
import unittest
from datetime import time
from typing import Any

from assistant.api.router import PREFIX
from assistant.errors import NotAllowedValue
from assistant.models.preferences import PreferencesForm, Weekday
from assistant.models.records import AssistantRecord, AssistantState
from assistant.rules.approval_kinds import ACTION_KINDS
from assistant.rules.lifecycle import is_running
from assistant.rules.sentences import is_plain_sentence
from assistant.rules.settings import DEFAULTS, effective_settings, form_answers
from assistant.service.input_pack import input_pack_for
from assistant.service.setup import ALREADY_HAS_ONE, settings_for
from standins.sample import (
    BACKEND_ENGINEER,
    LEGAL_LEAD,
    OPERATIONS_DIRECTOR,
    ORGANISATION_ID,
    OUTSIDER,
)
from tests.world import Bare, bare, viewer

MANAGER = OPERATIONS_DIRECTOR.id
FULL_FORM: dict[str, Any] = {
    "working_time": {
        "days": ["tuesday", "monday", "wednesday", "thursday"],
        "starts": "08:30",
        "ends": "17:00",
        "time_zone": "Europe/London",
    },
    "tone": {"tone": "Warm and brief", "sign_off": "Best wishes"},
    "priorities": {"priorities": ["CRM rollout on time"], "key_people": ["Legal lead"]},
    "follow_ups": {"follow_ups": 2, "answer_within_working_days": 2, "late_after_working_days": 1},
    "report": {"frequency": "every_two_weeks", "day": "friday", "time": "16:00"},
}


class SetUpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.b: Bare = bare()
        self.assistant = asyncio.run(self._set_up(MANAGER, "flow-ea-1"))

    def tearDown(self) -> None:
        self.b.client.close()

    async def _set_up(self, manager_id: str, flow_id: str) -> AssistantRecord:
        return await self.b.module.flow.set_up(
            ORGANISATION_ID, manager_id, "Executive Assistant", flow_id
        )

    def _page(self) -> dict[str, Any]:
        response = self.b.call("GET", f"{PREFIX}/assistant", MANAGER)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()  # type: ignore[no-any-return]

    def test_new_assistant_offers_the_form_with_every_part_optional(self) -> None:
        """Story 2.1 - new assistant offers the form."""
        self.assertEqual(self.assistant.state, AssistantState.NEW)
        opened = self.b.call(
            "POST", f"{PREFIX}/assistant/opened", MANAGER, {"time_zone": "Europe/Paris"}
        )
        self.assertEqual(opened.status_code, 200)
        self.assertTrue(opened.json()["form_offered"])
        self.assertFalse(opened.json()["running"])
        form = self.b.call("GET", f"{PREFIX}/preferences", MANAGER).json()
        self.assertTrue(form["form_offered"])
        self.assertEqual(
            form["form"],
            {"working_time": None, "tone": None, "priorities": None, "follow_ups": None,
             "report": None},
        )  # fmt: skip
        # every part is optional: an empty form is a valid answer
        self.assertEqual(self.b.call("PUT", f"{PREFIX}/preferences", MANAGER, {}).status_code, 200)

    def test_save_activates(self) -> None:
        """Story 2.1 - save or skip activates (save)."""
        self.assertFalse(is_running(self.assistant))  # BR-14: nothing happens while new
        saved = self.b.call("PUT", f"{PREFIX}/preferences", MANAGER, FULL_FORM)
        self.assertEqual(saved.status_code, 200, saved.text)
        page = self._page()
        self.assertEqual(page["state"], "active")
        self.assertFalse(page["form_offered"])
        self.assertTrue(page["running"])

    def test_skip_activates_and_applies_defaults(self) -> None:
        """Story 2.1 - save or skip activates (skip); skipping applies defaults."""
        self.b.call("POST", f"{PREFIX}/assistant/opened", MANAGER, {"time_zone": "Europe/Paris"})
        skipped = self.b.call("POST", f"{PREFIX}/preferences/skip", MANAGER)
        self.assertEqual(skipped.status_code, 200)
        self.assertEqual(self._page()["state"], "active")
        body = skipped.json()
        self.assertTrue(body["skipped"])
        self.assertEqual(
            body["settings"],
            {
                "working_days": ["monday", "tuesday", "wednesday", "thursday", "friday"],
                "starts": "09:00",
                "ends": "18:00",
                "time_zone": "Europe/Paris",  # the browser's, at first open
                "follow_ups": None,
                "answer_within_working_days": 1,
                "late_after_working_days": 0,
                "notices_per_owner_per_day": 3,
                "report_frequency": "weekly",
                "report_day": "monday",
                "report_time": "08:00",
                "ask_owners_working_days_before": 2,
            },
        )
        # the form stays available in Settings, and saving it later applies
        again = self.b.call("GET", f"{PREFIX}/preferences", MANAGER)
        self.assertEqual(again.status_code, 200)
        later = self.b.call("PUT", f"{PREFIX}/preferences", MANAGER, FULL_FORM).json()
        self.assertEqual(later["settings"]["time_zone"], "Europe/London")
        self.assertFalse(later["skipped"])

    def test_first_open_keeps_the_first_browser_time_zone(self) -> None:
        self.b.call("POST", f"{PREFIX}/assistant/opened", MANAGER, {"time_zone": "Europe/Paris"})
        self.b.call("POST", f"{PREFIX}/assistant/opened", MANAGER, {"time_zone": "Asia/Tokyo"})
        prefs = self.b.call("GET", f"{PREFIX}/preferences", MANAGER).json()
        self.assertEqual(prefs["settings"]["time_zone"], "Europe/Paris")

    def test_nonsense_answers_are_refused_with_one_sentence(self) -> None:
        cases = {
            "starts after ends": {"working_time": {"starts": "18:00", "ends": "09:00"}},
            "unknown zone": {"working_time": {"time_zone": "Nowhere/Land"}},
            "too many follow-ups": {"follow_ups": {"follow_ups": 6}},
            "answer time": {"follow_ups": {"answer_within_working_days": 0}},
            "unknown part": {"mood": "cheerful"},
            "same day twice": {"working_time": {"days": ["monday", "monday"]}},
        }
        for label, form in cases.items():
            with self.subTest(label):
                response = self.b.call("PUT", f"{PREFIX}/preferences", MANAGER, form)
                self.assertEqual(response.status_code, 422, response.text)
                detail = response.json()["detail"]
                self.assertEqual(detail["code"], "not_allowed")
                self.assertTrue(is_plain_sentence(detail["message"]), detail["message"])
        self.assertEqual(self._page()["state"], "new")  # nothing was saved
        bad_zone = self.b.call("POST", f"{PREFIX}/assistant/opened", MANAGER, {"time_zone": "X"})
        self.assertEqual(bad_zone.status_code, 422)

    def test_one_assistant_per_manager(self) -> None:
        """Story 2.1 - one assistant per manager (set up again)."""
        with self.assertRaises(NotAllowedValue) as caught:
            asyncio.run(self._set_up(MANAGER, "flow-ea-2"))
        self.assertEqual(caught.exception.message, ALREADY_HAS_ONE)
        self.assertTrue(is_plain_sentence(caught.exception.message))
        # the same call again is not a second assistant
        self.assertEqual(asyncio.run(self._set_up(MANAGER, "flow-ea-1")).id, self.assistant.id)
        # one Flow assistant cannot be set up for a second manager
        with self.assertRaises(NotAllowedValue):
            asyncio.run(self._set_up(LEGAL_LEAD.id, "flow-ea-1"))

    def test_only_people_of_the_organisation_get_an_assistant(self) -> None:
        for person in (BACKEND_ENGINEER.id, OUTSIDER.id, "nobody"):
            with self.subTest(person), self.assertRaises(NotAllowedValue):
                asyncio.run(self._set_up(person, f"flow-{person}"))

    def test_set_up_is_recorded(self) -> None:
        names = [r.name for r in self.b.standins.records.written]
        starting_levels = ["assistant.approval_level_set"] * len(ACTION_KINDS)
        self.assertEqual(names, ["assistant.assistant_set_up", *starting_levels])
        self.b.call("POST", f"{PREFIX}/preferences/skip", MANAGER)
        names = [r.name for r in self.b.standins.records.written]
        self.assertEqual(
            names[1 + len(ACTION_KINDS) :],
            [
                "assistant.preferences_created",
                "assistant.form_skipped",
                "assistant.assistant_activated",
            ],
        )


class SavedPreferencesAreUsedTest(unittest.IsolatedAsyncioTestCase):
    """Story 2.1 - saved preferences are used by schedules and in every input pack."""

    async def asyncSetUp(self) -> None:
        self.b = bare()
        self.assistant = await self.b.module.flow.set_up(
            ORGANISATION_ID, MANAGER, "Executive Assistant", "flow-ea-1"
        )
        self.setup = self.b.module.services.setup

    async def asyncTearDown(self) -> None:
        self.b.client.close()

    async def _settings_and_pack(self) -> tuple[Any, Any]:
        async with self.b.store.transaction(ORGANISATION_ID) as tx:
            return await settings_for(tx, self.assistant), await input_pack_for(tx, self.assistant)

    async def test_schedules_and_input_packs_use_the_saved_form(self) -> None:
        await self.setup.save(viewer(MANAGER), PreferencesForm.model_validate(FULL_FORM))
        settings, pack = await self._settings_and_pack()
        self.assertEqual(
            settings.working_days,
            (Weekday.MONDAY, Weekday.TUESDAY, Weekday.WEDNESDAY, Weekday.THURSDAY),
        )
        self.assertEqual((settings.starts, settings.ends), (time(8, 30), time(17, 0)))
        self.assertEqual(settings.time_zone, "Europe/London")
        self.assertEqual(settings.follow_ups, 2)
        self.assertEqual(settings.report_frequency, "every_two_weeks")
        self.assertEqual(settings.report_day, "friday")
        self.assertEqual(settings.notices_per_owner_per_day, 3)  # not answered: the default
        self.assertEqual(pack.preferences["sign_off"], "Best wishes")
        self.assertEqual(pack.preferences["working_days"], "Monday; Tuesday; Wednesday; Thursday")
        self.assertEqual(pack.preferences["report_frequency"], "every_two_weeks")
        self.assertNotIn("notices_per_owner_per_day", pack.preferences)

        # every later pack has the answers; a later save replaces them
        _, again = await self._settings_and_pack()
        self.assertEqual(again.preferences, pack.preferences)
        await self.setup.save(
            viewer(MANAGER), PreferencesForm.model_validate({"tone": {"sign_off": "Thanks"}})
        )
        settings, latest = await self._settings_and_pack()
        self.assertEqual(latest.preferences, {"sign_off": "Thanks"})
        self.assertEqual(settings.working_days, DEFAULTS.working_days)

    async def test_skipped_form_gives_defaults_and_no_answers(self) -> None:
        await self.setup.skip(viewer(MANAGER))
        settings, pack = await self._settings_and_pack()
        self.assertEqual(settings, DEFAULTS)
        self.assertEqual(pack.preferences, {})

    async def test_before_any_answer_the_defaults_apply(self) -> None:
        settings, pack = await self._settings_and_pack()
        self.assertEqual(settings, DEFAULTS)
        self.assertEqual(pack.preferences, {})


class SettingsRulesTest(unittest.TestCase):
    def test_one_end_of_the_day_against_the_others_default(self) -> None:
        form = PreferencesForm.model_validate({"working_time": {"starts": "19:00"}})
        settings = effective_settings(form)
        self.assertEqual((settings.starts, settings.ends), (DEFAULTS.starts, DEFAULTS.ends))

    def test_answers_leave_out_what_was_not_answered(self) -> None:
        self.assertEqual(form_answers(None), {})
        self.assertEqual(form_answers(PreferencesForm()), {})
        answers = form_answers(PreferencesForm.model_validate(FULL_FORM))
        self.assertEqual(answers["working_day_starts"], "08:30")
        self.assertEqual(answers["key_people"], "Legal lead")


if __name__ == "__main__":
    unittest.main()
