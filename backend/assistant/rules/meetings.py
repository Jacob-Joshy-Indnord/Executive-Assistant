"""Transcripts and their notes (part 2, Epic 1). Pure rules.

What a shared transcript may be and the one sentence for each reason it is
refused; how a transcript is split into numbered parts (times for .vtt and for
a meeting the assistant attended, line numbers for text); how the parts are
given to the assistant; and the checks on each line of the notes it returns.
Uploads are checked here in memory: nothing is written to disk or run.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal

from assistant.models.boundary import TranscriptSegment
from assistant.models.records import (
    NOTE_LINE_LIMIT,
    SUMMARY_LIMIT,
    TITLE_LIMIT,
    NoteLine,
    TranscriptPart,
)

MAX_TRANSCRIPT_BYTES = 2 * 1024 * 1024
"""2 MB, fixed (section 8)."""
KEEP_TEXT_FOR = timedelta(days=7)
"""A transcript's text is removed 7 days after it arrived at the latest (BR-3)."""
FILE_TYPES = {".vtt": "vtt", ".txt": "text"}
PARTS_PER_INPUT = 150
"""Numbered parts given to the assistant in one input of its pack."""
MAX_PROJECTS = 50

# One sentence for each reason a sharing is refused: no two reasons share one.
NOTES_NOT_ALLOWED = "Meeting notes are not allowed in this organisation."
TICK_MISSING = "Tick both boxes before sharing the transcript."
WRONG_TYPE = "Only a .vtt or .txt file can be shared."
NOT_TEXT = "The file is not text in UTF-8."
TOO_LARGE = "The transcript is larger than 2 MB."
KIND_NOT_ALLOWED = "This manager does not allow notes to be written from shared transcripts."
NO_TRANSCRIPT = "Add the transcript as a file or as pasted text."
FILE_AND_TEXT = "Share either a file or pasted text, not both."
EMPTY_TRANSCRIPT = "The transcript has no text in it."
TITLE_NEEDED = "Give the meeting a title."
TITLE_TOO_LONG = f"A title can be at most {TITLE_LIMIT} characters."
NO_ASSISTANT = "This person has no assistant to share a transcript with."
UNKNOWN_PROJECT = "One of the projects is not one the manager can see."
TOO_MANY_PROJECTS = f"A meeting can name at most {MAX_PROJECTS} projects."

SHARING_REFUSALS = (
    NOTES_NOT_ALLOWED, TICK_MISSING, WRONG_TYPE, NOT_TEXT, TOO_LARGE, KIND_NOT_ALLOWED,
    NO_TRANSCRIPT, FILE_AND_TEXT, EMPTY_TRANSCRIPT, TITLE_NEEDED, TITLE_TOO_LONG, NO_ASSISTANT,
    UNKNOWN_PROJECT, TOO_MANY_PROJECTS,
)  # fmt: skip

# The meeting's own sentences.
NOT_WITHDRAWABLE = "This transcript can no longer be withdrawn because its notes are written."
NOT_RETRYABLE = "Only notes that could not be written can be tried again."
TEXT_GONE = "The transcript's text was removed, so the notes cannot be written."
NO_NOTES_RETURNED = "The assistant returned no notes for this meeting."
NOTES_NOT_WRITTEN = "The notes could not be written."

SourceFormat = Literal["vtt", "text"]


class TranscriptRefused(ValueError):
    """A transcript that cannot be shared; ``sentence`` says why."""

    def __init__(self, sentence: str) -> None:
        super().__init__(sentence)
        self.sentence = sentence


# Reading what was shared ------------------------------------------------------------


def _is_text(text: str) -> bool:
    """Plain text: no control characters other than tabs, line breaks and form feeds."""
    return not any(ord(ch) < 32 and ch not in "\t\n\r\f" for ch in text)


