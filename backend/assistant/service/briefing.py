"""The briefing (part 2, Story 2.1).

When the briefing is due on the manager's schedule (the ``briefing`` due step,
once per due time) or the manager asks for it, the module works out the figures
of the sections the manager ticked and starts a briefing piece of work with them
in the input pack. The figures come from part 1's records and Flow's boundary
data, counted as part 1 defines them (BR-6); the assistant only adds notes.

The schedule and the sections are kept in one :class:`BriefingSettings` record
per assistant, set in Settings next to the schedule; without one the briefing is
weekly on Mondays at 08:00 in the manager's time zone, with every section.

The assistant's notes come back as the one deliverable of the finished work. A
note that cites nothing, or an input that was not in the pack, refuses the
briefing; the assistant is asked once more with the reasons, and after a second
refusal the manager sees the figures alone and "The notes could not be prepared."
A briefing that is ready becomes accepted when the manager opens it (there is no
Accept button). Sent back with a comment, also once accepted, it is sent back, a
new version is prepared and both are kept; it is ready again when that arrives.
"""

from __future__ import annotations

import json
from datetime import datetime
from datetime import time as TimeOfDay
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict

from assistant.boundary.interfaces import Boundary
from assistant.errors import NotAllowedValue
from assistant.models.boundary import PackInput, WorkKind
from assistant.models.preferences import Weekday
from assistant.models.records import (
    ActionItem,
    AssistantRecord,
    AssistantState,
    Briefing,
    BriefingFigures,
    BriefingMeeting,
    BriefingFrequency,
    BriefingSectionKey,
    BriefingSettings,
    BriefingState,
    BriefingVersion,
    DecisionPaper,
    MeetingNotes,
    PieceOfWork,
)
from assistant.rules import briefing as rules
from assistant.rules.input_pack import pack_label
from assistant.rules.settings import Settings
from assistant.rules.visibility import Viewer
from assistant.service.actions import Action, perform
from assistant.service.assistants import my_assistant
from assistant.service.due import DUE_STEPS, DueContext, DueStep
from assistant.service.meeting_notes import NOTES_INPUT_KIND, confirmed_of, notes_text
from assistant.service.notices import Tell, tell
from assistant.service.requests import RequestService
from assistant.service.setup import settings_for
from assistant.service.status import StatusService
from assistant.service.visibility import find_visible, get_visible, parse_id
from assistant.service.work import WorkService
from assistant.service.work_kinds import WORK_KINDS, PackContext, given_inputs
from assistant.store.interface import Store, Transaction

SUBJECT = "briefing"
FIGURES_INPUT_KIND = "briefing_figures"
FIGURES_LABEL = "Figures: {title}"
REFUSALS_INPUT_KIND = "refusals"
REFUSALS_LABEL = "Why the briefing was refused"
COMMENT_INPUT_KIND = "comment"
COMMENT_LABEL = "The manager's comment"
PREVIOUS_INPUT_KIND = "previous_notes"
PREVIOUS_LABEL = "Your notes in the version sent back"
READY_NOTICE = "Your briefing is ready."
NOT_ASKED = "The briefing was refused because this work was not asked for one."


class BriefingSettingsChange(BaseModel):
    """What the manager sets in Settings next to the briefing's schedule. Anything left out
    stays as it is; sections not named keep their tick."""

    model_config = ConfigDict(extra="forbid")

    frequency: BriefingFrequency | None = None
    day: Weekday | None = None
    time: TimeOfDay | None = None
    sections: dict[str, bool] | None = None


def settings_id(assistant: AssistantRecord) -> UUID:
    return uuid5(NAMESPACE_URL, f"https://ea-002.invalid/briefing-settings/{assistant.id}")


async def briefing_settings_of(
    reader: Transaction | Action, assistant: AssistantRecord
) -> BriefingSettings | None:
    return await reader.get(BriefingSettings, settings_id(assistant))


def since_taken_on(assistant: AssistantRecord) -> datetime:
    return assistant.manager_since or assistant.created_at


async def schedule_for(
    reader: Transaction | Action, assistant: AssistantRecord, settings: Settings
) -> rules.Schedule:
    setting = await briefing_settings_of(reader, assistant)
    return rules.schedule_of(setting, settings.time_zone, since_taken_on(assistant))


def figures_label(key: BriefingSectionKey) -> str:
    return pack_label(FIGURES_LABEL.format(title=rules.SECTION_TITLES[key]))


