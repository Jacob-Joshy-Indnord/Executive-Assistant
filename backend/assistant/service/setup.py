"""Set up and the first-time form (Story 2.1)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from assistant.boundary.interfaces import Boundary
from assistant.errors import NotAllowedValue
from assistant.models.preferences import PreferencesForm, valid_time_zone
from assistant.models.records import AssistantRecord, AssistantState, Preferences
from assistant.rules.settings import Settings, effective_settings
from assistant.rules.visibility import Viewer
from assistant.service.actions import Action, perform
from assistant.service.assistants import current_assistant, my_assistant
from assistant.service.input_pack import preferences_of
from assistant.store.interface import Store, Transaction

FLOW = "flow"
"""Who a record names when Flow, not a person, made the change."""

ALREADY_HAS_ONE = "This manager already has an Executive Assistant."

SetUpHook = Callable[[Action, AssistantRecord], Awaitable[None]]


def page_view(assistant: AssistantRecord, running: bool) -> dict[str, Any]:
    """The assistant as its page needs it: the form is offered while it is new."""
    return {
        **assistant.model_dump(mode="json"),
        "form_offered": assistant.state is AssistantState.NEW,
        "running": running,
    }


def preferences_view(assistant: AssistantRecord, preferences: Preferences | None) -> dict[str, Any]:
    settings = settings_from(preferences)
    return {
        "form": (preferences.form if preferences else PreferencesForm()).model_dump(mode="json"),
        "saved": bool(preferences and preferences.saved),
        "skipped": bool(preferences and preferences.skipped),
        "form_offered": assistant.state is AssistantState.NEW,
        "settings": {
            "working_days": [d.value for d in settings.working_days],
            "starts": settings.starts.strftime("%H:%M"),
            "ends": settings.ends.strftime("%H:%M"),
            "time_zone": settings.time_zone,
            "follow_ups": settings.follow_ups,
            "answer_within_working_days": settings.answer_within_working_days,
            "late_after_working_days": settings.late_after_working_days,
            "notices_per_owner_per_day": settings.notices_per_owner_per_day,
            "report_frequency": settings.report_frequency,
            "report_day": settings.report_day.value,
            "report_time": settings.report_time.strftime("%H:%M"),
            "ask_owners_working_days_before": settings.ask_owners_working_days_before,
        },
    }


def settings_from(preferences: Preferences | None) -> Settings:
    if preferences is None or not preferences.saved:
        return effective_settings(None, preferences.first_open_time_zone if preferences else None)
    return effective_settings(preferences.form, preferences.first_open_time_zone)


async def settings_for(reader: Transaction | Action, assistant: AssistantRecord) -> Settings:
    """What schedules use: the saved answers, or the section 8.1 defaults."""
    return settings_from(await preferences_of(reader, assistant))


class SetupService:
    def __init__(self, boundary: Boundary, store: Store) -> None:
        self.boundary = boundary
        self.store = store
        self.on_set_up: list[SetUpHook] = []
        """Run inside the set-up action, e.g. to give the assistant its starting levels."""

    async def ensure_can_take_an_assistant(self, organisation_id: str, manager_id: str) -> None:
        """A manager must be a person of the organisation; checked outside any transaction."""
        person = await self.boundary.people.person(manager_id)
        if person is None or person.organisation_id != organisation_id or person.kind != "person":
            raise NotAllowedValue("The manager must be a person in the organisation.")

    async def set_up(
        self,
        organisation_id: str,
        manager_id: str,
        display_name: str,
        flow_id: str,
        set_up_by: str = FLOW,
    ) -> AssistantRecord:
        """Flow's Set up call: a new assistant, with its form still to be filled in."""
        await self.ensure_can_take_an_assistant(organisation_id, manager_id)

        async def work(action: Action) -> AssistantRecord:
            existing = await current_assistant(action, manager_id)
            if existing is not None:
                if existing.flow_id == flow_id:
                    return existing  # the same call again
                raise NotAllowedValue(ALREADY_HAS_ONE)
            if [a for a in await action.find(AssistantRecord, flow_id=flow_id)
                    if a.state is not AssistantState.ENDED]:  # fmt: skip
                raise NotAllowedValue("This assistant is already set up for another manager.")
            return await self.create(action, manager_id, display_name, flow_id)

        return await perform(self.boundary, self.store, organisation_id, set_up_by, work)

    async def create(
        self, action: Action, manager_id: str, display_name: str, flow_id: str
    ) -> AssistantRecord:
        """A new assistant record, with what every new assistant starts with (the hooks)."""
        assistant = await action.add(
            "assistant.assistant_set_up",
            AssistantRecord(
                organisation_id=action.tx.organisation_id,
                created_at=action.now,
                manager_id=manager_id,
                display_name=display_name,
                flow_id=flow_id,
                state=AssistantState.NEW,
                manager_since=action.now,
            ),
        )
        for hook in self.on_set_up:
            await hook(action, assistant)
        return assistant

    # The manager's page and form ----------------------------------------------

    async def first_opened(self, viewer: Viewer, time_zone: str | None) -> AssistantRecord:
        """Keep the browser's time zone the first time the manager opens the page."""
        if time_zone is not None:
            try:
                valid_time_zone(time_zone)
            except ValueError as exc:
                raise NotAllowedValue(str(exc)) from exc

        async def work(action: Action) -> AssistantRecord:
            assistant = await my_assistant(action.tx, viewer)
            preferences = await preferences_of(action, assistant)
            if preferences is None:
                await action.add(
                    "assistant.page_first_opened",
                    Preferences(
                        organisation_id=assistant.organisation_id,
                        assistant_id=assistant.id,
                        created_at=action.now,
                        first_open_time_zone=time_zone,
                    ),
                )
            elif preferences.first_open_time_zone is None and time_zone is not None:
                await action.change(
                    "assistant.page_first_opened",
                    preferences,
                    preferences.evolve(first_open_time_zone=time_zone),
                )
            return assistant

        return await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )

    async def preferences(self, viewer: Viewer) -> dict[str, Any]:
        async with self.store.transaction(viewer.organisation_id) as tx:
            assistant = await my_assistant(tx, viewer)
            return preferences_view(assistant, await preferences_of(tx, assistant))

    async def save(self, viewer: Viewer, form: PreferencesForm) -> dict[str, Any]:
        """Save the form: its answers apply from now on, and a new assistant becomes active."""
        return await self._answer(viewer, form)

    async def skip(self, viewer: Viewer) -> dict[str, Any]:
        """Skip the form: the defaults apply and the form stays available in Settings."""
        return await self._answer(viewer, None)

    async def _answer(self, viewer: Viewer, form: PreferencesForm | None) -> dict[str, Any]:
        async def work(action: Action) -> dict[str, Any]:
            assistant = await my_assistant(action.tx, viewer)
            preferences = await preferences_of(action, assistant)
            if preferences is None:
                preferences = await action.add(
                    "assistant.preferences_created",
                    Preferences(
                        organisation_id=assistant.organisation_id,
                        assistant_id=assistant.id,
                        created_at=action.now,
                    ),
                )
            if form is not None:
                preferences = await action.change(
                    "assistant.form_saved",
                    preferences,
                    preferences.evolve(form=form, saved=True, skipped=False),
                    details={"parts": sorted(form.model_dump(exclude_none=True))},
                )
            elif not preferences.saved and not preferences.skipped:
                preferences = await action.change(
                    "assistant.form_skipped", preferences, preferences.evolve(skipped=True)
                )
            if assistant.state is AssistantState.NEW:
                assistant = await action.change(
                    "assistant.assistant_activated",
                    assistant,
                    assistant.evolve(state=AssistantState.ACTIVE),
                )
            return preferences_view(assistant, preferences)

        return await perform(
            self.boundary, self.store, viewer.organisation_id, viewer.person_id, work
        )