def read_transcript(
    file_name: str | None, file_base64: str | None, pasted: str | None
) -> tuple[SourceFormat, str]:
    """The transcript's format and text, read in memory, or why it is refused."""
    has_file = file_name is not None or file_base64 is not None
    has_text = pasted is not None and pasted.strip() != ""
    if has_file and has_text:
        raise TranscriptRefused(FILE_AND_TEXT)
    if not has_file:
        if pasted is None or not pasted.strip():
            raise TranscriptRefused(NO_TRANSCRIPT)
        if len(pasted.encode("utf-8")) > MAX_TRANSCRIPT_BYTES:
            raise TranscriptRefused(TOO_LARGE)
        if not _is_text(pasted):
            raise TranscriptRefused(NOT_TEXT)
        return "text", pasted
    name = (file_name or "").strip().lower()
    source = next((kind for ext, kind in FILE_TYPES.items() if name.endswith(ext)), None)
    if source is None:
        raise TranscriptRefused(WRONG_TYPE)
    encoded = file_base64 or ""
    if len(encoded) > (MAX_TRANSCRIPT_BYTES * 4) // 3 + 8:
        raise TranscriptRefused(TOO_LARGE)
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise TranscriptRefused(NOT_TEXT) from exc
    if len(raw) > MAX_TRANSCRIPT_BYTES:
        raise TranscriptRefused(TOO_LARGE)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TranscriptRefused(NOT_TEXT) from exc
    text = text.removeprefix("﻿")
    if not _is_text(text):
        raise TranscriptRefused(NOT_TEXT)
    if not text.strip():
        raise TranscriptRefused(EMPTY_TRANSCRIPT)
    return source, text


# Splitting into numbered parts ------------------------------------------------------

_TIMING = re.compile(
    r"^\s*(?P<start>(?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{3})\s+-->\s+"
    r"(?P<end>(?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{3})"
)
_VOICE = re.compile(r"<v(?:\.[^\s>]+)*\s+(?P<name>[^>]+)>")
_TAG = re.compile(r"</?[^>]*>")


def _vtt_time(raw: str) -> str:
    """A cue time as ``HH:MM:SS.mmm``."""
    raw = raw.replace(",", ".")
    pieces = raw.split(":")
    if len(pieces) == 2:
        pieces.insert(0, "00")
    return f"{int(pieces[0]):02d}:{pieces[1]}:{pieces[2]}"


def split_vtt(text: str) -> list[TranscriptPart]:
    """One numbered part per cue, with its times and the speaker of a voice tag."""
    parts: list[TranscriptPart] = []
    blocks = re.split(r"\r?\n\s*\r?\n", text.replace("\r\n", "\n"))
    for block in blocks:
        lines = [line for line in block.split("\n") if line.strip()]
        timing_at = next((i for i, line in enumerate(lines) if _TIMING.match(line)), None)
        if timing_at is None:
            continue  # the header, a NOTE, STYLE or REGION block
        timing = _TIMING.match(lines[timing_at])
        assert timing is not None
        payload = " ".join(line.strip() for line in lines[timing_at + 1 :])
        voice = _VOICE.search(payload)
        words = re.sub(r"\s+", " ", _TAG.sub("", payload)).strip()
        if not words:
            continue
        parts.append(
            TranscriptPart(
                number=len(parts) + 1,
                starts=_vtt_time(timing["start"]),
                ends=_vtt_time(timing["end"]),
                speaker=voice["name"].strip()[:200] if voice else None,
                text=words,
            )
        )
    return parts


def split_text(text: str) -> list[TranscriptPart]:
    """One numbered part per line that has words, with its line number."""
    parts: list[TranscriptPart] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if line.strip():
            parts.append(
                TranscriptPart(
                    number=len(parts) + 1,
                    first_line=line_number,
                    last_line=line_number,
                    text=line.strip(),
                )
            )
    return parts


def split(source: SourceFormat, text: str) -> list[TranscriptPart]:
    parts = split_vtt(text) if source == "vtt" else split_text(text)
    if not parts:
        raise TranscriptRefused(EMPTY_TRANSCRIPT)
    return parts


def _offset(at: datetime, start: datetime) -> str:
    seconds = max((at - start).total_seconds(), 0.0)
    whole = int(seconds)
    millis = int(round((seconds - whole) * 1000))
    if millis == 1000:
        whole, millis = whole + 1, 0
    return f"{whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}.{millis:03d}"


