"""Story 1.3: visibility (BR-8) on every API address.

The sweep enumerates every registered route under ``/api/assistant`` and
calls it as the manager, another manager, an owner and an administrator,
each call in a freshly seeded world so that one persona's change cannot
affect the next. It fails on any address without an expectation in
``EXPECTATIONS``.
"""

from __future__ import annotations

import unittest
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.routing import BaseRoute, Mount

from assistant.api.router import PREFIX
from assistant.module import create_module
from standins.meetings import sample_meetings
from standins.sample import FINANCE_LEAD, OPERATIONS_DIRECTOR, OUTSIDER
from tests.world import (
    MEETING_WORDS,
    PERSONAS,
    UNSHOWN_WORDING,
    Seeded,
    World,
    build_world_sync,
)

NOT_FOUND_BODY = {"detail": {"code": "not_found", "message": "Not found."}}


@dataclass(frozen=True)
class Expectation:
    """What each persona gets from one address, and how to fill its path."""

    path_params: Callable[[Seeded], dict[str, str]]
    status: dict[str, int]
    returns_id: Callable[[Seeded, str], str | None] = lambda seeded, persona: None
    body: Any = None
    checks: list[Callable[[World, str, Any], None]] = field(default_factory=list)
    """Extra checks on a world after the call: (world, persona, response)."""


def _no_params(seeded: Seeded) -> dict[str, str]:
    return {}


def _own(name: str) -> Callable[[Seeded, str], str | None]:
    """The seeded record's id, for the personas allowed to read it."""

    def returns(seeded: Seeded, persona: str) -> str | None:
        return str(getattr(seeded, name).id) if persona == "manager" else None

    return returns


MANAGER_ONLY = {"manager": 200, "other_manager": 404, "owner": 404, "administrator": 404}
OWNER_OR_MANAGER = {"manager": 200, "other_manager": 404, "owner": 200, "administrator": 404}
ADMINISTRATOR_ONLY = {"manager": 404, "other_manager": 404, "owner": 404, "administrator": 200}
EVERYONE = {"manager": 200, "other_manager": 200, "owner": 200, "administrator": 200}
"""Addresses that show nothing private: the colours and their meanings."""
OWN_ASSISTANT = {"manager": 200, "other_manager": 200, "owner": 404, "administrator": 404}
"""Addresses about "my assistant": another manager reaches only their own."""

SAMPLE_FORM = {
    "working_time": {"days": ["monday", "tuesday"], "time_zone": "Europe/London"},
    "tone": {"sign_off": "Best wishes"},
}


def _managers_preferences_untouched_by_others(world: World, persona: str, response: Any) -> None:
    if persona == "manager":
        return
    page = world.call("GET", f"{PREFIX}/preferences", PERSONAS["manager"]).json()
    assert page["saved"] is False and page["form"]["tone"] is None, page


def _only_the_managers_pause_reaches_their_assistant(
    world: World, persona: str, response: Any
) -> None:
    page = world.call("GET", f"{PREFIX}/assistant", PERSONAS["manager"]).json()
    expected = "paused" if persona == "manager" else "active"
    assert page["state"] == expected, (persona, page["state"])


def _meanings_written_only_by_administrators(world: World, persona: str, response: Any) -> None:
    colours = world.call("GET", f"{PREFIX}/colours", PERSONAS["owner"]).json()
    green = next(c for c in colours if c["colour"] == "green")
    expected = "On plan" if persona == "administrator" else None
    assert green["meaning"] == expected, (persona, green)


def _managers_levels_untouched_by_others(world: World, persona: str, response: Any) -> None:
    levels = world.call("GET", f"{PREFIX}/approval-levels", PERSONAS["manager"]).json()
    message = next(level for level in levels if level["kind"] == "send_message")
    expected = "without_approval" if persona == "manager" else "with_approval"
    assert message["level"] == expected, (persona, message)


def _chat_reaches_only_the_managers_assistant(world: World, persona: str, response: Any) -> None:
    chat = world.call("GET", f"{PREFIX}/chat", PERSONAS["manager"]).json()
    texts = [m["text"] for m in chat["messages"] if m["author"] == "manager"]
    expected = ["Hello"] if persona == "manager" else []
    assert texts == expected, (persona, texts)


MANAGER_REACHES = {"manager": 422, "other_manager": 404, "owner": 404, "administrator": 404}
"""The manager reaches it and is told why it cannot be done now; others find nothing."""


