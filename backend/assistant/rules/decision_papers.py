"""Decision papers and the decisions taken on them (part 2, Story 2.2). Pure rules.

A paper holds the decision, its background, two to five options, a recommendation,
a due date and its sources. It is refused when it has fewer than two options or
more than five, no recommendation, or a source that was not in the pack (BRD
section 11); a refused paper is never kept or shown. A decision belongs to the
manager: once taken it cannot be changed, and a decided paper cannot be sent back
(BR-7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from assistant.models.records import (
    COMMENT_LIMIT,
    DECISION_PAPER_LIMIT,
    MAX_OPTIONS,
    MIN_OPTIONS,
    DecisionPaper,
    DecisionPaperState,
    PaperVersion,
)

NOT_PREPARED = "The decision paper could not be prepared."
NO_PAPER_RETURNED = "The decision paper was refused because no decision paper came back."
DECIDED = "This decision has been taken and cannot be changed."
DECIDED_NOT_SENT_BACK = (
    "This decision has been taken and cannot be changed, so the paper cannot be sent back."
)
NOT_WAITING = "This decision paper is not waiting for a decision."
BEING_PREPARED = "A new version of this decision paper is being prepared."
NOT_RETRYABLE = "Only a decision paper that could not be prepared can be tried again."
ONE_ANSWER = "Pick one of the options or write your own decision."
NO_SUCH_OPTION = "Pick one of the options offered."
OWN_DECISION_LIMIT = 500
OWN_TOO_LONG = f"Your decision can be at most {OWN_DECISION_LIMIT} characters."
COMMENT_NEEDED = "Give a comment to send the decision paper back."
COMMENT_TOO_LONG = f"A comment can be at most {COMMENT_LIMIT} characters."
REFUSALS_BEFORE_GIVING_UP = 2
"""A paper refused this many times in a row is not prepared: the manager can try again."""


def _refused(why: str) -> str:
    return f"The decision paper was refused because {why}."


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


@dataclass
class CheckedPaper:
    fields: dict[str, Any] = field(default_factory=dict)
    """What a version is made from, when the paper passed every check."""
    refused: list[str] = field(default_factory=list)


def check_paper(raw: dict[str, Any], labels: set[str]) -> CheckedPaper:
    """Every check on its own, so the assistant is told each reason at once."""
    reasons: list[str] = []
    decision = _text(raw.get("decision"))
    background = _text(raw.get("background"))
    recommendation = _text(raw.get("recommendation"))
    options_raw = raw.get("options")
    options = [_text(o) for o in options_raw] if isinstance(options_raw, list) else []
    sources_raw = raw.get("sources")
    sources = list(sources_raw) if isinstance(sources_raw, list) else []

    if not decision:
        reasons.append(_refused("it does not say what is to be decided"))
    if len(options) < MIN_OPTIONS:
        reasons.append(_refused("it has fewer than two options"))
    elif len(options) > MAX_OPTIONS:
        reasons.append(_refused("it has more than five options"))
    elif not all(options):
        reasons.append(_refused("an option is empty"))
    if not recommendation:
        reasons.append(_refused("it has no recommendation"))
    due: date | None = None
    try:
        due = date.fromisoformat(str(raw.get("due_date")))
    except ValueError:
        reasons.append(_refused("its due date could not be read"))
    if not sources:
        reasons.append(_refused("it cites no source"))
    elif not all(isinstance(s, str) and s in labels for s in sources):
        reasons.append(_refused("it cites a source that was not in the pack"))
    size = len(decision) + len(background) + len(recommendation) + sum(map(len, options))
    if size > DECISION_PAPER_LIMIT:
        reasons.append(_refused(f"it is longer than {DECISION_PAPER_LIMIT:,} characters"))
    if reasons:
        return CheckedPaper(refused=reasons)
    return CheckedPaper(
        fields={
            "decision": decision,
            "background": background,
            "options": options,
            "recommendation": recommendation,
            "due_date": due,
            "sources": list(dict.fromkeys(sources)),
        }
    )


def is_late(version: PaperVersion, today: date) -> bool:
    """Late once its due date has passed, in the manager's time zone."""
    return version.due_date < today


def waiting(paper: DecisionPaper) -> bool:
    """Listed under Needs your approval: in state ready, with a version that passed."""
    return paper.state is DecisionPaperState.READY and bool(paper.versions)


def answer_refusal(option: int | None, own: str | None, version: PaperVersion) -> str | None:
    """The manager picks an option or writes their own decision: one of the two."""
    written = own.strip() if own is not None else ""
    if (option is None) == (not written):
        return ONE_ANSWER
    if option is not None and not 1 <= option <= len(version.options):
        return NO_SUCH_OPTION
    if len(written) > OWN_DECISION_LIMIT:
        return OWN_TOO_LONG
    return None


def comment_refusal(comment: str | None) -> str | None:
    if comment is None or not comment.strip():
        return COMMENT_NEEDED
    if len(comment) > COMMENT_LIMIT:
        return COMMENT_TOO_LONG
    return None


def paper_text(version: PaperVersion) -> str:
    """The paper as plain lines, for the assistant's pack."""
    lines = [f"Decision: {version.decision}"]
    if version.background:
        lines.append(f"Background: {version.background}")
    lines += [f"Option {n}: {text}" for n, text in enumerate(version.options, start=1)]
    lines.append(f"Recommendation: {version.recommendation}")
    lines.append(f"Due: {version.due_date.isoformat()}")
    return "\n".join(lines)