# Working out the figures --------------------------------------------------------------------


async def work_out_figures(
    reader: Transaction | Action,
    boundary: Boundary,
    store: Store,
    assistant: AssistantRecord,
    settings: Settings,
    sections: list[BriefingSectionKey],
    now: datetime,
) -> BriefingFigures:
    """The figures of each figure section held, from part 1's records and Flow's data."""
    figures: dict[str, Any] = {}
    manager_id = assistant.manager_id
    if "action_items" in sections:
        items = await reader.find(ActionItem, assistant_id=assistant.id)
        figures["action_items"] = rules.action_item_figures(items, now, settings)
    if "assignments" in sections:
        assignments = await boundary.work_data.assignments(manager_id)
        figures["assignments"] = rules.assignment_figures(assignments)
    if "spend" in sections:
        figures["spend"] = rules.spend_figures(await boundary.work_data.projects(manager_id))
    if "decisions" in sections:
        papers = await reader.find(DecisionPaper, assistant_id=assistant.id)
        figures["decisions_waiting"] = rules.decisions_waiting(papers)
    if "project_status" in sections:
        status = StatusService(boundary, store, RequestService(boundary, store))
        visible = await status.visible_projects(manager_id)
        lines = await status.lines(reader, assistant, visible, settings)
        figures["project_status"] = [line for _, line in lines]
    return BriefingFigures(**figures)


def figures_of_section(figures: BriefingFigures, key: BriefingSectionKey) -> Any:
    """One section's figures as plain data, or ``None`` when the section has none."""
    value: Any = {
        "action_items": figures.action_items,
        "assignments": figures.assignments,
        "spend": figures.spend,
        "decisions": figures.decisions_waiting,
        "project_status": figures.project_status,
    }.get(key)
    if value is None:
        return None
    if isinstance(value, list):
        return [v.model_dump(mode="json") for v in value]
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return {"decisions_waiting": value}


async def last_briefing(
    reader: Transaction | Action, assistant: AssistantRecord
) -> Briefing | None:
    found = await reader.find(Briefing, assistant_id=assistant.id)
    return max(found, key=lambda b: b.created_at) if found else None


async def prepare_briefing(
    action: Action,
    boundary: Boundary,
    store: Store,
    assistant: AssistantRecord,
    settings: Settings,
    made_by: Literal["schedule", "manager"],
    due_at: datetime | None = None,
) -> Briefing:
    """Work out the figures and start a briefing piece of work with them in its pack. The
    work is started once the action is saved (the work service's ``advance``)."""
    setting = await briefing_settings_of(action, assistant)
    previous = await last_briefing(action, assistant)
    since = previous.created_at if previous else None
    meetings = await kept_meetings_since(action, assistant, since)
    sections = rules.held_sections(rules.ticked_sections(setting), bool(meetings))
    figures = await work_out_figures(
        action, boundary, store, assistant, settings, sections, action.now
    )
    piece = await action.add(
        "assistant.work_asked",
        PieceOfWork(
            organisation_id=assistant.organisation_id,
            assistant_id=assistant.id,
            created_at=action.now,
            work_kind=WorkKind.BRIEFING,
            asked_by=assistant.manager_id if made_by == "manager" else None,
        ),
        details={"work_kind": WorkKind.BRIEFING.value},
    )
    return await action.add(
        "assistant.briefing_started",
        Briefing(
            organisation_id=assistant.organisation_id,
            assistant_id=assistant.id,
            created_at=action.now,
            made_by=made_by,
            due_at=due_at,
            since=since,
            sections=sections,
            figures=figures,
            meetings=meetings if "meetings" in sections else [],
            work_id=piece.id,
        ),
        details={"work_id": str(piece.id), "made_by": made_by, "sections": len(sections)},
    )


def notes_label(notes: MeetingNotes) -> str:
    """The label a meeting's notes have in a pack, as when the manager asks about them."""
    return pack_label(f"Meeting notes: {notes.title} ({notes.meeting_date.isoformat()})")


async def kept_meetings_since(
    reader: Transaction | Action, assistant: AssistantRecord, since: datetime | None
) -> list[BriefingMeeting]:
    """The meetings whose notes were kept since the last briefing (all kept notes for the
    first), oldest first, with their decisions and open points, each citing its notes."""
    kept = [
        n
        for n in await reader.find(MeetingNotes, assistant_id=assistant.id)
        if since is None or n.created_at > since
    ]
    kept.sort(key=lambda n: (n.meeting_date, n.created_at))
    return [
        BriefingMeeting(
            notes_id=n.id,
            title=n.title,
            meeting_date=n.meeting_date,
            decisions=[d.text for d in n.decisions],
            open_points=[o.text for o in n.open_points],
            source=notes_label(n),
        )
        for n in kept
    ]


