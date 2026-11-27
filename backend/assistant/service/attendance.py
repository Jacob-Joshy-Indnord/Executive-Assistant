"""The assistant attends a meeting for its manager (part 2, Epic 1).

The module never connects to a meeting service, records sound or joins anything
itself: every such request goes through the boundary's Meetings. Here:

* the manager's upcoming meetings, each with Attend for me, which books the
  assistant for one meeting or a series as the manager's own decision (no
  approval); the assistant's own proposal, the ``attend_meeting`` tool, goes
  through the approval check as "Attend a meeting and take notes";
* when the manager does not organise the meeting, its organiser is asked on
  Requests for you, and only an acceptance books the assistant;
* registered due steps, each safe to run twice, late or in two places: withdraw a
  booking whose meeting is cancelled or over; at the start, withdraw a booking the
  organiser has not answered, or ask the meeting service to let the assistant in
  as a silent note-taker under "<manager's name>'s assistant (notes)"; stop when
  refused or still waiting after 10 minutes; at the end, leave and ask for the
  transcript, failing when none has come 2 hours later.

The attendance record holds ids, times and states only, and the record of each
change is written before the change takes effect (the audited :class:`Action`).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from assistant.boundary.interfaces import Boundary
from assistant.errors import ModuleError, NotAllowedValue, NotFound
from assistant.models.boundary import ActionKind, JoinRequest, MeetingInfo, Person
from assistant.models.records import (
    AssistantRecord,
    AttendanceState,
    MeetingAttendance,
    PersonRequest,
    PersonRequestState,
    PieceOfWork,
    RequestRecord,
)
from assistant.rules.meetings import NOTES_NOT_ALLOWED, attendee_name
from assistant.rules.requests import OPEN_STATES
from assistant.rules.tools import refusal
from assistant.rules.visibility import Viewer
from assistant.service.actions import Action, perform
from assistant.service.assistants import my_assistant
from assistant.service.checks import CheckedRequest
from assistant.service.due import DUE_STEPS, DueContext, DueStep
from assistant.service.log import log_event
from assistant.service.meetings import MeetingService, keep_attended
from assistant.service.notices import Tell, tell
from assistant.service.organisation import meeting_notes_allowed
from assistant.service.request_kinds import RequestKind, register
from assistant.service.requests import RequestService
from assistant.service.visibility import find_visible, get_visible, parse_id
from assistant.store.interface import Store, Transaction

SUBJECT = "meeting_attendance"
ATTEND_REQUEST = "attend_meeting"
ADMISSION_CHECK = timedelta(minutes=2)
WAIT_TO_BE_LET_IN = timedelta(minutes=10)
TRANSCRIPT_CHECK = timedelta(minutes=10)
WAIT_FOR_TRANSCRIPT = timedelta(hours=2)

BOOKED_STATES = frozenset(
    {
        AttendanceState.BOOKED,
        AttendanceState.WAITING_FOR_ORGANISER,
        AttendanceState.JOINING,
        AttendanceState.ATTENDING,
        AttendanceState.LEFT,
        AttendanceState.WRITING_NOTES,
        AttendanceState.READY,
    }
)
"""The assistant is booked for the meeting, or has been to it."""
BEFORE_JOINING = frozenset({AttendanceState.BOOKED, AttendanceState.WAITING_FOR_ORGANISER})

ALREADY_BOOKED = "Your assistant is already booked for this meeting."
NOT_YOURS = "Only the organiser of this meeting can answer this request."
NOT_WAITING = "This request is no longer waiting for an answer."
STARTED = "The meeting has already started."
NOT_ATTENDING = "The assistant is not attending this meeting."
ENDED = "The meeting has already ended."

TOLD_WHY: dict[str, str] = {
    "cancelled": "{title} was cancelled, so your assistant will not attend it.",
    "ended_before_joining": "{title} ended before your assistant joined it.",
    "no_answer": "The organiser of {title} had not answered by the start, so your assistant "
    "did not attend.",
    "organiser_declined": "The organiser of {title} declined, so your assistant will not "
    "attend it.",
    "organiser_removed": "The organiser of {title} removed your assistant from the meeting.",
    "join_refused": "Your assistant could not join {title} because the meeting service "
    "refused it.",
    "not_let_in": "Your assistant was not let in to {title} within 10 minutes, so it stopped "
    "trying.",
    "no_transcript": "No transcript came for {title}, so no notes can be written.",
}


def told(because: str, title: str | None) -> str:
    text = TOLD_WHY[because].format(title=title or "your meeting")
    return text[:1].upper() + text[1:]


async def title_of(boundary: Boundary, attendance: MeetingAttendance) -> str | None:
    """The meeting's title, as the meeting service gives it now; the record keeps none."""
    try:
        for info in await boundary.meetings.upcoming(attendance.manager_id):
            if info.id == attendance.meeting_ref:
                return info.title
    except ModuleError:
        return None
    return None


