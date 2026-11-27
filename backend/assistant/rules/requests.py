"""Requests and the manager's decisions: what is shown and when. Pure rules."""

from __future__ import annotations

from assistant.models.records import REASON_LIMIT, RequestState

RESUME_FIRST = "Resume your assistant before approving its requests."
RESUME_TO_UNDO = "Resume your assistant before undoing what it did."
CANNOT_EDIT = "This request cannot be edited."
EMPTY_TEXT = "The text cannot be empty."
REASON_TOO_LONG = "A reason can be at most 500 characters."
FLOW_DECLINED = "Flow declined this request."
FLOW_STILL_ASKS = "Flow did not allow this request yet."
CARRY_OUT_FAILED = "Flow could not carry this out."
NOT_REACHED = "Flow could not be reached, so this was not carried out."
WITHDRAWN = "This request was withdrawn because what it was about has changed."
NOT_RETRYABLE = "Only a request that could not be carried out can be tried again."
CANNOT_UNDO = "This can no longer be undone."
UNDO_FAILED = "Flow could not undo this."
EFFECT_LIMIT = 300

OPEN_STATES = frozenset({RequestState.WAITING, RequestState.APPROVED})
"""Requests not yet carried out: withdrawn when their subject moves on."""
DONE_FOR_YOU_STATES = frozenset({RequestState.DONE, RequestState.UNDONE, RequestState.FAILED})


def text_refusal(text: str, limit: int | None) -> str | None:
    """Why edited text cannot be used, in one sentence; ``None`` when it can."""
    if not text.strip():
        return EMPTY_TEXT
    if limit is not None and len(text) > limit:
        return f"The text can be at most {limit:,} characters."
    return None


def reason_refusal(reason: str | None) -> str | None:
    if reason is not None and len(reason) > REASON_LIMIT:
        return REASON_TOO_LONG
    return None


def short(sentence: str, limit: int = EFFECT_LIMIT) -> str:
    """A sentence cut to ``limit`` characters, still ending in a full stop."""
    if len(sentence) <= limit:
        return sentence
    return sentence[: limit - 4].rstrip(" .") + "..."
