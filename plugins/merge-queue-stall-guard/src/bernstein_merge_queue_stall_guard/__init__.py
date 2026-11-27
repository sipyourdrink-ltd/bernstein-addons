"""Read-only watcher for GitHub's native merge queue."""

from bernstein_merge_queue_stall_guard.guard import evaluate_queue
from bernstein_merge_queue_stall_guard.models import (
    HeadEntryReport,
    PendingCheck,
    QueueEntrySummary,
    StallReport,
    Verdict,
)

__all__ = [
    "HeadEntryReport",
    "PendingCheck",
    "QueueEntrySummary",
    "StallReport",
    "Verdict",
    "evaluate_queue",
]
__version__ = "0.1.0"
