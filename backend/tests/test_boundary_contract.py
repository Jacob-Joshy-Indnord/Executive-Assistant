"""Story 1.1: the reusable boundary tests, run against the stand-ins and another implementation."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from assistant.service.guard import guard
from contract.boundary import BoundaryContract, BoundaryUnderTest
from standins import build_standins
from standins.people import PERSON_HEADER
from standins.sample import (
    LEGAL_LEAD,
    OPERATIONS_DIRECTOR,
    ORGANISATION_ADMINISTRATOR,
    ORGANISATION_ID,
)


def _subject(boundary_wrapper: object = None) -> BoundaryUnderTest:
    boundary = build_standins().boundary
    if boundary_wrapper is not None:
        boundary = guard(boundary)
    return BoundaryUnderTest(
        boundary=boundary,
        organisation_id=ORGANISATION_ID,
        asking_headers={PERSON_HEADER: OPERATIONS_DIRECTOR.id},
        asking_person_id=OPERATIONS_DIRECTOR.id,
        manager_id=OPERATIONS_DIRECTOR.id,
        administrator_id=ORGANISATION_ADMINISTRATOR.id,
        non_administrator_id=LEGAL_LEAD.id,
        unique_display_name="Finance lead",
        assistant_flow_id="flow-assistant-1",
    )


class StandInsPassBoundaryContractTest(BoundaryContract, unittest.IsolatedAsyncioTestCase):
    """Story 1.1 - stand-ins pass the reusable boundary tests."""

    async def make(self) -> BoundaryUnderTest:
        return _subject()


class GuardedBoundaryPassesBoundaryContractTest(BoundaryContract, unittest.IsolatedAsyncioTestCase):
    """Story 1.1 - the same tests run unchanged against another implementation."""

    async def make(self) -> BoundaryUnderTest:
        return _subject(boundary_wrapper=guard)


class ContractIsImplementationAgnosticTest(unittest.TestCase):
    """Story 1.1 - contract tests do not depend on any one implementation."""

    def test_contract_code_never_imports_the_stand_ins(self) -> None:
        contract_dir = Path(__file__).resolve().parents[1] / "contract"
        for path in contract_dir.glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    self.assertFalse(name.startswith("standins"), f"{path.name} imports {name}")
                    self.assertFalse(name.startswith("tests"), f"{path.name} imports {name}")


if __name__ == "__main__":
    unittest.main()
