"""Transcripts and the notes written from them (part 2, Epic 1).

A transcript arrives in one of two ways. The person who ran a meeting shares it
(any person in the organisation, for a manager with an assistant), with the two
ticks; writing its notes goes through Flow's approval check as "Write notes from
a shared transcript". Or the meeting service delivers it for a meeting the
assistant attended (:mod:`assistant.service.attendance`), whose notes need no
further approval. From the numbered parts onward there is one path:

* the meeting is set to writing and a meeting-notes piece of work is started,
  its pack holding the parts marked "data, not instructions";
* the ``meeting_notes`` tool is checked line by line: a line over its limit or
  naming a part that does not exist is refused alone, an unmatched owner is kept
  as "Owner not found", a statement about a project the manager does not follow
  is dropped;
* once the checks have passed, the transcript's text is removed (the numbers and
  times stay), the meeting is ready, each statement goes to the person who
  confirms its project, and the manager is told;
* when the notes cannot be written, the meeting fails with the reason and can be
  tried again while the text is kept.

The text is removed in three ways (BR-3): when the notes pass the checks, when
the organiser withdraws the transcript, and by the ``transcript_text_removal``
due step 7 days after it arrived at the latest. It is never in an audit record,
a notice or a log: records hold ids and states, notices name the meeting's title.
"""

from __future__ import annotations

import datetime as dt
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from assistant.boundary.interfaces import Boundary
from assistant.errors import NotAllowedValue, NotFound
from assistant.models.boundary import (
    ActionKind,
    ApprovalLevel,
    MeetingTranscript,
    PackInput,
    Person,
    ProjectRecord,
    WorkKind,
)
from assistant.models.records import (
    NOTE_LINE_LIMIT,
    TITLE_LIMIT,
    AssistantRecord,
    AttendanceState,
    DraftState,
    FollowedProject,
    Meeting,
    MeetingAttendance,
    MeetingNotes,
    MeetingState,
    NoteAction,
    NoteLine,
    NoteStatement,
    PieceOfWork,
    RequestRecord,
    RequestState,
    StatementDraft,
    StatementWording,
    WorkState,
)
from assistant.rules import meetings as rules
from assistant.rules.input_pack import pack_label
from assistant.rules.requests import OPEN_STATES
from assistant.rules.tools import refusal
from assistant.rules.visibility import Viewer
from assistant.rules.work import one_sentence
from assistant.service.actions import Action, perform
from assistant.service.approval_levels import level_for
from assistant.service.assistants import current_assistant, my_assistant
from assistant.service.checks import CheckedRequest
from assistant.service.due import DUE_STEPS, DueContext, DueStep
from assistant.service.log import log_event
from assistant.service.notices import Tell, tell
from assistant.service.organisation import meeting_notes_allowed
from assistant.service.request_kinds import STATEMENT_CONFIRM, RequestKind, register
from assistant.service.requests import RequestService
from assistant.service.visibility import find_visible, get_visible, parse_id
from assistant.service.work_kinds import WORK_KINDS, PackContext, given_inputs
from assistant.store.interface import Store, Transaction

SUBJECT = "meeting"
WRITE_NOTES = "write_meeting_notes"
MEETING_INPUT_KIND = "meeting"
PARTS_INPUT_KIND = "transcript_parts"
WITHDRAWABLE = frozenset({MeetingState.SHARED, MeetingState.WRITING, MeetingState.FAILED})
WITHOUT_NOTES = WITHDRAWABLE
"""States in which the 7 days running out leaves the meeting removed, without notes."""


class ShareBody(BaseModel):
    """What an organiser shares: the transcript as a .vtt or .txt file (its name and its
    bytes in base64) or as pasted text, never both, with the two ticks."""

    model_config = ConfigDict(extra="forbid")

    title: str = ""
    date: dt.date
    manager_id: str
    project_ids: list[str] = []
    file_name: str | None = None
    file_base64: str | None = None
    pasted_text: str | None = None
    ran_it: bool = False
    """ "I ran this meeting"."""
    people_knew: bool = False
    """ "People in it knew it was recorded and used for notes"."""


