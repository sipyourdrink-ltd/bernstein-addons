"""Merge queue stall guard — outcome-based, read-only queue watcher.

A merge queue can hold entries and merge nothing while every individual
health check reads as green. A review-lane watcher sees approvals moving; a
hung-job watcher sees no hung job; an unmergeable-entry watcher sees every
entry mergeable; an ejection watcher fires only after a pull request is
dropped. Four watchers, each correct, none asking whether anything actually
lands.

What wedges a queue without turning anything red is runner saturation:
speculative batches fan out more jobs for a constrained runner label than
available runner capacity can drain, so every batch crawls behind the same
labels and the head entry never finishes inside the window. Nothing is red,
nothing is hung, and nothing is unmergeable.

This guard keys on the outcome rather than any single failure mechanism:

    entries present  AND  nothing merged for N hours  AND  head not red

Then it answers the operator's question: which entry sits at the head, what
pending checks it is waiting on (split into "waiting for a runner" versus
"executing"), and the active (queued and in-progress) workflow job counts by
runner label across inspected runs.

It reads check-run conclusions and merge-queue entry states from the GitHub
API, and dates the last merge from the repository activity feed (the ref
update itself) rather than the tip commit's committer date, which the queue
stamps at speculative build time and can lag the merge by over an hour. If
the activity feed is unavailable and the tip commit date falls outside the
stall window, the guard returns ``unknown`` rather than asserting a stall.

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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bernstein_merge_queue_stall_guard.gh import GhClient, GhQueryError, GhUnavailableError
from bernstein_merge_queue_stall_guard.models import (
    HeadEntryReport,
    LastMergeSource,
    PendingCheck,
    QueueEntrySummary,
    StallReport,
    Verdict,
)

SCHEMA_VERSION = "1.0"
REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/@-]+$")
RED_CONCLUSIONS = frozenset(
    {
        "failure",
        "timed_out",
        "cancelled",
        "startup_failure",
        "action_required",
        "stale",
    }
)
LANDING_ACTIVITY_TYPES = frozenset({"merge_queue_merge", "push", "pr_merge", "force_push"})
MAX_CHECK_RUN_PAGES = 20
MAX_RUNS_TO_CENSUS = 30
MAX_JOB_PAGES = 5

EXIT_HEALTHY = 0
EXIT_STALLED = 1
EXIT_UNREADABLE = 2


def parse_iso_utc(raw: Any) -> datetime | None:
    """Parse an ISO-8601 UTC timestamp, returning None if missing or invalid."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


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
        return StallReport(
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


def _fetch_all_check_runs(
    gh: GhClient,
    repo: str,
    head_sha: str,
) -> tuple[list[dict[str, Any]], bool]:
    """Fetch paginated check runs for head_sha; returns (runs, truncated)."""
    all_runs: list[dict[str, Any]] = []
    truncated = False
    for page in range(1, MAX_CHECK_RUN_PAGES + 1):
        route = (
            f"repos/{repo}/commits/{head_sha}/check-runs?per_page=100"
            if page == 1
            else f"repos/{repo}/commits/{head_sha}/check-runs?per_page=100&page={page}"
        )
        payload = gh.get(route)
        if not isinstance(payload, dict) or not isinstance(payload.get("check_runs"), list):
            raise GhQueryError("check-runs payload missing check_runs list")
        page_runs = [r for r in payload["check_runs"] if isinstance(r, dict)]
        all_runs.extend(page_runs)
        total_count = _int_or_none(payload.get("total_count"))
        if total_count is not None and len(all_runs) >= total_count:
            break
        if len(page_runs) < 100:
            break
        if page == MAX_CHECK_RUN_PAGES:
            truncated = True
    return all_runs, truncated


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
    if isinstance(stall_hours, bool) or not math.isfinite(stall_hours) or stall_hours <= 0:
        raise ValueError(f"stall_hours must be a finite positive number, got {stall_hours!r}")
    if (
        isinstance(alert_repeat_hours, bool)
        or not math.isfinite(alert_repeat_hours)
        or alert_repeat_hours < 0
    ):
        raise ValueError(
            f"alert_repeat_hours must be a finite non-negative number, got {alert_repeat_hours!r}"
        )

    gh = client or GhClient()
    owner, name = repo.split("/", 1)
    effective_state_dir = state_dir if state_dir is not None else _default_state_dir(repo, branch)

    def finalize(report: StallReport) -> StallReport:
        if not write_state:
            return report
        return _persist_state(
            report,
            state_dir=effective_state_dir,
            now=checked_at,
            alert_repeat_hours=alert_repeat_hours,
        )

    # 1. Read merge queue entries via GraphQL.
    try:
        queue_payload = gh.graphql(_graphql_queue_query(owner, name, branch))
    except (GhUnavailableError, GhQueryError) as exc:
        return finalize(
            _unknown_report(
                repo=repo,
                branch=branch,
                now=checked_at,
                stall_hours=stall_hours,
                reason=f"merge-queue query failed: {exc}",
            )
        )

    data_obj = queue_payload.get("data")
    repo_obj = data_obj.get("repository") if isinstance(data_obj, dict) else None
    merge_queue = repo_obj.get("mergeQueue") if isinstance(repo_obj, dict) else None
    if not isinstance(merge_queue, dict):
        return finalize(
            _unknown_report(
                repo=repo,
                branch=branch,
                now=checked_at,
                stall_hours=stall_hours,
                reason="merge-queue payload missing mergeQueue object",
            )
        )

    entries_obj = merge_queue.get("entries")
    entries_count = (
        _int_or_none(entries_obj.get("totalCount")) if isinstance(entries_obj, dict) else None
    )
    if entries_count is None or entries_count < 0:
        return finalize(
            _unknown_report(
                repo=repo,
                branch=branch,
                now=checked_at,
                stall_hours=stall_hours,
                reason="merge-queue payload had no entry count",
            )
        )

    config_obj = merge_queue.get("configuration")
    build_depth = (
        _int_or_none(config_obj.get("maximumEntriesToBuild"))
        if isinstance(config_obj, dict)
        else None
    )

    raw_nodes = entries_obj.get("nodes") if isinstance(entries_obj, dict) else None
    nodes: list[dict[str, Any]] = (
        [n for n in raw_nodes if isinstance(n, dict)] if isinstance(raw_nodes, list) else []
    )
    queue_summaries = tuple(
        QueueEntrySummary(
            position=_int_or_none(node.get("position")) or 0,
            pr_number=(
                _int_or_none(node.get("pullRequest", {}).get("number"))
                if isinstance(node.get("pullRequest"), dict)
                else None
            ),
            state=str(node.get("state") or "?"),
        )
        for node in nodes
    )
    queue_truncated = len(queue_summaries) < entries_count

    # 2. Read branch tip and repository activity feed for last landing time.
    try:
        tip_payload = gh.get(f"repos/{repo}/commits/{branch}")
    except (GhUnavailableError, GhQueryError) as exc:
        return finalize(
            _unknown_report(
                repo=repo,
                branch=branch,
                now=checked_at,
                stall_hours=stall_hours,
                reason=f"could not read {branch} tip: {exc}",
                entries_count=entries_count,
                build_depth=build_depth,
                queue=queue_summaries,
                queue_truncated=queue_truncated,
            )
        )

    if not isinstance(tip_payload, dict):
        return finalize(
            _unknown_report(
                repo=repo,
                branch=branch,
                now=checked_at,
                stall_hours=stall_hours,
                reason=f"unexpected {branch} tip payload",
                entries_count=entries_count,
                build_depth=build_depth,
                queue=queue_summaries,
                queue_truncated=queue_truncated,
            )
        )

    tip_sha = str(tip_payload.get("sha") or "") or None
    tip_commit = tip_payload.get("commit")
    tip_committer = tip_commit.get("committer") if isinstance(tip_commit, dict) else None
    tip_iso = tip_committer.get("date") if isinstance(tip_committer, dict) else None

    landed_dt = parse_iso_utc(tip_iso)
    landed_source: LastMergeSource = "commit-date"

    try:
        activity_payload = gh.get(f"repos/{repo}/activity?ref=refs/heads/{branch}&per_page=10")
    except (GhUnavailableError, GhQueryError):
        activity_payload = None

    if isinstance(activity_payload, list):
        for event in activity_payload:
            if not isinstance(event, dict):
                continue
            if event.get("activity_type") in LANDING_ACTIVITY_TYPES:
                act_dt = parse_iso_utc(event.get("timestamp"))
                if act_dt is not None:
                    landed_dt = act_dt
                    landed_source = "activity"
                    break

    if landed_dt is None:
        return finalize(
            _unknown_report(
                repo=repo,
                branch=branch,
                now=checked_at,
                stall_hours=stall_hours,
                reason=f"unparseable {branch} landing timestamp",
                entries_count=entries_count,
                build_depth=build_depth,
                tip_sha=tip_sha,
                queue=queue_summaries,
                queue_truncated=queue_truncated,
            )
        )

    mins_since_merge = _minutes_since(checked_at, landed_dt)
    assert mins_since_merge is not None
    tip_short = tip_sha[:8] if tip_sha else "?"
    depth_str = str(build_depth) if build_depth is not None else "?"
    base_summary = (
        f"entries={entries_count} build_depth={depth_str} tip={tip_short} "
        f"last_merge={mins_since_merge}m ago (src={landed_source})"
    )

    if entries_count == 0:
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
                build_depth=build_depth,
                tip_sha=tip_sha,
                last_merge_at=format_iso_utc(landed_dt),
                last_merge_source=landed_source,
                minutes_since_merge=mins_since_merge,
                head=None,
                queue=queue_summaries,
                runner_pools=None,
                alert_emitted=False,
                summary=f"healthy: queue empty -- {base_summary}",
                queue_truncated=queue_truncated,
                active_jobs_truncated=False,
            )
        )

    stall_threshold = timedelta(hours=stall_hours)
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
                entries_count=entries_count,
                build_depth=build_depth,
                tip_sha=tip_sha,
                last_merge_at=format_iso_utc(landed_dt),
                last_merge_source=landed_source,
                minutes_since_merge=mins_since_merge,
                head=None,
                queue=queue_summaries,
                runner_pools=None,
                alert_emitted=False,
                summary=f"healthy: merges are flowing -- {base_summary}",
                queue_truncated=queue_truncated,
                active_jobs_truncated=False,
            )
        )

    # A commit's committer date can be much older than when it actually landed
    # on the branch, so never assert a stall when the activity feed was unavailable.
    if landed_source != "activity":
        reason = "activity feed unavailable; cannot confirm stall from commit date alone"
        return finalize(
            _unknown_report(
                repo=repo,
                branch=branch,
                now=checked_at,
                stall_hours=stall_hours,
                reason=reason,
                entries_count=entries_count,
                build_depth=build_depth,
                tip_sha=tip_sha,
                last_merge_at=format_iso_utc(landed_dt),
                last_merge_source=landed_source,
                minutes_since_merge=mins_since_merge,
                queue=queue_summaries,
                queue_truncated=queue_truncated,
                summary=f"unknown: {reason} -- {base_summary}",
            )
        )

    # 3. Inspect head entry (position 1) and its check runs.
    head_node = next(
        (node for node in nodes if _int_or_none(node.get("position")) == 1),
        nodes[0] if nodes else None,
    )
    head_pr: int | None = None
    head_title: str | None = None
    head_sha: str | None = None
    head_state = "?"
    head_enq_iso: str | None = None
    head_enq_mins: int | None = None

    if head_node is not None:
        pr_obj = head_node.get("pullRequest")
        if isinstance(pr_obj, dict):
            head_pr = _int_or_none(pr_obj.get("number"))
            raw_title = pr_obj.get("title")
            head_title = str(raw_title) if isinstance(raw_title, str) else None
        commit_obj = head_node.get("headCommit")
        if isinstance(commit_obj, dict) and isinstance(commit_obj.get("oid"), str):
            head_sha = commit_obj["oid"] or None
        head_state = str(head_node.get("state") or "?")
        enq_dt = parse_iso_utc(head_node.get("enqueuedAt"))
        if enq_dt is not None:
            head_enq_iso = format_iso_utc(enq_dt)
            head_enq_mins = _minutes_since(checked_at, enq_dt)

    red_count: int | None = None
    pending_count: int | None = None
    pending_checks: list[PendingCheck] = []
    checks_error: str | None = None

    if head_node is None:
        checks_error = "head entry missing from merge queue nodes"
    elif not head_sha:
        checks_error = "head entry missing headCommit.oid"
    else:
        try:
            runs, checks_truncated = _fetch_all_check_runs(gh, repo, head_sha)
            red_count = sum(1 for r in runs if r.get("conclusion") in RED_CONCLUSIONS)
            pending_runs = [r for r in runs if r.get("status") != "completed"]
            pending_count = len(pending_runs)
            for run in pending_runs:
                check_name = str(run.get("name") or "unnamed")
                started_dt = parse_iso_utc(run.get("started_at"))
                if started_dt is None:
                    pending_checks.append(
                        PendingCheck(
                            name=check_name,
                            wait_kind="waiting_for_runner",
                            running_minutes=None,
                        )
                    )
                else:
                    pending_checks.append(
                        PendingCheck(
                            name=check_name,
                            wait_kind="executing",
                            running_minutes=_minutes_since(checked_at, started_dt),
                        )
                    )
            if checks_truncated and red_count == 0:
                red_count = None
                checks_error = "check-runs pagination limit reached"
        except (GhUnavailableError, GhQueryError):
            red_count = None
            pending_count = None
            checks_error = "check-runs query failed"

    head_report = HeadEntryReport(
        pr_number=head_pr,
        title=head_title,
        state=head_state,
        head_sha=head_sha,
        enqueued_at=head_enq_iso,
        enqueued_minutes_ago=head_enq_mins,
        red_checks_count=red_count,
        pending_checks_count=pending_count,
        pending_checks=tuple(pending_checks),
        checks_error=checks_error,
    )

    # 4. Active (queued and in-progress) workflow job counts by runner label.
    runner_pools: dict[str, int] | None = None
    active_jobs_truncated = False
    try:
        runs_payload = gh.get(f"repos/{repo}/actions/runs?status=in_progress&per_page=50")
        if isinstance(runs_payload, dict) and isinstance(runs_payload.get("workflow_runs"), list):
            label_counts: Counter[str] = Counter()
            all_run_ids = [
                rid
                for item in runs_payload["workflow_runs"]
                if isinstance(item, dict) and (rid := _int_or_none(item.get("id"))) is not None
            ]
            total_runs = _int_or_none(runs_payload.get("total_count"))
            if len(all_run_ids) > MAX_RUNS_TO_CENSUS or (
                total_runs is not None and total_runs > len(all_run_ids[:MAX_RUNS_TO_CENSUS])
            ):
                active_jobs_truncated = True
            run_ids = all_run_ids[:MAX_RUNS_TO_CENSUS]
            for rid in run_ids:
                jobs_seen = 0
                for page in range(1, MAX_JOB_PAGES + 1):
                    jobs_route = (
                        f"repos/{repo}/actions/runs/{rid}/jobs?per_page=100"
                        if page == 1
                        else f"repos/{repo}/actions/runs/{rid}/jobs?per_page=100&page={page}"
                    )
                    try:
                        jobs_payload = gh.get(jobs_route)
                    except (GhUnavailableError, GhQueryError):
                        active_jobs_truncated = True
                        break
                    if not isinstance(jobs_payload, dict) or not isinstance(
                        jobs_payload.get("jobs"), list
                    ):
                        active_jobs_truncated = True
                        break
                    page_jobs = [j for j in jobs_payload["jobs"] if isinstance(j, dict)]
                    jobs_seen += len(page_jobs)
                    for job in page_jobs:
                        if job.get("status") not in ("in_progress", "queued"):
                            continue
                        labels = job.get("labels")
                        if isinstance(labels, list) and labels:
                            key = ",".join(str(lbl) for lbl in labels)
                            label_counts[key] += 1
                    total_jobs = _int_or_none(jobs_payload.get("total_count"))
                    if total_jobs is not None and jobs_seen >= total_jobs:
                        break
                    if len(page_jobs) < 100:
                        break
                    if page == MAX_JOB_PAGES:
                        active_jobs_truncated = True
            runner_pools = dict(
                sorted(label_counts.items(), key=lambda pair: (-pair[1], pair[0]))
            )
    except (GhUnavailableError, GhQueryError):
        runner_pools = None
        active_jobs_truncated = True

    # Format human summary line.
    if checks_error is not None:
        pending_desc = checks_error
    elif pending_checks:
        pending_desc = ", ".join(item.format_text() for item in pending_checks)
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
    enq_str = f"{head_enq_mins}m" if head_enq_mins is not None else "?"
    pending_str = str(pending_count) if pending_count is not None else "?"
    red_str = str(red_count) if red_count is not None else "?"
    detail = (
        f"head=#{head_pr if head_pr is not None else '?'} ({head_state}, enqueued {enq_str} ago) "
        f"waiting on {pending_str} check(s): {pending_desc}; red={red_str}; "
        f"active_jobs: {pool_desc}; queue: {queue_list}; {base_summary}"
    )

    if red_count is not None and red_count > 0:
        verdict: Verdict = "blocked-red"
        return finalize(
            StallReport(
                schema_version=SCHEMA_VERSION,
                verdict=verdict,
                exit_code=EXIT_HEALTHY,
                reason="head entry has failing checks",
                repo=repo,
                branch=branch,
                checked_at=format_iso_utc(checked_at),
                stall_hours=stall_hours,
                entries_count=entries_count,
                build_depth=build_depth,
                tip_sha=tip_sha,
                last_merge_at=format_iso_utc(landed_dt),
                last_merge_source=landed_source,
                minutes_since_merge=mins_since_merge,
                head=head_report,
                queue=queue_summaries,
                runner_pools=runner_pools,
                alert_emitted=False,
                summary=f"blocked-red: head entry is red -- {detail}",
                queue_truncated=queue_truncated,
                active_jobs_truncated=active_jobs_truncated,
            )
        )

    if red_count is None or checks_error is not None:
        return finalize(
            _unknown_report(
                repo=repo,
                branch=branch,
                now=checked_at,
                stall_hours=stall_hours,
                reason=checks_error or "head checks unreadable",
                entries_count=entries_count,
                build_depth=build_depth,
                tip_sha=tip_sha,
                last_merge_at=format_iso_utc(landed_dt),
                last_merge_source=landed_source,
                minutes_since_merge=mins_since_merge,
                head=head_report,
                queue=queue_summaries,
                queue_truncated=queue_truncated,
                runner_pools=runner_pools,
                active_jobs_truncated=active_jobs_truncated,
                summary=f"unknown: {checks_error or 'head checks unreadable'} -- {detail}",
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
            entries_count=entries_count,
            build_depth=build_depth,
            tip_sha=tip_sha,
            last_merge_at=format_iso_utc(landed_dt),
            last_merge_source=landed_source,
            minutes_since_merge=mins_since_merge,
            head=head_report,
            queue=queue_summaries,
            runner_pools=runner_pools,
            alert_emitted=False,
            summary=f"STALL: {detail}",
            queue_truncated=queue_truncated,
            active_jobs_truncated=active_jobs_truncated,
        )
    )