def _only_the_managers_decision_reaches_the_request(
    expected_state: str,
) -> Callable[[World, str, Any], None]:
    def check(world: World, persona: str, response: Any) -> None:
        path = f"{PREFIX}/requests/{world.seeded.request.id}"
        state = world.call("GET", path, PERSONAS["manager"]).json()["state"]
        assert state == (expected_state if persona == "manager" else "waiting"), (persona, state)

    return check


OWNER_ANSWERS = {"manager": 422, "other_manager": 404, "owner": 200, "administrator": 404}
"""Only the item's owner answers it; its manager reaches it and is told why not."""
OWNER_REACHES = {"manager": 422, "other_manager": 404, "owner": 422, "administrator": 404}


def _seeded_item(seeded: Seeded) -> dict[str, str]:
    return {"item_id": str(seeded.item.id)}


def _seeded_entry(seeded: Seeded) -> dict[str, str]:
    return {"entry_id": str(seeded.attention.id)}


def _only_the_managers_items_get_the_new_item(world: World, persona: str, response: Any) -> None:
    items = world.call("GET", f"{PREFIX}/action-items", PERSONAS["manager"]).json()["items"]
    texts = [i["text"] for i in items]
    expected = 1 if persona == "manager" else 0
    assert texts.count("Send the figures") == expected, (persona, texts)


def _only_the_manager_decides_the_entry(world: World, persona: str, response: Any) -> None:
    path = f"{PREFIX}/attention/{world.seeded.attention.id}"
    state = world.call("GET", path, PERSONAS["manager"]).json()["state"]
    assert state == ("decided" if persona == "manager" else "open"), (persona, state)


def _only_the_owners_answer_reaches_the_item(world: World, persona: str, response: Any) -> None:
    path = f"{PREFIX}/action-items/{world.seeded.item.id}"
    state = world.call("GET", path, PERSONAS["owner"]).json()["state"]
    assert state == ("open" if persona == "owner" else "waiting_for_owner"), (persona, state)


def _only_the_managers_control_reaches_the_item(
    field: str, expected: Any
) -> Callable[[World, str, Any], None]:
    def check(world: World, persona: str, response: Any) -> None:
        path = f"{PREFIX}/action-items/{world.seeded.item.id}"
        value = world.call("GET", path, PERSONAS["manager"]).json()[field]
        original = {"state": "waiting_for_owner", "due_date": "2026-11-27"}.get(field)
        assert value == (expected if persona == "manager" else original), (persona, value)

    return check


OWNER_ONLY = {"manager": 404, "other_manager": 404, "owner": 200, "administrator": 404}
"""What only the person asked may see: a status request may hold a draft (BR-4)."""
STATEMENT = {"colour": "amber", "reason": "Two contracts are still open."}


def _seeded_project(seeded: Seeded) -> dict[str, str]:
    return {"project_id": seeded.followed.project_id}


def _only_the_managers_report_is_made(world: World, persona: str, response: Any) -> None:
    reports = world.call("GET", f"{PREFIX}/reports", PERSONAS["manager"]).json()
    assert reports["count"] == (2 if persona == "manager" else 1), (persona, reports["count"])


def _only_the_manager_stops_following(world: World, persona: str, response: Any) -> None:
    projects = world.call("GET", f"{PREFIX}/projects", PERSONAS["manager"]).json()["projects"]
    hiring = next(p for p in projects if p["id"] == world.seeded.followed.project_id)
    assert hiring["followed"] is (persona != "manager"), (persona, hiring)


def _seeded_request(seeded: Seeded) -> dict[str, str]:
    return {"request_id": str(seeded.request.id)}


# Meetings (part 2) ------------------------------------------------------------------------

OWNER_REFUSED = {"manager": 404, "other_manager": 404, "owner": 422, "administrator": 404}
"""Only the organiser reaches it, and is told why it cannot be done now."""
SHARE = {
    "title": "Supplier call",
    "date": "2026-11-20",
    "manager_id": OPERATIONS_DIRECTOR.id,
    "pasted_text": "Decision: Go ahead with the supplier.",
    "ran_it": True,
    "people_knew": True,
}
OPERATIONS_REVIEW = sample_meetings()[0].id


def _seeded_meeting(seeded: Seeded) -> dict[str, str]:
    return {"meeting_id": str(seeded.shared_meeting.id)}


def _seeded_notes(seeded: Seeded) -> dict[str, str]:
    return {"notes_id": str(seeded.notes.id)}


