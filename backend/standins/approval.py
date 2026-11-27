"""Approval stand-in: applies the levels and limits of BRD section 8.2."""

from __future__ import annotations

from uuid import UUID

from assistant.models.boundary import (
    ActionKind,
    ApprovalLevel,
    ApprovalRequest,
    CarryOutResult,
    CheckOutcome,
    UndoResult,
)

NEVER_ALLOWED = frozenset({ActionKind.PURCHASE})
UNDOABLE = frozenset({ActionKind.ADD_ACTION_ITEM, ActionKind.ASK_OWNER})


class StandInApproval:
    def __init__(self) -> None:
        self.failing = False
        self.checked: list[tuple[ApprovalRequest, CheckOutcome]] = []
        self.carried_out: dict[UUID, CarryOutResult] = {}
        self.undone: set[UUID] = set()

    async def check(self, request: ApprovalRequest) -> CheckOutcome:
        outcome = self._decide(request)
        self.checked.append((request, outcome))
        return outcome

    @staticmethod
    def _decide(request: ApprovalRequest) -> CheckOutcome:
        if request.kind in NEVER_ALLOWED or request.level is ApprovalLevel.NOT_ALLOWED:
            return CheckOutcome(outcome="declined", reason="This kind of action is not allowed.")
        if request.approved_by_manager:
            return CheckOutcome(outcome="carry_out")
        if request.level is ApprovalLevel.WITH_APPROVAL:
            return CheckOutcome(outcome="ask_manager")
        limits = request.limits
        if limits.person_ids is not None and request.target_id not in limits.person_ids:
            return CheckOutcome(outcome="ask_manager")
        if limits.per_day is not None and request.count_today >= limits.per_day:
            return CheckOutcome(outcome="ask_manager")
        return CheckOutcome(outcome="carry_out")

    async def carry_out(self, request: ApprovalRequest) -> CarryOutResult:
        if request.request_id in self.carried_out:  # carried out once, however often asked
            return self.carried_out[request.request_id]
        if self.failing:
            return CarryOutResult(done=False, reason="Flow could not carry this out.")
        result = CarryOutResult(
            done=True, effect=request.effect, undoable=request.kind in UNDOABLE
        )
        self.carried_out[request.request_id] = result
        return result

    async def undo(self, request: ApprovalRequest) -> UndoResult:
        done = self.carried_out.get(request.request_id)
        if done is None:
            return UndoResult(done=False, reason="This was not carried out.")
        if not done.undoable:
            return UndoResult(done=False, reason="This cannot be undone.")
        if request.request_id in self.undone:
            return UndoResult(done=True)  # undone once, however often asked
        self.undone.add(request.request_id)
        return UndoResult(done=True)
