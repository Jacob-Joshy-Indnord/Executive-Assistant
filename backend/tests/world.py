"""A seeded module behind a test client: the sample organisation, two managers with assistants.

The assistants are made the way Flow makes them (``flow.set_up``) and their
managers skip the form, so both are active with their starting levels.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from assistant.models.boundary import ActionKind
from assistant.models.records import (
    ActionItem,
    AssistantRecord,
    AttendanceState,
    AttentionEntry,
    Briefing,
    BriefingState,
    BriefingVersion,
    ConfirmedStatement,
    DecisionPaper,
    DecisionPaperState,
    FollowedProject,
    Meeting,
    MeetingAttendance,
    MeetingNotes,
    MeetingState,
    NoteAction,
    NoteLine,
    OrganisationSettings,
    PaperVersion,
    PersonRequest,
    PersonRequestState,
    PieceOfWork,
    Report,
    RequestRecord,
    StatementWording,
    TranscriptPart,
    WorkKind,
    WorkState,
)
from assistant.module import AssistantModule, create_module
from assistant.rules.visibility import Viewer
from assistant.service.organisation import settings_id
from assistant.store.sqlite import SqliteStore
from standins import StandIns, build_standins
from standins.meetings import SampleMeeting
from standins.people import PERSON_HEADER
from standins.sample import (
    DEFAULT_START,
    FINANCE_LEAD,
    LEGAL_LEAD,
    OPERATIONS_DIRECTOR,
    ORGANISATION_ADMINISTRATOR,
    ORGANISATION_ID,
    PROGRAMME_LEAD,
    SALES_DIRECTOR,
    sample_id,
)

CRM = sample_id("project/crm_rollout")
"""Followed by the manager in the seeded world (only they see it), confirmed by the Legal lead."""

PERSONAS = {
    "manager": OPERATIONS_DIRECTOR.id,
    "other_manager": SALES_DIRECTOR.id,
    "owner": LEGAL_LEAD.id,
    "administrator": ORGANISATION_ADMINISTRATOR.id,
}
UNSHOWN_WORDING = "The assistant's wording the owner did not confirm"
MEETING_WORDS = "Decision: Renew the contract with the cleaning vendor."
SEEDED_MEETING = SampleMeeting(
    "contract_renewal",
    "Contract renewal",
    LEGAL_LEAD,
    DEFAULT_START + timedelta(days=60),
    DEFAULT_START + timedelta(days=60, hours=1),
    {OPERATIONS_DIRECTOR.id, LEGAL_LEAD.id},
)
"""A meeting the Legal lead (the owner persona) organises: the seeded booking waits for them."""
"""The seeded shared transcript's only words: they must never be shown or recorded."""


def viewer(person_id: str, administrator: bool = False) -> Viewer:
    return Viewer(person_id, ORGANISATION_ID, administrator)


@dataclass
class Seeded:
    assistant: AssistantRecord
    other_assistant: AssistantRecord
    work: PieceOfWork
    item: ActionItem
    person_request: PersonRequest
    statement: ConfirmedStatement
    request: RequestRecord
    attention: AttentionEntry
    followed: FollowedProject
    report: Report
    status_ask: PersonRequest
    shared_meeting: Meeting
    """Shared by the Legal lead (the owner persona) for the manager, waiting for approval."""
    notes: MeetingNotes
    """Notes of a meeting the Programme lead shared, with one action not yet kept."""
    attendance: MeetingAttendance
    """A booking for a meeting the Legal lead organises, waiting for them to answer."""
    organiser_ask: PersonRequest
    briefing: Briefing
    """Last week's briefing, ready: its notes could not be prepared."""
    paper: DecisionPaper
    """A decision paper the manager already decided: it waits for nothing."""