async def tell_manager(
    boundary: Boundary, attendance: MeetingAttendance, because: str, title: str | None = None
) -> None:
    """One notice saying why; it names the meeting and holds no transcript text."""
    title = title or await title_of(boundary, attendance)
    await tell(
        boundary,
        attendance.organisation_id,
        [Tell(attendance.manager_id, SUBJECT, str(attendance.id), told(because, title))],
    )


async def change_state(
    action: Action,
    attendance: MeetingAttendance,
    state: AttendanceState,
    *,
    because: str | None = None,
    **changes: Any,
) -> MeetingAttendance:
    """Every change is recorded before it takes effect."""
    if because is not None:
        changes["because"] = because
    return await action.change(
        "assistant.attendance_changed",
        attendance,
        attendance.evolve(state=state, **changes),
        details={"because": because} if because else None,
    )


async def end_ask(action: Action, attendance: MeetingAttendance, to: PersonRequestState) -> None:
    if attendance.ask_id is None:
        return
    ask = await action.get(PersonRequest, attendance.ask_id)
    if ask is not None and ask.state is PersonRequestState.SENT:
        name = "assistant.ask_answered" if to is PersonRequestState.ANSWERED else (
            "assistant.ask_withdrawn"
        )  # fmt: skip
        await action.change(name, ask, ask.evolve(state=to), details={"asks": "attend"})


async def withdraw(
    action: Action, attendance: MeetingAttendance, because: str
) -> MeetingAttendance:
    withdrawn = await change_state(action, attendance, AttendanceState.WITHDRAWN, because=because)
    await end_ask(action, attendance, PersonRequestState.WITHDRAWN)
    return withdrawn


async def is_booked(
    reader: Transaction | Action, assistant: AssistantRecord, meeting_ref: str
) -> bool:
    """Booked for the meeting, or a proposal to attend it is still waiting or approved."""
    for attendance in await reader.find(
        MeetingAttendance, assistant_id=assistant.id, meeting_ref=meeting_ref
    ):
        if attendance.state in BOOKED_STATES:
            return True
    for request in await reader.find(
        RequestRecord, assistant_id=assistant.id, request_kind=ATTEND_REQUEST
    ):
        if request.subject_id == meeting_ref and request.state in OPEN_STATES:
            return True
    return False


async def book(
    action: Action,
    assistant: AssistantRecord,
    info: MeetingInfo,
    booked_by: str,
    request_id: UUID | None = None,
) -> MeetingAttendance:
    """Book the assistant. For a meeting the manager does not organise, its organiser is
    asked on Requests for you, and the record waits for them."""
    own = info.organiser_id == assistant.manager_id
    ask_id = None if own else uuid4()
    attendance = await action.add(
        "assistant.attendance_booked",
        MeetingAttendance(
            organisation_id=assistant.organisation_id,
            assistant_id=assistant.id,
            created_at=action.now,
            manager_id=assistant.manager_id,
            meeting_ref=info.id,
            organiser_id=info.organiser_id,
            starts_at=info.starts_at,
            ends_at=info.ends_at,
            series_id=info.series_id,
            booked_by="assistant" if booked_by == "assistant" else "manager",
            request_id=request_id,
            ask_id=ask_id,
            state=AttendanceState.BOOKED if own else AttendanceState.WAITING_FOR_ORGANISER,
        ),
        details={"booked_by": booked_by},
    )
    if ask_id is None:
        return attendance
    await action.add(
        "assistant.ask_sent",
        PersonRequest(
            id=ask_id,
            organisation_id=assistant.organisation_id,
            assistant_id=assistant.id,
            created_at=action.now,
            person_id=info.organiser_id,
            asks="attend",
            subject_kind=SUBJECT,
            subject_id=str(attendance.id),
            answer_by=info.starts_at,
            sent_at=action.now,
            state=PersonRequestState.SENT,
        ),
        details={"asks": "attend", "subject_kind": SUBJECT},
    )
    manager = await action.boundary.people.person(assistant.manager_id)
    whose = f"The {manager.display_name}'s assistant" if manager else "An assistant"
    await tell(
        action.boundary,
        assistant.organisation_id,
        [
            Tell(
                info.organiser_id,
                SUBJECT,
                str(attendance.id),
                f"{whose} asks to attend {info.title} to take notes.",
            )
        ],
    )
    return attendance