def split_segments(
    segments: Sequence[TranscriptSegment], meeting_start: datetime, left_at: datetime | None
) -> list[TranscriptPart]:
    """The parts of an attended meeting, in order, each with its times from the start of
    the meeting and the speaker's name where one is given. Only what was said before the
    assistant left is used."""
    ordered = sorted(enumerate(segments), key=lambda pair: (pair[1].starts_at, pair[0]))
    parts: list[TranscriptPart] = []
    for _, segment in ordered:
        if left_at is not None and segment.starts_at >= left_at:
            continue
        words = re.sub(r"\s+", " ", segment.text).strip()
        if not words:
            continue
        parts.append(
            TranscriptPart(
                number=len(parts) + 1,
                starts=_offset(segment.starts_at, meeting_start),
                ends=_offset(segment.ends_at, meeting_start),
                speaker=segment.speaker.strip() if segment.speaker else None,
                text=words,
            )
        )
    return parts


def part_place(part: TranscriptPart) -> dict[str, Any]:
    """Where a part is: its number and times or line numbers, never its words."""
    return {
        "number": part.number,
        "starts": part.starts,
        "ends": part.ends,
        "first_line": part.first_line,
        "last_line": part.last_line,
    }


def part_lines(parts: Sequence[TranscriptPart]) -> list[tuple[int, int, str]]:
    """The parts as the assistant is given them: one JSON line per part, in inputs of at
    most :data:`PARTS_PER_INPUT` parts. Returns (first number, last number, content)."""
    chunks: list[tuple[int, int, str]] = []
    kept = [p for p in parts if p.text is not None]
    for start in range(0, len(kept), PARTS_PER_INPUT):
        chunk = kept[start : start + PARTS_PER_INPUT]
        content = "\n".join(
            json.dumps({**part_place(p), "speaker": p.speaker, "text": p.text}, sort_keys=True)
            for p in chunk
        )
        chunks.append((chunk[0].number, chunk[-1].number, content))
    return chunks


# Checking the notes, line by line -----------------------------------------------------

LineKind = Literal["summary", "decision", "action", "open point", "statement"]


def line_refusal(kind: LineKind, position: int | None, because: str) -> str:
    """Why one line of the notes was refused, in one sentence that says which line."""
    which = kind if position is None else f"{kind} {position}"
    return f"The notes' {which} was refused because {because}."


def parts_problem(raw: Any, numbers: set[int]) -> tuple[list[int] | None, str | None]:
    """The line's part numbers, or why they are not usable."""
    if not isinstance(raw, list) or not raw:
        return None, "it names no transcript part"
    cited: list[int] = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int):
            return None, "it names a part that is not a number"
        if value not in numbers:
            return None, f"it names part {value}, which does not exist"
        if value not in cited:
            cited.append(value)
    return cited, None


def text_problem(raw: Any, label: str, limit: int) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return f"its {label} is empty or not text"
    if len(raw) > limit:
        return f"its {label} is over {limit:,} characters"
    return None


@dataclass(frozen=True)
class CheckedLine:
    line: NoteLine | None
    refused: str | None


UNKNOWN_FIELD = "it has a field the notes do not have"


def check_plain_line(
    kind: LineKind, position: int | None, raw: dict[str, Any], numbers: set[int]
) -> CheckedLine:
    """A summary, decision or open point: its text within its limit and real parts."""
    limit = SUMMARY_LIMIT if kind == "summary" else NOTE_LINE_LIMIT
    if set(raw) - {"text", "parts"}:
        return CheckedLine(None, line_refusal(kind, position, UNKNOWN_FIELD))
    problem = text_problem(raw.get("text"), "text", limit)
    if problem:
        return CheckedLine(None, line_refusal(kind, position, problem))
    parts, problem = parts_problem(raw.get("parts"), numbers)
    if problem or parts is None:
        return CheckedLine(None, line_refusal(kind, position, problem or "it names no part"))
    return CheckedLine(NoteLine(text=raw["text"].strip(), parts=parts), None)


def due_date_of(raw: Any) -> tuple[date | None, str | None]:
    if raw is None:
        return None, None
    try:
        return date.fromisoformat(str(raw)), None
    except ValueError:
        return None, "its due date is not a date"


def attendee_name(manager_display_name: str) -> str:
    """The name everyone in the meeting sees: whose assistant it is, and that it takes
    notes."""
    return f"{manager_display_name}'s assistant (notes)"[:200]
