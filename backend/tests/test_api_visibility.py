"""Story 1.3: visibility (BR-8) on every API address.

The sweep enumerates every registered route under ``/api/assistant`` and
calls it as the manager, another manager, an owner and an administrator,
each call in a freshly seeded world so that one persona's change cannot
affect the next. It fails on any address without an expectation in
``EXPECTATIONS``.
"""

from __future__ import annotations

import unittest
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.routing import BaseRoute, Mount

from assistant.api.router import PREFIX
from assistant.module import create_module
from standins.sample import OUTSIDER
from tests.world import PERSONAS, UNSHOWN_WORDING, Seeded, World, build_world_sync

NOT_FOUND_BODY = {"detail": {"code": "not_found", "message": "Not found."}}


@dataclass(frozen=True)
class Expectation:
    """What each persona gets from one address, and how to fill its path."""

    path_params: Callable[[Seeded], dict[str, str]]
    status: dict[str, int]
    returns_id: Callable[[Seeded, str], str | None] = lambda seeded, persona: None
    body: Any = None
    checks: list[Callable[[World, str, Any], None]] = field(default_factory=list)
    """Extra checks on a world after the call: (world, persona, response)."""


def _no_params(seeded: Seeded) -> dict[str, str]:
    return {}


MANAGER_ONLY = {"manager": 200, "other_manager": 404, "owner": 404, "administrator": 404}
OWNER_OR_MANAGER = {"manager": 200, "other_manager": 404, "owner": 200, "administrator": 404}
ADMINISTRATOR_ONLY = {"manager": 404, "other_manager": 404, "owner": 404, "administrator": 200}
EVERYONE = {"manager": 200, "other_manager": 200, "owner": 200, "administrator": 200}
"""Addresses that show nothing private: the colours and their meanings."""
OWN_ASSISTANT = {"manager": 200, "other_manager": 200, "owner": 404, "administrator": 404}
"""Addresses about "my assistant": another manager reaches only their own."""

SAMPLE_FORM = {
    "working_time": {"days": ["monday", "tuesday"], "time_zone": "Europe/London"},
    "tone": {"sign_off": "Best wishes"},
}


def _managers_preferences_untouched_by_others(world: World, persona: str, response: Any) -> None:
    if persona == "manager":
        return
    page = world.call("GET", f"{PREFIX}/preferences", PERSONAS["manager"]).json()
    assert page["saved"] is False and page["form"]["tone"] is None, page


def _only_the_managers_pause_reaches_their_assistant(
    world: World, persona: str, response: Any
) -> None:
    page = world.call("GET", f"{PREFIX}/assistant", PERSONAS["manager"]).json()
    expected = "paused" if persona == "manager" else "active"
    assert page["state"] == expected, (persona, page["state"])


def _meanings_written_only_by_administrators(world: World, persona: str, response: Any) -> None:
    colours = world.call("GET", f"{PREFIX}/colours", PERSONAS["owner"]).json()
    green = next(c for c in colours if c["colour"] == "green")
    expected = "On plan" if persona == "administrator" else None
    assert green["meaning"] == expected, (persona, green)


def _managers_levels_untouched_by_others(world: World, persona: str, response: Any) -> None:
    levels = world.call("GET", f"{PREFIX}/approval-levels", PERSONAS["manager"]).json()
    message = next(level for level in levels if level["kind"] == "send_message")
    expected = "without_approval" if persona == "manager" else "with_approval"
    assert message["level"] == expected, (persona, message)

