"""Strict single-layer parsers for every GitHub API response.

Every external payload (GraphQL merge queue, branch tip, repository activity
feed, check-run pages, workflow-run pages, and workflow-job pages) is validated
completely here and converted into frozen typed dataclasses. Any missing
required key, null, wrong type, out-of-range integer, malformed SHA or
timestamp, future timestamp, non-empty/malformed GraphQL ``errors`` field, or
count/list mismatch raises ``ResponseParseError``.

Downstream verdict logic operates exclusively on these typed objects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
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
NON_RED_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})
PENDING_CHECK_STATUSES = frozenset({"queued", "in_progress", "waiting", "pending", "requested"})
VALID_JOB_STATUSES = frozenset(
    {"queued", "in_progress", "completed", "waiting", "requested", "pending"}
)
LANDING_ACTIVITY_TYPES = frozenset({"merge_queue_merge", "push", "pr_merge", "force_push"})

QUEUE_GRAPHQL_PAGE_LIMIT = 20
ACTIVITY_PER_PAGE = 10
CHECK_RUNS_PER_PAGE = 100
WORKFLOW_RUNS_PER_PAGE = 50
JOBS_PER_PAGE = 100


class ResponseParseError(ValueError):
    """Raised when a GitHub API response violates strict structural/semantic rules."""


# ---------------------------------------------------------------- primitives

def parse_iso_utc(raw: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp with explicit timezone into UTC, or return None."""
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
        return None
    return dt.astimezone(UTC)


def _req_dict(val: Any, ctx: str) -> dict[str, Any]:
    if not isinstance(val, dict):
        raise ResponseParseError(f"{ctx}: expected object, got {type(val).__name__}")
    return val


def _req_list(val: Any, ctx: str) -> list[Any]:
    if not isinstance(val, list):
        raise ResponseParseError(f"{ctx}: expected array, got {type(val).__name__}")
    return val


def _req_key(obj: dict[str, Any], key: str, ctx: str) -> Any:
    if key not in obj:
        raise ResponseParseError(f"{ctx}: missing required key {key!r}")
    return obj[key]


def _req_int(val: Any, ctx: str, *, min_value: int | None = None) -> int:
    if isinstance(val, bool) or not isinstance(val, int):
        raise ResponseParseError(f"{ctx}: expected integer, got {type(val).__name__}")
    if min_value is not None and val < min_value:
        raise ResponseParseError(f"{ctx}: integer {val} < minimum {min_value}")
    return val


def _req_nonempty_str(val: Any, ctx: str) -> str:
    if not isinstance(val, str) or not val.strip():
        raise ResponseParseError(f"{ctx}: expected non-empty string")
    return val


def _req_sha(val: Any, ctx: str) -> str:
    if not isinstance(val, str) or not SHA_RE.fullmatch(val):
        raise ResponseParseError(f"{ctx}: expected git SHA hex string")
    return val


def _req_iso_utc(val: Any, ctx: str, *, now: datetime) -> datetime:
    dt = parse_iso_utc(val)
    if dt is None:
        raise ResponseParseError(f"{ctx}: expected ISO-8601 UTC timestamp")
    if dt > now:
        raise ResponseParseError(f"{ctx}: timestamp {dt.isoformat()} is in the future")
    return dt


# ---------------------------------------------------------------- typed models

@dataclass(frozen=True)
class ParsedQueueNode:
    position: int
    state: str
    enqueued_at: datetime
    head_sha: str
    pr_number: int
    pr_title: str


@dataclass(frozen=True)
class ParsedMergeQueue:
    build_depth: int
    total_count: int
    nodes: tuple[ParsedQueueNode, ...]
    queue_truncated: bool
    head: ParsedQueueNode | None


@dataclass(frozen=True)
class ParsedBranchTip:
    sha: str
    committer_date: datetime


@dataclass(frozen=True)
class ParsedActivityEvent:
    activity_type: str
    timestamp: datetime


@dataclass(frozen=True)
class ParsedActivityFeed:
    events: tuple[ParsedActivityEvent, ...]
    latest_landing_at: datetime | None


@dataclass(frozen=True)
class ParsedCheckRun:
    name: str
    status: str
    conclusion: str | None
    is_red: bool
    is_pending: bool
    started_at: datetime | None