def _seeded_action(seeded: Seeded) -> dict[str, str]:
    return {"notes_id": str(seeded.notes.id), "action_id": str(seeded.notes.actions[0].id)}


def _seeded_ask(seeded: Seeded) -> dict[str, str]:
    return {"request_id": str(seeded.organiser_ask.id)}


def _organiser_sees_title_and_state_only(world: World, persona: str, response: Any) -> None:
    assert MEETING_WORDS not in response.text
    if response.status_code == 200:
        assert set(response.json()) == {"id", "title", "state"}, response.json()


def _only_the_manager_keeps_the_action(world: World, persona: str, response: Any) -> None:
    items = world.call("GET", f"{PREFIX}/action-items", PERSONAS["manager"]).json()["items"]
    texts = [i["text"] for i in items]
    expected = 1 if persona == "manager" else 0
    assert texts.count("Check the vendor invoices") == expected, (persona, texts)


def _only_the_organiser_withdraws(world: World, persona: str, response: Any) -> None:
    listed = world.call("GET", f"{PREFIX}/transcripts", PERSONAS["owner"]).json()["transcripts"]
    state = next(t["state"] for t in listed if t["id"] == str(world.seeded.shared_meeting.id))
    assert state == ("withdrawn" if persona == "owner" else "shared"), (persona, state)


def _only_the_organiser_answers(expected: str) -> Callable[[World, str, Any], None]:
    def check(world: World, persona: str, response: Any) -> None:
        path = f"{PREFIX}/meetings/bookings/{world.seeded.attendance.id}"
        state = world.call("GET", path, PERSONAS["manager"]).json()["state"]
        assert state == (expected if persona == "owner" else "waiting_for_organiser"), state

    return check


def _only_the_managers_booking(world: World, persona: str, response: Any) -> None:
    upcoming = world.call("GET", f"{PREFIX}/meetings/upcoming", PERSONAS["manager"]).json()
    review = next(m for m in upcoming["meetings"] if m["id"] == OPERATIONS_REVIEW)
    assert (review["booking"] is not None) is (persona == "manager"), (persona, review)


def _no_transcript_words(world: World, persona: str, response: Any) -> None:
    assert MEETING_WORDS not in response.text, persona


MEETING_EXPECTATIONS: dict[tuple[str, str], Expectation] = {
    ("POST", "/api/assistant/transcripts"): Expectation(
        _no_params, EVERYONE, body=SHARE, checks=[_organiser_sees_title_and_state_only]
    ),
    ("GET", "/api/assistant/transcripts"): Expectation(
        _no_params, EVERYONE, checks=[_no_transcript_words]
    ),
    ("POST", "/api/assistant/transcripts/{meeting_id}/withdraw"): Expectation(
        _seeded_meeting,
        OWNER_ONLY,
        checks=[_organiser_sees_title_and_state_only, _only_the_organiser_withdraws],
    ),
    ("GET", "/api/assistant/meetings"): Expectation(
        _no_params, OWN_ASSISTANT, checks=[_no_transcript_words]
    ),
    ("GET", "/api/assistant/meetings/upcoming"): Expectation(_no_params, OWN_ASSISTANT),
    ("POST", "/api/assistant/meetings/upcoming/{meeting_ref}/attend"): Expectation(
        lambda s: {"meeting_ref": OPERATIONS_REVIEW},
        MANAGER_ONLY,
        checks=[_only_the_managers_booking],
    ),
    ("GET", "/api/assistant/meetings/bookings"): Expectation(_no_params, OWN_ASSISTANT),
    ("GET", "/api/assistant/meetings/bookings/{booking_id}"): Expectation(
        lambda s: {"booking_id": str(s.attendance.id)}, MANAGER_ONLY, returns_id=_own("attendance")
    ),
    ("GET", "/api/assistant/meetings/{meeting_id}"): Expectation(
        _seeded_meeting,
        MANAGER_ONLY,
        returns_id=_own("shared_meeting"),
        checks=[_no_transcript_words],
    ),
    ("POST", "/api/assistant/meetings/{meeting_id}/retry"): Expectation(
        _seeded_meeting, MANAGER_REACHES
    ),
    ("GET", "/api/assistant/meeting-notes"): Expectation(_no_params, OWN_ASSISTANT),
    ("GET", "/api/assistant/meeting-notes/search"): Expectation(_no_params, OWN_ASSISTANT),
    ("GET", "/api/assistant/meeting-notes/{notes_id}"): Expectation(
        _seeded_notes, MANAGER_ONLY, returns_id=_own("notes")
    ),
    ("DELETE", "/api/assistant/meeting-notes/{notes_id}"): Expectation(
        _seeded_notes, MANAGER_ONLY
    ),
    ("POST", "/api/assistant/meeting-notes/{notes_id}/actions/{action_id}/keep"): Expectation(
        _seeded_action, MANAGER_ONLY, checks=[_only_the_manager_keeps_the_action]
    ),
    ("PUT", "/api/assistant/meeting-notes/{notes_id}/actions/{action_id}"): Expectation(
        _seeded_action, MANAGER_ONLY, body={"due_date": "2026-11-30"}
    ),
    ("POST", "/api/assistant/meeting-notes/{notes_id}/actions/{action_id}/remove"): Expectation(
        _seeded_action, MANAGER_ONLY
    ),
    ("POST", "/api/assistant/meeting-notes/{notes_id}/share"): Expectation(
        _seeded_notes, MANAGER_ONLY
    ),
    ("POST", "/api/assistant/meeting-notes/{notes_id}/unshare"): Expectation(
        _seeded_notes, MANAGER_ONLY
    ),
    ("GET", "/api/assistant/meeting-requests"): Expectation(_no_params, EVERYONE),
    ("GET", "/api/assistant/meeting-requests/{request_id}"): Expectation(
        _seeded_ask,
        OWNER_ONLY,
        returns_id=lambda s, persona: str(s.organiser_ask.id) if persona == "owner" else None,
    ),
    ("POST", "/api/assistant/meeting-requests/{request_id}/accept"): Expectation(
        _seeded_ask, OWNER_ONLY, checks=[_only_the_organiser_answers("booked")]
    ),
    ("POST", "/api/assistant/meeting-requests/{request_id}/decline"): Expectation(
        _seeded_ask, OWNER_ONLY, checks=[_only_the_organiser_answers("declined")]
    ),
    ("POST", "/api/assistant/meeting-requests/{request_id}/remove"): Expectation(
        _seeded_ask, OWNER_REFUSED
    ),
}


