"""Build the stand-ins and bundle them as the module's boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from assistant.boundary.interfaces import Boundary
from standins.approval import StandInApproval
from standins.assistant import ScriptedAssistant
from standins.clock import MovableClock
from standins.knowledge import ListKnowledge
from standins.lists import ListNotices, ListRecords
from standins.meetings import StandInMeetings
from standins.people import StandInPeople
from standins.sample import DEFAULT_START
from standins.work_data import StandInWorkData


@dataclass
class StandIns:
    """The concrete stand-ins, so the demonstration and tests can drive them."""

    people: StandInPeople
    work_data: StandInWorkData
    assistant: ScriptedAssistant
    approval: StandInApproval
    notices: ListNotices
    records: ListRecords
    clock: MovableClock
    meetings: StandInMeetings
    knowledge: ListKnowledge

    @property
    def boundary(self) -> Boundary:
        return Boundary(
            people=self.people,
            work_data=self.work_data,
            assistant=self.assistant,
            approval=self.approval,
            notices=self.notices,
            records=self.records,
            clock=self.clock,
            meetings=self.meetings,
            knowledge=self.knowledge,
        )


def build_standins(start: datetime = DEFAULT_START) -> StandIns:
    clock = MovableClock(start)
    return StandIns(
        people=StandInPeople(),
        work_data=StandInWorkData(clock),
        assistant=ScriptedAssistant(clock),
        approval=StandInApproval(),
        notices=ListNotices(),
        records=ListRecords(),
        clock=clock,
        meetings=StandInMeetings.sample(clock, start),
        knowledge=ListKnowledge(),
    )
