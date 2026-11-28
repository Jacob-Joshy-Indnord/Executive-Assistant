"""The foundation's addresses under ``/api/assistant``.

Each address reads one thing and applies BR-8 through the visibility service,
so anything that is not the person's own answers exactly like something that
does not exist. Later epics add their addresses here, each with an
expectation in the visibility test.
"""

# No ``from __future__ import annotations`` here: FastAPI must resolve the
# dependency annotations of the routes defined inside ``create_router``.
from datetime import date, datetime
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from assistant.api.errors import ModuleErrorRoute
from assistant.boundary.interfaces import Boundary
from assistant.models.preferences import PreferencesForm
from assistant.models.records import (
    AssistantRecord,
    ConfirmedStatement,
    PersonRequest,
    Record,
)
from assistant.rules.visibility import Viewer
from assistant.service.approval_levels import LevelChange
from assistant.service.assistants import my_assistant as find_my_assistant
from assistant.service.container import Services, build_services
from assistant.service.briefing import BriefingSettingsChange
from assistant.service.meetings import ShareBody
from assistant.service.organisation import MeaningsChange
from assistant.service.setup import page_view
from assistant.service.visibility import get_visible, parse_id, viewer_for
from assistant.store.interface import Store

PREFIX = "/api/assistant"

R = TypeVar("R", bound=Record)

# Fields that are kept in the record but never shown through the API.
NEVER_SHOWN: dict[type[Record], set[str]] = {
    ConfirmedStatement: {"unconfirmed_wording"},  # BR-5
}


def shown(record: Record) -> dict[str, Any]:
    hidden = NEVER_SHOWN.get(type(record), set())
    return record.model_dump(mode="json", exclude=hidden)


class OrganisationSettingsChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    colour_meanings: MeaningsChange | None = None
    meeting_notes_allowed: bool | None = None
    """Whether meeting notes may be used in the organisation; applies to the next transcript."""


class AttendBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    series: bool = False
    """Book every upcoming meeting of the series, not just this one."""


class KeepBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_id: str | None = None
    """For a virtual employee: the one of its assignments the item follows."""


class EditActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    owner: str | None = None
    """The owner by name: exactly one person or virtual employee, ignoring case."""
    owner_id: str | None = None
    due_date: date | None = None


class ChatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


class ApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    """The manager's edited text; leave it out to approve the text as it is."""


class DeclineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None


class OpenedBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time_zone: str | None = None


class NewItemBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    owner_id: str
    due_date: date
    project_id: str | None = None
    assignment_id: str | None = None
    """For a virtual employee: the one of its assignments the item follows."""
    follow_up_times: list[datetime] | None = None
    """Leave out for the section 8.1 schedule; an empty list for none."""


class OtherDateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    due_date: date
    reason: str | None = None


class CommentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment: str | None = None
    """The manager's comment, up to 500 characters."""


class DecideBodyForPaper(BaseModel):
    model_config = ConfigDict(extra="forbid")

    option: int | None = None
    """The option picked, counted from 1."""
    decision: str | None = None
    """Or the manager's own decision, in their words."""


class LessonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    """The lesson in the manager's words, up to 500 characters."""
    area: str | None = None
    """What it is about, e.g. ``tone``; leave out for ``general``."""


class LessonEditBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = None


class ReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None


class UpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    word: str
    """``on_track``, ``done`` or ``blocked``."""
    note: str | None = None
    date_asked_for: date | None = None


class FollowUpsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    times: list[datetime]
    """Up to five times, or none."""


class ExtendBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    due_date: date


class ReassignBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_id: str
    assignment_id: str | None = None


class FollowBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmer_id: str | None = None
    """Who confirms its status; leave out for the project's owner."""


class ConfirmerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: str


class StatementBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    colour: str | None = None
    """``green``, ``amber`` or ``red``: always chosen by the owner."""
    reason: str | None = None
    what_changed: str | None = None
    what_comes_next: str | None = None


class DecideBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    choice: str
    due_date: date | None = None
    """Extend: the new due date."""
    owner_id: str | None = None
    """Reassign: the new owner, and for a virtual employee ``assignment_id``."""
    assignment_id: str | None = None
    person_id: str | None = None
    """Take it up: the person the message goes to."""
    message: str | None = None
    """Take it up: the prepared message as the manager edited it."""
    reason: str | None = None
    """Close: the reason, if any."""


def create_router(boundary: Boundary, store: Store, services: Services | None = None) -> APIRouter:
    services = services or build_services(boundary, store)
    router = APIRouter(prefix=PREFIX, route_class=ModuleErrorRoute, tags=["assistant"])

    async def current_viewer(request: Request) -> Viewer:
        return await viewer_for(boundary, request.headers)

    ViewerDep = Annotated[Viewer, Depends(current_viewer)]

    async def page(assistant: AssistantRecord) -> dict[str, Any]:
        return page_view(assistant, running=await services.control.page_running(assistant))

    async def read_one(viewer: Viewer, record_type: type[R], raw_id: str) -> dict[str, Any]:
        record_id = parse_id(raw_id)
        async with store.transaction(viewer.organisation_id) as tx:
            return shown(await get_visible(tx, viewer, record_type, record_id))

    @router.get("/assistant")
    async def my_assistant(viewer: ViewerDep) -> dict[str, Any]:
        """The assistant of the person asking, if they are its manager."""
        async with store.transaction(viewer.organisation_id) as tx:
            assistant = await find_my_assistant(tx, viewer)
        return await page(assistant)

    @router.post("/assistant/opened")
    async def assistant_opened(body: OpenedBody, viewer: ViewerDep) -> dict[str, Any]:
        """The manager opened the page; the first time, their browser's time zone is kept."""
        return await page(await services.setup.first_opened(viewer, body.time_zone))

    @router.post("/assistant/pause")
    async def pause(viewer: ViewerDep) -> dict[str, Any]:
        """Nothing is started, sent or carried out until the manager resumes."""
        return await page(await services.control.pause(viewer))

    @router.post("/assistant/resume")
    async def resume(viewer: ViewerDep) -> dict[str, Any]:
        """Kept results are handled and each step that fell due meanwhile is done once."""
        return await page(await services.control.resume(viewer))

    @router.get("/preferences")
    async def get_preferences(viewer: ViewerDep) -> dict[str, Any]:
        return await services.setup.preferences(viewer)

    @router.put("/preferences")
    async def save_preferences(form: PreferencesForm, viewer: ViewerDep) -> dict[str, Any]:
        return await services.setup.save(viewer, form)

    @router.post("/preferences/skip")
    async def skip_preferences(viewer: ViewerDep) -> dict[str, Any]:
        return await services.setup.skip(viewer)

    @router.get("/approval-levels")
    async def approval_levels(viewer: ViewerDep) -> list[dict[str, Any]]:
        return await services.levels.levels(viewer)

    @router.put("/approval-levels/{kind}")
    async def change_approval_level(
        kind: str, change: LevelChange, viewer: ViewerDep
    ) -> dict[str, Any]:
        """Only the manager, from their own page, changes a level (BR-7)."""
        return await services.levels.change(viewer, kind, change)

    @router.get("/waiting-count")
    async def waiting_count(viewer: ViewerDep) -> dict[str, Any]:
        """For Flow's top bar: how many things wait for the manager."""
        return await services.manager_notices.waiting_count(viewer)

    @router.get("/assistants/{assistant_id}")
    async def assistant(assistant_id: str, viewer: ViewerDep) -> dict[str, Any]:
        return await read_one(viewer, AssistantRecord, assistant_id)

    @router.get("/chat")
    async def chat(viewer: ViewerDep) -> dict[str, Any]:
        """The manager's chat with their assistant, oldest first."""
        return await services.work.chat(viewer)

    @router.post("/chat")
    async def send_chat(body: ChatBody, viewer: ViewerDep) -> dict[str, Any]:
        """The manager's message: kept in the chat and given to the assistant as work."""
        return await services.work.send(viewer, body.text)

    @router.get("/work/{work_id}")
    async def piece_of_work(work_id: str, viewer: ViewerDep) -> dict[str, Any]:
        """A piece of work: its state, the reason it was refused or failed, and how many of
        its tool requests were refused, with their reasons."""
        return await services.work.work(viewer, work_id)

    @router.post("/work/{work_id}/retry")
    async def retry_work(work_id: str, viewer: ViewerDep) -> dict[str, Any]:
        """Try refused or failed work again."""
        return await services.work.retry(viewer, work_id)

    @router.get("/requests")
    async def waiting_requests(viewer: ViewerDep) -> dict[str, Any]:
        """Needs your approval: what waits for the manager, with the count."""
        return await services.requests.waiting(viewer)

    @router.post("/requests/approve-all")
    async def approve_all(viewer: ViewerDep) -> dict[str, Any]:
        """Approve every waiting request to an owner, and nothing else."""
        return await services.requests.approve_all(viewer)

    @router.get("/requests/{request_id}")
    async def request(request_id: str, viewer: ViewerDep) -> dict[str, Any]:
        return await services.requests.request(viewer, request_id)

    @router.post("/requests/{request_id}/approve")
    async def approve(
        request_id: str, viewer: ViewerDep, body: ApproveBody | None = None
    ) -> dict[str, Any]:
        """Approve, or edit and approve: carried out once."""
        return await services.requests.approve(viewer, request_id, body.text if body else None)

    @router.post("/requests/{request_id}/decline")
    async def decline(
        request_id: str, viewer: ViewerDep, body: DeclineBody | None = None
    ) -> dict[str, Any]:
        """Decline: nothing is carried out; the reason is kept if given."""
        return await services.requests.decline(viewer, request_id, body.reason if body else None)

    @router.post("/requests/{request_id}/retry")
    async def retry_request(request_id: str, viewer: ViewerDep) -> dict[str, Any]:
        """Try again what could not be carried out."""
        return await services.requests.retry(viewer, request_id)

    @router.post("/requests/{request_id}/undo")
    async def undo_request(request_id: str, viewer: ViewerDep) -> dict[str, Any]:
        """Undo what was carried out, while that is still possible."""
        return await services.requests.undo(viewer, request_id)

    @router.get("/done")
    async def done_for_you(viewer: ViewerDep) -> dict[str, Any]:
        """Done for you: everything carried out in the manager's name, newest first."""
        return await services.requests.done_for_you(viewer)

    @router.get("/action-items")
    async def action_items(viewer: ViewerDep) -> dict[str, Any]:
        """The manager's action items, by due date."""
        return await services.items.items(viewer)

    @router.post("/action-items")
    async def give_action_item(body: NewItemBody, viewer: ViewerDep) -> dict[str, Any]:
        """The manager gives an item: its owner is asked to accept, without approval."""
        return await services.items.add(viewer, **body.model_dump())

    @router.get("/my-action-items")
    async def my_action_items(viewer: ViewerDep) -> dict[str, Any]:
        """The items the person asking owns."""
        return await services.items.my_items(viewer)

    @router.get("/assignments")
    async def assignments(viewer: ViewerDep) -> dict[str, Any]:
        """The manager's virtual employees' assignments, to link an item to one."""
        return await services.items.assignments(viewer)

    @router.get("/action-items/{item_id}")
    async def action_item(item_id: str, viewer: ViewerDep) -> dict[str, Any]:
        """An item, for its manager or its owner."""
        return await services.items.item(viewer, item_id)

    @router.post("/action-items/{item_id}/accept")
    async def accept_item(item_id: str, viewer: ViewerDep) -> dict[str, Any]:
        """The owner accepts: the item is open and its follow-ups are set."""
        return await services.items.accept(viewer, item_id)

    @router.post("/action-items/{item_id}/propose-date")
    async def propose_date(item_id: str, body: OtherDateBody, viewer: ViewerDep) -> dict[str, Any]:
        """The owner proposes another date, with a reason."""
        answer = await services.items.propose_date(viewer, item_id, body.due_date, body.reason)
        await services.manager_notices.send_ready_in(viewer.organisation_id)
        return answer

    @router.post("/action-items/{item_id}/decline")
    async def decline_item(item_id: str, body: ReasonBody, viewer: ViewerDep) -> dict[str, Any]:
        """The owner declines, with a reason."""
        answer = await services.items.decline(viewer, item_id, body.reason)
        await services.manager_notices.send_ready_in(viewer.organisation_id)
        return answer

    @router.post("/action-items/{item_id}/updates")
    async def give_update(item_id: str, body: UpdateBody, viewer: ViewerDep) -> dict[str, Any]:
        """The owner's update in one step: On track, Done or Blocked, a note, another date."""
        answer = await services.items.give_update(viewer, item_id, **body.model_dump())
        await services.manager_notices.send_ready_in(viewer.organisation_id)
        return answer

    @router.put("/action-items/{item_id}/follow-ups")
    async def set_follow_ups(
        item_id: str, body: FollowUpsBody, viewer: ViewerDep
    ) -> dict[str, Any]:
        """The manager sets this item's follow-up times: up to five, or none."""
        return await services.items.set_follow_ups(viewer, item_id, body.times)

    @router.post("/action-items/{item_id}/extend")
    async def extend_item(item_id: str, body: ExtendBody, viewer: ViewerDep) -> dict[str, Any]:
        return await services.items.extend_item(viewer, item_id, body.due_date)

    @router.post("/action-items/{item_id}/reassign")
    async def reassign_item(item_id: str, body: ReassignBody, viewer: ViewerDep) -> dict[str, Any]:
        return await services.items.reassign_item(
            viewer, item_id, body.owner_id, body.assignment_id
        )

    @router.post("/action-items/{item_id}/close")
    async def close_item(
        item_id: str, viewer: ViewerDep, body: ReasonBody | None = None
    ) -> dict[str, Any]:
        return await services.items.close_item(viewer, item_id, body.reason if body else None)

    @router.post("/action-items/{item_id}/done")
    async def item_done(item_id: str, viewer: ViewerDep) -> dict[str, Any]:
        """The manager marks an item they gave themselves as done."""
        return await services.items.mark_done(viewer, item_id)

    @router.get("/attention")
    async def attention(viewer: ViewerDep) -> dict[str, Any]:
        """Needs your attention: open entries with their choices, and the count."""
        return await services.decisions.entries(viewer)

    @router.get("/attention/{entry_id}")
    async def attention_entry(entry_id: str, viewer: ViewerDep) -> dict[str, Any]:
        return await services.decisions.entry(viewer, entry_id)

    @router.post("/attention/{entry_id}/decide")
    async def decide(entry_id: str, body: DecideBody, viewer: ViewerDep) -> dict[str, Any]:
        """The manager's decision, with their own click: no approval."""
        return await services.decisions.decide(viewer, entry_id, **body.model_dump())

    @router.get("/person-requests/{request_id}")
    async def person_request(request_id: str, viewer: ViewerDep) -> dict[str, Any]:
        return await read_one(viewer, PersonRequest, request_id)

    @router.get("/my-statements")
    async def my_statements(viewer: ViewerDep) -> dict[str, Any]:
        """The latest version of each confirmed statement the person asking owns."""
        return await services.status.my_statements(viewer)

    @router.get("/statements/{statement_id}")
    async def statement(statement_id: str, viewer: ViewerDep) -> dict[str, Any]:
        """A confirmed statement; for a correction, who made it, when and which points
        changed, never the wording its owner did not confirm."""
        return await services.status.statement(viewer, statement_id)

    @router.post("/statements/{statement_id}/correct")
    async def correct_statement(
        statement_id: str, body: StatementBody, viewer: ViewerDep
    ) -> dict[str, Any]:
        """Its owner adds a correction: a new version; the earlier one is kept."""
        return await services.status.correct(viewer, statement_id, **body.model_dump())

    @router.get("/projects")
    async def projects(viewer: ViewerDep) -> dict[str, Any]:
        """The projects the manager can see, and whether each is followed."""
        return await services.status.projects(viewer)

    @router.post("/projects/{project_id}/follow")
    async def follow(
        project_id: str, viewer: ViewerDep, body: FollowBody | None = None
    ) -> dict[str, Any]:
        return await services.status.follow(viewer, project_id, body.confirmer_id if body else None)

    @router.put("/projects/{project_id}/confirmer")
    async def change_confirmer(
        project_id: str, body: ConfirmerBody, viewer: ViewerDep
    ) -> dict[str, Any]:
        return await services.status.change_confirmer(viewer, project_id, body.person_id)

    @router.post("/projects/{project_id}/unfollow")
    async def unfollow(project_id: str, viewer: ViewerDep) -> dict[str, Any]:
        return await services.status.unfollow(viewer, project_id)

    @router.get("/status")
    async def status_page(viewer: ViewerDep) -> dict[str, Any]:
        """Project status as it stands: confirmed words only."""
        return await services.status.page(viewer)

    @router.get("/reports")
    async def reports(viewer: ViewerDep) -> dict[str, Any]:
        return await services.status.reports(viewer)

    @router.post("/reports")
    async def report_now(viewer: ViewerDep) -> dict[str, Any]:
        """ "Report now": a report is made at once."""
        return await services.status.report_now(viewer)

    @router.get("/reports/{report_id}")
    async def report(report_id: str, viewer: ViewerDep) -> dict[str, Any]:
        return await services.status.report(viewer, report_id)

    # The briefing ------------------------------------------------------------------------

    @router.get("/briefing/settings")
    async def briefing_settings(viewer: ViewerDep) -> dict[str, Any]:
        """The briefing's schedule and the sections ticked next to it."""
        return await services.briefing.settings(viewer)

    @router.put("/briefing/settings")
    async def change_briefing_settings(
        change: BriefingSettingsChange, viewer: ViewerDep
    ) -> dict[str, Any]:
        """Weekly, every two weeks, monthly or off, with the day and time; and the sections."""
        return await services.briefing.change_settings(viewer, change)

    @router.get("/briefings")
    async def briefings(viewer: ViewerDep) -> dict[str, Any]:
        return await services.briefing.briefings(viewer)

    @router.post("/briefings")
    async def briefing_now(viewer: ViewerDep) -> dict[str, Any]:
        """The manager asks for the briefing: its figures are worked out at once."""
        assistant, made = await services.briefing.briefing_now(viewer)
        await services.work.advance(assistant)
        return await services.briefing.briefing(viewer, str(made.id))

    @router.get("/briefings/{briefing_id}")
    async def briefing(briefing_id: str, viewer: ViewerDep) -> dict[str, Any]:
        return await services.briefing.briefing(viewer, briefing_id)

    @router.post("/briefings/{briefing_id}/open")
    async def open_briefing(briefing_id: str, viewer: ViewerDep) -> dict[str, Any]:
        """The manager opens the briefing: one that is ready becomes accepted."""
        return await services.briefing.open_briefing(viewer, briefing_id)

    @router.post("/briefings/{briefing_id}/send-back")
    async def send_back_briefing(
        briefing_id: str, body: CommentBody, viewer: ViewerDep
    ) -> dict[str, Any]:
        """Send it back with a comment: a new version is prepared and both are kept."""
        return await services.briefing.send_back(viewer, briefing_id, body.comment)

    # Decision papers -----------------------------------------------------------------------

    @router.get("/decision-papers")
    async def decision_papers(viewer: ViewerDep) -> dict[str, Any]:
        """Every decision paper there is to show, by due date."""
        return await services.decision_papers.papers(viewer)

    @router.get("/decision-papers/{paper_id}")
    async def decision_paper(paper_id: str, viewer: ViewerDep) -> dict[str, Any]:
        return await services.decision_papers.paper(viewer, paper_id)

    @router.post("/decision-papers/{paper_id}/decide")
    async def decide_paper(
        paper_id: str, body: DecideBodyForPaper, viewer: ViewerDep
    ) -> dict[str, Any]:
        """Pick an option or write your own decision: recorded, and it cannot be changed."""
        return await services.decision_papers.decide(viewer, paper_id, body.option, body.decision)

    @router.post("/decision-papers/{paper_id}/send-back")
    async def send_back_paper(
        paper_id: str, body: CommentBody, viewer: ViewerDep
    ) -> dict[str, Any]:
        """Send it back with a comment: a new version is prepared and both are kept."""
        return await services.decision_papers.send_back(viewer, paper_id, body.comment)

    @router.post("/decision-papers/{paper_id}/retry")
    async def retry_paper(paper_id: str, viewer: ViewerDep) -> dict[str, Any]:
        """Try again a decision paper that could not be prepared."""
        return await services.decision_papers.retry(viewer, paper_id)

    @router.get("/status-requests")
    async def status_requests(viewer: ViewerDep) -> dict[str, Any]:
        """What the person asking is asked to confirm or give."""
        return await services.status.status_requests(viewer)

    @router.get("/status-requests/{request_id}")
    async def status_request(request_id: str, viewer: ViewerDep) -> dict[str, Any]:
        """For the person asked only: it may hold the assistant's draft (BR-4)."""
        return await services.status.status_request(viewer, request_id)

    @router.post("/status-requests/{request_id}/answer")
    async def answer_status(
        request_id: str, body: StatementBody, viewer: ViewerDep
    ) -> dict[str, Any]:
        """Confirm or correct the draft, or give the status: a colour and a reason."""
        return await services.status.answer(viewer, request_id, **body.model_dump())

    @router.get("/organisation/settings")
    async def organisation_settings(viewer: ViewerDep) -> dict[str, Any]:
        """Administrators only (BR-8)."""
        return await services.organisation.settings(viewer)

    @router.put("/organisation/settings")
    async def change_organisation_settings(
        change: OrganisationSettingsChange, viewer: ViewerDep
    ) -> dict[str, Any]:
        """Administrators only: what each status colour means, and whether meeting notes are
        allowed."""
        return await services.organisation.change(
            viewer,
            meanings=change.colour_meanings,
            meeting_notes_allowed=change.meeting_notes_allowed,
        )

    # Meetings: the manager's ---------------------------------------------------------------

    @router.get("/meetings")
    async def meetings(viewer: ViewerDep) -> dict[str, Any]:
        """Transcripts kept for the manager, shared or from attended meetings, with states."""
        return await services.meetings.meetings(viewer)

    @router.get("/meetings/upcoming")
    async def upcoming_meetings(viewer: ViewerDep) -> dict[str, Any]:
        """The manager's upcoming meetings from the meeting service, each with Attend for me."""
        return await services.attendance.upcoming(viewer)

    @router.post("/meetings/upcoming/{meeting_ref}/attend")
    async def attend_for_me(
        meeting_ref: str, viewer: ViewerDep, body: AttendBody | None = None
    ) -> dict[str, Any]:
        """Attend for me: the manager's own decision, without approval."""
        return await services.attendance.attend(viewer, meeting_ref, body.series if body else False)

    @router.get("/meetings/bookings")
    async def bookings(viewer: ViewerDep) -> dict[str, Any]:
        return await services.attendance.bookings(viewer)

    @router.get("/meetings/bookings/{booking_id}")
    async def booking(booking_id: str, viewer: ViewerDep) -> dict[str, Any]:
        return await services.attendance.booking(viewer, booking_id)

    @router.get("/meetings/{meeting_id}")
    async def meeting(meeting_id: str, viewer: ViewerDep) -> dict[str, Any]:
        """A meeting's state, why its notes could not be written, and its part numbers."""
        return await services.meetings.meeting(viewer, meeting_id)

    @router.post("/meetings/{meeting_id}/retry")
    async def retry_meeting_notes(meeting_id: str, viewer: ViewerDep) -> dict[str, Any]:
        """Try again notes that could not be written, while the transcript is kept."""
        return await services.meetings.retry(viewer, meeting_id)

    @router.get("/meeting-notes")
    async def meeting_notes(viewer: ViewerDep) -> dict[str, Any]:
        return await services.notes.all_notes(viewer)

    @router.get("/meeting-notes/search")
    async def search_meeting_notes(
        viewer: ViewerDep, words: str = "", meeting: str = "", project: str = "", person: str = ""
    ) -> dict[str, Any]:
        """Search the kept notes by words, meeting, project or person."""
        return await services.notes.search(
            viewer, words_wanted=words, meeting=meeting, project=project, person=person
        )

    @router.get("/meeting-notes/{notes_id}")
    async def notes(notes_id: str, viewer: ViewerDep) -> dict[str, Any]:
        """The notes with their sources: decisions, actions, open points and statements."""
        return await services.notes.notes(viewer, notes_id)

    @router.delete("/meeting-notes/{notes_id}")
    async def delete_notes(notes_id: str, viewer: ViewerDep) -> dict[str, Any]:
        """Kept items and confirmed statements stay."""
        return await services.notes.delete(viewer, notes_id)

    @router.post("/meeting-notes/{notes_id}/actions/{action_id}/keep")
    async def keep_action(
        notes_id: str, action_id: str, viewer: ViewerDep, body: KeepBody | None = None
    ) -> dict[str, Any]:
        """Keep: an action item from the meeting; its owner is asked to accept it."""
        return await services.notes.keep(
            viewer, notes_id, action_id, body.assignment_id if body else None
        )

    @router.put("/meeting-notes/{notes_id}/actions/{action_id}")
    async def edit_action(
        notes_id: str, action_id: str, body: EditActionBody, viewer: ViewerDep
    ) -> dict[str, Any]:
        """Edit an action's text, owner or due date before it is kept."""
        return await services.notes.edit(viewer, notes_id, action_id, **body.model_dump())

    @router.post("/meeting-notes/{notes_id}/actions/{action_id}/remove")
    async def remove_action(notes_id: str, action_id: str, viewer: ViewerDep) -> dict[str, Any]:
        return await services.notes.remove(viewer, notes_id, action_id)

    @router.post("/meeting-notes/{notes_id}/share")
    async def share_notes(notes_id: str, viewer: ViewerDep) -> dict[str, Any]:
        """Tell Flow, so that it can add the note to the organisation's knowledge store."""
        return await services.notes.share(viewer, notes_id)

    @router.post("/meeting-notes/{notes_id}/unshare")
    async def unshare_notes(notes_id: str, viewer: ViewerDep) -> dict[str, Any]:
        return await services.notes.unshare(viewer, notes_id)

    # Meetings: organisers ---------------------------------------------------------------------

    @router.post("/transcripts")
    async def share_transcript(body: ShareBody, viewer: ViewerDep) -> dict[str, Any]:
        """Any person in the organisation shares a meeting they ran, with the two ticks."""
        return await services.meetings.share(viewer, body)

    @router.get("/transcripts")
    async def shared_transcripts(viewer: ViewerDep) -> dict[str, Any]:
        """What the person asking shared: each meeting's title and state only (BR-5)."""
        return await services.meetings.shared_by_me(viewer)

    @router.post("/transcripts/{meeting_id}/withdraw")
    async def withdraw_transcript(meeting_id: str, viewer: ViewerDep) -> dict[str, Any]:
        """Its organiser takes it back before the notes are written."""
        return await services.meetings.withdraw(viewer, meeting_id)

    @router.get("/meeting-requests")
    async def meeting_requests(viewer: ViewerDep) -> dict[str, Any]:
        """Requests for you: assistants asking to attend the person's meetings."""
        return await services.attendance.organiser_requests(viewer)

    @router.get("/meeting-requests/{request_id}")
    async def meeting_request(request_id: str, viewer: ViewerDep) -> dict[str, Any]:
        return await services.attendance.organiser_request(viewer, request_id)

    @router.post("/meeting-requests/{request_id}/accept")
    async def accept_meeting_request(request_id: str, viewer: ViewerDep) -> dict[str, Any]:
        return await services.attendance.accept(viewer, request_id)

    @router.post("/meeting-requests/{request_id}/decline")
    async def decline_meeting_request(request_id: str, viewer: ViewerDep) -> dict[str, Any]:
        return await services.attendance.decline(viewer, request_id)

    @router.post("/meeting-requests/{request_id}/remove")
    async def remove_from_meeting(request_id: str, viewer: ViewerDep) -> dict[str, Any]:
        """The organiser removes the assistant: it leaves where it is still in."""
        return await services.attendance.remove(viewer, request_id)

    @router.get("/people")
    async def people(viewer: ViewerDep, name: str = "") -> dict[str, Any]:
        """People and virtual employees of the organisation called exactly ``name``, ignoring
        case (BR-12), so that a screen can name a person instead of addressing them."""
        found = (
            await boundary.people.find_by_name(viewer.organisation_id, name.strip())
            if name.strip()
            else []
        )
        return {
            "people": [
                {"id": p.id, "display_name": p.display_name, "kind": p.kind}
                for p in found
                if p.organisation_id == viewer.organisation_id
            ]
        }

    @router.get("/colours")
    async def colours(viewer: ViewerDep) -> list[dict[str, Any]]:
        """Each colour with its meaning, for the person giving or confirming a status."""
        return await services.organisation.colours(viewer)

    # Learning (part 2, Epic 3): the manager's only -----------------------------------------

    @router.get("/learning")
    async def learned(viewer: ViewerDep) -> dict[str, Any]:
        """What I've learned: the lessons, the suggestions and this month's improvement."""
        return await services.learning.learned(viewer)

    @router.get("/learning/signals")
    async def signals(viewer: ViewerDep) -> dict[str, Any]:
        return await services.learning.signals(viewer)

    @router.get("/learning/signals/{signal_id}")
    async def signal(signal_id: str, viewer: ViewerDep) -> dict[str, Any]:
        return await services.learning.signal(viewer, signal_id)

    @router.get("/learning/lessons")
    async def lessons(viewer: ViewerDep) -> dict[str, Any]:
        """Each lesson with its text, its evidence in one line, and where it came from."""
        return await services.learning.lessons(viewer)

    @router.post("/learning/lessons")
    async def add_lesson(body: LessonBody, viewer: ViewerDep) -> dict[str, Any]:
        """The manager types a lesson: it is stated, and applies at once."""
        return await services.learning.add(viewer, body.text, body.area)

    @router.delete("/learning/lessons")
    async def clear_lessons(viewer: ViewerDep) -> dict[str, Any]:
        """Clear the record: every kept and proposed lesson is removed."""
        return await services.learning.clear(viewer)

    @router.get("/learning/lessons/{lesson_id}")
    async def lesson(lesson_id: str, viewer: ViewerDep) -> dict[str, Any]:
        return await services.learning.lesson(viewer, lesson_id)

    @router.put("/learning/lessons/{lesson_id}")
    async def edit_lesson(
        lesson_id: str, body: LessonEditBody, viewer: ViewerDep
    ) -> dict[str, Any]:
        """Kept in the manager's wording; applies at once (BR-8)."""
        return await services.learning.edit(viewer, lesson_id, body.text)

    @router.post("/learning/lessons/{lesson_id}/keep")
    async def keep_lesson(lesson_id: str, viewer: ViewerDep) -> dict[str, Any]:
        return await services.learning.keep(viewer, lesson_id)

    @router.delete("/learning/lessons/{lesson_id}")
    async def remove_lesson(lesson_id: str, viewer: ViewerDep) -> dict[str, Any]:
        return await services.learning.remove(viewer, lesson_id)

    @router.get("/learning/suggestions")
    async def suggestions(viewer: ViewerDep) -> dict[str, Any]:
        return await services.learning.suggestions(viewer)

    @router.get("/learning/suggestions/{suggestion_id}")
    async def suggestion(suggestion_id: str, viewer: ViewerDep) -> dict[str, Any]:
        return await services.learning.suggestion(viewer, suggestion_id)

    @router.post("/learning/suggestions/{suggestion_id}/accept")
    async def accept_suggestion(suggestion_id: str, viewer: ViewerDep) -> dict[str, Any]:
        """The level changes to the proposed one, and the change is recorded."""
        return await services.learning.accept(viewer, suggestion_id)

    @router.post("/learning/suggestions/{suggestion_id}/decline")
    async def decline_suggestion(suggestion_id: str, viewer: ViewerDep) -> dict[str, Any]:
        """The same suggestion is refused for 30 days."""
        return await services.learning.decline(viewer, suggestion_id)

    @router.get("/learning/improvement")
    async def improvement(viewer: ViewerDep) -> dict[str, Any]:
        """This month's figures beside last month's."""
        return await services.learning.improvement(viewer)

    return router
