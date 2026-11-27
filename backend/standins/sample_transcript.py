"""The sample transcript of a vendor review meeting (BRD section 9.3 of part 2).

It holds at least a decision, an action and an open point. The marked lines are
the ones the assistant stand-in uses: "Decision:", "Action:", "Open:" and
"Status:". The same lines make a .vtt file (with the speakers as voice tags),
pasted text, and the transcript the Meetings stand-in delivers.
"""

from __future__ import annotations

import base64

SAMPLE_LINES: tuple[tuple[str, str], ...] = (
    ("Programme lead", "Thanks for joining the vendor review."),
    ("Legal lead", "Three vendors are left to consolidate after this quarter."),
    ("Operations director", "Decision: We keep two vendors for office supplies."),
    ("Legal lead", "Action: Legal lead to send the signed contracts by 2026-12-04."),
    ("Finance lead", "Action: Finance lead to check the vendor invoices by 2026-12-02."),
    ("Programme lead", "Open: Who owns the vendor list after January?"),
    ("Legal lead", "Status: Vendor consolidation: Two contracts are signed and one is open."),
    ("Operations director", "Thanks, that is all for today."),
)


def _time(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}.000"


def sample_vtt() -> str:
    """The sample as a WebVTT file: one cue every 30 seconds, each with its speaker."""
    cues = ["WEBVTT", ""]
    for index, (speaker, text) in enumerate(SAMPLE_LINES):
        start = 30 * index
        timing = f"{_time(start)} --> {_time(start + 25)}"
        cues += [str(index + 1), timing, f"<v {speaker}>{text}", ""]
    return "\n".join(cues)


def sample_text() -> str:
    """The sample as pasted text: one line for each thing said."""
    return "\n".join(text for _, text in SAMPLE_LINES) + "\n"


def as_file(text: str) -> str:
    """A file's bytes as the share form sends them: base64."""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")