@dataclass(frozen=True)
class ParsedCheckRunsPage:
    total_count: int
    runs: tuple[ParsedCheckRun, ...]


@dataclass(frozen=True)
class ParsedWorkflowRunsPage:
    total_count: int
    run_ids: tuple[int, ...]


@dataclass(frozen=True)
class ParsedJob:
    status: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class ParsedJobsPage:
    total_count: int
    jobs: tuple[ParsedJob, ...]


# ---------------------------------------------------------------- parsers

def parse_merge_queue(payload: Any, *, now: datetime) -> ParsedMergeQueue:
    """Strictly validate and parse a GraphQL mergeQueue response."""
    root = _req_dict(payload, "graphql")
    if "errors" in root:
        errs = root["errors"]
        if not isinstance(errs, list) or len(errs) > 0:
            raise ResponseParseError("graphql: response contained errors or invalid errors field")

    data = _req_dict(_req_key(root, "data", "graphql"), "graphql.data")
    repo_obj = _req_dict(_req_key(data, "repository", "graphql.data"), "graphql.data.repository")
    mq_obj = _req_dict(
        _req_key(repo_obj, "mergeQueue", "graphql.data.repository"),
        "graphql.data.repository.mergeQueue",
    )

    cfg = _req_dict(
        _req_key(mq_obj, "configuration", "mergeQueue"),
        "mergeQueue.configuration",
    )
    build_depth = _req_int(
        _req_key(cfg, "maximumEntriesToBuild", "mergeQueue.configuration"),
        "mergeQueue.configuration.maximumEntriesToBuild",
        min_value=0,
    )

    entries_obj = _req_dict(_req_key(mq_obj, "entries", "mergeQueue"), "mergeQueue.entries")
    total_count = _req_int(
        _req_key(entries_obj, "totalCount", "mergeQueue.entries"),
        "mergeQueue.entries.totalCount",
        min_value=0,
    )
    raw_nodes = _req_list(
        _req_key(entries_obj, "nodes", "mergeQueue.entries"),
        "mergeQueue.entries.nodes",
    )

    expected_node_count = min(total_count, QUEUE_GRAPHQL_PAGE_LIMIT)
    if len(raw_nodes) != expected_node_count:
        raise ResponseParseError(
            f"mergeQueue.entries: totalCount={total_count} inconsistent with "
            f"{len(raw_nodes)} nodes (expected {expected_node_count})"
        )

    parsed_nodes: list[ParsedQueueNode] = []
    seen_positions: set[int] = set()
    for idx, raw_node in enumerate(raw_nodes):
        ctx = f"mergeQueue.entries.nodes[{idx}]"
        node = _req_dict(raw_node, ctx)
        position = _req_int(_req_key(node, "position", ctx), f"{ctx}.position", min_value=1)
        if position > total_count:
            raise ResponseParseError(
                f"{ctx}.position: position {position} exceeds totalCount={total_count}"
            )
        if position in seen_positions:
            raise ResponseParseError(f"{ctx}.position: duplicate position {position}")
        seen_positions.add(position)

        state = _req_nonempty_str(_req_key(node, "state", ctx), f"{ctx}.state")
        enqueued_at = _req_iso_utc(
            _req_key(node, "enqueuedAt", ctx), f"{ctx}.enqueuedAt", now=now
        )
        head_commit = _req_dict(_req_key(node, "headCommit", ctx), f"{ctx}.headCommit")
        head_sha = _req_sha(
            _req_key(head_commit, "oid", f"{ctx}.headCommit"),
            f"{ctx}.headCommit.oid",
        )
        pr_obj = _req_dict(_req_key(node, "pullRequest", ctx), f"{ctx}.pullRequest")
        pr_number = _req_int(
            _req_key(pr_obj, "number", f"{ctx}.pullRequest"),
            f"{ctx}.pullRequest.number",
            min_value=1,
        )
        pr_title = _req_nonempty_str(
            _req_key(pr_obj, "title", f"{ctx}.pullRequest"),
            f"{ctx}.pullRequest.title",
        )
        parsed_nodes.append(
            ParsedQueueNode(
                position=position,
                state=state,
                enqueued_at=enqueued_at,
                head_sha=head_sha,
                pr_number=pr_number,
                pr_title=pr_title,
            )
        )

    head: ParsedQueueNode | None = None
    if total_count > 0:
        pos1 = [n for n in parsed_nodes if n.position == 1]
        if len(pos1) != 1:
            raise ResponseParseError(
                f"mergeQueue.entries: expected exactly one position 1 entry, found {len(pos1)}"
            )
        head = pos1[0]

    return ParsedMergeQueue(
        build_depth=build_depth,
        total_count=total_count,
        nodes=tuple(parsed_nodes),
        queue_truncated=len(parsed_nodes) < total_count,
        head=head,
    )


