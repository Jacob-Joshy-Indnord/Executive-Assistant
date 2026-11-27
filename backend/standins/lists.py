"""Notices and records stand-ins: kept in lists that the demonstration shows."""

from __future__ import annotations

from assistant.models.boundary import AuditRecord, Notice, NoticeResult, RecordResult


class ListNotices:
    def __init__(self) -> None:
        self.failing = False
        self.sent: list[Notice] = []

    async def notify(self, notice: Notice) -> NoticeResult:
        if self.failing:
            return NoticeResult(delivered=False, reason="The notice could not be delivered.")
        self.sent.append(notice)
        return NoticeResult(delivered=True)


class ListRecords:
    def __init__(self) -> None:
        self.failing = False
        self.written: list[AuditRecord] = []

    async def write(self, record: AuditRecord) -> RecordResult:
        if self.failing:
            return RecordResult(written=False, reason="The audit trail is not available.")
        self.written.append(record)
        return RecordResult(written=True)