# The pack of a briefing piece of work -----------------------------------------------------


async def briefing_of_work(reader: Transaction | Action, work: PieceOfWork) -> Briefing | None:
    found = await reader.find(Briefing, assistant_id=work.assistant_id, work_id=work.id)
    return found[0] if found else None


async def briefing_figures(ctx: PackContext) -> list[PackInput]:
    """Each figure section's figures, as the module worked them out, as data."""
    briefing = await briefing_of_work(ctx.reader, ctx.work)
    if briefing is None:
        return []
    inputs: list[PackInput] = []
    for key in briefing.sections:
        figures = figures_of_section(briefing.figures, key)
        if figures is None:
            continue
        inputs.append(
            PackInput(
                label=figures_label(key),
                read_at=briefing.created_at,
                content=json.dumps(figures, sort_keys=True),
                kind=FIGURES_INPUT_KIND,
                subject_id=key,
            )
        )
    return inputs


async def briefing_meetings(ctx: PackContext) -> list[PackInput]:
    """The notes of each meeting kept since the last briefing, as data, under the label the
    briefing's list cites them by. Notes deleted since are left out."""
    briefing = await briefing_of_work(ctx.reader, ctx.work)
    if briefing is None or rules.MEETINGS not in briefing.sections:
        return []
    inputs: list[PackInput] = []
    for meeting in briefing.meetings:
        notes = await ctx.reader.get(MeetingNotes, meeting.notes_id)
        if notes is None:
            continue
        inputs.append(
            PackInput(
                label=meeting.source,
                read_at=ctx.now,
                content=notes_text(notes, await confirmed_of(ctx.reader, notes)),
                kind=NOTES_INPUT_KIND,
                subject_id=str(notes.id),
            )
        )
    return inputs


WORK_KINDS.register(WorkKind.BRIEFING, given_inputs, briefing_figures, briefing_meetings)


# The assistant's notes ------------------------------------------------------------------


async def ask_for_notes(
    action: Action,
    assistant: AssistantRecord,
    briefing: Briefing,
    given: list[PackInput],
    asked_by: str | None,
    *,
    name: str,
    **changes: Any,
) -> Briefing:
    """A new briefing piece of work for the briefing's next version."""
    piece = await action.add(
        "assistant.work_asked",
        PieceOfWork(
            organisation_id=assistant.organisation_id,
            assistant_id=assistant.id,
            created_at=action.now,
            work_kind=WorkKind.BRIEFING,
            asked_by=asked_by,
            given_inputs=given,
        ),
        details={"work_kind": WorkKind.BRIEFING.value},
    )
    return await action.change(
        name,
        briefing,
        briefing.evolve(work_id=piece.id, **changes),
        details={"work_id": str(piece.id)},
    )


async def add_version(
    action: Action,
    briefing: Briefing,
    notes: dict[str, list[Any]],
    prepared: bool,
) -> Briefing:
    """The next version arrives: the briefing is ready (again) and the manager is told."""
    version = BriefingVersion(
        number=len(briefing.versions) + 1,
        notes=notes,
        notes_prepared=prepared,
        arrived_at=action.now,
    )
    ready = await action.change(
        "assistant.briefing_ready" if prepared else "assistant.briefing_notes_not_prepared",
        briefing,
        briefing.evolve(
            versions=[*briefing.versions, version],
            state=BriefingState.READY,
            work_id=None,
            refusals=0,
        ),
        details={"version": version.number, "notes_prepared": prepared},
    )
    assistant = await action.get(AssistantRecord, briefing.assistant_id or UUID(int=0))
    if assistant is not None:
        await tell(
            action.boundary,
            ready.organisation_id,
            [Tell(assistant.manager_id, SUBJECT, str(ready.id), READY_NOTICE)],
        )
    return ready