async def _carry_out_attend(
    action: Action, assistant: AssistantRecord, request: RequestRecord, text: str | None
) -> dict[str, Any]:
    """The module's part of "Attend a meeting and take notes": book the assistant."""
    info = MeetingInfo.model_validate(request.payload["meeting"])
    for attendance in await action.find(
        MeetingAttendance, assistant_id=assistant.id, meeting_ref=info.id
    ):
        if attendance.state in BOOKED_STATES:
            return {}  # booked meanwhile, e.g. by the manager's own Attend for me
    attendance = await book(action, assistant, info, "assistant", request.id)
    return {"payload": {**request.payload, "attendance_id": str(attendance.id)}}


register(
    RequestKind(
        ATTEND_REQUEST,
        ActionKind.ATTEND_MEETING,
        "Attend",
        _carry_out_attend,
        editable=False,
    )
)


# Due steps ------------------------------------------------------------------------------------


def _latest(first: datetime, every: timedelta, count: int, until: datetime) -> datetime | None:
    """The latest of ``first + k * every`` (k from 0 to ``count``) at or before ``until``."""
    if until < first:
        return None
    k = min(int((until - first) / every), count)
    return first + k * every


def _next(first: datetime, every: timedelta, count: int, after: datetime) -> datetime | None:
    if after < first:
        return first
    k = int((after - first) / every) + 1
    return first + k * every if k <= count else None


async def _attendances(
    reader: Transaction, ctx: DueContext, states: frozenset[AttendanceState]
) -> list[MeetingAttendance]:
    return [
        a
        for a in await reader.find(MeetingAttendance, assistant_id=ctx.assistant.id)
        if a.state in states
    ]


class MeetingWithdrawals:
    """A booked meeting that is cancelled, or ends before the assistant joins, withdraws
    the booking and tells the manager. Reads the meeting service on every run."""

    name = "meeting_withdrawals"

    async def due(self, reader: Transaction, ctx: DueContext, until: datetime) -> list[DueStep]:
        waiting = await _attendances(reader, ctx, BEFORE_JOINING)
        if not waiting:
            return []
        try:
            listed = {m.id for m in await ctx.boundary.meetings.upcoming(ctx.assistant.manager_id)}
        except ModuleError:
            return []  # the meeting service is asked again on the next run
        return [
            DueStep(str(a.id), a.created_at)
            for a in waiting
            if (a.meeting_ref not in listed or until >= a.ends_at) and a.created_at <= until
        ]

    async def next_after(self, reader: Transaction, ctx: DueContext, after: datetime) -> None:
        return None

    async def run(self, action: Action, ctx: DueContext, step: DueStep) -> None:
        attendance = await action.get(MeetingAttendance, UUID(step.subject))
        if attendance is None or attendance.state not in BEFORE_JOINING:
            return
        listed = await action.boundary.meetings.upcoming(attendance.manager_id)
        if action.now < attendance.ends_at and any(m.id == attendance.meeting_ref for m in listed):
            return
        because = "ended_before_joining" if action.now >= attendance.ends_at else "cancelled"
        await withdraw(action, attendance, because)
        await tell_manager(action.boundary, attendance, because)


