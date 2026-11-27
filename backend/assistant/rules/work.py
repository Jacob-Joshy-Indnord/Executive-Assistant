"""Pieces of work and the chat: what may be said and how reasons are shown. Pure rules."""

from __future__ import annotations

from assistant.models.records import CHAT_LIMIT, WorkState
from assistant.rules.sentences import is_plain_sentence

EMPTY_MESSAGE = "Write a message first."
CHAT_TOO_LONG = "A chat message can be at most 4,000 characters."
REPLY_TOO_LONG = "The reply was over 4,000 characters, so it was refused."
FORM_FIRST = "Save or skip the first-time form before giving your assistant work."
NOT_RETRYABLE = "Only work that was refused or failed can be tried again."
FLOW_REFUSED = "Flow refused this work."
WORK_FAILED = "The assistant could not finish this work."
NOT_STARTED = "The work could not be handed to the assistant."

RETRYABLE = frozenset({WorkState.REFUSED, WorkState.FAILED})


def chat_refusal(text: str) -> str | None:
    """Why a chat message or reply cannot be kept, in one sentence; ``None`` if it can."""
    if not text.strip():
        return EMPTY_MESSAGE
    if len(text) > CHAT_LIMIT:
        return CHAT_TOO_LONG
    return None


def one_sentence(reason: str | None, fallback: str) -> str:
    """Flow's reason when it is one plain sentence; otherwise ``fallback``."""
    reason = (reason or "").strip()
    return reason if is_plain_sentence(reason) else fallback
