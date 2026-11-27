"""Meetings stand-in: the meeting service as Flow would reach it, with a scripted meeting.

The Operations director has three upcoming meetings: one they organise (part of a
weekly series), one the Programme lead organises, and one with a guest from
outside the organisation, where joining is refused. Three switches change how it
behaves: ``kept_waiting`` keeps the assistant waiting to be let in,
``join_refused`` refuses every join, and ``no_transcript`` delivers none. The
sample transcript of a vendor review is delivered 10 minutes after a meeting the
assistant attended ends, as the clock moves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from assistant.boundary.interfaces import Clock
from assistant.models.boundary import (
    JoinRequest,
    JoinResult,
    LeaveResult,
    MeetingInfo,
    MeetingTranscript,
    TranscriptSegment,
)
from standins.sample import (
    DEFAULT_START,
    FINANCE_LEAD,
    LEGAL_LEAD,
    OPERATIONS_DIRECTOR,
    PROGRAMME_LEAD,
    SamplePerson,
    sample_id,
)
from standins.sample_transcript import SAMPLE_LINES

TRANSCRIPT_DELAY = timedelta(minutes=10)
GUEST_REFUSAL = (
    "This meeting has a guest from outside the organisation, so the assistant cannot join."
)
REFUSED = "The meeting service did not let the assistant join."
UNKNOWN = "The meeting service does not know this meeting."


@dataclass
class SampleMeeting:
    key: str
    title: str
    organiser: SamplePerson
    starts_at: datetime
    ends_at: datetime
    attendees: set[str]
    series_key: str | None = None
    outside_guest: bool = False
    manager_attends: bool = True

    @property
    def id(self) -> str:
        return sample_id(f"meeting/{self.key}")

    @property
    def series_id(self) -> str | None:
        return sample_id(f"series/{self.series_key}") if self.series_key else None

    def info(self) -> MeetingInfo:
        return MeetingInfo(
            id=self.id,
            title=self.title,
            organiser_id=self.organiser.id,
            starts_at=self.starts_at,
            ends_at=self.ends_at,
            series_id=self.series_id,
        )


def sample_meetings(start: datetime = DEFAULT_START) -> list[SampleMeeting]:
    """Three upcoming meetings for the Operations director, from the day after ``start``."""
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    ops, legal, finance = OPERATIONS_DIRECTOR.id, LEGAL_LEAD.id, FINANCE_LEAD.id
    programme = PROGRAMME_LEAD.id

    def at(days: int, hour: int) -> datetime:
        return day + timedelta(days=days, hours=hour)

    return [
        SampleMeeting(
            "operations_review",
            "Weekly operations review",
            OPERATIONS_DIRECTOR,
            at(1, 10),
            at(1, 11),
            {ops, legal, finance},
            series_key="weekly_operations_review",
        ),
        SampleMeeting(
            "vendor_review",
            "Vendor review",
            PROGRAMME_LEAD,
            at(2, 14),
            at(2, 15),
            {ops, legal, finance, programme},
        ),
        SampleMeeting(
            "partner_briefing",
            "Partner briefing",
            OPERATIONS_DIRECTOR,
            at(3, 11),
            at(3, 12),
            {ops},
            outside_guest=True,
        ),
    ]


@dataclass
class _Attendance:
    request: JoinRequest
    admitted_at: datetime | None = None
    left_at: datetime | None = None
    removed_at: datetime | None = None
    refused: str | None = None


@dataclass
class StandInMeetings:
    clock: Clock
    meetings: dict[str, SampleMeeting] = field(default_factory=dict)
    kept_waiting: bool = False
    join_refused: bool = False
    no_transcript: bool = False
    cancelled: set[str] = field(default_factory=set)
    join_requests: list[JoinRequest] = field(default_factory=list)
    """Every join asked for, with the name the assistant was to be shown under."""
    left: list[tuple[str, str]] = field(default_factory=list)
    _attending: dict[tuple[str, str], _Attendance] = field(default_factory=dict)

    @classmethod
    def sample(cls, clock: Clock, start: datetime = DEFAULT_START) -> StandInMeetings:
        return cls(clock, {m.id: m for m in sample_meetings(start)})

    # The boundary ---------------------------------------------------------------

    async def upcoming(self, manager_id: str) -> list[MeetingInfo]:
        now = await self.clock.now()
        return [
            m.info()
            for m in sorted(self.meetings.values(), key=lambda m: m.starts_at)
            if manager_id in m.attendees and m.id not in self.cancelled and now < m.ends_at
        ]

    async def join(self, request: JoinRequest) -> JoinResult:
        self.join_requests.append(request)
        meeting = self.meetings.get(request.meeting_id)
        if meeting is None or meeting.id in self.cancelled:
            return JoinResult(outcome="refused", reason=UNKNOWN)
        key = (request.meeting_id, request.assistant_flow_id)
        now = await self.clock.now()
        current = self._attending.setdefault(key, _Attendance(request))
        if current.refused:
            return JoinResult(outcome="refused", reason=current.refused)
        if current.admitted_at is not None:
            return JoinResult(outcome="joined")
        if meeting.outside_guest:
            current.refused = GUEST_REFUSAL
        elif self.join_refused:
            current.refused = REFUSED
        if current.refused:
            return JoinResult(outcome="refused", reason=current.refused)
        if self.kept_waiting:
            return JoinResult(outcome="waiting")
        current.admitted_at = now
        return JoinResult(outcome="joined")

    async def leave(self, meeting_id: str, assistant_flow_id: str) -> LeaveResult:
        key = (meeting_id, assistant_flow_id)
        self.left.append(key)
        current = self._attending.get(key)
        if current is not None and current.left_at is None:
            current.left_at = await self.clock.now()
        return LeaveResult(left=True, removed_at=current.removed_at if current else None)

    async def transcript(
        self, meeting_id: str, assistant_flow_id: str
    ) -> MeetingTranscript | None:
        meeting = self.meetings.get(meeting_id)
        current = self._attending.get((meeting_id, assistant_flow_id))
        if meeting is None or current is None or current.admitted_at is None:
            return None
        if self.no_transcript or await self.clock.now() < meeting.ends_at + TRANSCRIPT_DELAY:
            return None
        heard_until = min(
            t for t in (current.left_at, current.removed_at, meeting.ends_at) if t is not None
        )
        return MeetingTranscript(
            meeting_id=meeting.id,
            title=meeting.title,
            organiser_id=meeting.organiser.id,
            starts_at=meeting.starts_at,
            ends_at=meeting.ends_at,
            manager_attended=meeting.manager_attends,
            segments=[s for s in sample_segments(meeting) if s.starts_at < heard_until],
        )

    # Demonstration controls -------------------------------------------------------

    def cancel(self, meeting_id: str) -> None:
        self.cancelled.add(meeting_id)

    async def remove_in_meeting(self, meeting_id: str, assistant_flow_id: str) -> None:
        """The organiser removes the assistant in the meeting itself."""
        current = self._attending.get((meeting_id, assistant_flow_id))
        if current is not None and current.removed_at is None:
            current.removed_at = await self.clock.now()

    def display_names(self, meeting_id: str) -> list[str]:
        """The names the assistant asked to be shown under in a meeting."""
        return [r.display_name for r in self.join_requests if r.meeting_id == meeting_id]


def sample_segments(meeting: SampleMeeting) -> list[TranscriptSegment]:
    """The sample transcript, one line every 5 minutes from a minute after the start."""
    segments = []
    for index, (speaker, text) in enumerate(SAMPLE_LINES):
        starts = meeting.starts_at + timedelta(minutes=1 + 5 * index)
        segments.append(
            TranscriptSegment(
                starts_at=starts,
                ends_at=starts + timedelta(seconds=30),
                speaker=speaker,
                text=text,
            )
        )
    return segments