@dataclass
class World:
    standins: StandIns
    store: SqliteStore
    module: AssistantModule
    app: FastAPI
    client: TestClient
    seeded: Seeded

    def call(self, method: str, path: str, person_id: str | None, json: Any = None) -> Response:
        headers = {PERSON_HEADER: person_id} if person_id else {}
        return self.client.request(method, path, headers=headers, json=json)

    def close(self) -> None:
        self.client.close()


async def set_up_active(module: AssistantModule, manager_id: str, flow_id: str) -> AssistantRecord:
    await module.flow.set_up(ORGANISATION_ID, manager_id, "Executive Assistant", flow_id)
    await module.services.setup.skip(viewer(manager_id))
    async with module.store.transaction(ORGANISATION_ID) as tx:
        found = await tx.find(AssistantRecord, flow_id=flow_id)
    return found[0]


async def seed(module: AssistantModule) -> Seeded:
    t = await module.boundary.clock.now()
    org = ORGANISATION_ID
    assistant = await set_up_active(module, OPERATIONS_DIRECTOR.id, "flow-assistant-ops")
    other = await set_up_active(module, SALES_DIRECTOR.id, "flow-assistant-sales")
    async with module.store.transaction(org) as tx:
        mine = {"organisation_id": org, "assistant_id": assistant.id, "created_at": t}
        work = await tx.add(
            PieceOfWork(
                **mine,
                work_kind=WorkKind.REQUEST,
                asked_by=OPERATIONS_DIRECTOR.id,
                state=WorkState.FAILED,
                reason="The assistant could not finish this work.",
                finished_at=t,
            )
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
        request = await tx.add(
            RequestRecord(
                **mine,
                action_kind=ActionKind.SEND_MESSAGE,
                request_kind="message",
                subject_kind="person",
                subject_id=FINANCE_LEAD.id,
                target_id=FINANCE_LEAD.id,
                text="Please send the budget figures.",
                effect="Send a message to the Finance lead.",
            )
        )
        attention = await tx.add(
            AttentionEntry(
                **mine,
                subject_kind="assistant",
                subject_id=str(work.id),
                reason="The vendor contract renews next week.",
                known="Legal has not replied about the renewal terms.",
                choices=["Renew it", "Let it lapse"],
            )
        )
        followed = await tx.add(
            FollowedProject(
                **mine,
                project_id=CRM,
                confirmer_id=LEGAL_LEAD.id,
                project_name="CRM rollout",
                window_start=t,
                spend_at_start="112500.50",
                assignment_states={
                    sample_id("assignment/crm_import"): "in_progress",
                    sample_id("assignment/crm_tests"): "waiting_for_review",
                },
            )
        )
        report = await tx.add(Report(**mine, made_by="manager"))
        status_ask = await tx.add(
            PersonRequest(
                **mine,
                person_id=LEGAL_LEAD.id,
                asks="status",
                subject_kind="project",
                subject_id=CRM,
                answer_by=t + timedelta(days=7),
                sent_at=t,
                state=PersonRequestState.SENT,
            )
        )
        await tx.add(
            OrganisationSettings(
                id=settings_id(org), organisation_id=org, created_at=t, meeting_notes_allowed=True
            )
        )
        shared_meeting = await tx.add(
            Meeting(
                **mine,
                title="Contract review",
                meeting_date=date(2026, 11, 20),
                organiser_id=LEGAL_LEAD.id,
                manager_id=OPERATIONS_DIRECTOR.id,
                ran_it=True,
                people_knew=True,
                parts=[TranscriptPart(number=1, first_line=1, last_line=1, text=MEETING_WORDS)],
                text_arrived_at=t,
            )
        )
        ready_meeting = await tx.add(
            Meeting(
                **mine,
                title="Vendor review",
                meeting_date=date(2026, 11, 20),
                organiser_id=PROGRAMME_LEAD.id,
                manager_id=OPERATIONS_DIRECTOR.id,
                ran_it=True,
                people_knew=True,
                parts=[TranscriptPart(number=1, first_line=1, last_line=1)],
                text_arrived_at=t,
                text_removed_at=t,
                state=MeetingState.READY,
            )
        )
        notes = await tx.add(
            MeetingNotes(
                **mine,
                meeting_id=ready_meeting.id,
                title="Vendor review",
                meeting_date=date(2026, 11, 20),
                organiser_id=PROGRAMME_LEAD.id,
                decisions=[NoteLine(text="We keep two vendors.", parts=[1])],
                actions=[
                    NoteAction(
                        text="Check the vendor invoices",
                        owner_name="Finance lead",
                        owner_id=FINANCE_LEAD.id,
                        due_date=date(2026, 11, 27),
                        parts=[1],
                    )
                ],
                parts=[TranscriptPart(number=1, first_line=1, last_line=1)],
            )
        )
        ask_id = uuid4()
        attendance = await tx.add(
            MeetingAttendance(
                **mine,
                manager_id=OPERATIONS_DIRECTOR.id,
                meeting_ref=SEEDED_MEETING.id,
                organiser_id=LEGAL_LEAD.id,
                starts_at=SEEDED_MEETING.starts_at,
                ends_at=SEEDED_MEETING.ends_at,
                ask_id=ask_id,
                state=AttendanceState.WAITING_FOR_ORGANISER,
            )
        )
        organiser_ask = await tx.add(
            PersonRequest(
                **mine,
                id=ask_id,
                person_id=LEGAL_LEAD.id,
                asks="attend",
                subject_kind="meeting_attendance",
                subject_id=str(attendance.id),
                answer_by=attendance.starts_at,
                sent_at=t,
                state=PersonRequestState.SENT,
            )
        )
        last_week = t - timedelta(days=7)
        briefing = await tx.add(
            Briefing(
                organisation_id=org,
                assistant_id=assistant.id,
                created_at=last_week,
                made_by="schedule",
                due_at=last_week,
                sections=["decisions"],
                versions=[BriefingVersion(number=1, notes_prepared=False, arrived_at=last_week)],
                state=BriefingState.READY,
            )
        )
        paper = await tx.add(
            DecisionPaper(
                organisation_id=org,
                assistant_id=assistant.id,
                created_at=last_week,
                versions=[
                    PaperVersion(
                        number=1,
                        decision="Renew the cleaning contract?",
                        options=["Renew it", "Let it lapse"],
                        recommendation="Renew it",
                        due_date=date(2026, 11, 20),
                        sources=["Message from the manager"],
                        arrived_at=last_week,
                    )
                ],
                state=DecisionPaperState.DECIDED,
            )
        )
    return Seeded(
        assistant, other, work, item, person_request, statement, request, attention, followed,
        report, status_ask, shared_meeting, notes, attendance, organiser_ask, briefing, paper,
    )  # fmt: skip


async def build_world(**module_options: Any) -> World:
    standins = build_standins()
    standins.meetings.meetings[SEEDED_MEETING.id] = SEEDED_MEETING
    store = SqliteStore()
    module = create_module(standins.boundary, store, **module_options)
    seeded = await seed(module)
    app = FastAPI()
    app.include_router(module.router)
    return World(standins, store, module, app, TestClient(app), seeded)


def build_world_sync(**module_options: Any) -> World:
    """For tests that are not async; the test client runs its own event loop."""
    return asyncio.run(build_world(**module_options))


@dataclass
class Bare:
    """A module with the stand-ins and an empty store: no assistant set up yet."""

    standins: StandIns
    store: SqliteStore
    module: AssistantModule
    client: TestClient

    def call(self, method: str, path: str, person_id: str | None, json: Any = None) -> Response:
        headers = {PERSON_HEADER: person_id} if person_id else {}
        return self.client.request(method, path, headers=headers, json=json)


def bare(**module_options: Any) -> Bare:
    standins = build_standins()
    store = SqliteStore()
    module = create_module(standins.boundary, store, **module_options)
    app = FastAPI()
    app.include_router(module.router)
    return Bare(standins, store, module, TestClient(app))
