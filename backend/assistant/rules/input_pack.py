"""Input pack sizes (BRD section 8.1) and how statuses enter a pack. Pure rules.

A pack is measured as the characters handed to Flow: its JSON form. One input
holds at most 40,000 characters of content and the whole pack at most 200,000.
A longer input is cut and marked "shortened"; when the pack is still too big,
the last inputs are cut further, and an input with no room left is left out
and counted.
"""

from __future__ import annotations

from assistant.models.boundary import InputPack, PackInput
from assistant.models.records import ConfirmedStatement

PACK_LIMIT = 200_000
INPUT_LIMIT = 40_000
LABEL_LIMIT = 200
MAX_REASONS = 50
"""Reasons refused last time given in full; the rest are counted in one line."""


def pack_label(text: str) -> str:
    """A source label within its limit, however long the name it is made from."""
    return text if len(text) <= LABEL_LIMIT else text[: LABEL_LIMIT - 3].rstrip() + "..."


def pack_size(pack: InputPack) -> int:
    """The characters of the pack as it is handed over."""
    return len(pack.model_dump_json())


def shortened(item: PackInput, keep: int) -> PackInput:
    """``item`` with at most ``keep`` characters of content, marked if anything was cut."""
    if len(item.content) <= keep:
        return item
    return item.model_copy(update={"content": item.content[: max(keep, 0)], "shortened": True})


def fit_pack(pack: InputPack) -> InputPack:
    """The pack within the limits: each input within 40,000, the whole within 200,000."""
    inputs = [shortened(item, INPUT_LIMIT) for item in pack.inputs]
    left_out = pack.left_out
    refused = list(pack.refused_last_time)
    if len(refused) > MAX_REASONS:
        more = len(refused) - MAX_REASONS
        refused = [*refused[:MAX_REASONS], f"{more:,} more tool requests were refused."]
    pack = pack.model_copy(update={"refused_last_time": refused})
    fitted = pack.model_copy(update={"inputs": list(inputs)})
    while inputs and (excess := pack_size(fitted) - PACK_LIMIT) > 0:
        last = inputs[-1]
        keep = len(last.content) - excess
        if keep > 0:
            inputs[-1] = shortened(last, keep)
        else:
            inputs.pop()
            left_out += 1
        fitted = pack.model_copy(update={"inputs": list(inputs), "left_out": left_out})
    return fitted


COLOUR_WORDS = {"green": "Green", "amber": "Amber", "red": "Red"}


def status_content(statement: ConfirmedStatement) -> str:
    """A project's status for a pack. Only a confirmed statement can be given (BR-4)."""
    if not isinstance(statement, ConfirmedStatement):
        raise TypeError("only confirmed statements go into an input pack")
    wording = statement.wording
    lines = [
        f"Colour: {COLOUR_WORDS[statement.colour]}",
        f"Reason: {wording.reason}",
        f"What changed: {wording.what_changed}" if wording.what_changed else "",
        f"What comes next: {wording.what_comes_next}" if wording.what_comes_next else "",
        f"Mark: {'Corrected' if statement.mark == 'corrected' else 'Confirmed'} by its owner",
        f"Date: {statement.created_at.date().isoformat()}",
    ]
    return "\n".join(line for line in lines if line)