# The briefing and decision papers (part 2, Epic 2) --------------------------------------------


def _managers_briefing_settings_untouched_by_others(
    world: World, persona: str, response: Any
) -> None:
    page = world.call("GET", f"{PREFIX}/briefing/settings", PERSONAS["manager"]).json()
    spend = next(s for s in page["sections"] if s["key"] == "spend")
    assert spend["ticked"] is (persona != "manager"), (persona, spend)


def _only_the_manager_gets_a_briefing(world: World, persona: str, response: Any) -> None:
    listed = world.call("GET", f"{PREFIX}/briefings", PERSONAS["manager"]).json()
    assert listed["count"] == (2 if persona == "manager" else 1), (persona, listed["count"])


def _only_the_manager_changes_the_briefing(
    expected: str,
) -> Callable[[World, str, Any], None]:
    def check(world: World, persona: str, response: Any) -> None:
        path = f"{PREFIX}/briefings/{world.seeded.briefing.id}"
        state = world.call("GET", path, PERSONAS["manager"]).json()["state"]
        assert state in ((expected,) if persona == "manager" else ("ready",)), (persona, state)

    return check


def _seeded_briefing(seeded: Seeded) -> dict[str, str]:
    return {"briefing_id": str(seeded.briefing.id)}


def _seeded_paper(seeded: Seeded) -> dict[str, str]:
    return {"paper_id": str(seeded.paper.id)}


def _the_decided_paper_stays_decided(world: World, persona: str, response: Any) -> None:
    path = f"{PREFIX}/decision-papers/{world.seeded.paper.id}"
    state = world.call("GET", path, PERSONAS["manager"]).json()["state"]
    assert state == "decided", (persona, state)


