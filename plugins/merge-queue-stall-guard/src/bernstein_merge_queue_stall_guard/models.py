"""Typed report structures for merge-queue-stall-guard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Verdict = Literal["healthy", "blocked-red", "stall", "unknown"]
WaitKind = Literal["waiting_for_runner", "executing"]
LastMergeSource = Literal["activity", "commit-date"]


@dataclass(frozen=True)
class PendingCheck:
    """One non-completed check run on the head merge-queue entry."""

    name: str
    wait_kind: WaitKind
    running_minutes: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "wait_kind": self.wait_kind,
            "running_minutes": self.running_minutes,
        }

    def format_text(self) -> str:
        if self.wait_kind == "waiting_for_runner" or self.running_minutes is None:
            return f"{self.name} [waiting for a runner]"
        return f"{self.name} [executing {self.running_minutes}m]"


@dataclass(frozen=True)
class QueueEntrySummary:
    """Compact summary of an entry in the merge queue."""

    position: int
    pr_number: int | None
    state: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "pr_number": self.pr_number,
            "state": self.state,
        }


@dataclass(frozen=True)
class HeadEntryReport:
    """Details of the head (position 1) entry in the merge queue."""

    pr_number: int | None
    title: str | None
    state: str
    head_sha: str | None
    enqueued_at: str | None
    enqueued_minutes_ago: int | None
    red_checks_count: int | None
    pending_checks_count: int | None
    pending_checks: tuple[PendingCheck, ...]
    checks_error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pr_number": self.pr_number,
            "title": self.title,
            "state": self.state,
            "head_sha": self.head_sha,
            "enqueued_at": self.enqueued_at,
            "enqueued_minutes_ago": self.enqueued_minutes_ago,
            "red_checks_count": self.red_checks_count,
            "pending_checks_count": self.pending_checks_count,
            "pending_checks": [item.to_dict() for item in self.pending_checks],
            "checks_error": self.checks_error,
        }


@dataclass(frozen=True)
class StallReport:
    """Complete read-only merge-queue health report."""

    schema_version: str
    verdict: Verdict
    exit_code: int
    reason: str
    repo: str
    branch: str
    checked_at: str
    stall_hours: float
    entries_count: int | None
    build_depth: int | None
    tip_sha: str | None
    last_merge_at: str | None
    last_merge_source: LastMergeSource | None
    minutes_since_merge: int | None
    head: HeadEntryReport | None
    queue: tuple[QueueEntrySummary, ...]
    runner_pools: dict[str, int] | None
    alert_emitted: bool
    summary: str
    queue_truncated: bool = False
    active_jobs_truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "verdict": self.verdict,
            "exit_code": self.exit_code,
            "reason": self.reason,
            "repo": self.repo,
            "branch": self.branch,
            "checked_at": self.checked_at,
            "stall_hours": self.stall_hours,
            "entries_count": self.entries_count,
            "build_depth": self.build_depth,
            "tip_sha": self.tip_sha,
            "last_merge_at": self.last_merge_at,
            "last_merge_source": self.last_merge_source,
            "minutes_since_merge": self.minutes_since_merge,
            "head": self.head.to_dict() if self.head is not None else None,
            "queue": [entry.to_dict() for entry in self.queue],
            "queue_truncated": self.queue_truncated,
            "runner_pools": self.runner_pools,
            "active_jobs_truncated": self.active_jobs_truncated,
            "alert_emitted": self.alert_emitted,
            "summary": self.summary,
        }
