"""Story 7.1: the demonstration, its bar and its startup."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from assistant.api.router import PREFIX
from standins.demo import (
    BAR_PEOPLE,
    DEMO_PREFIX,
    FIRST_FLOW_ID,
    DemonstrationApp,
    address_from_env,
    create_app,
    flow_id_for,
)
from standins.people import PERSON_HEADER
from standins.sample import (
    LEGAL_LEAD,
    OPERATIONS_DIRECTOR,
    ORGANISATION_ADMINISTRATOR,
    SALES_DIRECTOR,
)
from tests.test_api_visibility import _walk

BACKEND = Path(__file__).resolve().parents[1]
OPS, LEGAL, SALES, ADMIN = (
    OPERATIONS_DIRECTOR.id,
    LEGAL_LEAD.id,
    SALES_DIRECTOR.id,
    ORGANISATION_ADMINISTRATOR.id,
)


class DemonstrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app: DemonstrationApp = create_app()
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)

    def as_person(self, method: str, path: str, person: str, json: Any = None) -> Response:
        return self.client.request(
            method, f"{PREFIX}{path}", headers={PERSON_HEADER: person}, json=json
        )

    def bar(self, method: str, path: str = "", json: Any = None) -> dict[str, Any]:
        response = self.client.request(method, f"{DEMO_PREFIX}{path}", json=json)
        self.assertEqual(response.status_code, 200, response.text)
        body: dict[str, Any] = response.json()
        return body

    # Starts with two commands, no setup ------------------------------------------------

    def test_opens_with_the_sample_organisation_and_a_new_assistant(self) -> None:
        """Story 7.1 - starts with the sample organisation and a newly set-up assistant."""
        state = self.bar("GET")
        self.assertEqual(
            [p["name"] for p in state["people"]], [p.display_name for p, _ in BAR_PEOPLE]
        )
        self.assertEqual(
            state["assistants"],
            [
                {
                    "flow_id": FIRST_FLOW_ID,
                    "manager_id": OPS,
                    "manager_name": "Operations director",
                    "state": "new",
                }
            ],
        )
        page = self.as_person("GET", "/assistant", OPS)
        self.assertEqual(page.status_code, 200)
        self.assertTrue(page.json()["form_offered"])

    def test_start_needs_no_account_key_or_connection(self) -> None:
        """Story 7.1 - the start command reads only an optional host and port."""
        self.assertEqual((address_from_env().host, address_from_env().port), ("127.0.0.1", 8000))
        source = (BACKEND / "standins" / "demo.py").read_text()
        for needed in ("API_KEY", "SECRET", "PASSWORD", "TOKEN", "DATABASE_URL"):
            self.assertNotIn(needed, source)

    # Person switching ------------------------------------------------------------------

    def test_each_person_sees_what_they_see(self) -> None:
        """Story 7.1 - choosing a person in the bar shows what that person sees."""
        self.assertEqual(self.as_person("GET", "/assistant", OPS).status_code, 200)
        for other in (SALES, LEGAL, ADMIN):
            self.assertEqual(self.as_person("GET", "/assistant", other).status_code, 404)
        self.assertEqual(self.as_person("GET", "/organisation/settings", ADMIN).status_code, 200)
        self.assertEqual(self.as_person("GET", "/organisation/settings", OPS).status_code, 404)

    def test_no_person_named_is_not_signed_in(self) -> None:
        self.assertEqual(self.client.get(f"{PREFIX}/assistant").status_code, 401)

    # Clock, faults and reset -----------------------------------------------------------

    def test_moving_the_clock(self) -> None:
        """Story 7.1 - the clock moves by an hour, a day or to the next step that is due."""
        start = self.bar("GET")["now"]
        hour = self.bar("POST", "/clock", {"move": "hour"})
        self.assertEqual(hour["now"], "2026-11-23T09:00:00+00:00")
        day = self.bar("POST", "/clock", {"move": "day"})
        self.assertEqual(day["now"], "2026-11-24T09:00:00+00:00")
        nxt = self.bar("POST", "/clock", {"move": "next"})
        self.assertGreaterEqual(nxt["now"], day["now"])
        self.assertLess(start, nxt["now"])
        self.assertIn("ran", nxt)

    def test_faults_switch_on_and_off(self) -> None:
        """Story 7.1 - each fault can be turned on, and the stand-in follows."""
        for fault in ("assistant_faults", "assistant_refusing", "approval", "notices", "records"):
            state = self.bar("PUT", "/faults", {"fault": fault, "on": True})
            self.assertTrue(state["faults"][fault])
        self.assertTrue(self.app.demo.standins.records.failing)
        # With the records failing, nothing can be saved.
        saved = self.as_person("POST", "/preferences/skip", OPS)
        self.assertEqual(saved.status_code, 503)
        state = self.bar("PUT", "/faults", {"fault": "records", "on": False})
        self.assertFalse(state["faults"]["records"])

    def test_assistant_faults_are_refused_on_the_page(self) -> None:
        """Story 7.1 - with faults on, the assistant's faulty requests are refused."""
        self.as_person("POST", "/preferences/skip", OPS)
        self.bar("PUT", "/faults", {"fault": "assistant_faults", "on": True})
        sent = self.as_person(
            "POST", "/chat", OPS, {"text": "Remind the Legal lead to finish the review by Friday"}
        )
        self.assertEqual(sent.status_code, 200, sent.text)
        work_id = sent.json()["work"]["id"]
        work = self.as_person("GET", f"/work/{work_id}", OPS).json()
        self.assertEqual(work["state"], "done")
        self.assertEqual(work["refused_count"], 4)

    def test_start_again_from_sample_data(self) -> None:
        """Story 7.1 - starting again brings back the sample data and a new assistant."""
        self.as_person("POST", "/preferences/skip", OPS)
        self.bar("POST", "/clock", {"move": "day"})
        self.bar("PUT", "/faults", {"fault": "notices", "on": True})
        state = self.bar("POST", "/reset")
        self.assertEqual(state["now"], "2026-11-23T08:00:00+00:00")
        self.assertEqual(state["assistants"][0]["state"], "new")
        self.assertFalse(any(state["faults"].values()))
        self.assertEqual(state["notices"], [])

    # Flow calls simulated --------------------------------------------------------------

    def test_set_up_hand_over_and_end_call_the_module_as_flow(self) -> None:
        """Story 7.1 - set up, hand over and end in the bar are Flow's calls."""
        state = self.bar("POST", "/set-up", {"manager_id": SALES})
        self.assertEqual(
            [(a["manager_name"], a["state"]) for a in state["assistants"]],
            [("Operations director", "new"), ("Sales director", "new")],
        )
        self.assertEqual(self.as_person("GET", "/assistant", SALES).status_code, 200)

        refused = self.client.post(f"{DEMO_PREFIX}/set-up", json={"manager_id": SALES})
        self.assertEqual(refused.status_code, 422)
        self.assertEqual(
            refused.json()["detail"]["message"],
            "This manager already has an Executive Assistant.",
        )

        state = self.bar("POST", "/end", {"flow_id": flow_id_for(SALES_DIRECTOR)})
        self.assertEqual([a["manager_name"] for a in state["assistants"]], ["Operations director"])
        self.assertEqual(self.as_person("GET", "/assistant", SALES).status_code, 404)

        state = self.bar("POST", "/hand-over", {"flow_id": FIRST_FLOW_ID, "new_manager_id": SALES})
        self.assertEqual(
            [(a["manager_name"], a["state"]) for a in state["assistants"]],
            [("Sales director", "new")],
        )
        self.assertEqual(self.as_person("GET", "/assistant", OPS).status_code, 404)
        self.assertTrue(self.as_person("GET", "/assistant", SALES).json()["form_offered"])

    def test_unknown_people_and_assistants_are_not_found(self) -> None:
        for path, body in (
            ("/set-up", {"manager_id": "nobody"}),
            ("/hand-over", {"flow_id": "nothing", "new_manager_id": SALES}),
            ("/hand-over", {"flow_id": FIRST_FLOW_ID, "new_manager_id": "nobody"}),
            ("/end", {"flow_id": "nothing"}),
        ):
            self.assertEqual(self.client.post(f"{DEMO_PREFIX}{path}", json=body).status_code, 404)

    def test_pause_everything_calls_the_module_as_flow(self) -> None:
        """Story 7.1 - pausing everything is Flow's call, and the page says so."""
        self.as_person("POST", "/preferences/skip", OPS)
        state = self.bar("POST", "/pause-all", {"paused": True})
        self.assertTrue(state["all_paused"])
        self.assertFalse(self.as_person("GET", "/assistant", OPS).json()["running"])
        state = self.bar("POST", "/pause-all", {"paused": False})
        self.assertFalse(state["all_paused"])
        self.assertTrue(self.as_person("GET", "/assistant", OPS).json()["running"])

    def test_ended_turns_are_handed_back_as_flow_would(self) -> None:
        """Story 7.1 - a message's proposed item waits for approval after the turn ends."""
        self.as_person("POST", "/preferences/skip", OPS)
        self.as_person(
            "POST", "/chat", OPS, {"text": "Remind the Legal lead to finish the review by Friday"}
        )
        requests = self.as_person("GET", "/requests", OPS).json()
        self.assertEqual(requests["count"], 1)
        self.assertFalse(self.app.demo.standins.assistant.has_finished)

    def test_work_data_changes_through_the_bar(self) -> None:
        """Story 7.1 - an assignment's state changes in the stand-in's work data."""
        assignment = self.bar("GET")["assignments"][0]
        state = self.bar(
            "POST", "/assignment", {"assignment_id": assignment["id"], "state": "approved"}
        )
        changed = next(a for a in state["assignments"] if a["id"] == assignment["id"])
        self.assertEqual(changed["state"], "approved")
        missing = self.client.post(
            f"{DEMO_PREFIX}/assignment", json={"assignment_id": "nothing", "state": "approved"}
        )
        self.assertEqual(missing.status_code, 404)

    def test_lifespan_is_answered(self) -> None:
        # Entering the client in setUp ran the startup; leaving it runs the shutdown.
        self.assertEqual(self.bar("GET")["assistants"][0]["flow_id"], FIRST_FLOW_ID)


class BarNotInModuleTest(unittest.TestCase):
    """Story 7.1 - when the module is used without the demonstration, the bar is not part of it."""

    def test_module_never_imports_the_stand_ins(self) -> None:
        for path in (BACKEND / "assistant").rglob("*.py"):
            tree = ast.parse(path.read_text(), str(path))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    self.assertFalse(name.split(".")[0] == "standins", f"{path} imports {name}")

    def test_module_router_has_no_bar_addresses(self) -> None:
        app = create_app()
        paths = [path for _, path in _walk(app.demo.app.routes)]
        self.assertTrue(paths)
        self.assertTrue(all(p.startswith(PREFIX) for p in paths))
        self.assertFalse(any(p.startswith(DEMO_PREFIX) for p in paths))


if __name__ == "__main__":
    unittest.main()