def parse_branch_tip(payload: Any, *, now: datetime) -> ParsedBranchTip:
    """Strictly validate and parse a REST commits/{branch} response."""
    root = _req_dict(payload, "branch_tip")
    sha = _req_sha(_req_key(root, "sha", "branch_tip"), "branch_tip.sha")
    commit = _req_dict(_req_key(root, "commit", "branch_tip"), "branch_tip.commit")
    committer = _req_dict(
        _req_key(commit, "committer", "branch_tip.commit"),
        "branch_tip.commit.committer",
    )
    committer_date = _req_iso_utc(
        _req_key(committer, "date", "branch_tip.commit.committer"),
        "branch_tip.commit.committer.date",
        now=now,
    )
    return ParsedBranchTip(sha=sha, committer_date=committer_date)


def parse_activity_feed(payload: Any, *, now: datetime) -> ParsedActivityFeed:
    """Strictly validate and parse a REST repos/{repo}/activity response."""
    raw_events = _req_list(payload, "activity")
    if len(raw_events) > ACTIVITY_PER_PAGE:
        raise ResponseParseError(
            f"activity: length {len(raw_events)} exceeds per_page={ACTIVITY_PER_PAGE}"
        )
    parsed: list[ParsedActivityEvent] = []
    latest_landing: datetime | None = None
    for idx, raw_event in enumerate(raw_events):
        ctx = f"activity[{idx}]"
        ev = _req_dict(raw_event, ctx)
        activity_type = _req_nonempty_str(
            _req_key(ev, "activity_type", ctx), f"{ctx}.activity_type"
        )
        ts = _req_iso_utc(_req_key(ev, "timestamp", ctx), f"{ctx}.timestamp", now=now)
        parsed.append(ParsedActivityEvent(activity_type=activity_type, timestamp=ts))
        if activity_type in LANDING_ACTIVITY_TYPES and (
            latest_landing is None or ts > latest_landing
        ):
            latest_landing = ts
    return ParsedActivityFeed(events=tuple(parsed), latest_landing_at=latest_landing)


def parse_check_runs_page(payload: Any, *, now: datetime) -> ParsedCheckRunsPage:
    """Strictly validate and parse one REST commits/{sha}/check-runs page."""
    root = _req_dict(payload, "check_runs")
    total_count = _req_int(
        _req_key(root, "total_count", "check_runs"),
        "check_runs.total_count",
        min_value=0,
    )
    raw_runs = _req_list(_req_key(root, "check_runs", "check_runs"), "check_runs.check_runs")
    if len(raw_runs) > CHECK_RUNS_PER_PAGE:
        raise ResponseParseError(
            f"check_runs.check_runs: page length {len(raw_runs)} "
            f"exceeds per_page={CHECK_RUNS_PER_PAGE}"
        )
    if len(raw_runs) > total_count:
        raise ResponseParseError(
            f"check_runs: page length {len(raw_runs)} exceeds total_count={total_count}"
        )

    parsed_runs: list[ParsedCheckRun] = []
    for idx, raw_run in enumerate(raw_runs):
        ctx = f"check_runs.check_runs[{idx}]"
        run = _req_dict(raw_run, ctx)
        name = _req_nonempty_str(_req_key(run, "name", ctx), f"{ctx}.name")
        status = _req_nonempty_str(_req_key(run, "status", ctx), f"{ctx}.status")

        conclusion: str | None = None
        is_red = False
        is_pending = False
        if status == "completed":
            conclusion = _req_nonempty_str(_req_key(run, "conclusion", ctx), f"{ctx}.conclusion")
            if conclusion in RED_CONCLUSIONS:
                is_red = True
            elif conclusion in NON_RED_CONCLUSIONS:
                is_red = False
            else:
                raise ResponseParseError(
                    f"{ctx}.conclusion: unrecognized completed conclusion {conclusion!r}"
                )
        elif status in PENDING_CHECK_STATUSES:
            is_pending = True
            if "conclusion" in run and run["conclusion"] is not None:
                raise ResponseParseError(
                    f"{ctx}.conclusion: pending check run must have null conclusion"
                )
        else:
            raise ResponseParseError(f"{ctx}.status: unrecognized check-run status {status!r}")

        started_at: datetime | None = None
        if "started_at" in run and run["started_at"] is not None:
            started_at = _req_iso_utc(run["started_at"], f"{ctx}.started_at", now=now)

        parsed_runs.append(
            ParsedCheckRun(
                name=name,
                status=status,
                conclusion=conclusion,
                is_red=is_red,
                is_pending=is_pending,
                started_at=started_at,
            )
        )

    return ParsedCheckRunsPage(total_count=total_count, runs=tuple(parsed_runs))


