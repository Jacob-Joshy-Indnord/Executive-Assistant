"""What counts as one plain sentence shown to a person."""

from __future__ import annotations

import re

MAX_SENTENCE_LENGTH = 300

_INNER_STOP = re.compile(r"[.!?]\s")


def is_plain_sentence(text: str | None) -> bool:
    """True when ``text`` is one short sentence on one line, ending in a full stop.

    Plain means no markup and no line breaks; one sentence means no sentence
    stop before the end.
    """
    if not text or len(text) > MAX_SENTENCE_LENGTH:
        return False
    if "\n" in text or "\r" in text or "<" in text or ">" in text:
        return False
    if not text[0].isupper() or not text.endswith("."):
        return False
    return _INNER_STOP.search(text[:-1]) is None