EXPECTATIONS: dict[tuple[str, str], Expectation] = {
    ("GET", "/api/assistant/assistant"): Expectation(
        _no_params,
        OWN_ASSISTANT,
        returns_id=lambda s, persona: {
            "manager": str(s.assistant.id),
            "other_manager": str(s.other_assistant.id),
        }.get(persona),
    ),
    ("POST", "/api/assistant/assistant/opened"): Expectation(
        _no_params, OWN_ASSISTANT, body={"time_zone": "Europe/London"}
    ),
    ("POST", "/api/assistant/assistant/pause"): Expectation(
        _no_params, OWN_ASSISTANT, checks=[_only_the_managers_pause_reaches_their_assistant]
    ),
    ("POST", "/api/assistant/assistant/resume"): Expectation(_no_params, OWN_ASSISTANT),
    ("GET", "/api/assistant/preferences"): Expectation(_no_params, OWN_ASSISTANT),
    ("PUT", "/api/assistant/preferences"): Expectation(
        _no_params,
        OWN_ASSISTANT,
        body=SAMPLE_FORM,
        checks=[_managers_preferences_untouched_by_others],
    ),
    ("POST", "/api/assistant/preferences/skip"): Expectation(_no_params, OWN_ASSISTANT),
    ("GET", "/api/assistant/approval-levels"): Expectation(_no_params, OWN_ASSISTANT),
    ("PUT", "/api/assistant/approval-levels/{kind}"): Expectation(
        lambda s: {"kind": "send_message"},
        OWN_ASSISTANT,
        body={"level": "without_approval", "limits": {"per_day": 2}},
        checks=[_managers_levels_untouched_by_others],
    ),
    ("GET", "/api/assistant/assistants/{assistant_id}"): Expectation(
        lambda s: {"assistant_id": str(s.assistant.id)}, MANAGER_ONLY
    ),
    ("GET", "/api/assistant/work/{work_id}"): Expectation(
        lambda s: {"work_id": str(s.work.id)}, MANAGER_ONLY
    ),
    ("GET", "/api/assistant/action-items/{item_id}"): Expectation(
        lambda s: {"item_id": str(s.item.id)}, OWNER_OR_MANAGER
    ),
    ("GET", "/api/assistant/person-requests/{request_id}"): Expectation(
        lambda s: {"request_id": str(s.person_request.id)}, OWNER_OR_MANAGER
    ),
    ("GET", "/api/assistant/statements/{statement_id}"): Expectation(
        lambda s: {"statement_id": str(s.statement.id)}, OWNER_OR_MANAGER
    ),
    ("GET", "/api/assistant/organisation/settings"): Expectation(_no_params, ADMINISTRATOR_ONLY),
    ("PUT", "/api/assistant/organisation/settings"): Expectation(
        _no_params,
        ADMINISTRATOR_ONLY,
        body={"colour_meanings": {"green": "On plan"}},
        checks=[_meanings_written_only_by_administrators],
    ),
    ("GET", "/api/assistant/colours"): Expectation(_no_params, EVERYONE),
}


def _walk(routes: Iterable[BaseRoute], prefix: str = "") -> Iterator[tuple[str, str]]:
    for route in routes:
        if isinstance(route, APIRoute):
            for method in route.methods - {"HEAD", "OPTIONS"}:
                yield method, prefix + route.path
            continue
        # Included routers: FastAPI >= 0.16x keeps them as one wrapper route.
        included = getattr(route, "original_router", None)
        if included is not None:
            context = getattr(route, "include_context", None)
            yield from _walk(included.routes, prefix + (getattr(context, "prefix", "") or ""))
        elif isinstance(route, Mount):
            yield from _walk(route.routes, prefix + route.path)


def api_addresses(app: FastAPI) -> set[tuple[str, str]]:
    """Every (method, path) the app answers under the module's prefix."""
    found = set(_walk(app.routes))
    if not found:
        raise AssertionError("no routes found: the route walk does not understand this FastAPI")
    return {(method, path) for method, path in found if path.startswith(PREFIX)}


def addresses_without_expectation(app: FastAPI) -> set[tuple[str, str]]:
    return api_addresses(app) - set(EXPECTATIONS)


class ApiVisibilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.world = build_world_sync()
        self.seeded = self.world.seeded
        self.app = self.world.app

    def tearDown(self) -> None:
        self.world.close()

    def _call(self, method: str, path: str, person_id: str | None, json: Any = None) -> Any:
        return self.world.call(method, path, person_id, json)

    def test_visibility_test_covers_every_address(self) -> None:
        """Story 1.3 - it calls every address as all four personas."""
        missing = addresses_without_expectation(self.app)
        self.assertEqual(missing, set(), f"addresses without an expectation: {sorted(missing)}")
        stale = set(EXPECTATIONS) - api_addresses(self.app)
        self.assertEqual(stale, set(), f"expectations for addresses that do not exist: {stale}")

        for (method, template), expectation in EXPECTATIONS.items():
            self.assertEqual(set(expectation.status), set(PERSONAS))
            for persona, person_id in PERSONAS.items():
                with self.subTest(address=f"{method} {template}", persona=persona):
                    world = build_world_sync()
                    try:
                        path = template.format(**expectation.path_params(world.seeded))
                        response = world.call(method, path, person_id, expectation.body)
                        self.assertEqual(
                            response.status_code, expectation.status[persona], response.text
                        )
                        if response.status_code == 404:
                            self.assertEqual(response.json(), NOT_FOUND_BODY)
                        expected_id = expectation.returns_id(world.seeded, persona)
                        if expected_id is not None:
                            self.assertEqual(response.json()["id"], expected_id)
                        for check in expectation.checks:
                            check(world, persona, response)
                    finally:
                        world.close()

    def test_sweep_fails_on_an_address_it_does_not_know(self) -> None:
        self.app.add_api_route(f"{PREFIX}/surprise", lambda: {}, methods=["POST"])
        self.assertEqual(
            addresses_without_expectation(self.app), {("POST", f"{PREFIX}/surprise")}
        )

    def test_sweep_fails_on_a_new_module_address_without_expectation(self) -> None:
        module = create_module(self.world.standins.boundary, self.world.store)
        module.router.add_api_route("/items/{item_id}", lambda item_id: {}, methods=["DELETE"])
        app = FastAPI()
        app.include_router(module.router)
        self.assertEqual(
            addresses_without_expectation(app), {("DELETE", f"{PREFIX}/items/{{item_id}}")}
        )

    def test_not_theirs_looks_exactly_like_not_found(self) -> None:
        """Story 1.3 - not-theirs looks like not-found (404)."""
        for (method, template), expectation in EXPECTATIONS.items():
            params = expectation.path_params(self.seeded)
            if not params:
                continue
            real = template.format(**params)
            missing = template.format(**{k: str(uuid4()) for k in params})
            malformed = template.format(**{k: "not-an-id" for k in params})
            for persona, status in expectation.status.items():
                if status != 404:
                    continue
                with self.subTest(address=template, persona=persona):
                    body = expectation.body
                    not_theirs = self._call(method, real, PERSONAS[persona], body)
                    absent = self._call(method, missing, PERSONAS[persona], body)
                    bad = self._call(method, malformed, PERSONAS[persona], body)
                    for other in (absent, bad):
                        self.assertEqual(not_theirs.status_code, other.status_code)
                        self.assertEqual(not_theirs.content, other.content)
                        self.assertEqual(
                            dict(not_theirs.headers), dict(other.headers)
                        )

    def test_someone_from_another_organisation_finds_nothing(self) -> None:
        path = f"{PREFIX}/action-items/{self.seeded.item.id}"
        response = self._call("GET", path, OUTSIDER.id)
        self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND_BODY))

    def test_unconfirmed_wording_is_never_shown(self) -> None:
        path = f"{PREFIX}/statements/{self.seeded.statement.id}"
        for persona in ("manager", "owner"):
            response = self._call("GET", path, PERSONAS[persona])
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(UNSHOWN_WORDING, response.text)
            self.assertNotIn("unconfirmed_wording", response.json())

    def test_no_person_named_is_not_signed_in(self) -> None:
        response = self._call("GET", f"{PREFIX}/assistant", None)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"]["code"], "not_signed_in")

    def test_people_unavailable_is_one_plain_sentence(self) -> None:
        async def broken(headers: Any) -> None:
            raise ConnectionError("dns failure")

        self.world.standins.people.person_asking = broken  # type: ignore[method-assign]
        response = self._call("GET", f"{PREFIX}/assistant", PERSONAS["manager"])
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            {
                "code": "unavailable",
                "message": "The people directory could not be reached, so nothing was changed.",
            },
        )


if __name__ == "__main__":
    unittest.main()