class MeetingStart:
    """When a booked meeting starts: withdraw it if the organiser has not answered, or ask
    the meeting service to let the assistant in as a silent note-taker."""

    name = "meeting_start"

    async def due(self, reader: Transaction, ctx: DueContext, until: datetime) -> list[DueStep]:
        return [
            DueStep(str(a.id), a.starts_at)
            for a in await _attendances(reader, ctx, BEFORE_JOINING)
            if a.starts_at <= until
        ]

    async def next_after(
        self, reader: Transaction, ctx: DueContext, after: datetime
    ) -> datetime | None:
        times = [
            a.starts_at
            for a in await _attendances(reader, ctx, BEFORE_JOINING)
            if a.starts_at > after
        ]
        return min(times) if times else None

    async def run(self, action: Action, ctx: DueContext, step: DueStep) -> None:
        attendance = await action.get(MeetingAttendance, UUID(step.subject))
        if attendance is None or attendance.state not in BEFORE_JOINING:
            return
        if attendance.state is AttendanceState.WAITING_FOR_ORGANISER:
            await withdraw(action, attendance, "no_answer")
            await tell_manager(action.boundary, attendance, "no_answer")
            return
        if action.now >= attendance.ends_at:
            await withdraw(action, attendance, "ended_before_joining")
            await tell_manager(action.boundary, attendance, "ended_before_joining")
            return
        listed = await action.boundary.meetings.upcoming(attendance.manager_id)
        info = next((m for m in listed if m.id == attendance.meeting_ref), None)
        if info is None:
            await withdraw(action, attendance, "cancelled")
            await tell_manager(action.boundary, attendance, "cancelled")
            return
        joining = await change_state(
            action, attendance, AttendanceState.JOINING, join_asked_at=action.now
        )
        await join(action, ctx.assistant, joining, info.title)


async def join(
    action: Action, assistant: AssistantRecord, attendance: MeetingAttendance, title: str | None
) -> MeetingAttendance:
    """Ask the meeting service to let the assistant in, as a silent note-taker under a name
    that says whose assistant it is and that it takes notes. Asking again is safe."""
    manager = await action.boundary.people.person(assistant.manager_id)
    result = await action.boundary.meetings.join(
        JoinRequest(
            organisation_id=assistant.organisation_id,
            meeting_id=attendance.meeting_ref,
            manager_id=assistant.manager_id,
            assistant_flow_id=assistant.flow_id,
            display_name=attendee_name(manager.display_name if manager else "A manager"),
        )
    )
    if result.outcome == "joined":
        return await change_state(action, attendance, AttendanceState.ATTENDING)
    if result.outcome == "refused":
        failed = await change_state(
            action, attendance, AttendanceState.FAILED, because="join_refused"
        )
        await tell_manager(action.boundary, attendance, "join_refused", title)
        return failed
    return attendance  # still waiting to be let in


class MeetingAdmission:
    """While the assistant waits to be let in: ask again every 2 minutes, and stop when it
    is still waiting after 10 minutes."""

    name = "meeting_admission"

    async def due(self, reader: Transaction, ctx: DueContext, until: datetime) -> list[DueStep]:
        steps: list[DueStep] = []
        for a in await _attendances(reader, ctx, frozenset({AttendanceState.JOINING})):
            if a.join_asked_at is None:
                continue
            first = a.join_asked_at + ADMISSION_CHECK
            when = _latest(first, ADMISSION_CHECK, 4, until)
            if when is not None:
                steps.append(DueStep(str(a.id), when))
        return steps

    async def next_after(
        self, reader: Transaction, ctx: DueContext, after: datetime
    ) -> datetime | None:
        times = [
            when
            for a in await _attendances(reader, ctx, frozenset({AttendanceState.JOINING}))
            if a.join_asked_at is not None
            and (when := _next(a.join_asked_at + ADMISSION_CHECK, ADMISSION_CHECK, 4, after))
        ]
        return min(times) if times else None

    async def run(self, action: Action, ctx: DueContext, step: DueStep) -> None:
        attendance = await action.get(MeetingAttendance, UUID(step.subject))
        if attendance is None or attendance.state is not AttendanceState.JOINING:
            return
        current = await join(action, ctx.assistant, attendance, None)
        if current.state is not AttendanceState.JOINING:
            return
        assert attendance.join_asked_at is not None
        if action.now >= attendance.join_asked_at + WAIT_TO_BE_LET_IN:
            await action.boundary.meetings.leave(attendance.meeting_ref, ctx.assistant.flow_id)
            await change_state(action, current, AttendanceState.FAILED, because="not_let_in")
            await tell_manager(action.boundary, attendance, "not_let_in")