async def refuse_notes(
    action: Action,
    assistant: AssistantRecord,
    briefing: Briefing,
    piece: PieceOfWork,
    reasons: list[str],
) -> Briefing:
    """Asked once more with the reasons; refused again, the figures are shown alone."""
    refusals = briefing.refusals + 1
    if refusals >= rules.REFUSALS_BEFORE_FIGURES_ALONE:
        return await add_version(action, briefing, {}, prepared=False)
    given = [i for i in piece.given_inputs if i.kind != REFUSALS_INPUT_KIND]
    given.append(
        PackInput(
            label=REFUSALS_LABEL,
            read_at=action.now,
            content="\n".join(reasons),
            kind=REFUSALS_INPUT_KIND,
            subject_id=str(briefing.id),
        )
    )
    return await ask_for_notes(
        action,
        assistant,
        briefing,
        given,
        piece.asked_by,
        name="assistant.briefing_asked_again",
        refusals=refusals,
    )


async def take_briefing(
    action: Action, assistant: AssistantRecord, piece: PieceOfWork, raw: dict[str, Any]
) -> list[str]:
    """The briefing deliverable: checked against the sections held and the labels of the
    pack the work was given (BR-6). Returns why it was refused, if it was."""
    briefing = await briefing_of_work(action, piece)
    if briefing is None or piece.work_kind is not WorkKind.BRIEFING:
        return [NOT_ASKED]
    checked = rules.check_briefing(raw, briefing.sections, set(piece.input_labels))
    if checked.refused:
        await refuse_notes(action, assistant, briefing, piece, checked.refused)
        return checked.refused
    await add_version(action, briefing, dict(checked.notes), prepared=True)
    return []


async def briefing_work_finished(
    action: Action, assistant: AssistantRecord, piece: PieceOfWork
) -> None:
    """Briefing work that ended without notes (it failed, Flow refused it, or no briefing
    came back) counts as a refusal, so the briefing never waits for ever."""
    if piece.work_kind is not WorkKind.BRIEFING:
        return
    briefing = await briefing_of_work(action, piece)
    if briefing is None:
        return
    reason = piece.reason or rules.NO_BRIEFING_RETURNED
    await refuse_notes(action, assistant, briefing, piece, [reason])


# On the manager's schedule ----------------------------------------------------------------


class BriefingDue:
    """The briefing, on the manager's schedule: one piece of work for each due time."""

    name = "briefing"

    async def due(self, reader: Transaction, ctx: DueContext, until: datetime) -> list[DueStep]:
        schedule = await schedule_for(reader, ctx.assistant, ctx.settings)
        at = rules.latest_due(until, schedule)
        return [DueStep(SUBJECT, at)] if at is not None else []

    async def next_after(
        self, reader: Transaction, ctx: DueContext, after: datetime
    ) -> datetime | None:
        return rules.next_after(after, await schedule_for(reader, ctx.assistant, ctx.settings))

    async def run(self, action: Action, ctx: DueContext, step: DueStep) -> None:
        schedule = await schedule_for(action, ctx.assistant, ctx.settings)
        if step.due_at not in rules.briefing_times(step.due_at, schedule):
            return  # the schedule changed since: its own time has its own step
        await prepare_briefing(
            action, ctx.boundary, ctx.store, ctx.assistant, ctx.settings, "schedule", step.due_at
        )


DUE_STEPS.register(BriefingDue())


# What the manager sees and does -----------------------------------------------------------


