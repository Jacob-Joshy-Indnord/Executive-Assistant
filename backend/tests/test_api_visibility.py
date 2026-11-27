"""Story 1.3: visibility (BR-8) on every API address.

The sweep enumerates every registered route under ``/api/assistant`` and
calls it as the manager, another manager, an owner and an administrator.
It fails on any address without an expectation in ``EXPECTATIONS``.
"""

from __future__ import annotations

import unittest
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import BaseRoute, Mount

from assistant.api.router import PREFIX
from assistant.models.records import (
    ActionItem,
    AssistantRecord,
    AssistantState,
    ConfirmedStatement,
    OrganisationSettings,
    PersonRequest,
    PieceOfWork,
    StatementWording,
    WorkKind,
)
from assistant.module import create_module
from assistant.store.sqlite import SqliteStore
from standins import build_standins
from standins.people import PERSON_HEADER
from standins.sample import (
    DEFAULT_START,
    LEGAL_LEAD,
    OPERATIONS_DIRECTOR,
    ORGANISATION_ADMINISTRATOR,
    ORGANISATION_ID,
    OUTSIDER,
    SALES_DIRECTOR,
)

PERSONAS = {
    "manager": OPERATIONS_DIRECTOR.id,
    "other_manager": SALES_DIRECTOR.id,
    "owner": LEGAL_LEAD.id,
    "administrator": ORGANISATION_ADMINISTRATOR.id,
}
NOT_FOUND_BODY = {"detail": {"code": "not_found", "message": "Not found."}}
UNSHOWN_WORDING = "The assistant's wording the owner did not confirm"


@dataclass
class Seeded:
    assistant: AssistantRecord
    other_assistant: AssistantRecord
    work: PieceOfWork
    item: ActionItem
    person_request: PersonRequest
    statement: ConfirmedStatement


@dataclass(frozen=True)
class Expectation:
    """What each persona gets from one address, and how to fill its path."""

    path_params: Callable[[Seeded], dict[str, str]]
    status: dict[str, int]
    returns_id: Callable[[Seeded, str], str | None] = lambda seeded, persona: None


def _no_params(seeded: Seeded) -> dict[str, str]:
    return {}


MANAGER_ONLY = {"manager": 200, "other_manager": 404, "owner": 404, "administrator": 404}
OWNER_OR_MANAGER = {"manager": 200, "other_manager": 404, "owner": 200, "administrator": 404}
ADMINISTRATOR_ONLY = {"manager": 404, "other_manager": 404, "owner": 404, "administrator": 200}

EXPECTATIONS: dict[tuple[str, str], Expectation] = {
    ("GET", "/api/assistant/assistant"): Expectation(
        _no_params,
        {"manager": 200, "other_manager": 200, "owner": 404, "administrator": 404},
        returns_id=lambda s, persona: {
            "manager": str(s.assistant.id),
            "other_manager": str(s.other_assistant.id),
        }.get(persona),
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


async def seed(store: SqliteStore) -> Seeded:
    t = DEFAULT_START
    org = ORGANISATION_ID
    async with store.transaction(org) as tx:
        assistant = await tx.add(
            AssistantRecord(
                organisation_id=org,
                created_at=t,
                manager_id=OPERATIONS_DIRECTOR.id,
                display_name="Executive Assistant",
                flow_id="flow-assistant-ops",
                state=AssistantState.ACTIVE,
            )
        )
        other = await tx.add(
            AssistantRecord(
                organisation_id=org,
                created_at=t,
                manager_id=SALES_DIRECTOR.id,
                display_name="Executive Assistant",
                flow_id="flow-assistant-sales",
                state=AssistantState.ACTIVE,
            )
        )
        mine = {"organisation_id": org, "assistant_id": assistant.id, "created_at": t}
        work = await tx.add(
            PieceOfWork(**mine, work_kind=WorkKind.REQUEST, asked_by=OPERATIONS_DIRECTOR.id)
        )
        item = await tx.add(
            ActionItem(
                **mine,
                text="Finish the review",
                owner_id=LEGAL_LEAD.id,
                due_date=date(2026, 11, 27),
                origin="manager",
            )
        )
        person_request = await tx.add(
            PersonRequest(
                **mine,
                person_id=LEGAL_LEAD.id,
                asks="accept",
                subject_kind="action_item",
                subject_id=str(item.id),
                answer_by=t + timedelta(days=1),
            )
        )
        statement = await tx.add(
            ConfirmedStatement(
                **mine,
                project_id="vendor-consolidation",
                owner_id=LEGAL_LEAD.id,
                colour="amber",
                wording=StatementWording(reason="Two contracts are still open."),
                source="Legal lead",
                author_id=LEGAL_LEAD.id,
                mark="corrected",
                unconfirmed_wording=StatementWording(reason=UNSHOWN_WORDING),
            )
        )
        await tx.add(
            OrganisationSettings(
                organisation_id=org, created_at=t, colour_meanings={"green": "On plan"}
            )
        )
    return Seeded(assistant, other, work, item, person_request, statement)


class ApiVisibilityTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.standins = build_standins()
        self.store = SqliteStore()
        self.seeded = await seed(self.store)
        self.module = create_module(self.standins.boundary, self.store)
        self.app = FastAPI()
        self.app.include_router(self.module.router)
        self.client = TestClient(self.app)

    async def asyncTearDown(self) -> None:
        self.client.close()
        await self.store.close()

    def _call(self, method: str, path: str, person_id: str | None) -> Any:
        headers = {PERSON_HEADER: person_id} if person_id else {}
        return self.client.request(method, path, headers=headers)

    def test_visibility_test_covers_every_address(self) -> None:
        """Story 1.3 - it calls every address as all four personas."""
        missing = addresses_without_expectation(self.app)
        self.assertEqual(missing, set(), f"addresses without an expectation: {sorted(missing)}")
        stale = set(EXPECTATIONS) - api_addresses(self.app)
        self.assertEqual(stale, set(), f"expectations for addresses that do not exist: {stale}")

        for (method, template), expectation in EXPECTATIONS.items():
            path = template.format(**expectation.path_params(self.seeded))
            self.assertEqual(set(expectation.status), set(PERSONAS))
            for persona, person_id in PERSONAS.items():
                with self.subTest(address=f"{method} {template}", persona=persona):
                    response = self._call(method, path, person_id)
                    self.assertEqual(response.status_code, expectation.status[persona])
                    if response.status_code == 404:
                        self.assertEqual(response.json(), NOT_FOUND_BODY)
                    expected_id = expectation.returns_id(self.seeded, persona)
                    if expected_id is not None:
                        self.assertEqual(response.json()["id"], expected_id)

    def test_sweep_fails_on_an_address_it_does_not_know(self) -> None:
        self.app.add_api_route(f"{PREFIX}/surprise", lambda: {}, methods=["POST"])
        self.assertEqual(
            addresses_without_expectation(self.app), {("POST", f"{PREFIX}/surprise")}
        )

    def test_sweep_fails_on_a_new_module_address_without_expectation(self) -> None:
        module = create_module(self.standins.boundary, self.store)
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
                    not_theirs = self._call(method, real, PERSONAS[persona])
                    absent = self._call(method, missing, PERSONAS[persona])
                    bad = self._call(method, malformed, PERSONAS[persona])
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

        self.standins.people.person_asking = broken  # type: ignore[method-assign]
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