def the(person: Person | None, fallback: str = "someone") -> str:
    return f"the {person.display_name}" if person else fallback


def organiser_view(meeting: Meeting) -> dict[str, Any]:
    """What the organiser sees of what they shared: its title and state only (BR-5)."""
    return {"id": str(meeting.id), "title": meeting.title, "state": meeting.state.value}


def without_text(meeting: Meeting, now: datetime) -> dict[str, Any]:
    """The changes that remove a meeting's transcript text: numbers and times stay."""
    return {"parts": [p.without_text() for p in meeting.parts], "text_removed_at": now}


# Starting the notes ---------------------------------------------------------------------


async def begin_writing(action: Action, assistant: AssistantRecord, meeting: Meeting) -> Meeting:
    """Set the meeting to writing and queue its meeting-notes piece of work. The work is
    started once the action is saved (the work service's ``advance``)."""
    piece = await action.add(
        "assistant.work_asked",
        PieceOfWork(
            organisation_id=assistant.organisation_id,
            assistant_id=assistant.id,
            created_at=action.now,
            work_kind=WorkKind.MEETING_NOTES,
        ),
        details={"work_kind": WorkKind.MEETING_NOTES.value, "meeting_id": str(meeting.id)},
    )
    writing = await action.change(
        "assistant.meeting_notes_started",
        meeting,
        meeting.evolve(state=MeetingState.WRITING, work_id=piece.id, reason=None),
        details={"work_id": str(piece.id)},
    )
    await set_attendance(action, writing, AttendanceState.WRITING_NOTES)
    return writing


async def set_attendance(
    action: Action, meeting: Meeting, state: AttendanceState, because: str | None = None
) -> None:
    """Keep an attended meeting's attendance record in step with its Meeting."""
    if meeting.attendance_id is None:
        return
    attendance = await action.get(MeetingAttendance, meeting.attendance_id)
    if attendance is None or attendance.state is state:
        return
    changes: dict[str, Any] = {"state": state}
    if because is not None:
        changes["because"] = because
    await action.change(
        "assistant.attendance_changed",
        attendance,
        attendance.evolve(**changes),
        details={"meeting_id": str(meeting.id)},
    )


async def _carry_out_write_notes(
    action: Action, assistant: AssistantRecord, request: RequestRecord, text: str | None
) -> dict[str, Any]:
    """The module's part of "Write notes from a shared transcript": start the notes."""
    assert request.subject_id is not None
    meeting = await action.get(Meeting, UUID(request.subject_id))
    assert meeting is not None
    writing = await begin_writing(action, assistant, meeting)
    return {"subject_version": writing.version}


register(
    RequestKind(
        WRITE_NOTES,
        ActionKind.WRITE_MEETING_NOTES,
        "Notes",
        _carry_out_write_notes,
        editable=False,
        subject_type=Meeting,
    )
)


# The pack of a meeting-notes piece of work -------------------------------------------------


async def meeting_of_work(reader: Transaction | Action, work: PieceOfWork) -> Meeting | None:
    found = await reader.find(Meeting, assistant_id=work.assistant_id, work_id=work.id)
    return found[0] if found else None


async def transcript_parts(ctx: PackContext) -> list[PackInput]:
    """The meeting and its numbered parts, as data. Each part has its number and its times
    or line numbers, and the speaker's name where one is given."""
    meeting = await meeting_of_work(ctx.reader, ctx.work)
    if meeting is None or not meeting.text_kept:
        return []
    names = {p.id: p.name for p in await ctx.boundary.work_data.projects(meeting.manager_id)}
    followed = await ctx.reader.find(FollowedProject, assistant_id=ctx.assistant.id)
    about = {
        "meeting": str(meeting.id),
        "title": meeting.title,
        "date": meeting.meeting_date.isoformat(),
        "projects_discussed": [names.get(p, p) for p in meeting.project_ids],
        "followed_projects": [f.project_name or names.get(f.project_id, "") for f in followed],
        "parts": len(meeting.parts),
    }
    inputs = [
        PackInput(
            label=pack_label(f"Meeting: {meeting.title}"),
            read_at=ctx.now,
            content=json.dumps(about, sort_keys=True),
            kind=MEETING_INPUT_KIND,
            subject_id=str(meeting.id),
        )
    ]
    for first, last, content in rules.part_lines(meeting.parts):
        inputs.append(
            PackInput(
                label=f"Transcript parts {first} to {last}",
                read_at=ctx.now,
                content=content,
                kind=PARTS_INPUT_KIND,
                subject_id=f"{first}-{last}",
            )
        )
    return inputs


