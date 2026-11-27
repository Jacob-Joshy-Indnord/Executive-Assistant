"""Story 1.3: the BR-8 rules and the API's error answers."""

from __future__ import annotations

import unittest
from collections.abc import Callable
from datetime import date

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from assistant.api.errors import ModuleErrorRoute
from assistant.errors import (
    AlreadyChanged,
    BoundaryUnavailable,
    ModuleError,
    NotAllowedValue,
    NotFound,
    NotSaved,
)
from assistant.models.records import (
    ActionItem,
    AssistantRecord,
    Report,
    StatementDraft,
    StatementWording,
)
from assistant.rules.visibility import VISIBILITY, Viewer, can_see, unruled_kinds
from tests.fakes import FIXED_NOW

ORG = "org"
MANAGER = Viewer("manager", ORG)
OTHER_MANAGER = Viewer("other-manager", ORG)
OWNER = Viewer("owner", ORG)
ADMIN = Viewer("admin", ORG, is_administrator=True)


def _assistant(**changes: object) -> AssistantRecord:
    base: dict[str, object] = {
        "organisation_id": ORG,
        "created_at": FIXED_NOW,
        "manager_id": "manager",
        "display_name": "Executive Assistant",
        "flow_id": "f",
    }
    return AssistantRecord(**{**base, **changes})  # type: ignore[arg-type]


class VisibilityRulesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assistant = _assistant()
        mine = {"organisation_id": ORG, "assistant_id": self.assistant.id, "created_at": FIXED_NOW}
        self.item = ActionItem(
            **mine, text="t", owner_id="owner", due_date=date(2026, 11, 27), origin="manager"
        )
        self.report = Report(**mine)
        self.draft = StatementDraft(
            **mine,
            project_id="p",
            owner_id="owner",
            wording=StatementWording(reason="r"),
            source="s",
            author_id="assistant",
        )

    def _who_sees(self, record: object) -> set[str]:
        viewers = {"manager": MANAGER, "other": OTHER_MANAGER, "owner": OWNER, "admin": ADMIN}
        return {
            name
            for name, viewer in viewers.items()
            if can_see(viewer, record, self.assistant)  # type: ignore[arg-type]
        }

    def test_every_record_kind_has_a_rule(self) -> None:
        self.assertEqual(unruled_kinds(), set())

    def test_assistant_work_is_visible_to_its_manager_only(self) -> None:
        self.assertEqual(self._who_sees(self.report), {"manager"})
        self.assertEqual(self._who_sees(self.assistant), {"manager"})

    def test_owners_see_their_own_items(self) -> None:
        self.assertEqual(self._who_sees(self.item), {"manager", "owner"})

    def test_unconfirmed_statements_go_to_the_owner_only(self) -> None:
        self.assertEqual(self._who_sees(self.draft), {"owner"})

    def test_an_administrator_sees_nothing_more_for_an_assistant(self) -> None:
        admin_as_manager = _assistant(manager_id="admin")
        self.assertTrue(can_see(ADMIN, admin_as_manager, None))  # their own, as manager
        self.assertFalse(can_see(ADMIN, self.item, self.assistant))

    def test_a_new_manager_sees_nothing_from_before_the_hand_over(self) -> None:
        # A hand-over ends the record and starts a new one for the new manager, at the
        # same moment: what belongs to the old record is not theirs, whatever the clock says.
        ended = self.assistant.evolve(state="ended", handed_over_to="other-manager")
        fresh = _assistant(manager_id="other-manager")
        self.assertFalse(can_see(OTHER_MANAGER, self.report, ended))
        later = self.report.model_validate({**self.report.model_dump(), "assistant_id": fresh.id})
        self.assertTrue(can_see(OTHER_MANAGER, later, fresh))
        self.assertFalse(can_see(MANAGER, later, fresh))

    def test_nothing_crosses_organisations(self) -> None:
        outsider = Viewer("manager", "another-org")
        self.assertFalse(can_see(outsider, self.item, self.assistant))
        self.assertFalse(can_see(Viewer("admin", "another-org", True), self.item, None))

    def test_unknown_kind_has_no_rule(self) -> None:
        self.assertNotIn("", VISIBILITY)


class ApiErrorAnswersTest(unittest.TestCase):
    """The API answers errors with detail.code and detail.message."""

    def test_each_error_has_its_status_code_and_message(self) -> None:
        router = APIRouter(route_class=ModuleErrorRoute)
        errors: dict[str, ModuleError] = {
            "not-found": NotFound(),
            "conflict": AlreadyChanged(),
            "not-allowed": NotAllowedValue("A daily number must be at least 1."),
            "not-saved": NotSaved(),
            "unavailable": BoundaryUnavailable(),
        }

        def raiser_for(error: ModuleError) -> Callable[[], None]:
            def raiser() -> None:
                raise error

            return raiser

        for name, error in errors.items():
            router.add_api_route(f"/{name}", raiser_for(error), methods=["POST"])
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        expected = {
            "not-found": (404, "not_found", "Not found."),
            "conflict": (409, "already_changed", "This has already changed."),
            "not-allowed": (422, "not_allowed", "A daily number must be at least 1."),
            "not-saved": (503, "not_saved", "Not saved. Try again."),
            "unavailable": (
                503,
                "unavailable",
                "Flow could not be reached, so nothing was changed.",
            ),
        }
        for name, (status, code, message) in expected.items():
            with self.subTest(name):
                response = client.post(f"/{name}")
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json(), {"detail": {"code": code, "message": message}})


if __name__ == "__main__":
    unittest.main()
