"""What the assistant prepared goes at hand-over or end (part 2, Epic 3).

Part 1's hand-over and end (:mod:`assistant.service.closing`) run every registered closing
handler in one audited transaction. These handlers add part 2's records:

* every booking for a meeting that has not ended is withdrawn (what its organiser was asked
  is withdrawn by part 1's handler for asks), an assistant still in a meeting asks the
  boundary to leave it, and no transcript is asked for and no notes are written for it;
* its notes, transcripts and deliverables (briefings and decision papers) are removed and
  can no longer be read. A note the manager shared stays in Flow: they chose to share it.

Taken decisions are never changed or removed (BR-7). Lessons, signals and suggestions go
through :func:`assistant.service.learning.forget_learning`.
"""

from __future__ import annotations

from assistant.errors import ModuleError
from assistant.models.records import (
    AssistantRecord,
    AttendanceState,
    Briefing,
    BriefingSettings,
    DecisionPaper,
    Meeting,
    MeetingAttendance,
    MeetingNotes,
    MutableRecord,
    PersonRequestState,
)
from assistant.service.actions import Action
from assistant.service.attendance import change_state, end_ask
from assistant.service.closing import CLOSING_HANDLERS, Closing
from assistant.service.log import log_event

BOOKED_STATES = (AttendanceState.BOOKED, AttendanceState.WAITING_FOR_ORGANISER)
IN_MEETING_STATES = (AttendanceState.JOINING, AttendanceState.ATTENDING)


@CLOSING_HANDLERS.register
async def withdraw_meetings(action: Action, assistant: AssistantRecord, closing: Closing) -> None:
    """Bookings are withdrawn, an assistant in a meeting leaves it, and no transcript is
    asked for any more."""
    for attendance in await action.find(MeetingAttendance, assistant_id=assistant.id):
        if attendance.state in BOOKED_STATES:
            if action.now >= attendance.ends_at:
                continue  # the meeting has ended: nothing is left to withdraw
            await change_state(
                action,
                attendance,
                AttendanceState.WITHDRAWN,
                because=closing.reason,
                transcript_wanted=False,
            )
            await end_ask(action, attendance, PersonRequestState.WITHDRAWN)
        elif attendance.state in IN_MEETING_STATES:
            await change_state(
                action,
                attendance,
                AttendanceState.LEFT,
                because=closing.reason,
                left_at=action.now,
                transcript_wanted=False,
            )
            try:
                await action.boundary.meetings.leave(attendance.meeting_ref, assistant.flow_id)
            except ModuleError:
                # Leaving is asked once. When the meeting service cannot be reached, the
                # hand-over or end still goes ahead, and it is logged for Flow's operators.
                log_event(
                    "assistant.leave_not_reached",
                    attendance_id=str(attendance.id),
                    because=closing.reason,
                )
        elif (
            attendance.state is AttendanceState.LEFT
            and attendance.transcript_wanted
            and attendance.meeting_id is None
        ):
            await action.change(
                "assistant.attendance_changed",
                attendance,
                attendance.evolve(transcript_wanted=False),
                details={"because": closing.reason},
            )


@CLOSING_HANDLERS.register
async def remove_prepared_work(
    action: Action, assistant: AssistantRecord, closing: Closing
) -> None:
    """Notes, transcripts and deliverables are removed. Shared notes stay in Flow."""
    removable: list[tuple[str, type[MutableRecord]]] = [
        ("assistant.meeting_notes_removed", MeetingNotes),
        ("assistant.transcript_removed", Meeting),
        ("assistant.briefing_removed", Briefing),
        ("assistant.briefing_settings_removed", BriefingSettings),
        ("assistant.decision_paper_removed", DecisionPaper),
    ]
    for name, record_type in removable:
        for record in await action.find(record_type, assistant_id=assistant.id):
            await action.remove(name, record, details={"because": closing.reason})