AFTER_STATES = frozenset(
    {AttendanceState.JOINING, AttendanceState.ATTENDING, AttendanceState.LEFT}
)


class MeetingEnd:
    """When the meeting ends: leave, and ask for the transcript every 10 minutes until it
    comes; when none has come 2 hours after the end, no notes can be written."""

    name = "meeting_end"

    @staticmethod
    def _waiting(a: MeetingAttendance) -> bool:
        if a.state is AttendanceState.LEFT:
            return a.transcript_wanted and a.meeting_id is None
        return a.state in AFTER_STATES

    async def due(self, reader: Transaction, ctx: DueContext, until: datetime) -> list[DueStep]:
        steps: list[DueStep] = []
        for a in await _attendances(reader, ctx, AFTER_STATES):
            if not self._waiting(a):
                continue
            when = _latest(a.ends_at, TRANSCRIPT_CHECK, 12, until)
            if when is not None:
                steps.append(DueStep(str(a.id), when))
        return steps

    async def next_after(
        self, reader: Transaction, ctx: DueContext, after: datetime
    ) -> datetime | None:
        times = [
            when
            for a in await _attendances(reader, ctx, AFTER_STATES)
            if self._waiting(a) and (when := _next(a.ends_at, TRANSCRIPT_CHECK, 12, after))
        ]
        return min(times) if times else None

    async def run(self, action: Action, ctx: DueContext, step: DueStep) -> None:
        attendance = await action.get(MeetingAttendance, UUID(step.subject))
        if attendance is None or not self._waiting(attendance):
            return
        flow_id = ctx.assistant.flow_id
        if attendance.state is AttendanceState.JOINING:
            await action.boundary.meetings.leave(attendance.meeting_ref, flow_id)
            await change_state(action, attendance, AttendanceState.FAILED, because="not_let_in")
            await tell_manager(action.boundary, attendance, "not_let_in")
            return
        if attendance.state is AttendanceState.ATTENDING:
            left = await action.boundary.meetings.leave(attendance.meeting_ref, flow_id)
            removed = left.removed_at is not None and left.removed_at < attendance.ends_at
            attendance = await change_state(
                action,
                attendance,
                AttendanceState.LEFT,
                because="organiser_removed" if removed else None,
                left_at=left.removed_at if removed else action.now,
            )
        transcript = await action.boundary.meetings.transcript(attendance.meeting_ref, flow_id)
        if transcript is None:
            if action.now >= attendance.ends_at + WAIT_FOR_TRANSCRIPT:
                await change_state(
                    action, attendance, AttendanceState.FAILED, because="no_transcript"
                )
                await tell_manager(action.boundary, attendance, "no_transcript")
            return
        recorded = await change_state(action, attendance, AttendanceState.WRITING_NOTES)
        meeting = await keep_attended(action, ctx.assistant, recorded, transcript)
        current = await action.get(MeetingAttendance, attendance.id)
        assert current is not None
        await action.change(
            "assistant.attendance_transcript_kept",
            current,
            current.evolve(meeting_id=meeting.id),
            details={"meeting_id": str(meeting.id)},
        )
        log_event(
            "assistant.transcript_arrived",
            attendance_id=str(attendance.id),
            meeting_id=str(meeting.id),
            parts=len(meeting.parts),
        )


for _kind in (MeetingWithdrawals(), MeetingStart(), MeetingAdmission(), MeetingEnd()):
    DUE_STEPS.register(_kind)


# The service ------------------------------------------------------------------------------------