WORK_KINDS.register(WorkKind.MEETING_NOTES, given_inputs, transcript_parts)


# Removing the text after 7 days ------------------------------------------------------------


class TranscriptTextRemoval:
    """BR-3: any text still kept 7 days after it arrived is removed, once. A meeting still
    shared, writing or failed is then removed without notes, and the manager is told. This
    runs while the assistant is paused or new too: the 7 days are the latest."""

    name = "transcript_text_removal"
    runs_while_paused = True

    async def due(self, reader: Transaction, ctx: DueContext, until: datetime) -> list[DueStep]:
        return [
            DueStep(str(m.id), m.text_arrived_at + rules.KEEP_TEXT_FOR)
            for m in await reader.find(Meeting, assistant_id=ctx.assistant.id)
            if m.text_kept and m.text_arrived_at + rules.KEEP_TEXT_FOR <= until
        ]

    async def next_after(
        self, reader: Transaction, ctx: DueContext, after: datetime
    ) -> datetime | None:
        times = [
            m.text_arrived_at + rules.KEEP_TEXT_FOR
            for m in await reader.find(Meeting, assistant_id=ctx.assistant.id)
            if m.text_kept and m.text_arrived_at + rules.KEEP_TEXT_FOR > after
        ]
        return min(times) if times else None

    async def run(self, action: Action, ctx: DueContext, step: DueStep) -> None:
        meeting = await action.get(Meeting, UUID(step.subject))
        if meeting is None or not meeting.text_kept:
            return
        without_notes = meeting.state in WITHOUT_NOTES
        changes = without_text(meeting, action.now)
        if without_notes:
            changes["state"] = MeetingState.REMOVED
        removed = await action.change(
            "assistant.transcript_text_removed",
            meeting,
            meeting.evolve(**changes),
            details={"because": "seven_days"},
        )
        if not without_notes:
            return
        await withdraw_requests(action, removed, "transcript_removed")
        await set_attendance(action, removed, AttendanceState.FAILED, "notes_failed")
        await tell(
            action.boundary,
            removed.organisation_id,
            [
                Tell(
                    removed.manager_id,
                    SUBJECT,
                    str(removed.id),
                    f"The transcript of {removed.title} was removed without notes.",
                )
            ],
        )


DUE_STEPS.register(TranscriptTextRemoval())


async def withdraw_requests(action: Action, meeting: Meeting, because: str) -> None:
    """Close what still waits about the meeting, e.g. its request for the notes."""
    found = await action.find(RequestRecord, subject_kind=SUBJECT, subject_id=str(meeting.id))
    for request in found:
        if request.state in OPEN_STATES:
            await action.change(
                "assistant.request_withdrawn",
                request,
                request.evolve(state=RequestState.WITHDRAWN, decided_at=action.now),
                details={"because": because},
            )


# A meeting the assistant attended, and notes that cannot be written ------------------------


async def keep_attended(
    action: Action,
    assistant: AssistantRecord,
    attendance: MeetingAttendance,
    transcript: MeetingTranscript,
) -> Meeting:
    """Keep the transcript of an attended meeting as a Meeting with numbered parts and start
    its notes at once: attending was already agreed, so no further approval is asked. Only
    what was said before the assistant left is used."""
    parts = rules.split_segments(transcript.segments, attendance.starts_at, attendance.left_at)
    meeting = await action.add(
        "assistant.transcript_delivered",
        Meeting(
            organisation_id=assistant.organisation_id,
            assistant_id=assistant.id,
            created_at=action.now,
            title=transcript.title,
            meeting_date=transcript.starts_at.date(),
            organiser_id=transcript.organiser_id,
            manager_id=assistant.manager_id,
            origin="attended",
            organiser_agreed=True,
            source_format="attended",
            parts=parts,
            text_arrived_at=action.now,
            attendance_id=attendance.id,
            manager_attended=transcript.manager_attended,
            state=MeetingState.SHARED,
        ),
        details={"parts": len(parts), "attendance_id": str(attendance.id)},
    )
    if not parts:
        return await fail_meeting(action, meeting, rules.EMPTY_TRANSCRIPT)
    return await begin_writing(action, assistant, meeting)


