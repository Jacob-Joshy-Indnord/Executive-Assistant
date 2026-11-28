"""Keeping signals (part 2, Epic 3: Signals and lessons).

A signal is kept whenever a draft is edited, approved or declined, a briefing or decision
paper is sent back, notes are corrected, an owner corrects a statement, or an item ends on
time or late. It may hold the before and after wording, so:

* it is its manager's only (see :mod:`assistant.rules.visibility`) and is removed when the
  assistant is handed over or ended;
* its change record and the log line written for it hold only ids and kinds, never the
  wording (the audited :class:`~assistant.service.actions.Action` writes ids and states);
* it never holds transcript text: wording that a transcript still kept contains, or that
  contains a part of one, is left out.

The services that see these moments call the helpers here inside their own action, so the
signal is kept in the same all-or-nothing change as what it is about.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from assistant.models.boundary import ActionKind
from assistant.models.records import (
    SIGNAL_TEXT_LIMIT,
    ActionItem,
    AssistantRecord,
    Meeting,
    MeetingNotes,
    NoteAction,
    Signal,
    SignalKind,
    StatementWording,
)
from assistant.rules.working_time import local_date
from assistant.service.actions import Action
from assistant.service.setup import settings_for


def without_transcript(text: str | None, transcript: Iterable[str]) -> str | None:
    """``text`` cut to its limit, or ``None`` when it holds transcript text."""
    if text is None or not text.strip():
        return None
    said = text.strip()
    for part in transcript:
        part = part.strip()
        if part and (said in part or part in said):
            return None
    return said[:SIGNAL_TEXT_LIMIT]


async def keep_signal(
    action: Action,
    assistant: AssistantRecord,
    signal_kind: SignalKind,
    *,
    subject_kind: str,
    subject_id: str | None = None,
    action_kind: ActionKind | None = None,
    before: str | None = None,
    after: str | None = None,
    said: str | None = None,
    transcript: Iterable[str] = (),
) -> Signal:
    """Keep one signal; its record names the signal's kind and subject, never its wording."""
    texts = list(transcript)
    signal = Signal(
        organisation_id=assistant.organisation_id,
        assistant_id=assistant.id,
        created_at=action.now,
        signal_kind=signal_kind,
        subject_kind=subject_kind[:50],
        subject_id=subject_id,
        action_kind=action_kind,
        before=without_transcript(before, texts),
        after=without_transcript(after, texts),
        said=without_transcript(said, texts),
    )
    details: dict[str, str] = {
        "signal_kind": signal_kind.value,
        "subject_kind": signal.subject_kind,
    }
    if subject_id is not None:
        details["subject_id"] = subject_id
    if action_kind is not None:
        details["action_kind"] = action_kind.value
    return await action.add("assistant.signal_kept", signal, details=details)


# Wording, as a signal holds it -------------------------------------------------------------


def statement_words(wording: StatementWording, colour: str | None = None) -> str:
    lines = [
        f"Colour: {colour}" if colour else "",
        f"Reason: {wording.reason}",
        f"What changed: {wording.what_changed}" if wording.what_changed else "",
        f"What comes next: {wording.what_comes_next}" if wording.what_comes_next else "",
    ]
    return "\n".join(line for line in lines if line)


def note_action_words(line: NoteAction) -> str:
    parts = [line.text]
    if line.owner_name:
        parts.append(f"Owner: {line.owner_name}")
    if line.due_date:
        parts.append(f"Due: {line.due_date.isoformat()}")
    return "\n".join(parts)


async def transcript_of(action: Action, meeting_id: UUID | None) -> list[str]:
    """The text a meeting's transcript still holds, which no signal may hold."""
    if meeting_id is None:
        return []
    meeting = await action.get(Meeting, meeting_id)
    if meeting is None:
        return []
    texts = [p.text for p in meeting.parts if p.text]
    return texts + [p.speaker for p in meeting.parts if p.speaker]


async def transcript_for_draft(
    action: Action, assistant: AssistantRecord, draft_id: UUID | None
) -> list[str]:
    """For a statement drafted from meeting notes: that meeting's transcript text."""
    if draft_id is None:
        return []
    texts: list[str] = []
    for notes in await action.find(MeetingNotes, assistant_id=assistant.id):
        if any(s.draft_id == draft_id for s in notes.statements):
            texts.extend(await transcript_of(action, notes.meeting_id))
    return texts


# The moments -----------------------------------------------------------------------------


async def item_ended(action: Action, assistant: AssistantRecord, item: ActionItem) -> Signal:
    """An item ended: on time when it ended on or before its due date, in the manager's time
    zone; otherwise late."""
    settings = await settings_for(action, assistant)
    on_time = local_date(action.now, settings) <= item.due_date
    return await keep_signal(
        action,
        assistant,
        SignalKind.ITEM_ON_TIME if on_time else SignalKind.ITEM_LATE,
        subject_kind="action_item",
        subject_id=str(item.id),
    )


async def notes_corrected(
    action: Action,
    assistant: AssistantRecord,
    notes: MeetingNotes,
    before: NoteAction,
    after: NoteAction | None,
) -> Signal:
    """The manager corrected an action of the notes (edited it, or removed it)."""
    return await keep_signal(
        action,
        assistant,
        SignalKind.NOTES_CORRECTED,
        subject_kind="meeting_notes",
        subject_id=str(notes.id),
        before=note_action_words(before),
        after=note_action_words(after) if after is not None else None,
        transcript=await transcript_of(action, notes.meeting_id),
    )