def parse_workflow_runs_page(payload: Any) -> ParsedWorkflowRunsPage:
    """Strictly validate and parse one REST actions/runs?status=... page."""
    root = _req_dict(payload, "workflow_runs")
    total_count = _req_int(
        _req_key(root, "total_count", "workflow_runs"),
        "workflow_runs.total_count",
        min_value=0,
    )
    raw_runs = _req_list(
        _req_key(root, "workflow_runs", "workflow_runs"),
        "workflow_runs.workflow_runs",
    )
    expected_len = min(total_count, WORKFLOW_RUNS_PER_PAGE)
    if len(raw_runs) != expected_len:
        raise ResponseParseError(
            f"workflow_runs: total_count={total_count} inconsistent with "
            f"{len(raw_runs)} items (expected {expected_len})"
        )

    run_ids: list[int] = []
    seen_ids: set[int] = set()
    for idx, raw_run in enumerate(raw_runs):
        ctx = f"workflow_runs.workflow_runs[{idx}]"
        run = _req_dict(raw_run, ctx)
        rid = _req_int(_req_key(run, "id", ctx), f"{ctx}.id", min_value=1)
        if rid in seen_ids:
            raise ResponseParseError(
                f"{ctx}.id: duplicate workflow run id {rid} within single page"
            )
        seen_ids.add(rid)
        run_ids.append(rid)

    return ParsedWorkflowRunsPage(total_count=total_count, run_ids=tuple(run_ids))


def parse_jobs_page(payload: Any) -> ParsedJobsPage:
    """Strictly validate and parse one REST actions/runs/{id}/jobs page."""
    root = _req_dict(payload, "jobs")
    total_count = _req_int(
        _req_key(root, "total_count", "jobs"),
        "jobs.total_count",
        min_value=0,
    )
    raw_jobs = _req_list(_req_key(root, "jobs", "jobs"), "jobs.jobs")
    if len(raw_jobs) > JOBS_PER_PAGE:
        raise ResponseParseError(
            f"jobs.jobs: page length {len(raw_jobs)} exceeds per_page={JOBS_PER_PAGE}"
        )
    if len(raw_jobs) > total_count:
        raise ResponseParseError(
            f"jobs: page length {len(raw_jobs)} exceeds total_count={total_count}"
        )

    parsed_jobs: list[ParsedJob] = []
    for idx, raw_job in enumerate(raw_jobs):
        ctx = f"jobs.jobs[{idx}]"
        job = _req_dict(raw_job, ctx)
        status = _req_nonempty_str(_req_key(job, "status", ctx), f"{ctx}.status")
        if status not in VALID_JOB_STATUSES:
            raise ResponseParseError(f"{ctx}.status: unrecognized job status {status!r}")
        raw_labels = _req_list(_req_key(job, "labels", ctx), f"{ctx}.labels")
        labels = tuple(
            _req_nonempty_str(lbl, f"{ctx}.labels[{l_idx}]")
            for l_idx, lbl in enumerate(raw_labels)
        )
        parsed_jobs.append(ParsedJob(status=status, labels=labels))

    return ParsedJobsPage(total_count=total_count, jobs=tuple(parsed_jobs))
