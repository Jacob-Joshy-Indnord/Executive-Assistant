"""Shared steps for the tests of part 2, Epic 2 (the briefing and decision papers)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from assistant.api.router import PREFIX
from assistant.models.boundary import WorkKind, WorkOrder, WorkResult
from assistant.models.preferences import PreferencesForm, WorkingTime
from assistant.models.records import (
    ActionItem,
    Briefing,
    DecisionPaper,
    DecisionPaperState,
    ItemState,
    Meeting,
    MeetingNotes,
    MeetingState,
    NoteLine,
    PaperVersion,
    PieceOfWork,
    TakenDecision,
    TranscriptPart,
)
from standins.sample import LEGAL_LEAD, OPERATIONS_DIRECTOR, ORGANISATION_ID, PROGRAMME_LEAD
from tests.world import World, viewer

MANAGER = OPERATIONS_DIRECTOR.id
OWNER = LEGAL_LEAD.id


class Briefings:
    """Mixed into a test case that has ``self.w``, a seeded world."""

    w: World

    # Calling the API -----------------------------------------------------------------------

    def call(self, method: str, path: str, person: str = MANAGER, body: Any = None) -> Any:
        return self.w.call(method, f"{PREFIX}{path}", person, body)

    def ok(self, method: str, path: str, person: str = MANAGER, body: Any = None) -> Any:
        response = self.call(method, path, person, body)
        assert response.status_code == 200, response.text
        return response.json()

    def refused(self, method: str, path: str, person: str = MANAGER, body: Any = None) -> str:
        response = self.call(method, path, person, body)
        assert response.status_code == 422, response.text
        message: str = response.json()["detail"]["message"]
        return message

    # The world -----------------------------------------------------------------------------

    @property
    def assistant_id(self) -> UUID:
        return self.w.seeded.assistant.id

    async def now(self) -> datetime:
        return await self.w.standins.clock.now()

    async def move_to(self, when: datetime) -> None:
        await self.w.standins.clock.move_to(when)

    async def set_time_zone(self, time_zone: str) -> None:
        form = PreferencesForm(working_time=WorkingTime(time_zone=time_zone))
        await self.w.module.services.setup.save(viewer(MANAGER), form)

    def set_briefing(self, **change: Any) -> Any:
        return self.ok("PUT", "/briefing/settings", body=change)

    async def add_item(self, due: date, state: ItemState, text: str = "Check it") -> ActionItem:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return await tx.add(
                ActionItem(
                    organisation_id=ORGANISATION_ID,
                    assistant_id=self.assistant_id,
                    created_at=await self.now(),
                    text=text,
                    owner_id=OWNER,
                    due_date=due,
                    origin="manager",
                    state=state,
                )
            )

    async def add_paper(
        self, state: DecisionPaperState, due: date = date(2026, 11, 30), **changes: Any
    ) -> DecisionPaper:
        now = await self.now()
        version = PaperVersion(
            number=1,
            decision=changes.pop("decision", "Extend the two vendor contracts by a month?"),
            background="Both contracts end in December.",
            options=changes.pop("options", ["Extend both", "Extend one", "Let both end"]),
            recommendation="Extend both",
            due_date=due,
            sources=["Message from the manager"],
            arrived_at=now,
        )
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return await tx.add(
                DecisionPaper(
                    organisation_id=ORGANISATION_ID,
                    assistant_id=self.assistant_id,
                    created_at=now,
                    versions=[version],
                    state=state,
                    **changes,
                )
            )

    async def keep_notes(
        self,
        title: str = "Supplier call",
        decisions: tuple[str, ...] = ("We go ahead with the supplier.",),
        open_points: tuple[str, ...] = ("Who signs the order?",),
    ) -> MeetingNotes:
        """Notes kept now, as Epic 1 keeps them once they pass the checks."""
        now = await self.now()
        mine = {
            "organisation_id": ORGANISATION_ID,
            "assistant_id": self.assistant_id,
            "created_at": now,
        }
        part = TranscriptPart(number=1, first_line=1, last_line=1)
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            meeting = await tx.add(
                Meeting(
                    **mine,
                    title=title,
                    meeting_date=now.date(),
                    organiser_id=PROGRAMME_LEAD.id,
                    manager_id=MANAGER,
                    ran_it=True,
                    people_knew=True,
                    parts=[part],
                    text_arrived_at=now,
                    text_removed_at=now,
                    state=MeetingState.READY,
                )
            )
            return await tx.add(
                MeetingNotes(
                    **mine,
                    meeting_id=meeting.id,
                    title=title,
                    meeting_date=now.date(),
                    organiser_id=PROGRAMME_LEAD.id,
                    decisions=[NoteLine(text=d, parts=[1]) for d in decisions],
                    open_points=[NoteLine(text=o, parts=[1]) for o in open_points],
                    parts=[part],
                )
            )

    # Reading the store -----------------------------------------------------------------------

    async def briefings(self) -> list[Briefing]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            found = await tx.find(Briefing, assistant_id=self.assistant_id)
        made = [b for b in found if b.id != self.w.seeded.briefing.id]  # not last week's
        return sorted(made, key=lambda b: b.created_at)

    async def briefing(self, briefing_id: str | UUID) -> Briefing:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            found = await tx.get(Briefing, UUID(str(briefing_id)))
        assert found is not None
        return found

    async def papers(self) -> list[DecisionPaper]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            found = await tx.find(DecisionPaper, assistant_id=self.assistant_id)
        return [p for p in found if p.id != self.w.seeded.paper.id]  # not the seeded one

    async def paper(self, paper_id: str | UUID) -> DecisionPaper:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            found = await tx.get(DecisionPaper, UUID(str(paper_id)))
        assert found is not None
        return found

    async def decisions(self) -> list[TakenDecision]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return await tx.find(TakenDecision, assistant_id=self.assistant_id)

    async def piece(self, work_id: UUID) -> PieceOfWork:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            found = await tx.get(PieceOfWork, work_id)
        assert found is not None
        return found

    # The assistant's turn ------------------------------------------------------------------

    def started(self, kind: WorkKind) -> list[WorkOrder]:
        """Work started for the manager's assistant (the other manager's runs too)."""
        flow_id = self.w.seeded.assistant.flow_id
        return [
            o
            for o in self.w.standins.assistant.started
            if o.kind is kind and o.assistant_flow_id == flow_id
        ]

    async def finish(self, change: Any = None) -> list[UUID]:
        """Hand the stand-in's answers to the module, as Flow does at the end of a turn, until
        none is left; ``change`` may alter each result first. Returns the work ids handled."""
        handled: list[UUID] = []
        while self.w.standins.assistant.has_finished:
            for result in self.w.standins.assistant.take_finished():
                if change is not None:
                    result = change(result)
                await self.w.module.flow.finish_work(
                    ORGANISATION_ID, self.w.seeded.assistant.flow_id, result
                )
                handled.append(result.work_id)
        return handled

    def notices_to(self, person: str = MANAGER) -> list[str]:
        return [n.text for n in self.w.standins.notices.sent if n.person_id == person]


def section(view: dict[str, Any], key: str) -> dict[str, Any] | None:
    return next((s for s in view["sections"] if s["key"] == key), None)


def result_with(**changes: Any) -> Any:
    def change(result: WorkResult) -> WorkResult:
        return result.model_copy(update=changes)

    return change