BRIEFING_EXPECTATIONS: dict[tuple[str, str], Expectation] = {
    ("GET", "/api/assistant/briefing/settings"): Expectation(_no_params, OWN_ASSISTANT),
    ("PUT", "/api/assistant/briefing/settings"): Expectation(
        _no_params,
        OWN_ASSISTANT,
        body={"sections": {"spend": False}},
        checks=[_managers_briefing_settings_untouched_by_others],
    ),
    ("GET", "/api/assistant/briefings"): Expectation(_no_params, OWN_ASSISTANT),
    ("POST", "/api/assistant/briefings"): Expectation(
        _no_params, OWN_ASSISTANT, checks=[_only_the_manager_gets_a_briefing]
    ),
    ("GET", "/api/assistant/briefings/{briefing_id}"): Expectation(
        _seeded_briefing, MANAGER_ONLY, returns_id=_own("briefing")
    ),
    ("POST", "/api/assistant/briefings/{briefing_id}/open"): Expectation(
        _seeded_briefing,
        MANAGER_ONLY,
        returns_id=_own("briefing"),
        checks=[_only_the_manager_changes_the_briefing("accepted")],
    ),
    ("POST", "/api/assistant/briefings/{briefing_id}/send-back"): Expectation(
        _seeded_briefing,
        MANAGER_ONLY,
        returns_id=_own("briefing"),
        body={"comment": "Say more about spend."},
        checks=[_only_the_manager_changes_the_briefing("sent_back")],
    ),
    ("GET", "/api/assistant/decision-papers"): Expectation(_no_params, OWN_ASSISTANT),
    ("GET", "/api/assistant/decision-papers/{paper_id}"): Expectation(
        _seeded_paper, MANAGER_ONLY, returns_id=_own("paper")
    ),
    ("POST", "/api/assistant/decision-papers/{paper_id}/decide"): Expectation(
        _seeded_paper,
        MANAGER_REACHES,
        body={"option": 1},
        checks=[_the_decided_paper_stays_decided],
    ),
    ("POST", "/api/assistant/decision-papers/{paper_id}/send-back"): Expectation(
        _seeded_paper,
        MANAGER_REACHES,
        body={"comment": "Add the costs."},
        checks=[_the_decided_paper_stays_decided],
    ),
    ("POST", "/api/assistant/decision-papers/{paper_id}/retry"): Expectation(
        _seeded_paper, MANAGER_REACHES, checks=[_the_decided_paper_stays_decided]
    ),
}