async def fail_meeting(action: Action, meeting: Meeting, reason: str) -> Meeting:
    """The notes could not be written: the manager sees why and can try again while the
    transcript is kept."""
    failed = await action.change(
        "assistant.meeting_notes_failed",
        meeting,
        meeting.evolve(state=MeetingState.FAILED, reason=reason),
    )
    await set_attendance(action, failed, AttendanceState.FAILED, "notes_failed")
    await tell(
        action.boundary,
        failed.organisation_id,
        [
            Tell(
                failed.manager_id,
                SUBJECT,
                str(failed.id),
                f"The notes of {failed.title} could not be written.",
            )
        ],
    )
    return failed


# The service ----------------------------------------------------------------------------------


class MeetingService:
    def __init__(self, boundary: Boundary, store: Store, requests: RequestService) -> None:
        self.boundary = boundary
        self.store = store
        self.requests = requests

    # Sharing --------------------------------------------------------------------------

    async def _projects(self, manager_id: str, project_ids: list[str]) -> list[str]:
        if len(project_ids) > rules.MAX_PROJECTS:
            raise NotAllowedValue(rules.TOO_MANY_PROJECTS)
        if not project_ids:
            return []
        visible = {p.id for p in await self.boundary.work_data.projects(manager_id)}
        wanted: list[str] = []
        for project_id in project_ids:
            if project_id not in visible:
                raise NotAllowedValue(rules.UNKNOWN_PROJECT)
            if project_id not in wanted:
                wanted.append(project_id)
        return wanted

    async def share(self, viewer: Viewer, body: ShareBody) -> dict[str, Any]:
        """An organiser shares a transcript. Each refusal has its own sentence, and nothing
        is kept when it is refused. Writing the notes is asked through the approval check."""
        org = viewer.organisation_id
        async with self.store.transaction(org) as tx:
            if not await meeting_notes_allowed(tx, org):
                raise NotAllowedValue(rules.NOTES_NOT_ALLOWED)
        if not (body.ran_it and body.people_knew):
            raise NotAllowedValue(rules.TICK_MISSING)
        title = body.title.strip()
        if not title:
            raise NotAllowedValue(rules.TITLE_NEEDED)
        if len(title) > TITLE_LIMIT:
            raise NotAllowedValue(rules.TITLE_TOO_LONG)
        try:
            source, text = rules.read_transcript(body.file_name, body.file_base64, body.pasted_text)
            parts = rules.split(source, text)
        except rules.TranscriptRefused as refused:
            raise NotAllowedValue(refused.sentence) from refused
        del text  # only the parts are kept, in memory until stored
        async with self.store.transaction(org) as tx:
            assistant = await current_assistant(tx, body.manager_id)
            if assistant is None:
                raise NotAllowedValue(rules.NO_ASSISTANT)
            level, _ = await level_for(tx, assistant, ActionKind.WRITE_MEETING_NOTES)
        if level is ApprovalLevel.NOT_ALLOWED:
            raise NotAllowedValue(rules.KIND_NOT_ALLOWED)
        project_ids = await self._projects(body.manager_id, body.project_ids)

        async def work(action: Action) -> tuple[Meeting, RequestRecord]:
            mine = await current_assistant(action, body.manager_id)
            if mine is None:
                raise NotAllowedValue(rules.NO_ASSISTANT)
            action.assistant_id = mine.id
            meeting = await action.add(
                "assistant.transcript_shared",
                Meeting(
                    organisation_id=org,
                    assistant_id=mine.id,
                    created_at=action.now,
                    title=title,
                    meeting_date=body.date,
                    organiser_id=viewer.person_id,
                    manager_id=mine.manager_id,
                    project_ids=project_ids,
                    origin="shared",
                    ran_it=True,
                    people_knew=True,
                    source_format=source,
                    parts=parts,
                    text_arrived_at=action.now,
                ),
                details={"parts": len(parts), "projects": len(project_ids)},
            )
            request = await self.requests.propose(
                action,
                mine,
                request_kind=WRITE_NOTES,
                effect=f"Write notes from the transcript of {title}.",
                subject_kind=SUBJECT,
                subject_id=str(meeting.id),
                subject_version=meeting.version,
                payload={"project_ids": project_ids},
            )
            if request.state is RequestState.DECLINED:
                raise NotAllowedValue(rules.KIND_NOT_ALLOWED)  # nothing is kept
            return meeting, request

        meeting, request = await perform(self.boundary, self.store, org, viewer.person_id, work)
        if request.state is RequestState.APPROVED:
            await self.requests.carry_out(org, request.id, viewer.person_id)
        async with self.store.transaction(org) as tx:
            current = await tx.get(Meeting, meeting.id)
        return organiser_view(current or meeting)

    async def shared_by_me(self, viewer: Viewer) -> dict[str, Any]:
        """What the organiser shared: each meeting's title and state, and nothing the
        assistant wrote (BR-5)."""
        async with self.store.transaction(viewer.organisation_id) as tx:
            found = await find_visible(
                tx, viewer, Meeting, organiser_id=viewer.person_id, origin="shared"
            )
        listed = sorted(found, key=lambda m: m.created_at, reverse=True)
        return {"transcripts": [organiser_view(m) for m in listed]}

    async def withdraw(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        """The organiser takes the transcript back before its notes are written: its text is
        removed, a waiting request for the notes is closed, and the manager is told. Notes
        that arrive afterwards are discarded."""
        meeting_id = parse_id(raw_id)

        async def work(action: Action) -> tuple[Meeting, bool]:
            meeting = await get_visible(action.tx, viewer, Meeting, meeting_id)
            if meeting.origin != "shared" or meeting.organiser_id != viewer.person_id:
                raise NotFound()
            if meeting.state is MeetingState.WITHDRAWN:
                return meeting, False  # pressed twice
            if meeting.state not in WITHDRAWABLE:
                raise NotAllowedValue(rules.NOT_WITHDRAWABLE)
            action.assistant_id = meeting.assistant_id
            withdrawn = await action.change(
                "assistant.transcript_withdrawn",
                meeting,
                meeting.evolve(
                    **without_text(meeting, action.now), state=MeetingState.WITHDRAWN, reason=None
                ),
                details={"state_was": meeting.state.value},
            )
            await withdraw_requests(action, withdrawn, "withdrawn")
            return withdrawn, True

        meeting, changed = await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )
        if changed:
            organiser = await self.boundary.people.person(viewer.person_id)
            whom = the(organiser, "The organiser")
            await tell(
                self.boundary,
                meeting.organisation_id,
                [
                    Tell(
                        meeting.manager_id,
                        SUBJECT,
                        str(meeting.id),
                        f"{whom[:1].upper()}{whom[1:]} withdrew the transcript of {meeting.title}.",
                    )
                ],
            )
        return organiser_view(meeting)

    # The manager's view of meetings ----------------------------------------------------

    async def _view(self, meeting: Meeting) -> dict[str, Any]:
        organiser = await self.boundary.people.person(meeting.organiser_id)
        return {
            "id": str(meeting.id),
            "title": meeting.title,
            "date": meeting.meeting_date.isoformat(),
            "organiser": {
                "id": meeting.organiser_id,
                "display_name": organiser.display_name if organiser else None,
            },
            "origin": meeting.origin,
            "project_ids": list(meeting.project_ids),
            "state": meeting.state.value,
            "reason": meeting.reason,
            "can_retry": meeting.state is MeetingState.FAILED and meeting.text_kept,
            "text_kept": meeting.text_kept,
            "notes_id": str(meeting.notes_id) if meeting.notes_id else None,
            "parts": [rules.part_place(p) for p in meeting.parts],
            "created_at": meeting.created_at.isoformat(),
        }

    async def meetings(self, viewer: Viewer) -> dict[str, Any]:
        async with self.store.transaction(viewer.organisation_id) as tx:
            assistant = await my_assistant(tx, viewer)
            found = await find_visible(tx, viewer, Meeting, assistant_id=assistant.id)
        listed = sorted(found, key=lambda m: m.created_at, reverse=True)
        return {"meetings": [await self._view(m) for m in listed]}

    async def meeting(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        async with self.store.transaction(viewer.organisation_id) as tx:
            meeting = await self._mine(tx, viewer, parse_id(raw_id))
        return await self._view(meeting)

    async def _mine(self, tx: Transaction, viewer: Viewer, meeting_id: UUID) -> Meeting:
        meeting = await get_visible(tx, viewer, Meeting, meeting_id)
        assistant = await my_assistant(tx, viewer)
        if meeting.assistant_id != assistant.id:
            raise NotFound()
        return meeting

    async def retry(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        """Try again notes that could not be written, while the transcript is kept."""
        meeting_id = parse_id(raw_id)

        async def work(action: Action) -> AssistantRecord:
            meeting = await self._mine(action.tx, viewer, meeting_id)
            assistant = await my_assistant(action.tx, viewer)
            action.assistant_id = assistant.id
            if meeting.state is MeetingState.WRITING:
                return assistant  # pressed twice
            if meeting.state is not MeetingState.FAILED:
                raise NotAllowedValue(rules.NOT_RETRYABLE)
            if not meeting.text_kept:
                raise NotAllowedValue(rules.TEXT_GONE)
            await begin_writing(action, assistant, meeting)
            return assistant

        assistant = await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )
        for hook in self.requests.after_carry_out:
            await hook(assistant)
        return await self.meeting(viewer, raw_id)

    async def work_finished(
        self, action: Action, assistant: AssistantRecord, piece: PieceOfWork
    ) -> None:
        """A meeting-notes piece of work that ends without notes fails its meeting, with the
        reason shown to the manager. Withdrawn or removed meetings stay as they are."""
        if piece.work_kind is not WorkKind.MEETING_NOTES:
            return
        meeting = await meeting_of_work(action, piece)
        if meeting is None or meeting.state is not MeetingState.WRITING:
            return
        if piece.state in (WorkState.REFUSED, WorkState.FAILED):
            reason = one_sentence(piece.reason, rules.NOTES_NOT_WRITTEN)
        else:
            reason = rules.NO_NOTES_RETURNED
        await fail_meeting(action, meeting, reason)

    async def take_notes(
        self, action: Action, assistant: AssistantRecord, work: PieceOfWork, c: CheckedRequest
    ) -> list[str] | str | None:
        """The ``meeting_notes`` tool: check every line on its own, keep the notes, remove
        the transcript's text and ask each statement's confirmer at once."""
        if work.work_kind is not WorkKind.MEETING_NOTES:
            return refusal(c.index, "notes are written only from a meeting's transcript")
        meeting = await meeting_of_work(action, work)
        if meeting is None or c.values["meeting"].strip() != str(meeting.id):
            return refusal(c.index, "it names a meeting this work is not about")
        if meeting.state is MeetingState.WITHDRAWN:
            log_event("assistant.meeting_notes_discarded", meeting_id=str(meeting.id))
            return refusal(c.index, "the transcript was withdrawn, so its notes were discarded")
        if meeting.state is not MeetingState.WRITING or not meeting.text_kept:
            return refusal(c.index, "the meeting's notes are no longer being written")
        numbers = {p.number for p in meeting.parts}
        refused: list[str] = []

        summary: NoteLine | None = None
        if "summary" in c.values:
            checked = rules.check_plain_line("summary", None, c.values["summary"], numbers)
            summary = checked.line
            refused += [checked.refused] if checked.refused else []
        lines: dict[str, list[NoteLine]] = {"decisions": [], "open_points": []}
        kinds: tuple[tuple[str, rules.LineKind], ...] = (
            ("decisions", "decision"),
            ("open_points", "open point"),
        )
        for field, kind in kinds:
            for position, raw in enumerate(c.values.get(field, []), start=1):
                checked = rules.check_plain_line(kind, position, raw, numbers)
                if checked.line is not None:
                    lines[field].append(checked.line)
                elif checked.refused:
                    refused.append(checked.refused)
        actions: list[NoteAction] = []
        for position, raw in enumerate(c.values.get("actions", []), start=1):
            action_line, reason = await self._action(raw, position, numbers, assistant)
            if action_line is not None:
                actions.append(action_line)
            elif reason:
                refused.append(reason)
        statements, dropped, reasons = await self._statements(
            action, assistant, c.values.get("statements", []), numbers
        )
        refused += reasons

        notes = await action.add(
            "assistant.meeting_notes_kept",
            MeetingNotes(
                organisation_id=assistant.organisation_id,
                assistant_id=assistant.id,
                created_at=action.now,
                meeting_id=meeting.id,
                title=meeting.title,
                meeting_date=meeting.meeting_date,
                organiser_id=meeting.organiser_id,
                project_ids=list(meeting.project_ids),
                origin=meeting.origin,
                summary=summary,
                decisions=lines["decisions"],
                actions=actions,
                open_points=lines["open_points"],
                statements=statements,
                parts=[p.without_text() for p in meeting.parts],
                manager_attended=meeting.manager_attended,
                dropped_statements=dropped,
            ),
            details={
                "meeting_id": str(meeting.id),
                "decisions": len(lines["decisions"]),
                "actions": len(actions),
                "open_points": len(lines["open_points"]),
                "statements": len(statements),
                "refused": len(refused),
            },
        )
        ready = await action.change(
            "assistant.transcript_text_removed",
            meeting,
            meeting.evolve(
                **without_text(meeting, action.now),
                state=MeetingState.READY,
                notes_id=notes.id,
                reason=None,
            ),
            details={"because": "notes_written"},
        )
        await set_attendance(action, ready, AttendanceState.READY)
        await self._ask_confirmers(action, assistant, notes)
        await tell(
            action.boundary,
            ready.organisation_id,
            [
                Tell(
                    ready.manager_id,
                    SUBJECT,
                    str(ready.id),
                    f"The notes of {ready.title} are ready.",
                )
            ],
        )
        return refused

    async def _action(
        self, raw: dict[str, Any], position: int, numbers: set[int], assistant: AssistantRecord
    ) -> tuple[NoteAction | None, str | None]:
        """An action with its text, owner and due date. An owner who does not match exactly
        one person or virtual employee is kept as "Owner not found"."""
        if set(raw) - {"text", "owner", "due_date", "parts"}:
            return None, rules.line_refusal("action", position, rules.UNKNOWN_FIELD)
        problem = rules.text_problem(raw.get("text"), "text", NOTE_LINE_LIMIT)
        if problem:
            return None, rules.line_refusal("action", position, problem)
        parts, problem = rules.parts_problem(raw.get("parts"), numbers)
        if problem or parts is None:
            return None, rules.line_refusal("action", position, problem or "it names no part")
        due, problem = rules.due_date_of(raw.get("due_date"))
        if problem:
            return None, rules.line_refusal("action", position, problem)
        owner_name = raw.get("owner")
        owner_id: str | None = None
        if isinstance(owner_name, str) and owner_name.strip():
            found = [
                p
                for p in await self.boundary.people.find_by_name(
                    assistant.organisation_id, owner_name.strip()
                )
                if p.organisation_id == assistant.organisation_id
            ]
            owner_id = found[0].id if len(found) == 1 else None
        else:
            owner_name = None
        return (
            NoteAction(
                text=raw["text"].strip(),
                owner_name=owner_name.strip()[:200] if owner_name else None,
                owner_id=owner_id,
                due_date=due,
                parts=parts,
            ),
            None,
        )

    async def _statements(
        self,
        action: Action,
        assistant: AssistantRecord,
        raws: list[dict[str, Any]],
        numbers: set[int],
    ) -> tuple[list[NoteStatement], int, list[str]]:
        """Statements about followed projects; any other is dropped."""
        if not raws:
            return [], 0, []
        projects = await self.boundary.work_data.projects(assistant.manager_id)
        followed = {
            f.project_id: f for f in await action.find(FollowedProject, assistant_id=assistant.id)
        }
        kept: list[NoteStatement] = []
        refused: list[str] = []
        dropped = 0
        for position, raw in enumerate(raws, start=1):
            if set(raw) - {"project", "reason", "what_changed", "what_comes_next", "parts"}:
                refused.append(rules.line_refusal("statement", position, rules.UNKNOWN_FIELD))
                continue
            problem = rules.text_problem(raw.get("reason"), "reason", NOTE_LINE_LIMIT)
            for optional in ("what_changed", "what_comes_next"):
                if problem is None and raw.get(optional) is not None:
                    problem = rules.text_problem(
                        raw[optional], optional.replace("_", " "), NOTE_LINE_LIMIT
                    )
            if problem:
                refused.append(rules.line_refusal("statement", position, problem))
                continue
            parts, problem = rules.parts_problem(raw.get("parts"), numbers)
            if problem or parts is None:
                refused.append(rules.line_refusal("statement", position, problem or "no part"))
                continue
            project = _project_named(projects, raw.get("project"))
            if project is None or project.id not in followed:
                dropped += 1
                continue
            kept.append(
                NoteStatement(
                    project_id=project.id,
                    project_name=project.name,
                    wording=StatementWording(
                        reason=raw["reason"].strip(),
                        what_changed=(raw.get("what_changed") or "").strip(),
                        what_comes_next=(raw.get("what_comes_next") or "").strip(),
                    ),
                    parts=parts,
                )
            )
        return kept, dropped, refused

    async def _ask_confirmers(
        self, action: Action, assistant: AssistantRecord, notes: MeetingNotes
    ) -> None:
        """Each statement becomes a draft for the person who confirms its project's status,
        who is asked at once through the approval check. It carries no colour (BR-4)."""
        if not notes.statements:
            return
        followed = {
            f.project_id: f for f in await action.find(FollowedProject, assistant_id=assistant.id)
        }
        statements: list[NoteStatement] = []
        for statement in notes.statements:
            confirmer_id = followed[statement.project_id].confirmer_id
            for older in await action.find(
                StatementDraft, assistant_id=assistant.id, project_id=statement.project_id
            ):
                if older.state is DraftState.DRAFT:
                    await action.change(
                        "assistant.statement_draft_replaced",
                        older,
                        older.evolve(state=DraftState.REPLACED),
                    )
            draft = await action.add(
                "assistant.statement_drafted",
                StatementDraft(
                    organisation_id=assistant.organisation_id,
                    assistant_id=assistant.id,
                    created_at=action.now,
                    project_id=statement.project_id,
                    owner_id=confirmer_id,
                    wording=statement.wording,
                    source=pack_label(f"Notes of {notes.title}"),
                    author_id=assistant.flow_id,
                ),
                details={"project_id": statement.project_id, "notes_id": str(notes.id)},
            )
            confirmer = await self.boundary.people.person(confirmer_id)
            await self.requests.propose(
                action,
                assistant,
                request_kind=STATEMENT_CONFIRM,
                effect=f"Ask {the(confirmer, 'a person')} to confirm the status of "
                f"{statement.project_name}.",
                subject_kind="statement_draft",
                subject_id=str(draft.id),
                subject_version=draft.version,
                target_id=confirmer_id,
                payload={
                    "project_id": statement.project_id,
                    "project_name": statement.project_name,
                },
            )
            statements.append(statement.model_copy(update={"draft_id": draft.id}))
        current = await action.get(MeetingNotes, notes.id)
        assert current is not None
        await action.change(
            "assistant.meeting_notes_statements_asked",
            current,
            current.evolve(statements=statements),
            details={"statements": len(statements)},
        )


def _project_named(projects: list[ProjectRecord], wanted: Any) -> ProjectRecord | None:
    if not isinstance(wanted, str) or not wanted.strip():
        return None
    key = wanted.strip().casefold()
    found = [p for p in projects if p.id == wanted.strip() or p.name.casefold() == key]
    return found[0] if len(found) == 1 else None
