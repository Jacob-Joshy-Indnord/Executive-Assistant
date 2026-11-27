"""Shared steps for the tests of part 2, Epic 1 (meeting notes)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from assistant.api.router import PREFIX
from assistant.models.boundary import WorkKind, WorkResult
from assistant.models.records import (
    Meeting,
    MeetingAttendance,
    MeetingNotes,
    PieceOfWork,
    RequestRecord,
)
from assistant.service.approval_levels import LevelChange
from standins.sample import (
    LEGAL_LEAD,
    OPERATIONS_DIRECTOR,
    ORGANISATION_ADMINISTRATOR,
    ORGANISATION_ID,
    PROGRAMME_LEAD,
    sample_id,
)
from standins.sample_transcript import SAMPLE_LINES, as_file, sample_text, sample_vtt
from tests.world import World, viewer

MANAGER = OPERATIONS_DIRECTOR.id
ORGANISER = PROGRAMME_LEAD.id
OWNER = LEGAL_LEAD.id
ADMIN = ORGANISATION_ADMINISTRATOR.id
CRM = sample_id("project/crm_rollout")
VENDORS = sample_id("project/vendor_consolidation")
HIRING = sample_id("project/hiring_plan")
TRANSCRIPT_WORDS = [text for _, text in SAMPLE_LINES]
"""Every line of the sample transcript: none may appear in a record, notice or log."""


class Meetings:
    """Mixed into a test case that has ``self.w``, a seeded world in which meeting notes are
    allowed."""

    w: World

    def call(self, method: str, path: str, person: str, body: Any = None) -> Any:
        return self.w.call(method, f"{PREFIX}{path}", person, body)

    def ok(self, method: str, path: str, person: str, body: Any = None) -> Any:
        response = self.call(method, path, person, body)
        assert response.status_code == 200, response.text
        return response.json()

    def refused(self, method: str, path: str, person: str, body: Any = None) -> str:
        response = self.call(method, path, person, body)
        assert response.status_code == 422, response.text
        message: str = response.json()["detail"]["message"]
        return message

    # Sharing -----------------------------------------------------------------------------

    @staticmethod
    def share_body(**changes: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "title": "Vendor review",
            "date": "2026-11-20",
            "manager_id": MANAGER,
            "project_ids": [VENDORS],
            "file_name": "vendor-review.vtt",
            "file_base64": as_file(sample_vtt()),
            "ran_it": True,
            "people_knew": True,
        }
        body.update(changes)
        return {k: v for k, v in body.items() if v is not None}

    def share(self, person: str = ORGANISER, **changes: Any) -> Any:
        return self.call("POST", "/transcripts", person, self.share_body(**changes))

    def share_text(self, person: str = ORGANISER, **changes: Any) -> Any:
        return self.share(
            person, file_name=None, file_base64=None, pasted_text=sample_text(), **changes
        )

    def allow_meeting_notes(self, allowed: bool) -> None:
        self.ok("PUT", "/organisation/settings", ADMIN, {"meeting_notes_allowed": allowed})

    async def set_level(self, kind: str, level: str, projects: list[str] | None = None) -> None:
        change: dict[str, Any] = {"level": level}
        if projects is not None:
            change["limits"] = {"projects": projects}
        await self.w.module.services.levels.change(
            viewer(MANAGER), kind, LevelChange.model_validate(change)
        )

    async def follow(self, project_id: str) -> None:
        await self.w.module.services.status.follow(viewer(MANAGER), project_id, None)

    # Reading the store -----------------------------------------------------------------------

    async def meetings(self) -> list[Meeting]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return await tx.find(Meeting, assistant_id=self.w.seeded.assistant.id)

    async def meeting(self, meeting_id: str | UUID) -> Meeting:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            found = await tx.get(Meeting, UUID(str(meeting_id)))
        assert found is not None
        return found

    async def notes_of(self, meeting_id: str | UUID) -> MeetingNotes | None:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            found = await tx.find(MeetingNotes, meeting_id=UUID(str(meeting_id)))
        return found[0] if found else None

    async def requests_about(self, meeting_id: str | UUID) -> list[RequestRecord]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return await tx.find(RequestRecord, subject_kind="meeting", subject_id=str(meeting_id))

    async def attendances(self) -> list[MeetingAttendance]:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            return [
                a
                for a in await tx.find(MeetingAttendance, assistant_id=self.w.seeded.assistant.id)
                if a.id != self.w.seeded.attendance.id
            ]

    async def piece(self, work_id: UUID) -> PieceOfWork:
        async with self.w.store.transaction(ORGANISATION_ID) as tx:
            found = await tx.get(PieceOfWork, work_id)
        assert found is not None
        return found

    # The assistant's turn ------------------------------------------------------------------

    async def finish_notes(self, change: Any = None) -> list[UUID]:
        """Hand the stand-in's answers to the module, as Flow does at the end of a turn;
        ``change`` may alter each result first. Returns the work ids handled."""
        handled: list[UUID] = []
        for result in self.w.standins.assistant.take_finished():
            if change is not None:
                result = change(result)
            await self.w.module.flow.finish_work(
                ORGANISATION_ID, self.w.seeded.assistant.flow_id, result
            )
            handled.append(result.work_id)
        return handled

    def started_notes(self) -> list[Any]:
        return [o for o in self.w.standins.assistant.started if o.kind is WorkKind.MEETING_NOTES]

    async def write_notes(self, **share: Any) -> Meeting:
        """Share the sample transcript with writing allowed without approval, and hand back
        the stand-in's notes."""
        await self.set_level("write_meeting_notes", "without_approval")
        response = self.share(**share)
        assert response.status_code == 200, response.text
        await self.finish_notes()
        return await self.meeting(response.json()["id"])

    # What must never hold transcript text -------------------------------------------------

    def assert_no_transcript_text(self, logs: list[str] | None = None) -> None:
        records = [r.model_dump_json() for r in self.w.standins.records.written]
        notices = [n.model_dump_json() for n in self.w.standins.notices.sent]
        for words in TRANSCRIPT_WORDS:
            for place, texts in (("record", records), ("notice", notices), ("log", logs or [])):
                for text in texts:
                    assert words not in text, f"a {place} holds transcript text: {text}"

    def notices_to(self, person: str) -> list[str]:
        return [n.text for n in self.w.standins.notices.sent if n.person_id == person]


@contextmanager
def captured_logs(case: Any) -> Iterator[list[str]]:
    """Every log line of the module while the block runs."""
    with case.assertLogs("assistant", level="INFO") as caught:
        yield caught.output


def result_with(**changes: Any) -> Any:
    def change(result: WorkResult) -> WorkResult:
        return result.model_copy(update=changes)

    return change
