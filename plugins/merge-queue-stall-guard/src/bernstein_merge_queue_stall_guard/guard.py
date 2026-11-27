"""Merge queue stall guard — outcome-based, read-only queue watcher.

A merge queue can hold entries and merge nothing while every individual
health check reads as green. This guard keys on the outcome rather than any
single failure mechanism:

    entries present  AND  nothing merged for N hours  AND  head not red

All GitHub responses are parsed and strictly validated in ``parsers.py`` before
verdict logic runs. Any unreadable, null, mistyped, out-of-range, future-dated,
or count-inconsistent response fails closed to ``unknown`` (exit 2, no alert)
unless the head entry is independently proven red (``blocked-red``, exit 0).
Every emitted report is validated against the bundled ``report.schema.json``
before state persistence or output.

This watcher is strictly read-only on GitHub: it never dequeues, cancels,
relabels, pushes, or edits merge-queue settings.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from bernstein_merge_queue_stall_guard.gh import GhClient, GhQueryError, GhUnavailableError
from bernstein_merge_queue_stall_guard.models import (
    HeadEntryReport,
    LastMergeSource,
    PendingCheck,
    QueueEntrySummary,
    StallReport,
)
from bernstein_merge_queue_stall_guard.parsers import (
    CHECK_RUNS_PER_PAGE,
    JOBS_PER_PAGE,
    LANDING_ACTIVITY_TYPES,
    NON_RED_CONCLUSIONS,
    PENDING_CHECK_STATUSES,
    RED_CONCLUSIONS,
    SHA_RE,
    ParsedActivityFeed,
    ParsedBranchTip,
    ParsedCheckRun,
    ParsedMergeQueue,
    ParsedQueueNode,
    ResponseParseError,
    parse_activity_feed,
    parse_branch_tip,
    parse_check_runs_page,
    parse_iso_utc,
    parse_jobs_page,
    parse_merge_queue,
    parse_workflow_runs_page,
)
from bernstein_merge_queue_stall_guard.schema_check import validate_report_dict

__all__ = [
    "BRANCH_RE",
    "EXIT_HEALTHY",
    "EXIT_STALLED",
    "EXIT_UNREADABLE",
    "LANDING_ACTIVITY_TYPES",
    "MAX_ALERT_REPEAT_HOURS",
    "MAX_CHECK_RUN_PAGES",
    "MAX_JOB_PAGES",
    "MAX_RUNS_TO_CENSUS",
    "MAX_STALL_HOURS",
    "NON_RED_CONCLUSIONS",
    "PENDING_CHECK_STATUSES",
    "RED_CONCLUSIONS",
    "REPO_RE",
    "SCHEMA_VERSION",
    "SHA_RE",
    "evaluate_queue",
    "format_iso_utc",
    "parse_iso_utc",
]

SCHEMA_VERSION = "1.0"
REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/@-]+$")
MAX_STALL_HOURS = 8760.0
MAX_ALERT_REPEAT_HOURS = 8760.0
MAX_CHECK_RUN_PAGES = 20
MAX_RUNS_TO_CENSUS = 30
MAX_JOB_PAGES = 5

EXIT_HEALTHY = 0
EXIT_STALLED = 1
EXIT_UNREADABLE = 2

ActivityStatus = Literal["ok", "unavailable", "malformed"]


def format_iso_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _minutes_since(now: datetime, then: datetime | None) -> int | None:
    if then is None:
        return None
    return int((now - then).total_seconds() // 60)


def _graphql_queue_query(owner: str, name: str, branch: str) -> str:
    return (
        f'\n{{ repository(owner:"{owner}", name:"{name}") {{\n'
        f'    mergeQueue(branch:"{branch}") {{\n'
        f"      configuration {{ maximumEntriesToBuild }}\n"
        f"      entries(first: 20) {{ totalCount nodes {{\n"
        f"        position state enqueuedAt\n"
        f"        headCommit {{ oid }}\n"
        f"        pullRequest {{ number title }} }} }} }} }} }}"
    )


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _state_namespace(repo: str, branch: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{repo}__{branch}").strip("_")[:64]
    digest = hashlib.sha256(f"{repo}\0{branch}".encode()).hexdigest()[:12]
    return f"{slug}-{digest}" if slug else digest


def _default_state_dir(repo: str, branch: str) -> Path:
    xdg = os.environ.get("XDG_STATE_HOME", "").strip()
    base = (
        Path(xdg) / "merge-queue-stall-guard"
        if xdg
        else Path.home() / ".local" / "state" / "merge-queue-stall-guard"
    )
    return base / _state_namespace(repo, branch)


def _persist_state(
    report: StallReport,
    *,
    state_dir: Path,
    now: datetime,
    alert_repeat_hours: float,
) -> StallReport:
    """Persist history, stall marker, and throttled alert state under state_dir."""
    state_dir.mkdir(parents=True, exist_ok=True)
    history_path = state_dir / "history.tsv"
    marker_path = state_dir / "MERGE-QUEUE-STALL"
    episode_path = state_dir / ".episode.json"
    alerts_path = state_dir / "alerts.jsonl"
    attention_path = state_dir / "ATTENTION"

    alert_emitted = False
    if report.verdict == "stall":
        now_epoch = int(now.timestamp())
        ep_start = now_epoch
        ep_last = 0
        if episode_path.exists():
            try:
                ep = json.loads(episode_path.read_text(encoding="utf-8"))
                if isinstance(ep, dict):
                    s_val = _int_or_none(ep.get("started_at"))
                    l_val = _int_or_none(ep.get("last_alert_at"))
                    if s_val is not None:
                        ep_start = s_val
                    if l_val is not None:
                        ep_last = l_val
            except (OSError, json.JSONDecodeError):
                pass

        repeat_seconds = int(alert_repeat_hours * 3600)
        if now_epoch - ep_last >= repeat_seconds:
            alert_emitted = True
            ep_last = now_epoch
            alert_row = {
                "ts": format_iso_utc(now),
                "script": "merge-queue-stall-guard",
                "alertname": "Merge queue not merging",
                "severity": "warning",
                "startsAt": format_iso_utc(datetime.fromtimestamp(ep_start, tz=UTC)),
                "repo": report.repo,
                "branch": report.branch,
                "entries": report.entries_count,
                "head_pr": report.head.pr_number if report.head else None,
                "head_state": report.head.state if report.head else None,
                "pending_checks": (
                    [c.to_dict() for c in report.head.pending_checks] if report.head else []
                ),
                "checks_error": report.head.checks_error if report.head else None,
                "build_depth": report.build_depth,
                "runner_pools": report.runner_pools,
                "window_h": report.stall_hours,
                "summary": report.summary,
            }
            with alerts_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(alert_row, sort_keys=True) + "\n")

        episode_path.write_text(
            json.dumps({"started_at": ep_start, "last_alert_at": ep_last}) + "\n",
            encoding="utf-8",
        )
        marker_path.write_text(f"MERGE-QUEUE-STALL {report.summary}\n", encoding="utf-8")
        with attention_path.open("a", encoding="utf-8") as fh:
            fh.write(f"MERGE-QUEUE-STALL {report.summary}\n")
    else:
        if report.verdict in ("healthy", "blocked-red") and episode_path.exists():
            episode_path.unlink(missing_ok=True)
        marker_path.write_text("", encoding="utf-8")

    with history_path.open("a", encoding="utf-8") as fh:
        fh.write(f"{report.checked_at}\t{report.verdict}\t{report.summary}\n")

    if alert_emitted != report.alert_emitted:
        updated = StallReport(
            schema_version=report.schema_version,
            verdict=report.verdict,
            exit_code=report.exit_code,
            reason=report.reason,
            repo=report.repo,
            branch=report.branch,
            checked_at=report.checked_at,
            stall_hours=report.stall_hours,
            entries_count=report.entries_count,
            build_depth=report.build_depth,
            tip_sha=report.tip_sha,
            last_merge_at=report.last_merge_at,
            last_merge_source=report.last_merge_source,
            minutes_since_merge=report.minutes_since_merge,
            head=report.head,
            queue=report.queue,
            runner_pools=report.runner_pools,
            alert_emitted=alert_emitted,
            summary=report.summary,
            queue_truncated=report.queue_truncated,
            active_jobs_truncated=report.active_jobs_truncated,
        )
        validate_report_dict(updated.to_dict())
        return updated
    return report


def _unknown_report(
    *,
    repo: str,
    branch: str,
    now: datetime,
    stall_hours: float,
    reason: str,
    entries_count: int | None = None,
    build_depth: int | None = None,
    tip_sha: str | None = None,
    last_merge_at: str | None = None,
    last_merge_source: LastMergeSource | None = None,
    minutes_since_merge: int | None = None,
    head: HeadEntryReport | None = None,
    queue: tuple[QueueEntrySummary, ...] = (),
    queue_truncated: bool = False,
    runner_pools: dict[str, int] | None = None,
    active_jobs_truncated: bool = False,
    summary: str | None = None,
) -> StallReport:
    return StallReport(
        schema_version=SCHEMA_VERSION,
        verdict="unknown",
        exit_code=EXIT_UNREADABLE,
        reason=reason,
        repo=repo,
        branch=branch,
        checked_at=format_iso_utc(now),
        stall_hours=stall_hours,
        entries_count=entries_count,
        build_depth=build_depth,
        tip_sha=tip_sha,
        last_merge_at=last_merge_at,
        last_merge_source=last_merge_source,
        minutes_since_merge=minutes_since_merge,
        head=head,
        queue=queue,
        runner_pools=runner_pools,
        alert_emitted=False,
        summary=summary if summary is not None else f"unknown: {reason}",
        queue_truncated=queue_truncated,
        active_jobs_truncated=active_jobs_truncated,
    )


@dataclass(frozen=True)
class _ParsedChecksResult:
    red_count: int
    pending_checks: tuple[PendingCheck, ...]
    truncated: bool


def _fetch_head_checks(
    gh: GhClient,
    repo: str,
    head_sha: str,
    *,
    now: datetime,
) -> _ParsedChecksResult:
    """Fetch and strictly parse all check-run pages for head_sha."""
    all_runs: list[ParsedCheckRun] = []
    expected_total: int | None = None
    truncated = False

    for page_num in range(1, MAX_CHECK_RUN_PAGES + 1):
        route = (
            f"repos/{repo}/commits/{head_sha}/check-runs?per_page={CHECK_RUNS_PER_PAGE}"
            if page_num == 1
            else (
                f"repos/{repo}/commits/{head_sha}/check-runs"
                f"?per_page={CHECK_RUNS_PER_PAGE}&page={page_num}"
            )
        )
        raw = gh.get(route)
        page = parse_check_runs_page(raw, now=now)

        if expected_total is None:
            expected_total = page.total_count
        elif page.total_count != expected_total:
            raise ResponseParseError("check_runs: total_count changed across pages")

        all_runs.extend(page.runs)
        if len(all_runs) > expected_total:
            raise ResponseParseError("check_runs: returned more entries than total_count")
        if len(all_runs) == expected_total:
            break
        if len(page.runs) < CHECK_RUNS_PER_PAGE:
            raise ResponseParseError("check_runs: ended before reaching total_count")
        if page_num == MAX_CHECK_RUN_PAGES:
            truncated = True

    red_count = sum(1 for run in all_runs if run.is_red)
    pending: list[PendingCheck] = []
    for run in all_runs:
        if not run.is_pending:
            continue
        if run.started_at is None:
            pending.append(
                PendingCheck(
                    name=run.name,
                    wait_kind="waiting_for_runner",
                    running_minutes=None,
                )
            )
        else:
            pending.append(
                PendingCheck(
                    name=run.name,
                    wait_kind="executing",
                    running_minutes=_minutes_since(now, run.started_at),
                )
            )
    return _ParsedChecksResult(
        red_count=red_count,
        pending_checks=tuple(pending),
        truncated=truncated,
    )


def _inspect_active_jobs(
    gh: GhClient,
    repo: str,
) -> tuple[dict[str, int] | None, bool]:
    """Count queued and in-progress jobs across active (in_progress + queued) runs."""
    label_counts: Counter[str] = Counter()
    run_ids: list[int] = []
    seen_ids: set[int] = set()
    active_jobs_truncated = False
    any_runs_ok = False

    for run_status in ("in_progress", "queued"):
        route = f"repos/{repo}/actions/runs?status={run_status}&per_page=50"
        try:
            page = parse_workflow_runs_page(gh.get(route))
        except (GhUnavailableError, GhQueryError, ResponseParseError):
            active_jobs_truncated = True
            continue
        any_runs_ok = True
        if page.total_count > len(page.run_ids):
            active_jobs_truncated = True
        for rid in page.run_ids:
            if rid in seen_ids:
                continue
            if len(run_ids) >= MAX_RUNS_TO_CENSUS:
                active_jobs_truncated = True
                break
            seen_ids.add(rid)
            run_ids.append(rid)

    if not any_runs_ok:
        return None, True

    for rid in run_ids:
        jobs_seen = 0
        expected_jobs_total: int | None = None
        run_counts: Counter[str] = Counter()
        run_valid = True

        for page_num in range(1, MAX_JOB_PAGES + 1):
            jobs_route = (
                f"repos/{repo}/actions/runs/{rid}/jobs?per_page={JOBS_PER_PAGE}"
                if page_num == 1
                else (
                    f"repos/{repo}/actions/runs/{rid}/jobs"
                    f"?per_page={JOBS_PER_PAGE}&page={page_num}"
                )
            )
            try:
                jobs_page = parse_jobs_page(gh.get(jobs_route))
            except (GhUnavailableError, GhQueryError, ResponseParseError):
                active_jobs_truncated = True
                run_valid = False
                break

            if expected_jobs_total is None:
                expected_jobs_total = jobs_page.total_count
            elif jobs_page.total_count != expected_jobs_total:
                active_jobs_truncated = True
                run_valid = False
                break

            jobs_seen += len(jobs_page.jobs)
            if jobs_seen > expected_jobs_total:
                active_jobs_truncated = True
                run_valid = False
                break

            for job in jobs_page.jobs:
                if job.status not in ("in_progress", "queued"):
                    continue
                if not job.labels:
                    active_jobs_truncated = True
                    continue
                run_counts[",".join(job.labels)] += 1

            if jobs_seen == expected_jobs_total:
                break
            if len(jobs_page.jobs) < JOBS_PER_PAGE:
                active_jobs_truncated = True
                run_valid = False
                break
            if page_num == MAX_JOB_PAGES:
                active_jobs_truncated = True

        if run_valid:
            label_counts.update(run_counts)

    sorted_counts = dict(sorted(label_counts.items(), key=lambda pair: (-pair[1], pair[0])))
    return sorted_counts, active_jobs_truncated


@dataclass(frozen=True)
class _TimingRead:
    tip: ParsedBranchTip | None
    tip_error: str | None
    activity: ParsedActivityFeed | None
    activity_status: ActivityStatus
    activity_error: str | None


def _read_branch_timing(
    gh: GhClient,
    repo: str,
    branch: str,
    *,
    now: datetime,
) -> _TimingRead:
    """Read and strictly parse branch tip and repository activity feed."""
    tip: ParsedBranchTip | None = None
    tip_error: str | None = None
    try:
        tip = parse_branch_tip(gh.get(f"repos/{repo}/commits/{branch}"), now=now)
    except (GhUnavailableError, GhQueryError, ResponseParseError) as exc:
        tip_error = f"could not read {branch} tip: {exc}"

    activity: ParsedActivityFeed | None = None
    activity_status: ActivityStatus = "ok"
    activity_error: str | None = None
    try:
        raw_activity = gh.get(f"repos/{repo}/activity?ref=refs/heads/{branch}&per_page=10")
    except (GhUnavailableError, GhQueryError) as exc:
        activity_status = "unavailable"
        activity_error = str(exc)
    else:
        try:
            activity = parse_activity_feed(raw_activity, now=now)
        except ResponseParseError as exc:
            activity_status = "malformed"
            activity_error = str(exc)

    return _TimingRead(
        tip=tip,
        tip_error=tip_error,
        activity=activity,
        activity_status=activity_status,
        activity_error=activity_error,
    )


def _resolve_landing(
    timing: _TimingRead,
) -> tuple[datetime | None, LastMergeSource | None]:
    if (
        timing.activity_status == "ok"
        and timing.activity is not None
        and timing.activity.latest_landing_at is not None
    ):
        return timing.activity.latest_landing_at, "activity"
    if timing.tip is not None:
        return timing.tip.committer_date, "commit-date"
    return None, None


def _build_head_report(
    head: ParsedQueueNode,
    *,
    now: datetime,
    red_count: int | None,
    pending_checks: tuple[PendingCheck, ...],
    pending_count: int | None,
    checks_error: str | None,
) -> HeadEntryReport:
    return HeadEntryReport(
        pr_number=head.pr_number,
        title=head.pr_title,
        state=head.state,
        head_sha=head.head_sha,
        enqueued_at=format_iso_utc(head.enqueued_at),
        enqueued_minutes_ago=_minutes_since(now, head.enqueued_at),
        red_checks_count=red_count,
        pending_checks_count=pending_count,
        pending_checks=pending_checks,
        checks_error=checks_error,
    )


def _format_base_summary(
    *,
    mq: ParsedMergeQueue,
    tip_sha: str | None,
    mins_since_merge: int | None,
    landed_source: LastMergeSource | None,
) -> str:
    tip_short = tip_sha[:8] if tip_sha else "?"
    mins_str = f"{mins_since_merge}m" if mins_since_merge is not None else "?"
    src_str = landed_source if landed_source is not None else "?"
    return (
        f"entries={mq.total_count} build_depth={mq.build_depth} tip={tip_short} "
        f"last_merge={mins_str} ago (src={src_str})"
    )


def _format_detail(
    *,
    head_report: HeadEntryReport,
    runner_pools: dict[str, int] | None,
    queue_summaries: tuple[QueueEntrySummary, ...],
    base_summary: str,
) -> str:
    if head_report.checks_error is not None:
        pending_desc = head_report.checks_error
    elif head_report.pending_checks:
        pending_desc = ", ".join(item.format_text() for item in head_report.pending_checks)
    else:
        pending_desc = "-"

    if runner_pools is None:
        pool_desc = "?"
    elif not runner_pools:
        pool_desc = "none-live"
    else:
        pool_desc = " ".join(f"{label}={count}" for label, count in runner_pools.items())

    queue_list = " ".join(
        f"#{e.pr_number if e.pr_number is not None else '?'}:{e.state}" for e in queue_summaries
    )
    enq_str = (
        f"{head_report.enqueued_minutes_ago}m"
        if head_report.enqueued_minutes_ago is not None
        else "?"
    )
    pending_str = (
        str(head_report.pending_checks_count)
        if head_report.pending_checks_count is not None
        else "?"
    )
    red_str = (
        str(head_report.red_checks_count) if head_report.red_checks_count is not None else "?"
    )
    return (
        f"head=#{head_report.pr_number if head_report.pr_number is not None else '?'} "
        f"({head_report.state}, enqueued {enq_str} ago) "
        f"waiting on {pending_str} check(s): {pending_desc}; red={red_str}; "
        f"active_jobs: {pool_desc}; queue: {queue_list}; {base_summary}"
    )


def evaluate_queue(
    *,
    repo: str,
    branch: str = "main",
    stall_hours: float = 2.0,
    alert_repeat_hours: float = 4.0,
    client: GhClient | None = None,
    now: datetime | None = None,
    state_dir: Path | None = None,
    write_state: bool = True,
) -> StallReport:
    """Inspect a repository's native merge queue and return a structured verdict."""
    checked_at = (now or datetime.now(UTC)).astimezone(UTC)

    if not REPO_RE.fullmatch(repo):
        raise ValueError(f"invalid repository format (expected owner/name): {repo!r}")
    if not BRANCH_RE.fullmatch(branch):
        raise ValueError(f"invalid branch name: {branch!r}")
    if (
        isinstance(stall_hours, bool)
        or not math.isfinite(stall_hours)
        or stall_hours <= 0
        or stall_hours > MAX_STALL_HOURS
    ):
        raise ValueError(
            f"stall_hours must be finite in (0, {MAX_STALL_HOURS:g}], got {stall_hours!r}"
        )
    if (
        isinstance(alert_repeat_hours, bool)
        or not math.isfinite(alert_repeat_hours)
        or alert_repeat_hours < 0
        or alert_repeat_hours > MAX_ALERT_REPEAT_HOURS
    ):
        raise ValueError(
            f"alert_repeat_hours must be finite in [0, {MAX_ALERT_REPEAT_HOURS:g}], "
            f"got {alert_repeat_hours!r}"
        )

    try:
        stall_threshold = timedelta(hours=stall_hours)
    except OverflowError as exc:
        raise ValueError(f"stall_hours out of timedelta range: {stall_hours!r}") from exc

    gh = client or GhClient()
    owner, name = repo.split("/", 1)
    effective_state_dir = state_dir if state_dir is not None else _default_state_dir(repo, branch)

    def finalize(report: StallReport) -> StallReport:
        validate_report_dict(report.to_dict())
        if not write_state:
            return report
        return _persist_state(
            report,
            state_dir=effective_state_dir,
            now=checked_at,
            alert_repeat_hours=alert_repeat_hours,
        )

    # 1. Read and strictly parse the merge queue GraphQL response.
    try:
        mq = parse_merge_queue(
            gh.graphql(_graphql_queue_query(owner, name, branch)),
            now=checked_at,
        )
    except (GhUnavailableError, GhQueryError, ResponseParseError) as exc:
        return finalize(
            _unknown_report(
                repo=repo,
                branch=branch,
                now=checked_at,
                stall_hours=stall_hours,
                reason=f"merge-queue query failed: {exc}",
            )
        )

    queue_summaries = tuple(
        QueueEntrySummary(position=n.position, pr_number=n.pr_number, state=n.state)
        for n in mq.nodes
    )

    # 2. If entries exist, inspect head check runs FIRST so a definitively red
    #    head always yields blocked-red (exit 0) regardless of tip/activity state.
    head_report: HeadEntryReport | None = None
    checks_result: _ParsedChecksResult | None = None
    checks_error: str | None = None

    if mq.head is not None:
        try:
            checks_result = _fetch_head_checks(gh, repo, mq.head.head_sha, now=checked_at)
        except (GhUnavailableError, GhQueryError, ResponseParseError) as exc:
            checks_error = f"check-runs query failed: {exc}"
            head_report = _build_head_report(
                mq.head,
                now=checked_at,
                red_count=None,
                pending_checks=(),
                pending_count=None,
                checks_error=checks_error,
            )
        else:
            if checks_result.red_count > 0:
                head_report = _build_head_report(
                    mq.head,
                    now=checked_at,
                    red_count=checks_result.red_count,
                    pending_checks=checks_result.pending_checks,
                    pending_count=len(checks_result.pending_checks),
                    checks_error=None,
                )
                timing = _read_branch_timing(gh, repo, branch, now=checked_at)
                landed_dt, landed_source = _resolve_landing(timing)
                mins_since_merge = _minutes_since(checked_at, landed_dt)
                tip_sha = timing.tip.sha if timing.tip is not None else None
                runner_pools, active_jobs_truncated = _inspect_active_jobs(gh, repo)
                base_summary = _format_base_summary(
                    mq=mq,
                    tip_sha=tip_sha,
                    mins_since_merge=mins_since_merge,
                    landed_source=landed_source,
                )
                detail = _format_detail(
                    head_report=head_report,
                    runner_pools=runner_pools,
                    queue_summaries=queue_summaries,
                    base_summary=base_summary,
                )
                return finalize(
                    StallReport(
                        schema_version=SCHEMA_VERSION,
                        verdict="blocked-red",
                        exit_code=EXIT_HEALTHY,
                        reason="head entry has failing checks",
                        repo=repo,
                        branch=branch,
                        checked_at=format_iso_utc(checked_at),
                        stall_hours=stall_hours,
                        entries_count=mq.total_count,
                        build_depth=mq.build_depth,
                        tip_sha=tip_sha,
                        last_merge_at=format_iso_utc(landed_dt) if landed_dt is not None else None,
                        last_merge_source=landed_source,
                        minutes_since_merge=mins_since_merge,
                        head=head_report,
                        queue=queue_summaries,
                        runner_pools=runner_pools,
                        alert_emitted=False,
                        summary=f"blocked-red: head entry is red -- {detail}",
                        queue_truncated=mq.queue_truncated,
                        active_jobs_truncated=active_jobs_truncated,
                    )
                )

            if checks_result.truncated:
                checks_error = "check-runs pagination limit reached"
                head_report = _build_head_report(
                    mq.head,
                    now=checked_at,
                    red_count=None,
                    pending_checks=checks_result.pending_checks,
                    pending_count=len(checks_result.pending_checks),
                    checks_error=checks_error,
                )
            else:
                head_report = _build_head_report(
                    mq.head,
                    now=checked_at,
                    red_count=0,
                    pending_checks=checks_result.pending_checks,
                    pending_count=len(checks_result.pending_checks),
                    checks_error=None,
                )

    # 3. Read and strictly parse branch tip and repository activity feed.
    timing = _read_branch_timing(gh, repo, branch, now=checked_at)
    landed_dt, landed_source = _resolve_landing(timing)
    mins_since_merge = _minutes_since(checked_at, landed_dt)
    tip_sha = timing.tip.sha if timing.tip is not None else None
    base_summary = _format_base_summary(
        mq=mq,
        tip_sha=tip_sha,
        mins_since_merge=mins_since_merge,
        landed_source=landed_source,
    )

    if timing.tip is None:
        return finalize(
            _unknown_report(
                repo=repo,
                branch=branch,
                now=checked_at,
                stall_hours=stall_hours,
                reason=timing.tip_error or f"could not read {branch} tip",
                entries_count=mq.total_count,
                build_depth=mq.build_depth,
                head=head_report,
                queue=queue_summaries,
                queue_truncated=mq.queue_truncated,
            )
        )

    if timing.activity_status == "malformed":
        reason = f"malformed repository activity feed: {timing.activity_error}"
        return finalize(
            _unknown_report(
                repo=repo,
                branch=branch,
                now=checked_at,
                stall_hours=stall_hours,
                reason=reason,
                entries_count=mq.total_count,
                build_depth=mq.build_depth,
                tip_sha=tip_sha,
                last_merge_at=format_iso_utc(timing.tip.committer_date),
                last_merge_source="commit-date",
                minutes_since_merge=_minutes_since(checked_at, timing.tip.committer_date),
                head=head_report,
                queue=queue_summaries,
                queue_truncated=mq.queue_truncated,
            )
        )

    assert landed_dt is not None and landed_source is not None and mins_since_merge is not None

    # 4. Empty queue -> healthy (tip and activity already validated above).
    if mq.total_count == 0:
        return finalize(
            StallReport(
                schema_version=SCHEMA_VERSION,
                verdict="healthy",
                exit_code=EXIT_HEALTHY,
                reason="queue empty",
                repo=repo,
                branch=branch,
                checked_at=format_iso_utc(checked_at),
                stall_hours=stall_hours,
                entries_count=0,
                build_depth=mq.build_depth,
                tip_sha=tip_sha,
                last_merge_at=format_iso_utc(landed_dt),
                last_merge_source=landed_source,
                minutes_since_merge=mins_since_merge,
                head=None,
                queue=queue_summaries,
                runner_pools=None,
                alert_emitted=False,
                summary=f"healthy: queue empty -- {base_summary}",
                queue_truncated=mq.queue_truncated,
                active_jobs_truncated=False,
            )
        )

    assert head_report is not None

    # 5. Unreadable or truncated head checks -> unknown (exit 2, no alert).
    if checks_error is not None or head_report.red_checks_count is None:
        runner_pools, active_jobs_truncated = _inspect_active_jobs(gh, repo)
        detail = _format_detail(
            head_report=head_report,
            runner_pools=runner_pools,
            queue_summaries=queue_summaries,
            base_summary=base_summary,
        )
        return finalize(
            _unknown_report(
                repo=repo,
                branch=branch,
                now=checked_at,
                stall_hours=stall_hours,
                reason=checks_error or "head checks unreadable",
                entries_count=mq.total_count,
                build_depth=mq.build_depth,
                tip_sha=tip_sha,
                last_merge_at=format_iso_utc(landed_dt),
                last_merge_source=landed_source,
                minutes_since_merge=mins_since_merge,
                head=head_report,
                queue=queue_summaries,
                queue_truncated=mq.queue_truncated,
                runner_pools=runner_pools,
                active_jobs_truncated=active_jobs_truncated,
                summary=f"unknown: {checks_error or 'head checks unreadable'} -- {detail}",
            )
        )

    # 6. Head is proven non-red; check whether merges landed inside the window.
    if (checked_at - landed_dt) < stall_threshold:
        return finalize(
            StallReport(
                schema_version=SCHEMA_VERSION,
                verdict="healthy",
                exit_code=EXIT_HEALTHY,
                reason="merges are flowing",
                repo=repo,
                branch=branch,
                checked_at=format_iso_utc(checked_at),
                stall_hours=stall_hours,
                entries_count=mq.total_count,
                build_depth=mq.build_depth,
                tip_sha=tip_sha,
                last_merge_at=format_iso_utc(landed_dt),
                last_merge_source=landed_source,
                minutes_since_merge=mins_since_merge,
                head=head_report,
                queue=queue_summaries,
                runner_pools=None,
                alert_emitted=False,
                summary=f"healthy: merges are flowing -- {base_summary}",
                queue_truncated=mq.queue_truncated,
                active_jobs_truncated=False,
            )
        )

    # 7. Past window with non-red head: inspect active-job diagnostics.
    runner_pools, active_jobs_truncated = _inspect_active_jobs(gh, repo)
    detail = _format_detail(
        head_report=head_report,
        runner_pools=runner_pools,
        queue_summaries=queue_summaries,
        base_summary=base_summary,
    )

    if landed_source != "activity":
        reason = (
            "no landing event in activity feed; cannot confirm stall from commit date alone"
            if timing.activity_status == "ok"
            else "activity feed unavailable; cannot confirm stall from commit date alone"
        )
        return finalize(
            _unknown_report(
                repo=repo,
                branch=branch,
                now=checked_at,
                stall_hours=stall_hours,
                reason=reason,
                entries_count=mq.total_count,
                build_depth=mq.build_depth,
                tip_sha=tip_sha,
                last_merge_at=format_iso_utc(landed_dt),
                last_merge_source=landed_source,
                minutes_since_merge=mins_since_merge,
                head=head_report,
                queue=queue_summaries,
                queue_truncated=mq.queue_truncated,
                runner_pools=runner_pools,
                active_jobs_truncated=active_jobs_truncated,
                summary=f"unknown: {reason} -- {detail}",
            )
        )

    return finalize(
        StallReport(
            schema_version=SCHEMA_VERSION,
            verdict="stall",
            exit_code=EXIT_STALLED,
            reason="entries present, no merges in window, head not red",
            repo=repo,
            branch=branch,
            checked_at=format_iso_utc(checked_at),
            stall_hours=stall_hours,
            entries_count=mq.total_count,
            build_depth=mq.build_depth,
            tip_sha=tip_sha,
            last_merge_at=format_iso_utc(landed_dt),
            last_merge_source=landed_source,
            minutes_since_merge=mins_since_merge,
            head=head_report,
            queue=queue_summaries,
            runner_pools=runner_pools,
            alert_emitted=False,
            summary=f"STALL: {detail}",
            queue_truncated=mq.queue_truncated,
            active_jobs_truncated=active_jobs_truncated,
        )
    )
