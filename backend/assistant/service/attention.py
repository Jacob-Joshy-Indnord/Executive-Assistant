"""Attention entries for action items (Story 4.4) and followed projects (Story 5.1, 5.3).

An item has at most one open entry. A trigger raises one, or updates the open
one with its new reason; an update that settles the reason clears it; the
manager's decision marks it decided, with the decision and whom it concerned.
Each entry gets an id derived from its subject and how many entries it had
before, so two triggers at the same moment cannot both add one: the store
refuses the second and that action is tried again.
"""

from __future__ import annotations

from datetime import date
from uuid import NAMESPACE_URL, UUID, uuid5

from assistant.models.records import ActionItem, AttentionEntry, AttentionState
from assistant.rules.items import CHOICES, REASON_SENTENCES, Reason, settles
from assistant.service.actions import Action
from assistant.store.interface import Transaction

ITEM_SUBJECT = "action_item"


def entry_id(item: ActionItem, ordinal: int) -> UUID:
    return uuid5(NAMESPACE_URL, f"https://ea-001.invalid/attention/{item.id}/{ordinal}")


async def entries_of(reader: Transaction | Action, item: ActionItem) -> list[AttentionEntry]:
    return await reader.find(AttentionEntry, subject_kind=ITEM_SUBJECT, subject_id=str(item.id))


async def open_entry(reader: Transaction | Action, item: ActionItem) -> AttentionEntry | None:
    found = [e for e in await entries_of(reader, item) if e.state is AttentionState.OPEN]
    return found[0] if found else None


async def raise_entry(
    action: Action,
    *,
    subject_kind: str,
    subject_id: str,
    organisation_id: str,
    assistant_id: UUID | None,
    reason_code: str,
    reason: str,
    choices: list[str],
    asked_date: date | None = None,
) -> AttentionEntry:
    """Raise the subject's entry, or update its open one with the new reason: a subject
    never has two open entries."""
    fields = {
        "reason": reason,
        "reason_code": reason_code,
        "choices": list(choices),
        "asked_date": asked_date,
    }
    entries = await action.find(AttentionEntry, subject_kind=subject_kind, subject_id=subject_id)
    current = next((e for e in entries if e.state is AttentionState.OPEN), None)
    details = {"trigger": reason_code, "subject_kind": subject_kind, "subject_id": subject_id}
    if current is not None:
        if current.reason_code == reason_code and current.asked_date == asked_date:
            return current
        return await action.change(
            "assistant.attention_updated", current, current.evolve(**fields), details=details
        )
    return await action.add(
        "assistant.attention_raised",
        AttentionEntry(
            id=uuid5(
                NAMESPACE_URL, f"https://ea-001.invalid/attention/{subject_id}/{len(entries)}"
            ),
            organisation_id=organisation_id,
            assistant_id=assistant_id,
            created_at=action.now,
            subject_kind=subject_kind,
            subject_id=subject_id,
            **fields,
        ),
        details=details,
    )


async def raise_for_item(
    action: Action, item: ActionItem, reason: Reason, asked_date: date | None = None
) -> AttentionEntry:
    """Raise the item's entry, or update its open one with the new reason."""
    return await raise_entry(
        action,
        subject_kind=ITEM_SUBJECT,
        subject_id=str(item.id),
        organisation_id=item.organisation_id,
        assistant_id=item.assistant_id,
        reason_code=reason.value,
        reason=REASON_SENTENCES[reason],
        choices=CHOICES[reason],
        asked_date=asked_date,
    )


async def open_entry_about(
    reader: Transaction | Action, subject_kind: str, subject_id: str
) -> AttentionEntry | None:
    found = await reader.find(AttentionEntry, subject_kind=subject_kind, subject_id=subject_id)
    return next((e for e in found if e.state is AttentionState.OPEN), None)


async def clear_about(
    action: Action, subject_kind: str, subject_id: str, because: str
) -> AttentionEntry | None:
    """Clear the subject's open entry, if any: what it was about is settled."""
    current = await open_entry_about(action, subject_kind, subject_id)
    if current is None:
        return None
    return await action.change(
        "assistant.attention_cleared",
        current,
        current.evolve(state=AttentionState.CLEARED),
        details={"because": because, "subject_kind": subject_kind},
    )


async def clear_for_item(
    action: Action, item: ActionItem, because: str, word: str | None = None
) -> AttentionEntry | None:
    """Clear the open entry when ``word`` settles its reason; with no word, clear it anyway
    (the item is done)."""
    current = await open_entry(action, item)
    if current is None or (word is not None and not settles(current.reason_code, word)):
        return None
    return await action.change(
        "assistant.attention_cleared",
        current,
        current.evolve(state=AttentionState.CLEARED),
        details={"because": because, "item_id": str(item.id)},
    )


async def decide_for_item(
    action: Action, item: ActionItem, decision: str, target_id: str | None = None
) -> AttentionEntry | None:
    """The manager decided about the item: its open entry, if any, is marked decided."""
    current = await open_entry(action, item)
    if current is None:
        return None
    return await mark_decided(action, current, decision, target_id)


async def mark_decided(
    action: Action, entry: AttentionEntry, decision: str, target_id: str | None = None
) -> AttentionEntry:
    return await action.change(
        "assistant.attention_decided",
        entry,
        entry.evolve(
            state=AttentionState.DECIDED,
            decision=decision,
            decision_target_id=target_id,
            decided_by=action.who,
            decided_at=action.now,
        ),
        details={
            # The position of the choice, never its words: an assistant's choices are text.
            "choice": entry.choices.index(decision) + 1 if decision in entry.choices else 0,
            **({"target_id": target_id} if target_id else {}),
        },
    )