class BriefingService:
    def __init__(self, boundary: Boundary, store: Store, work: WorkService) -> None:
        self.boundary = boundary
        self.store = store
        self.work = work
        work.deliverables[SUBJECT] = take_briefing
        work.on_finished.append(briefing_work_finished)

    async def _settings_view(
        self, reader: Transaction | Action, assistant: AssistantRecord, now: datetime
    ) -> dict[str, Any]:
        settings = await settings_for(reader, assistant)
        setting = await briefing_settings_of(reader, assistant)
        schedule = rules.schedule_of(setting, settings.time_zone, since_taken_on(assistant))
        ticked = rules.ticked_sections(setting)
        upcoming = rules.next_after(now, schedule)
        return {
            "frequency": schedule.frequency,
            "day": schedule.day.value,
            "time": schedule.at.strftime("%H:%M"),
            "time_zone": schedule.time_zone,
            "sections": [
                {"key": key, "title": rules.SECTION_TITLES[key], "ticked": key in ticked}
                for key in rules.SECTION_TITLES
            ],
            "next_briefing_at": upcoming.isoformat() if upcoming else None,
        }

    async def settings(self, viewer: Viewer) -> dict[str, Any]:
        """The briefing's schedule and its sections, as set in Settings."""
        now = await self.boundary.clock.now()
        async with self.store.transaction(viewer.organisation_id) as tx:
            assistant = await my_assistant(tx, viewer)
            return await self._settings_view(tx, assistant, now)

    async def change_settings(
        self, viewer: Viewer, change: BriefingSettingsChange
    ) -> dict[str, Any]:
        """Set the schedule (weekly, every two weeks, monthly or off, with the day and time)
        and tick or untick sections. A changed schedule counts from now."""
        if change.sections is not None:
            refusal = rules.sections_refusal(change.sections)
            if refusal:
                raise NotAllowedValue(refusal)

        async def work(action: Action) -> dict[str, Any]:
            assistant = await my_assistant(action.tx, viewer)
            action.assistant_id = assistant.id
            current = await briefing_settings_of(action, assistant)
            base = current or BriefingSettings(
                id=settings_id(assistant),
                organisation_id=assistant.organisation_id,
                assistant_id=assistant.id,
                created_at=action.now,
                set_at=since_taken_on(assistant),
            )
            changes: dict[str, Any] = {}
            for field in ("frequency", "day", "time"):
                value = getattr(change, field)
                if value is not None:
                    changes["at" if field == "time" else field] = value
            schedule_changed = any(getattr(base, k) != v for k, v in changes.items())
            if schedule_changed:
                changes["set_at"] = action.now
            if change.sections is not None:
                changes["sections"] = rules.apply_ticks(base.sections, change.sections)
            updated = base.evolve(**changes)
            details = {"schedule_changed": schedule_changed, "sections": len(updated.sections)}
            if current is None:
                await action.add("assistant.briefing_settings_set", updated, details=details)
            elif updated != current:
                await action.change("assistant.briefing_settings_set", current, updated, details)
            return await self._settings_view(action, assistant, action.now)

        return await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )

    async def briefing_now(self, viewer: Viewer) -> tuple[AssistantRecord, Briefing]:
        """The manager asks for the briefing: its figures are worked out at once and the
        assistant is asked for its notes. Call the work service's ``advance`` afterwards."""

        async def work(action: Action) -> tuple[AssistantRecord, Briefing]:
            assistant = await my_assistant(action.tx, viewer)
            if assistant.state is AssistantState.NEW:
                raise NotAllowedValue(rules.FORM_FIRST)
            action.assistant_id = assistant.id
            settings = await settings_for(action, assistant)
            briefing = await prepare_briefing(
                action, self.boundary, self.store, assistant, settings, "manager"
            )
            return assistant, briefing

        return await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )

    async def briefings(self, viewer: Viewer) -> dict[str, Any]:
        """The briefings, newest first, with when the next is due."""
        now = await self.boundary.clock.now()
        async with self.store.transaction(viewer.organisation_id) as tx:
            assistant = await my_assistant(tx, viewer)
            found = await find_visible(tx, viewer, Briefing, assistant_id=assistant.id)
            schedule = await schedule_for(tx, assistant, await settings_for(tx, assistant))
        ordered = sorted(found, key=lambda b: b.created_at, reverse=True)
        upcoming = rules.next_after(now, schedule)
        return {
            "count": len(ordered),
            "next_briefing_at": upcoming.isoformat() if upcoming else None,
            "briefings": [briefing_view(b) for b in ordered],
        }

    async def briefing(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        async with self.store.transaction(viewer.organisation_id) as tx:
            return briefing_view(await self._mine(tx, viewer, parse_id(raw_id)))

    async def _mine(self, tx: Transaction, viewer: Viewer, briefing_id: UUID) -> Briefing:
        briefing = await get_visible(tx, viewer, Briefing, briefing_id)
        await my_assistant(tx, viewer)
        return briefing

    async def open_briefing(self, viewer: Viewer, raw_id: str) -> dict[str, Any]:
        """The manager opens the briefing: one that is ready becomes accepted. Reading is
        enough, so there is no Accept button."""
        briefing_id = parse_id(raw_id)

        async def work(action: Action) -> Briefing:
            briefing = await self._mine(action.tx, viewer, briefing_id)
            action.assistant_id = briefing.assistant_id
            if briefing.state is not BriefingState.READY:
                return briefing
            return await action.change(
                "assistant.briefing_accepted",
                briefing,
                briefing.evolve(state=BriefingState.ACCEPTED),
                details={"version": len(briefing.versions)},
            )

        opened = await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )
        return briefing_view(opened)

    async def send_back(self, viewer: Viewer, raw_id: str, comment: str | None) -> dict[str, Any]:
        """Send the briefing back with a comment, also once it is accepted: it is sent back,
        a new version is prepared, and both are kept."""
        briefing_id = parse_id(raw_id)

        async def work(action: Action) -> tuple[AssistantRecord, Briefing]:
            briefing = await self._mine(action.tx, viewer, briefing_id)
            assistant = await my_assistant(action.tx, viewer)
            action.assistant_id = assistant.id
            if briefing.state not in (BriefingState.READY, BriefingState.ACCEPTED):
                raise NotAllowedValue(rules.BEING_PREPARED)
            refusal = rules.comment_refusal(comment)
            if refusal or comment is None:
                raise NotAllowedValue(refusal or rules.COMMENT_NEEDED)
            said = comment.strip()
            last = briefing.versions[-1]
            given = [
                PackInput(
                    label=COMMENT_LABEL,
                    read_at=action.now,
                    content=said,
                    kind=COMMENT_INPUT_KIND,
                    subject_id=str(briefing.id),
                ),
                PackInput(
                    label=PREVIOUS_LABEL,
                    read_at=action.now,
                    content=json.dumps(
                        {k: [n.model_dump() for n in v] for k, v in last.notes.items()},
                        sort_keys=True,
                    ),
                    kind=PREVIOUS_INPUT_KIND,
                    subject_id=str(briefing.id),
                ),
            ]
            sent_back = briefing.evolve(
                versions=[
                    *briefing.versions[:-1],
                    last.model_copy(update={"comment": said, "sent_back_at": action.now}),
                ],
                state=BriefingState.SENT_BACK,
                refusals=0,
            )
            # The record names the version only: never the comment (BR-10).
            sent = await ask_for_notes(
                action,
                assistant,
                sent_back,
                given,
                viewer.person_id,
                name="assistant.briefing_sent_back",
            )
            return assistant, sent

        assistant, sent = await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )
        await self.work.advance(assistant)
        return await self.briefing(viewer, str(sent.id))


