"""Knowledge stand-in: the organisation's knowledge store, kept as a list."""

from __future__ import annotations

from assistant.models.boundary import KnowledgeNote, KnowledgeResult


class ListKnowledge:
    def __init__(self) -> None:
        self.failing = False
        self.notes: dict[str, KnowledgeNote] = {}
        """What the knowledge store holds now, by note id."""
        self.told: list[tuple[str, str]] = []
        """Every request, in order: ("add" or "remove", note id)."""

    async def add(self, note: KnowledgeNote) -> KnowledgeResult:
        if self.failing:
            return KnowledgeResult(done=False, reason="The knowledge store is not available.")
        self.told.append(("add", note.note_id))
        self.notes[note.note_id] = note
        return KnowledgeResult(done=True)

    async def remove(self, organisation_id: str, note_id: str) -> KnowledgeResult:
        if self.failing:
            return KnowledgeResult(done=False, reason="The knowledge store is not available.")
        self.told.append(("remove", note_id))
        found = self.notes.get(note_id)
        if found is not None and found.organisation_id == organisation_id:
            del self.notes[note_id]
        return KnowledgeResult(done=True)