EXPECTATIONS: dict[tuple[str, str], Expectation] = {
    ("GET", "/api/assistant/assistant"): Expectation(
        _no_params,
        OWN_ASSISTANT,
        returns_id=lambda s, persona: {
            "manager": str(s.assistant.id),
            "other_manager": str(s.other_assistant.id),
        }.get(persona),
    ),
    ("POST", "/api/assistant/assistant/opened"): Expectation(
        _no_params, OWN_ASSISTANT, body={"time_zone": "Europe/London"}
    ),
    ("POST", "/api/assistant/assistant/pause"): Expectation(
        _no_params, OWN_ASSISTANT, checks=[_only_the_managers_pause_reaches_their_assistant]
    ),
    ("POST", "/api/assistant/assistant/resume"): Expectation(_no_params, OWN_ASSISTANT),
    ("GET", "/api/assistant/preferences"): Expectation(_no_params, OWN_ASSISTANT),
    ("PUT", "/api/assistant/preferences"): Expectation(
        _no_params,
        OWN_ASSISTANT,
        body=SAMPLE_FORM,
        checks=[_managers_preferences_untouched_by_others],
    ),
    ("POST", "/api/assistant/preferences/skip"): Expectation(_no_params, OWN_ASSISTANT),
    ("GET", "/api/assistant/approval-levels"): Expectation(_no_params, OWN_ASSISTANT),
    ("PUT", "/api/assistant/approval-levels/{kind}"): Expectation(
        lambda s: {"kind": "send_message"},
        OWN_ASSISTANT,
        body={"level": "without_approval", "limits": {"per_day": 2}},
        checks=[_managers_levels_untouched_by_others],
    ),
    ("GET", "/api/assistant/assistants/{assistant_id}"): Expectation(
        lambda s: {"assistant_id": str(s.assistant.id)}, MANAGER_ONLY
    ),
    ("GET", "/api/assistant/chat"): Expectation(_no_params, OWN_ASSISTANT),
    ("POST", "/api/assistant/chat"): Expectation(
        _no_params,
        OWN_ASSISTANT,
        body={"text": "Hello"},
        checks=[_chat_reaches_only_the_managers_assistant],
    ),
    ("GET", "/api/assistant/work/{work_id}"): Expectation(
        lambda s: {"work_id": str(s.work.id)}, MANAGER_ONLY, returns_id=_own("work")
    ),
    ("POST", "/api/assistant/work/{work_id}/retry"): Expectation(
        lambda s: {"work_id": str(s.work.id)}, MANAGER_ONLY
    ),
    ("GET", "/api/assistant/requests"): Expectation(_no_params, OWN_ASSISTANT),
    ("POST", "/api/assistant/requests/approve-all"): Expectation(_no_params, OWN_ASSISTANT),
    ("GET", "/api/assistant/requests/{request_id}"): Expectation(
        _seeded_request, MANAGER_ONLY, returns_id=_own("request")
    ),
    ("POST", "/api/assistant/requests/{request_id}/approve"): Expectation(
        _seeded_request,
        MANAGER_ONLY,
        body={"text": "Please send the budget figures today."},
        checks=[_only_the_managers_decision_reaches_the_request("done")],
    ),
    ("POST", "/api/assistant/requests/{request_id}/decline"): Expectation(
        _seeded_request,
        MANAGER_ONLY,
        body={"reason": "Not now."},
        checks=[_only_the_managers_decision_reaches_the_request("declined")],
    ),
    ("POST", "/api/assistant/requests/{request_id}/retry"): Expectation(
        _seeded_request, MANAGER_REACHES
    ),
    ("POST", "/api/assistant/requests/{request_id}/undo"): Expectation(
        _seeded_request, MANAGER_REACHES
    ),
    ("GET", "/api/assistant/done"): Expectation(_no_params, OWN_ASSISTANT),
    ("GET", "/api/assistant/action-items"): Expectation(_no_params, OWN_ASSISTANT),
    ("POST", "/api/assistant/action-items"): Expectation(
        _no_params,
        OWN_ASSISTANT,
        body={"text": "Send the figures", "owner_id": FINANCE_LEAD.id, "due_date": "2026-11-27"},
        checks=[_only_the_managers_items_get_the_new_item],
    ),
    ("GET", "/api/assistant/my-action-items"): Expectation(_no_params, EVERYONE),
    ("GET", "/api/assistant/assignments"): Expectation(_no_params, OWN_ASSISTANT),
    ("GET", "/api/assistant/action-items/{item_id}"): Expectation(
        _seeded_item,
        OWNER_OR_MANAGER,
        returns_id=lambda s, persona: str(s.item.id) if persona in ("manager", "owner") else None,
    ),
    ("POST", "/api/assistant/action-items/{item_id}/accept"): Expectation(
        _seeded_item,
        OWNER_ANSWERS,
        checks=[_only_the_owners_answer_reaches_the_item],
    ),
    ("POST", "/api/assistant/action-items/{item_id}/propose-date"): Expectation(
        _seeded_item, OWNER_ANSWERS, body={"due_date": "2026-12-04", "reason": "Legal is away."}
    ),
    ("POST", "/api/assistant/action-items/{item_id}/decline"): Expectation(
        _seeded_item, OWNER_ANSWERS, body={"reason": "This is not mine."}
    ),
    ("POST", "/api/assistant/action-items/{item_id}/updates"): Expectation(
        _seeded_item, OWNER_REACHES, body={"word": "on_track"}
    ),
    ("PUT", "/api/assistant/action-items/{item_id}/follow-ups"): Expectation(
        _seeded_item, MANAGER_ONLY, body={"times": ["2026-11-25T09:00:00+00:00"]}
    ),
    ("POST", "/api/assistant/action-items/{item_id}/extend"): Expectation(
        _seeded_item,
        MANAGER_ONLY,
        body={"due_date": "2026-12-04"},
        checks=[_only_the_managers_control_reaches_the_item("due_date", "2026-12-04")],
    ),
    ("POST", "/api/assistant/action-items/{item_id}/reassign"): Expectation(
        _seeded_item, MANAGER_ONLY, body={"owner_id": FINANCE_LEAD.id}
    ),
    ("POST", "/api/assistant/action-items/{item_id}/close"): Expectation(
        _seeded_item,
        MANAGER_ONLY,
        body={"reason": "No longer needed."},
        checks=[_only_the_managers_control_reaches_the_item("state", "closed")],
    ),
    ("POST", "/api/assistant/action-items/{item_id}/done"): Expectation(
        _seeded_item, MANAGER_REACHES
    ),
    ("GET", "/api/assistant/attention"): Expectation(_no_params, OWN_ASSISTANT),
    ("GET", "/api/assistant/attention/{entry_id}"): Expectation(
        _seeded_entry, MANAGER_ONLY, returns_id=_own("attention")
    ),
    ("POST", "/api/assistant/attention/{entry_id}/decide"): Expectation(
        _seeded_entry,
        MANAGER_ONLY,
        body={"choice": "Renew it"},
        checks=[_only_the_manager_decides_the_entry],
    ),
    ("GET", "/api/assistant/person-requests/{request_id}"): Expectation(
        lambda s: {"request_id": str(s.person_request.id)}, OWNER_OR_MANAGER
    ),
    ("GET", "/api/assistant/statements/{statement_id}"): Expectation(
        lambda s: {"statement_id": str(s.statement.id)}, OWNER_OR_MANAGER
    ),
    ("POST", "/api/assistant/statements/{statement_id}/correct"): Expectation(
        lambda s: {"statement_id": str(s.statement.id)}, OWNER_ONLY, body=STATEMENT
    ),
    ("GET", "/api/assistant/projects"): Expectation(_no_params, OWN_ASSISTANT),
    ("POST", "/api/assistant/projects/{project_id}/follow"): Expectation(
        _seeded_project, MANAGER_ONLY
    ),
    ("PUT", "/api/assistant/projects/{project_id}/confirmer"): Expectation(
        _seeded_project, MANAGER_ONLY, body={"person_id": FINANCE_LEAD.id}
    ),
    ("POST", "/api/assistant/projects/{project_id}/unfollow"): Expectation(
        _seeded_project, MANAGER_ONLY, checks=[_only_the_manager_stops_following]
    ),
    ("GET", "/api/assistant/status"): Expectation(_no_params, OWN_ASSISTANT),
    ("GET", "/api/assistant/reports"): Expectation(_no_params, OWN_ASSISTANT),
    ("POST", "/api/assistant/reports"): Expectation(
        _no_params, OWN_ASSISTANT, checks=[_only_the_managers_report_is_made]
    ),
    ("GET", "/api/assistant/reports/{report_id}"): Expectation(
        lambda s: {"report_id": str(s.report.id)}, MANAGER_ONLY, returns_id=_own("report")
    ),
    ("GET", "/api/assistant/status-requests"): Expectation(_no_params, EVERYONE),
    ("GET", "/api/assistant/status-requests/{request_id}"): Expectation(
        lambda s: {"request_id": str(s.status_ask.id)}, OWNER_ONLY
    ),
    ("POST", "/api/assistant/status-requests/{request_id}/answer"): Expectation(
        lambda s: {"request_id": str(s.status_ask.id)}, OWNER_ONLY, body=STATEMENT
    ),
    ("GET", "/api/assistant/organisation/settings"): Expectation(_no_params, ADMINISTRATOR_ONLY),
    ("PUT", "/api/assistant/organisation/settings"): Expectation(
        _no_params,
        ADMINISTRATOR_ONLY,
        body={"colour_meanings": {"green": "On plan"}},
        checks=[_meanings_written_only_by_administrators],
    ),
    ("GET", "/api/assistant/colours"): Expectation(_no_params, EVERYONE),
    ("GET", "/api/assistant/my-statements"): Expectation(_no_params, EVERYONE),
    ("GET", "/api/assistant/waiting-count"): Expectation(_no_params, OWN_ASSISTANT),
    ("GET", "/api/assistant/people"): Expectation(_no_params, EVERYONE),
    **MEETING_EXPECTATIONS,
    **BRIEFING_EXPECTATIONS,
}