class AttendanceService:
    def __init__(
        self,
        boundary: Boundary,
        store: Store,
        requests: RequestService,
        meetings: MeetingService,
    ) -> None:
        self.boundary = boundary
        self.store = store
        self.requests = requests
        self.meetings = meetings

    # The manager ------------------------------------------------------------------------

    async def _names(self, ids: set[str]) -> dict[str, str | None]:
        names: dict[str, str | None] = {}
        for person_id in ids:
            person = await self.boundary.people.person(person_id)
            names[person_id] = person.display_name if person else None
        return names

    async def upcoming(self, viewer: Viewer) -> dict[str, Any]:
        """The manager's upcoming meetings, as the meeting service gives them, each with
        Attend for me."""
        async with self.store.transaction(viewer.organisation_id) as tx:
            assistant = await my_assistant(tx, viewer)
            allowed = await meeting_notes_allowed(tx, viewer.organisation_id)
            bookings = await tx.find(MeetingAttendance, assistant_id=assistant.id)
        listed = await self.boundary.meetings.upcoming(assistant.manager_id)
        names = await self._names({m.organiser_id for m in listed})
        current = {a.meeting_ref: a for a in bookings if a.state in BOOKED_STATES}
        meetings = []
        for info in sorted(listed, key=lambda m: m.starts_at):
            booking = current.get(info.id)
            meetings.append(
                {
                    "id": info.id,
                    "title": info.title,
                    "organiser": {
                        "id": info.organiser_id,
                        "display_name": names.get(info.organiser_id),
                    },
                    "organised_by_you": info.organiser_id == assistant.manager_id,
                    "starts_at": info.starts_at.isoformat(),
                    "ends_at": info.ends_at.isoformat(),
                    "series_id": info.series_id,
                    "booking": (
                        {"id": str(booking.id), "state": booking.state.value} if booking else None
                    ),
                    "attend_for_me": allowed and booking is None,
                }
            )
        return {"meeting_notes_allowed": allowed, "meetings": meetings}

    async def attend(
        self, viewer: Viewer, meeting_ref: str, series: bool = False
    ) -> dict[str, Any]:
        """Attend for me, for one meeting or its series: the manager's own decision, so no
        approval. Refused with one sentence when meeting notes are not allowed."""
        async with self.store.transaction(viewer.organisation_id) as tx:
            assistant = await my_assistant(tx, viewer)
            if not await meeting_notes_allowed(tx, viewer.organisation_id):
                raise NotAllowedValue(NOTES_NOT_ALLOWED)
        listed = await self.boundary.meetings.upcoming(assistant.manager_id)
        chosen = next((m for m in listed if m.id == meeting_ref), None)
        if chosen is None:
            raise NotFound()
        wanted = [chosen]
        if series and chosen.series_id is not None:
            wanted = [m for m in listed if m.series_id == chosen.series_id]

        async def work(action: Action) -> list[MeetingAttendance]:
            mine = await my_assistant(action.tx, viewer)
            action.assistant_id = mine.id
            if not await meeting_notes_allowed(action, viewer.organisation_id):
                raise NotAllowedValue(NOTES_NOT_ALLOWED)
            booked: list[MeetingAttendance] = []
            for info in wanted:
                if await is_booked(action, mine, info.id):
                    if info.id == chosen.id and not series:
                        raise NotAllowedValue(ALREADY_BOOKED)
                    continue
                if info.ends_at <= action.now:
                    continue
                booked.append(await book(action, mine, info, "manager"))
            return booked

        booked = await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )
        return {
            "booked": [await self._booking_view(a, {m.id: m for m in listed}) for a in booked],
            **await self.upcoming(viewer),
        }

    async def _booking_view(
        self, attendance: MeetingAttendance, listed: dict[str, MeetingInfo]
    ) -> dict[str, Any]:
        info = listed.get(attendance.meeting_ref)
        names = await self._names({attendance.organiser_id})
        return {
            "id": str(attendance.id),
            "meeting_ref": attendance.meeting_ref,
            "title": info.title if info else None,
            "organiser": {
                "id": attendance.organiser_id,
                "display_name": names.get(attendance.organiser_id),
            },
            "starts_at": attendance.starts_at.isoformat(),
            "ends_at": attendance.ends_at.isoformat(),
            "series_id": attendance.series_id,
            "booked_by": attendance.booked_by,
            "state": attendance.state.value,
            "because": attendance.because,
            "meeting_id": str(attendance.meeting_id) if attendance.meeting_id else None,
        }

    async def bookings(self, viewer: Viewer) -> dict[str, Any]:
        async with self.store.transaction(viewer.organisation_id) as tx:
            assistant = await my_assistant(tx, viewer)
            found = await find_visible(tx, viewer, MeetingAttendance, assistant_id=assistant.id)
        listed = {m.id: m for m in await self.boundary.meetings.upcoming(assistant.manager_id)}
        found.sort(key=lambda a: a.starts_at)
        return {"bookings": [await self._booking_view(a, listed) for a in found]}

    async def booking(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        async with self.store.transaction(viewer.organisation_id) as tx:
            attendance = await get_visible(tx, viewer, MeetingAttendance, parse_id(raw_id))
            assistant = await my_assistant(tx, viewer)
            if attendance.assistant_id != assistant.id:
                raise NotFound()
        listed = {m.id: m for m in await self.boundary.meetings.upcoming(assistant.manager_id)}
        return await self._booking_view(attendance, listed)

    # The assistant's proposal: the attend_meeting tool -------------------------------------

    async def propose_attend(
        self, action: Action, assistant: AssistantRecord, work: PieceOfWork, c: CheckedRequest
    ) -> str | None:
        """Refused with a reason when meeting notes are not allowed, the manager cannot see
        the meeting, or the assistant is already booked for it; otherwise it goes through
        the approval check as "Attend a meeting and take notes"."""
        if not await meeting_notes_allowed(action, assistant.organisation_id):
            return refusal(c.index, "meeting notes are not allowed in the organisation")
        wanted = c.values["meeting"].strip()
        listed = await self.boundary.meetings.upcoming(assistant.manager_id)
        info = next((m for m in listed if m.id == wanted), None)
        if info is None:
            return refusal(c.index, "the manager cannot see the meeting it names")
        if await is_booked(action, assistant, info.id):
            return refusal(c.index, "the assistant is already booked for that meeting")
        await self.requests.propose(
            action,
            assistant,
            request_kind=ATTEND_REQUEST,
            effect=f"Attend {info.title} and take notes.",
            subject_kind="meeting",
            subject_id=info.id,
            payload={"meeting": info.model_dump(mode="json")},
            work_id=work.id,
        )
        return None

    # The organiser: Requests for you ------------------------------------------------------

    async def _ask_of(
        self, tx: Transaction, viewer: Viewer, raw_id: str
    ) -> tuple[PersonRequest, MeetingAttendance]:
        ask = await get_visible(tx, viewer, PersonRequest, parse_id(raw_id))
        if ask.person_id != viewer.person_id or ask.asks != "attend":
            raise NotFound()
        attendance = await tx.get(MeetingAttendance, UUID(ask.subject_id))
        if attendance is None:
            raise NotFound()
        return ask, attendance

    @staticmethod
    def _request_state(ask: PersonRequest, attendance: MeetingAttendance) -> str:
        if ask.state is PersonRequestState.SENT:
            return "waiting"
        if ask.state is PersonRequestState.WITHDRAWN:
            return "withdrawn"
        if attendance.because == "organiser_removed":
            return "removed"
        if attendance.state is AttendanceState.DECLINED:
            return "declined"
        return "accepted"

    async def _request_view(
        self, ask: PersonRequest, attendance: MeetingAttendance, now: datetime
    ) -> dict[str, Any]:
        """What the organiser sees: the meeting, whose assistant asks and the state of the
        request; nothing of the transcript or the notes."""
        title = await title_of(self.boundary, attendance)
        manager: Person | None = await self.boundary.people.person(attendance.manager_id)
        state = self._request_state(ask, attendance)
        removable = state == "accepted" and now < attendance.ends_at and attendance.state in (
            AttendanceState.BOOKED, AttendanceState.JOINING, AttendanceState.ATTENDING
        )  # fmt: skip
        return {
            "id": str(ask.id),
            "meeting": {
                "title": title,
                "starts_at": attendance.starts_at.isoformat(),
                "ends_at": attendance.ends_at.isoformat(),
            },
            "asked_by": attendee_name(manager.display_name) if manager else None,
            "state": state,
            "can_accept": state == "waiting" and now < attendance.starts_at,
            "can_decline": state == "waiting",
            "can_remove": removable,
        }

    async def organiser_requests(self, viewer: Viewer) -> dict[str, Any]:
        async with self.store.transaction(viewer.organisation_id) as tx:
            asks = [
                a
                for a in await find_visible(tx, viewer, PersonRequest, person_id=viewer.person_id)
                if a.asks == "attend"
            ]
            pairs = []
            for ask in asks:
                attendance = await tx.get(MeetingAttendance, UUID(ask.subject_id))
                if attendance is not None:
                    pairs.append((ask, attendance))
        now = await self.boundary.clock.now()
        return {"requests": [await self._request_view(a, m, now) for a, m in pairs]}

    async def organiser_request(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        async with self.store.transaction(viewer.organisation_id) as tx:
            ask, attendance = await self._ask_of(tx, viewer, raw_id)
        return await self._request_view(ask, attendance, await self.boundary.clock.now())

    async def _answer(self, viewer: Viewer, raw_id: str, answer: str) -> dict[str, Any]:
        async def work(action: Action) -> tuple[MeetingAttendance, str | None]:
            ask, attendance = await self._ask_of(action.tx, viewer, raw_id)
            action.assistant_id = attendance.assistant_id
            if answer == "remove":
                return await self._remove(action, ask, attendance)
            if ask.state is not PersonRequestState.SENT:
                if answer == "accept" and attendance.state is AttendanceState.BOOKED:
                    return attendance, None  # pressed twice
                if answer == "decline" and attendance.state is AttendanceState.DECLINED:
                    return attendance, None
                raise NotAllowedValue(NOT_WAITING)
            if attendance.state is not AttendanceState.WAITING_FOR_ORGANISER:
                raise NotAllowedValue(NOT_WAITING)
            if answer == "accept":
                if action.now >= attendance.starts_at:
                    raise NotAllowedValue(STARTED)
                booked = await change_state(action, attendance, AttendanceState.BOOKED)
                await end_ask(action, attendance, PersonRequestState.ANSWERED)
                return booked, None
            declined = await change_state(
                action, attendance, AttendanceState.DECLINED, because="organiser_declined"
            )
            await end_ask(action, attendance, PersonRequestState.ANSWERED)
            return declined, "organiser_declined"

        attendance, because = await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )
        if because is not None:
            await tell_manager(self.boundary, attendance, because)
        return await self.organiser_request(viewer, raw_id)

    async def _remove(
        self, action: Action, ask: PersonRequest, attendance: MeetingAttendance
    ) -> tuple[MeetingAttendance, str | None]:
        """The organiser removes the assistant on Requests for you, until the meeting ends:
        it leaves where it is still in, and the notes use only what was said before."""
        if attendance.because == "organiser_removed":
            return attendance, None  # pressed twice
        if action.now >= attendance.ends_at:
            raise NotAllowedValue(ENDED)
        if attendance.state is AttendanceState.WAITING_FOR_ORGANISER:
            raise NotAllowedValue(NOT_ATTENDING)
        if attendance.state is AttendanceState.BOOKED:
            removed = await change_state(
                action, attendance, AttendanceState.DECLINED, because="organiser_removed"
            )
            return removed, "organiser_removed"
        if attendance.state not in (AttendanceState.JOINING, AttendanceState.ATTENDING):
            raise NotAllowedValue(NOT_ATTENDING)
        left = await change_state(
            action,
            attendance,
            AttendanceState.LEFT,
            because="organiser_removed",
            left_at=action.now,
        )
        assistant = await action.get(AssistantRecord, attendance.assistant_id or uuid4())
        if assistant is not None:
            await action.boundary.meetings.leave(attendance.meeting_ref, assistant.flow_id)
        return left, "organiser_removed"

    async def accept(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        return await self._answer(viewer, raw_id, "accept")

    async def decline(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        return await self._answer(viewer, raw_id, "decline")

    async def remove(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        return await self._answer(viewer, raw_id, "remove")