def _note_view(note: Any) -> dict[str, Any]:
    return {"text": note.text, "sources": list(note.sources)}


def _version_view(version: BriefingVersion) -> dict[str, Any]:
    return {
        "number": version.number,
        "arrived_at": version.arrived_at.isoformat(),
        "notes_prepared": version.notes_prepared,
        "notes": {k: [_note_view(n) for n in v] for k, v in version.notes.items()},
        "comment": version.comment,
        "sent_back_at": version.sent_back_at.isoformat() if version.sent_back_at else None,
    }


def briefing_view(briefing: Briefing) -> dict[str, Any]:
    """The briefing as the manager sees it: each section's figures from the module, with the
    assistant's notes of the latest version beside them, each with the sources it cites.
    After a second refusal the figures stand alone, with "The notes could not be prepared."
    Every version is kept and listed. Opening it accepts it: there is no Accept action."""
    current = briefing.versions[-1] if briefing.versions else None
    notes = current.notes if current is not None and current.notes_prepared else {}
    sections = []
    for key in briefing.sections:
        shown: dict[str, Any] = {
            "key": key,
            "title": rules.SECTION_TITLES[key],
            "figures": figures_of_section(briefing.figures, key),
            "notes": [_note_view(n) for n in notes.get(key, [])],
        }
        if key == rules.MEETINGS:
            shown["meetings"] = [meeting_view(m) for m in briefing.meetings]
        sections.append(shown)
    return {
        "id": str(briefing.id),
        "state": briefing.state.value,
        "made_by": briefing.made_by,
        "due_at": briefing.due_at.isoformat() if briefing.due_at else None,
        "created_at": briefing.created_at.isoformat(),
        "version": current.number if current else None,
        "notes_prepared": current.notes_prepared if current else None,
        "message": (
            rules.NOTES_NOT_PREPARED if current is not None and not current.notes_prepared else None
        ),
        "sections": sections,
        "versions": [_version_view(v) for v in briefing.versions],
        "can_send_back": briefing.state in (BriefingState.READY, BriefingState.ACCEPTED),
    }


def meeting_view(meeting: BriefingMeeting) -> dict[str, Any]:
    """A meeting kept since the last briefing, citing its notes so the manager can open them."""
    return {
        "notes_id": str(meeting.notes_id),
        "title": meeting.title,
        "date": meeting.meeting_date.isoformat(),
        "decisions": list(meeting.decisions),
        "open_points": list(meeting.open_points),
        "source": meeting.source,
    }
