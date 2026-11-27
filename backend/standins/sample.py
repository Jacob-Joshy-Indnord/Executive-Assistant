"""The sample organisation of BRD section 9.3."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from assistant.models.boundary import AssignmentState, PersonKind


def sample_id(name: str) -> str:
    """A stable UUID text for a sample thing, so ids survive restarts."""
    return str(uuid5(NAMESPACE_URL, f"https://ea-001.invalid/sample/{name}"))


ORGANISATION_ID = sample_id("organisation")
OTHER_ORGANISATION_ID = sample_id("other-organisation")

# A Monday morning, so that "by Friday" is the same week.
DEFAULT_START = datetime(2026, 11, 23, 8, 0, tzinfo=UTC)


@dataclass(frozen=True)
class SamplePerson:
    key: str
    display_name: str
    kind: PersonKind = "person"
    administrator: bool = False
    organisation_id: str = ORGANISATION_ID

    @property
    def id(self) -> str:
        return sample_id(f"person/{self.key}")


OPERATIONS_DIRECTOR = SamplePerson("operations_director", "Operations director")
LEGAL_LEAD = SamplePerson("legal_lead", "Legal lead")
FINANCE_LEAD = SamplePerson("finance_lead", "Finance lead")
PROGRAMME_LEAD = SamplePerson("programme_lead", "Programme lead")
ORGANISATION_ADMINISTRATOR = SamplePerson(
    "organisation_administrator", "Organisation administrator", administrator=True
)
SALES_DIRECTOR = SamplePerson("sales_director", "Sales director")
BACKEND_ENGINEER = SamplePerson("backend_engineer", "Backend Engineer", kind="virtual_employee")
TEST_ENGINEER = SamplePerson("test_engineer", "Test Engineer", kind="virtual_employee")
# Someone in another organisation, to prove that nothing crosses over.
OUTSIDER = SamplePerson("outsider", "Legal lead", organisation_id=OTHER_ORGANISATION_ID)

PEOPLE: tuple[SamplePerson, ...] = (
    OPERATIONS_DIRECTOR,
    LEGAL_LEAD,
    FINANCE_LEAD,
    PROGRAMME_LEAD,
    ORGANISATION_ADMINISTRATOR,
    SALES_DIRECTOR,
    BACKEND_ENGINEER,
    TEST_ENGINEER,
    OUTSIDER,
)

MANAGERS = (OPERATIONS_DIRECTOR, SALES_DIRECTOR)


@dataclass
class SampleProject:
    key: str
    name: str
    owner: SamplePerson
    budget: str
    spend: str
    visible_to: set[str] = field(default_factory=set)

    @property
    def id(self) -> str:
        return sample_id(f"project/{self.key}")


@dataclass
class SampleAssignment:
    key: str
    title: str
    virtual_employee: SamplePerson
    manager: SamplePerson
    state: AssignmentState
    project_key: str | None = None

    @property
    def id(self) -> str:
        return sample_id(f"assignment/{self.key}")

    @property
    def project_id(self) -> str | None:
        return sample_id(f"project/{self.project_key}") if self.project_key else None


def sample_projects() -> list[SampleProject]:
    ops, sales = OPERATIONS_DIRECTOR.id, SALES_DIRECTOR.id
    p = SampleProject
    return [
        p("crm_rollout", "CRM rollout", PROGRAMME_LEAD, "250000.00", "112500.50", {ops}),
        p(
            "vendor_consolidation",
            "Vendor consolidation",
            LEGAL_LEAD,
            "80000.00",
            "23000.00",
            {ops},
        ),
        p("hiring_plan", "Hiring plan", FINANCE_LEAD, "150000.00", "61000.00", {ops, sales}),
    ]


def sample_assignments() -> list[SampleAssignment]:
    ops, backend, tester = OPERATIONS_DIRECTOR, BACKEND_ENGINEER, TEST_ENGINEER
    a = SampleAssignment
    return [
        a("crm_import", "Build the CRM data import", backend, ops, "in_progress", "crm_rollout"),
        a(
            "vendor_export",
            "Fix the vendor list export",
            backend,
            ops,
            "blocked",
            "vendor_consolidation",
        ),
        a("crm_tests", "Test the CRM rollout", tester, ops, "waiting_for_review", "crm_rollout"),
        a("hiring_forms", "Check the hiring plan forms", tester, ops, "approved", "hiring_plan"),
    ]