def _walk(routes: Iterable[BaseRoute], prefix: str = "") -> Iterator[tuple[str, str]]:
    for route in routes:
        if isinstance(route, APIRoute):
            for method in route.methods - {"HEAD", "OPTIONS"}:
                yield method, prefix + route.path
            continue
        # Included routers: FastAPI >= 0.16x keeps them as one wrapper route.
        included = getattr(route, "original_router", None)
        if included is not None:
            context = getattr(route, "include_context", None)
            yield from _walk(included.routes, prefix + (getattr(context, "prefix", "") or ""))
        elif isinstance(route, Mount):
            yield from _walk(route.routes, prefix + route.path)


def api_addresses(app: FastAPI) -> set[tuple[str, str]]:
    """Every (method, path) the app answers under the module's prefix."""
    found = set(_walk(app.routes))
    if not found:
        raise AssertionError("no routes found: the route walk does not understand this FastAPI")
    return {(method, path) for method, path in found if path.startswith(PREFIX)}


def addresses_without_expectation(app: FastAPI) -> set[tuple[str, str]]:
    return api_addresses(app) - set(EXPECTATIONS)


class ApiVisibilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.world = build_world_sync()
        self.seeded = self.world.seeded
        self.app = self.world.app

    def tearDown(self) -> None:
        self.world.close()

    def _call(self, method: str, path: str, person_id: str | None, json: Any = None) -> Any:
        return self.world.call(method, path, person_id, json)

    def test_visibility_test_covers_every_address(self) -> None:
        """Story 1.3 - it calls every address as all four personas."""
        missing = addresses_without_expectation(self.app)
        self.assertEqual(missing, set(), f"addresses without an expectation: {sorted(missing)}")
        stale = set(EXPECTATIONS) - api_addresses(self.app)
        self.assertEqual(stale, set(), f"expectations for addresses that do not exist: {stale}")

        for (method, template), expectation in EXPECTATIONS.items():
            self.assertEqual(set(expectation.status), set(PERSONAS))
            for persona, person_id in PERSONAS.items():
                with self.subTest(address=f"{method} {template}", persona=persona):
                    world = build_world_sync()
                    try:
                        path = template.format(**expectation.path_params(world.seeded))
                        response = world.call(method, path, person_id, expectation.body)
                        self.assertEqual(
                            response.status_code, expectation.status[persona], response.text
                        )
                        if response.status_code == 404:
                            self.assertEqual(response.json(), NOT_FOUND_BODY)
                        expected_id = expectation.returns_id(world.seeded, persona)
                        if expected_id is not None:
                            self.assertEqual(response.json()["id"], expected_id)
                        for check in expectation.checks:
                            check(world, persona, response)
                    finally:
                        world.close()

    def test_sweep_fails_on_an_address_it_does_not_know(self) -> None:
        self.app.add_api_route(f"{PREFIX}/surprise", lambda: {}, methods=["POST"])
        self.assertEqual(addresses_without_expectation(self.app), {("POST", f"{PREFIX}/surprise")})

    def test_sweep_fails_on_a_new_module_address_without_expectation(self) -> None:
        module = create_module(self.world.standins.boundary, self.world.store)
        module.router.add_api_route("/items/{item_id}", lambda item_id: {}, methods=["DELETE"])
        app = FastAPI()
        app.include_router(module.router)
        self.assertEqual(
            addresses_without_expectation(app), {("DELETE", f"{PREFIX}/items/{{item_id}}")}
        )

    def test_not_theirs_looks_exactly_like_not_found(self) -> None:
        """Story 1.3 - not-theirs looks like not-found (404)."""
        for (method, template), expectation in EXPECTATIONS.items():
            params = expectation.path_params(self.seeded)
            if not params:
                continue
            real = template.format(**params)
            missing = template.format(**{k: str(uuid4()) for k in params})
            malformed = template.format(**{k: "not-an-id" for k in params})
            for persona, status in expectation.status.items():
                if status != 404:
                    continue
                with self.subTest(address=template, persona=persona):
                    body = expectation.body
                    not_theirs = self._call(method, real, PERSONAS[persona], body)
                    absent = self._call(method, missing, PERSONAS[persona], body)
                    bad = self._call(method, malformed, PERSONAS[persona], body)
                    for other in (absent, bad):
                        self.assertEqual(not_theirs.status_code, other.status_code)
                        self.assertEqual(not_theirs.content, other.content)
                        self.assertEqual(dict(not_theirs.headers), dict(other.headers))

    def test_someone_from_another_organisation_finds_nothing(self) -> None:
        path = f"{PREFIX}/action-items/{self.seeded.item.id}"
        response = self._call("GET", path, OUTSIDER.id)
        self.assertEqual((response.status_code, response.json()), (404, NOT_FOUND_BODY))

    def test_unconfirmed_wording_is_never_shown(self) -> None:
        path = f"{PREFIX}/statements/{self.seeded.statement.id}"
        for persona in ("manager", "owner"):
            response = self._call("GET", path, PERSONAS[persona])
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(UNSHOWN_WORDING, response.text)
            self.assertNotIn("unconfirmed_wording", response.json())

    def test_no_person_named_is_not_signed_in(self) -> None:
        response = self._call("GET", f"{PREFIX}/assistant", None)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"]["code"], "not_signed_in")

    def test_people_unavailable_is_one_plain_sentence(self) -> None:
        async def broken(headers: Any) -> None:
            raise ConnectionError("dns failure")

        self.world.standins.people.person_asking = broken  # type: ignore[method-assign]
        response = self._call("GET", f"{PREFIX}/assistant", PERSONAS["manager"])
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            {
                "code": "unavailable",
                "message": "The people directory could not be reached, so nothing was changed.",
            },
        )


if __name__ == "__main__":
    unittest.main()
