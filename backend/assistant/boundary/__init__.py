"""What the module asks of Flow: interfaces only (BRD section 9.1)."""

from assistant.boundary.interfaces import (
    Approval,
    Assistant,
    Boundary,
    Clock,
    Notices,
    People,
    Records,
    WorkData,
)

__all__ = [
    "Approval",
    "Assistant",
    "Boundary",
    "Clock",
    "Notices",
    "People",
    "Records",
    "WorkData",
]
