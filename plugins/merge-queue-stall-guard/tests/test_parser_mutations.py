"""Class-wide mutation suite and 46 regression scenarios."""

from __future__ import annotations

import copy
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bernstein_merge_queue_stall_guard.gh import GhClient, GhQueryError
from bernstein_merge_queue_stall_guard.guard import evaluate_queue
from bernstein_merge_queue_stall_guard.parsers import (
    ResponseParseError,
    parse_workflow_runs_page,
)
from bernstein_merge_queue_stall_guard.schema_check import validate_report_dict

REPO = "example-org/example-repo"
BRANCH = "main"
HEAD_SHA = "a" * 40
TIP_SHA = "b" * 40
NOW_DT = datetime(2026, 1, 14, 15, 0, 0, tzinfo=UTC)

TIP_ROUTE = f"repos/{REPO}/commits/{BRANCH}"
ACTIVITY_ROUTE = f"repos/{REPO}/activity?ref=refs/heads/{BRANCH}&per_page=10"
CHECKS_ROUTE = f"repos/{REPO}/commits/{HEAD_SHA}/check-runs?per_page=100"
CHECKS_PAGE2_ROUTE = f"repos/{REPO}/commits/{HEAD_SHA}/check-runs?per_page=100&page=2"
RUNS_ROUTE = f"repos/{REPO}/actions/runs?status=in_progress&per_page=50"
QUEUED_RUNS_ROUTE = f"repos/{REPO}/actions/runs?status=queued&per_page=50"
JOBS_ROUTE = f"repos/{REPO}/actions/runs/101/jobs?per_page=100"
JOBS_PAGE2_ROUTE = f"repos/{REPO}/actions/runs/101/jobs?per_page=100&page=2"

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FUTURE_ISO = "2027-01-14T15:00:00Z"


class StubGhClient(GhClient):
    """In-memory stub GhClient for fast class-wide mutation testing."""

    def __init__(self, replies: dict[str, Any]) -> None:
        super().__init__(gh_bin="stub")
        self._replies = replies
        self.called_routes: list[str] = []

    def graphql(self, _query: str) -> dict[str, Any]:
        self.called_routes.append("graphql")
        val = self._replies.get("graphql")
        if isinstance(val, Exception):
            raise val
        if val is None:
            raise GhQueryError("stub graphql error")
        if not isinstance(val, dict):
            raise GhQueryError("gh api graphql returned non-object payload")
        return val

    def get(self, route: str) -> Any:
        self.called_routes.append(route)
        if route not in self._replies:
            raise GhQueryError(f"unexpected route: {route}")
        val = self._replies[route]
        if isinstance(val, Exception):
            raise val
        if val is None:
            raise GhQueryError(f"stub error on {route}")
        return val


def _load(name: str) -> Any:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _base_replies() -> dict[str, Any]:
    return {
        "graphql": _load("queue_stalled.json"),
        TIP_ROUTE: _load("tip_old.json"),
        ACTIVITY_ROUTE: _load("activity_old.json"),
        CHECKS_ROUTE: _load("checks_waiting.json"),
        RUNS_ROUTE: _load("runs_in_progress.json"),
        QUEUED_RUNS_ROUTE: {"total_count": 0, "workflow_runs": []},
        JOBS_ROUTE: _load("jobs_101.json"),
    }


def _node(
    *,
    position: Any = 1,
    state: Any = "AWAITING_CHECKS",
    enqueued_at: Any = "2026-01-14T12:00:00Z",
    head_sha: Any = HEAD_SHA,
    pr_number: Any = 17,
    pr_title: Any = "Example PR",
) -> dict[str, Any]:
    return {
        "position": position,
        "state": state,
        "enqueuedAt": enqueued_at,
        "headCommit": {"oid": head_sha},
        "pullRequest": {"number": pr_number, "title": pr_title},
    }


def _mq_payload(
    *,
    total_count: Any = 1,
    nodes: Any = None,
    build_depth: Any = 2,
    extra_top: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "data": {
            "repository": {
                "mergeQueue": {
                    "configuration": {"maximumEntriesToBuild": build_depth},
                    "entries": {
                        "totalCount": total_count,
                        "nodes": [_node()] if nodes is None else nodes,
                    },
                }
            }
        }
    }
    if extra_top:
        out.update(extra_top)
    return out


# ----------------------------------------------------------------------------
# Class-wide systematic mutation generator
# ----------------------------------------------------------------------------

_DELETE = object()


def _apply_at_path(doc: Any, path: tuple[str | int, ...], value: Any) -> Any:
    cloned = copy.deepcopy(doc)
    if not path:
        return value
    cur = cloned
    for key in path[:-1]:
        cur = cur[key]
    last = path[-1]
    if value is _DELETE:
        del cur[last]
    else:
        cur[last] = value
    return cloned


def _mutations_for_value(path: tuple[str | int, ...], val: Any) -> list[tuple[str, Any]]:
    """Generate systematic field-level corruption mutations for a JSON value."""
    name = ".".join(str(p) for p in path) if path else "<root>"
    out: list[tuple[str, Any]] = []
    if path:
        out.append((f"{name}:delete", _DELETE))
    out.append((f"{name}:null", None))

    if isinstance(val, dict):
        out.append((f"{name}:wrong_type_list", []))
        out.append((f"{name}:wrong_type_str", "corrupt"))
        out.append((f"{name}:empty_dict", {}))
    elif isinstance(val, list):
        out.append((f"{name}:wrong_type_dict", {}))
        out.append((f"{name}:wrong_type_str", "corrupt"))
        if val:
            out.append((f"{name}:empty_list", []))
            out.append((f"{name}:extra_elem", [*copy.deepcopy(val), copy.deepcopy(val[0])]))
            out.append((f"{name}:null_elem", [None, *copy.deepcopy(val[1:])]))
            if len(val) > 1:
                out.append((f"{name}:missing_elem", copy.deepcopy(val[:-1])))
    elif isinstance(val, int) and not isinstance(val, bool):
        out.append((f"{name}:negative", -1))
        out.append((f"{name}:wrong_type_str", str(val)))
        out.append((f"{name}:wrong_type_bool", True))
        if val > 0:
            out.append((f"{name}:off_by_minus_1", val - 1))
            out.append((f"{name}:off_by_plus_1", val + 1))
    elif isinstance(val, str):
        out.append((f"{name}:wrong_type_int", 12345))
        out.append((f"{name}:wrong_type_dict", {}))
        out.append((f"{name}:empty_str", ""))
        if val.endswith("Z") and "T" in val:
            out.append((f"{name}:bad_date", "not-a-date"))
            out.append((f"{name}:naive_date", val[:-1]))
            out.append((f"{name}:future_date", FUTURE_ISO))
        elif len(val) == 40 and all(c in "0123456789abcdef" for c in val.lower()):
            out.append((f"{name}:bad_sha", "../not-a-sha"))
    return out


def _collect_paths(
    doc: Any, prefix: tuple[str | int, ...] = ()
) -> list[tuple[tuple[str | int, ...], Any]]:
    paths: list[tuple[tuple[str | int, ...], Any]] = [(prefix, doc)]
    if isinstance(doc, dict):
        for k, v in doc.items():
            paths.extend(_collect_paths(v, (*prefix, k)))
    elif isinstance(doc, list) and doc:
        # Mutate the first element's subtree (representative of element schema).
        paths.extend(_collect_paths(doc[0], (*prefix, 0)))
    return paths


def _build_verdict_mutations() -> list[tuple[str, str, Any]]:
    """Build all systematic mutations across the 4 verdict-controlling responses."""
    base = _base_replies()
    cases: list[tuple[str, str, Any]] = []

    # Use a completed-green check fixture too so conclusion & started_at are mutated.
    verdict_targets: list[tuple[str, str, Any]] = [
        ("graphql", "graphql", base["graphql"]),
        ("tip", TIP_ROUTE, base[TIP_ROUTE]),
        ("activity", ACTIVITY_ROUTE, base[ACTIVITY_ROUTE]),
        ("checks_waiting", CHECKS_ROUTE, base[CHECKS_ROUTE]),
        (
            "checks_green",
            CHECKS_ROUTE,
            {
                "total_count": 1,
                "check_runs": [
                    {
                        "name": "ci-pass",
                        "status": "completed",
                        "conclusion": "success",
                        "started_at": "2026-01-14T14:00:00Z",
                    }
                ],
            },
        ),
    ]

    for label, route, doc in verdict_targets:
        for path, val in _collect_paths(doc):
            # Optional/nullable fields where deletion or null is explicitly valid:
            if path and path[-1] == "started_at":
                for mut_name, mut_val in _mutations_for_value(path, val):
                    if mut_val in (_DELETE, None):
                        continue
                    cases.append((f"{label}:{mut_name}", route, _apply_at_path(doc, path, mut_val)))
                continue
            # Changing activity_type to a valid non-empty non-landing string is valid;
            # still tested because it turns activity into no-landing -> unknown!
            # Changing maximumEntriesToBuild from 2 to 1 or 3 is a valid positive int;
            # skip off_by_±1 for non-count fields.
            is_count_field = bool(path and path[-1] in ("totalCount", "total_count", "position"))
            for mut_name, mut_val in _mutations_for_value(path, val):
                if mut_name.endswith(("off_by_minus_1", "off_by_plus_1")) and not is_count_field:
                    continue
                if path and path[-1] == "position" and mut_val == 0:
                    pass
                elif (
                    label == "graphql"
                    and path
                    == (
                        "data",
                        "repository",
                        "mergeQueue",
                        "configuration",
                        "maximumEntriesToBuild",
                    )
                    and isinstance(mut_val, int)
                    and not isinstance(mut_val, bool)
                    and mut_val >= 0
                ):
                    continue
                if label == "activity" and not path and mut_name.endswith("extra_elem"):
                    # /activity has no total_count; test exceeding per_page=10 and trailing null.
                    cases.append(
                        (
                            "activity:<root>:exceeds_per_page",
                            route,
                            [copy.deepcopy(doc[0]) for _ in range(11)],
                        )
                    )
                    cases.append(
                        (
                            "activity:<root>:trailing_null_elem",
                            route,
                            [*copy.deepcopy(doc), None],
                        )
                    )
                    continue
                cases.append((f"{label}:{mut_name}", route, _apply_at_path(doc, path, mut_val)))

    # Add explicit GraphQL errors field type corruptions.
    for err_label, err_val in (
        ("errors_list", [{"message": "fail"}]),
        ("errors_dict", {"message": "partial"}),
        ("errors_str", "partial"),
        ("errors_null", None),
        ("errors_int", 1),
    ):
        mut_doc = copy.deepcopy(base["graphql"])
        mut_doc["errors"] = err_val
        cases.append((f"graphql:{err_label}", "graphql", mut_doc))

    return cases


def _build_diagnostic_mutations() -> list[tuple[str, str, Any]]:
    """Build all systematic mutations across Actions runs & jobs diagnostics."""
    base = _base_replies()
    cases: list[tuple[str, str, Any]] = []
    diag_targets = [
        ("runs_in_progress", RUNS_ROUTE, base[RUNS_ROUTE]),
        ("jobs_101", JOBS_ROUTE, base[JOBS_ROUTE]),
    ]
    for label, route, doc in diag_targets:
        for path, val in _collect_paths(doc):
            is_count_field = bool(path and path[-1] == "total_count")
            for mut_name, mut_val in _mutations_for_value(path, val):
                if mut_name.endswith(("off_by_minus_1", "off_by_plus_1")) and not is_count_field:
                    continue
                # On jobs_101, changing status to valid non-active string or adding valid label
                # isn't corruption; only non-empty non-dict mutations count when breaking schema.
                if (
                    label == "jobs_101"
                    and path == ("jobs", 0, "labels")
                    and mut_name.endswith(("extra_elem", "missing_elem"))
                ):
                    continue
                if (
                    label == "jobs_101"
                    and path == ("jobs", 0, "labels", 0)
                    and mut_val is _DELETE
                ):
                    # Deleting the only label leaves labels=[] which still sets truncated=True.
                    pass
                cases.append((f"{label}:{mut_name}", route, _apply_at_path(doc, path, mut_val)))
    return cases


VERDICT_MUTATIONS = _build_verdict_mutations()
DIAGNOSTIC_MUTATIONS = _build_diagnostic_mutations()


def test_verdict_mutation_generator_invariant_never_stalls_and_always_unknown(
    tmp_path: Path,
) -> None:
    """Every single field mutation across all 4 verdict responses must return unknown/exit 2."""
    assert len(VERDICT_MUTATIONS) >= 120
    unknown_count = 0
    for idx, (case_id, route, mutated_payload) in enumerate(VERDICT_MUTATIONS):
        replies = _base_replies()
        replies[route] = mutated_payload
        state_dir = tmp_path / f"st-{idx}"
        report = evaluate_queue(
            repo=REPO,
            branch=BRANCH,
            client=StubGhClient(replies),
            now=NOW_DT,
            state_dir=state_dir,
        )
        assert report.verdict == "unknown", f"{case_id}: got {report.verdict} ({report.reason})"
        assert report.exit_code == 2, f"{case_id}: expected exit 2, got {report.exit_code}"
        assert report.alert_emitted is False, f"{case_id}: alert must not be emitted"
        assert not (state_dir / "alerts.jsonl").exists(), f"{case_id}: alerts.jsonl created"
        validate_report_dict(report.to_dict())
        unknown_count += 1
    assert unknown_count == len(VERDICT_MUTATIONS)


def test_timing_mutations_never_produce_healthy_on_recent_activity(tmp_path: Path) -> None:
    """Corrupted tip or activity data must never yield healthy or stall."""
    timing_mutations = [
        m for m in VERDICT_MUTATIONS if m[1] in (TIP_ROUTE, ACTIVITY_ROUTE)
    ]
    assert len(timing_mutations) >= 30
    for idx, (case_id, route, mutated_payload) in enumerate(timing_mutations):
        replies = _base_replies()
        replies[ACTIVITY_ROUTE] = _load("activity_recent.json")
        replies[route] = mutated_payload
        state_dir = tmp_path / f"tm-{idx}"
        report = evaluate_queue(
            repo=REPO,
            branch=BRANCH,
            client=StubGhClient(replies),
            now=NOW_DT,
            state_dir=state_dir,
        )
        assert report.verdict == "unknown", f"{case_id}: got {report.verdict} ({report.reason})"
        assert report.exit_code == 2
        assert report.alert_emitted is False
        assert not (state_dir / "alerts.jsonl").exists()
        validate_report_dict(report.to_dict())


def test_red_head_stays_blocked_red_under_all_timing_and_diagnostic_mutations(
    tmp_path: Path,
) -> None:
    """Exception to unknown: an independently red head stays blocked-red/exit 0."""
    non_head_mutations = [
        m
        for m in [*VERDICT_MUTATIONS, *DIAGNOSTIC_MUTATIONS]
        if m[1] in (TIP_ROUTE, ACTIVITY_ROUTE, RUNS_ROUTE, JOBS_ROUTE)
    ]
    assert len(non_head_mutations) >= 60
    for idx, (case_id, route, mutated_payload) in enumerate(non_head_mutations):
        replies = _base_replies()
        replies[CHECKS_ROUTE] = _load("checks_red.json")
        replies[route] = mutated_payload
        state_dir = tmp_path / f"red-{idx}"
        report = evaluate_queue(
            repo=REPO,
            branch=BRANCH,
            client=StubGhClient(replies),
            now=NOW_DT,
            state_dir=state_dir,
        )
        assert report.verdict == "blocked-red", f"{case_id}: got {report.verdict}"
        assert report.exit_code == 0
        assert report.alert_emitted is False
        assert not (state_dir / "alerts.jsonl").exists()
        validate_report_dict(report.to_dict())


def test_diagnostic_mutations_always_flag_truncated_and_never_fabricate_labels() -> None:
    """Malformed Actions runs/jobs always set active_jobs_truncated=True."""
    assert len(DIAGNOSTIC_MUTATIONS) >= 35
    for case_id, route, mutated_payload in DIAGNOSTIC_MUTATIONS:
        replies = _base_replies()
        replies[route] = mutated_payload
        report = evaluate_queue(
            repo=REPO,
            branch=BRANCH,
            client=StubGhClient(replies),
            now=NOW_DT,
            write_state=False,
        )
        assert report.active_jobs_truncated is True, f"{case_id}: truncation flag was False"
        if report.runner_pools is not None:
            for lbl in report.runner_pools:
                assert lbl not in ("{}", "None", "12345", ""), (
                    f"{case_id}: fabricated label {lbl!r}"
                )
        validate_report_dict(report.to_dict())


# ----------------------------------------------------------------------------
# Regression scenarios covering malformed and edge-case API payloads
# ----------------------------------------------------------------------------


def _review_regression_cases() -> list[tuple[str, dict[str, Any], str, int, bool | None]]:
    """Return (id, reply_overrides, expected_verdict, expected_rc, expected_truncated)."""
    green = {
        "name": "ci-pass",
        "status": "completed",
        "conclusion": "success",
        "started_at": "2026-01-14T14:00:00Z",
    }
    red_check = {
        "name": "ci-fail",
        "status": "completed",
        "conclusion": "failure",
        "started_at": "2026-01-14T14:00:00Z",
    }
    page_100_green = [dict(green, name=f"g-{i}") for i in range(100)]
    page_99_plus_null: list[Any] = [dict(green, name=f"g-{i}") for i in range(99)] + [None]
    page_100_jobs = [{"status": "in_progress", "labels": ["ubuntu-latest"]} for _ in range(100)]

    return [
        # 1-8: GraphQL top-level & errors
        ("01-gql-empty-object", {"graphql": {}}, "unknown", 2, None),
        ("02-gql-missing-data", {"graphql": {"errors": []}}, "unknown", 2, None),
        ("03-gql-null-data", {"graphql": {"data": None}}, "unknown", 2, None),
        ("04-gql-null-repository", {"graphql": {"data": {"repository": None}}}, "unknown", 2, None),
        (
            "05-gql-null-mergequeue",
            {"graphql": {"data": {"repository": {"mergeQueue": None}}}},
            "unknown",
            2,
            None,
        ),
        (
            "06-gql-errors-nonempty-list",
            {"graphql": _mq_payload(extra_top={"errors": [{"message": "forbidden"}]})},
            "unknown",
            2,
            None,
        ),
        (
            "07-gql-errors-dict-new-B2",
            {"graphql": _mq_payload(extra_top={"errors": {"message": "partial failure"}})},
            "unknown",
            2,
            None,
        ),
        (
            "08-gql-errors-str-new-B2",
            {"graphql": _mq_payload(extra_top={"errors": "partial failure"})},
            "unknown",
            2,
            None,
        ),
        # 9-10: configuration.maximumEntriesToBuild (New S2)
        (
            "09-gql-build-depth-str-new-S2",
            {"graphql": _mq_payload(build_depth="2")},
            "unknown",
            2,
            None,
        ),
        (
            "10-gql-build-depth-negative-new-S2",
            {"graphql": _mq_payload(build_depth=-2)},
            "unknown",
            2,
            None,
        ),
        # 11-20: entries.totalCount & nodes consistency (New S3)
        (
            "11-gql-totalcount-missing",
            {
                "graphql": {
                    "data": {
                        "repository": {
                            "mergeQueue": {
                                "configuration": {"maximumEntriesToBuild": 2},
                                "entries": {"nodes": [_node()]},
                            }
                        }
                    }
                }
            },
            "unknown",
            2,
            None,
        ),
        ("12-gql-totalcount-null", {"graphql": _mq_payload(total_count=None)}, "unknown", 2, None),
        ("13-gql-totalcount-bool", {"graphql": _mq_payload(total_count=True)}, "unknown", 2, None),
        ("14-gql-totalcount-str", {"graphql": _mq_payload(total_count="1")}, "unknown", 2, None),
        ("15-gql-totalcount-neg", {"graphql": _mq_payload(total_count=-1)}, "unknown", 2, None),
        (
            "16-gql-zero-count-with-node-new-S3",
            {"graphql": _mq_payload(total_count=0, nodes=[_node()])},
            "unknown",
            2,
            None,
        ),
        (
            "17-gql-zero-count-null-nodes-new-S3",
            {"graphql": _mq_payload(total_count=0, nodes=None)},
            "unknown",
            2,
            None,
        ),
        (
            "18-gql-count-1-with-2-nodes-new-S3",
            {
                "graphql": _mq_payload(
                    total_count=1,
                    nodes=[_node(position=1), _node(position=2, pr_number=18)],
                )
            },
            "unknown",
            2,
            None,
        ),
        (
            "19-gql-count-2-pos1-and-null-new-S3",
            {"graphql": _mq_payload(total_count=2, nodes=[_node(position=1), None])},
            "unknown",
            2,
            None,
        ),
        (
            "20-gql-count-1-empty-nodes",
            {"graphql": _mq_payload(total_count=1, nodes=[])},
            "unknown",
            2,
            None,
        ),
        # 21-28: head position, PR metadata, enqueuedAt, headCommit.oid
        (
            "21-gql-null-and-pos2-old-B1",
            {"graphql": _mq_payload(total_count=2, nodes=[None, _node(position=2, pr_number=18)])},
            "unknown",
            2,
            None,
        ),
        (
            "22-gql-head-position-missing",
            {
                "graphql": _mq_payload(
                    total_count=1,
                    nodes=[{k: v for k, v in _node().items() if k != "position"}],
                )
            },
            "unknown",
            2,
            None,
        ),
        (
            "23-gql-head-position-str",
            {"graphql": _mq_payload(total_count=1, nodes=[_node(position="1")])},
            "unknown",
            2,
            None,
        ),
        (
            "24-gql-duplicate-position-1",
            {
                "graphql": _mq_payload(
                    total_count=2,
                    nodes=[_node(position=1), _node(position=1, pr_number=18)],
                )
            },
            "unknown",
            2,
            None,
        ),
        (
            "25-gql-pr-number-str-new-S2",
            {"graphql": _mq_payload(total_count=1, nodes=[_node(pr_number="17")])},
            "unknown",
            2,
            None,
        ),
        (
            "26-gql-pr-title-null-new-S2",
            {"graphql": _mq_payload(total_count=1, nodes=[_node(pr_title=None)])},
            "unknown",
            2,
            None,
        ),
        (
            "27-gql-enqueued-dict-new-S2",
            {"graphql": _mq_payload(total_count=1, nodes=[_node(enqueued_at={})])},
            "unknown",
            2,
            None,
        ),
        (
            "28-gql-head-sha-invalid",
            {"graphql": _mq_payload(total_count=1, nodes=[_node(head_sha="../evil")])},
            "unknown",
            2,
            None,
        ),
        # 29-32: branch tip object, date, and sha (New S4)
        ("29-tip-non-dict", {TIP_ROUTE: ["not-a-dict"]}, "unknown", 2, None),
        (
            "30-tip-bad-date",
            {TIP_ROUTE: {"sha": TIP_SHA, "commit": {"committer": {"date": "bad"}}}},
            "unknown",
            2,
            None,
        ),
        (
            "31-tip-sha-missing-new-S4",
            {TIP_ROUTE: {"commit": {"committer": {"date": "2026-01-14T12:00:00Z"}}}},
            "unknown",
            2,
            None,
        ),
        (
            "32-tip-sha-numeric-new-S4",
            {TIP_ROUTE: {"sha": 12345, "commit": {"committer": {"date": "2026-01-14T12:00:00Z"}}}},
            "unknown",
            2,
            None,
        ),
        # 33-38: S1 red-head ordering & B3 activity feed corruption
        (
            "33-activity-403-recent-tip-red-head-new-S1A",
            {
                ACTIVITY_ROUTE: None,
                TIP_ROUTE: {
                    "sha": TIP_SHA,
                    "commit": {"committer": {"date": "2026-01-14T14:50:00Z"}},
                },
                CHECKS_ROUTE: {"total_count": 1, "check_runs": [red_check]},
            },
            "blocked-red",
            0,
            None,
        ),
        (
            "34-activity-403-bad-tip-date-red-head-new-S1B",
            {
                ACTIVITY_ROUTE: None,
                TIP_ROUTE: {"sha": TIP_SHA, "commit": {"committer": {"date": "bad"}}},
                CHECKS_ROUTE: {"total_count": 1, "check_runs": [red_check]},
            },
            "blocked-red",
            0,
            None,
        ),
        (
            "35-activity-403-old-tip-red-head-old-S1",
            {
                ACTIVITY_ROUTE: None,
                CHECKS_ROUTE: {"total_count": 1, "check_runs": [red_check]},
            },
            "blocked-red",
            0,
            None,
        ),
        (
            "36-activity-null-item-plus-old-push-new-B3",
            {
                ACTIVITY_ROUTE: [
                    None,
                    {"activity_type": "push", "timestamp": "2026-01-14T12:00:00Z"},
                ]
            },
            "unknown",
            2,
            None,
        ),
        (
            "37-activity-bad-ts-plus-old-push-new-B3",
            {
                ACTIVITY_ROUTE: [
                    {"activity_type": "push", "timestamp": "bad"},
                    {"activity_type": "push", "timestamp": "2026-01-14T12:00:00Z"},
                ]
            },
            "unknown",
            2,
            None,
        ),
        (
            "38-activity-future-timestamp-new-B3",
            {ACTIVITY_ROUTE: [{"activity_type": "merge_queue_merge", "timestamp": FUTURE_ISO}]},
            "unknown",
            2,
            None,
        ),
        # 39-43: check runs total_count & classification (New B1, Old B2)
        (
            "39-checks-missing-total-count-page1-new-B1",
            {CHECKS_ROUTE: {"check_runs": [green]}},
            "unknown",
            2,
            None,
        ),
        (
            "40-checks-missing-total-count-page2-new-B1",
            {
                CHECKS_ROUTE: {"total_count": 101, "check_runs": page_100_green},
                CHECKS_PAGE2_ROUTE: {"check_runs": [green]},
            },
            "unknown",
            2,
            None,
        ),
        (
            "41-checks-null-item-on-full-page-old-B2",
            {
                CHECKS_ROUTE: {"total_count": 101, "check_runs": page_99_plus_null},
                CHECKS_PAGE2_ROUTE: {"total_count": 101, "check_runs": [red_check]},
            },
            "unknown",
            2,
            None,
        ),
        (
            "42-checks-changing-total-count-across-pages",
            {
                CHECKS_ROUTE: {"total_count": 101, "check_runs": page_100_green},
                CHECKS_PAGE2_ROUTE: {"total_count": 102, "check_runs": [green]},
            },
            "unknown",
            2,
            None,
        ),
        (
            "43-checks-completed-null-conclusion",
            {
                CHECKS_ROUTE: {
                    "total_count": 1,
                    "check_runs": [{"name": "c", "status": "completed", "conclusion": None}],
                }
            },
            "unknown",
            2,
            None,
        ),
        # 44-46: Actions runs/jobs diagnostics (New S5)
        (
            "44-actions-runs-missing-total-count-new-S5",
            {RUNS_ROUTE: {"workflow_runs": []}},
            "stall",
            1,
            True,
        ),
        (
            "45-actions-jobs-dict-and-null-labels-new-S5",
            {
                JOBS_ROUTE: {
                    "total_count": 2,
                    "jobs": [
                        {"status": "in_progress", "labels": None},
                        {"status": "in_progress", "labels": [{}]},
                    ],
                }
            },
            "stall",
            1,
            True,
        ),
        (
            "46-actions-jobs-changing-total-count-new-S5",
            {
                JOBS_ROUTE: {"total_count": 101, "jobs": page_100_jobs},
                JOBS_PAGE2_ROUTE: {
                    "total_count": 102,
                    "jobs": [{"status": "queued", "labels": ["macos-latest"]}],
                },
            },
            "stall",
            1,
            True,
        ),
    ]


REVIEW_REGRESSION_CASES = _review_regression_cases()


@pytest.mark.parametrize(
    ("case_id", "overrides", "expected_verdict", "expected_rc", "expected_truncated"),
    REVIEW_REGRESSION_CASES,
    ids=[c[0] for c in REVIEW_REGRESSION_CASES],
)
def test_review_regression_scenarios(
    tmp_path: Path,
    case_id: str,
    overrides: dict[str, Any],
    expected_verdict: str,
    expected_rc: int,
    expected_truncated: bool | None,
) -> None:
    assert len(REVIEW_REGRESSION_CASES) == 46
    replies = _base_replies()
    replies.update(overrides)
    state_dir = tmp_path / "state"
    client = StubGhClient(replies)

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=client,
        now=NOW_DT,
        state_dir=state_dir,
    )
    assert report.verdict == expected_verdict, f"{case_id}: {report.verdict} ({report.reason})"
    assert report.exit_code == expected_rc, f"{case_id}: exit {report.exit_code}"
    if expected_verdict != "stall":
        assert report.alert_emitted is False
        assert not (state_dir / "alerts.jsonl").exists()
    if expected_truncated is not None:
        assert report.active_jobs_truncated is expected_truncated
        if case_id == "45-actions-jobs-dict-and-null-labels-new-S5":
            assert report.runner_pools == {}
    if case_id == "21-gql-null-and-pos2-old-B1":
        assert CHECKS_ROUTE not in client.called_routes
    validate_report_dict(report.to_dict())


def test_duplicate_run_ids_on_single_page_mark_truncated_while_cross_status_dedups(
    tmp_path: Path,
) -> None:
    """Same-page duplicate run IDs degrade diagnostics; cross-status dedup stays complete."""
    dup_page = {"total_count": 2, "workflow_runs": [{"id": 101}, {"id": 101}]}
    with pytest.raises(ResponseParseError, match="duplicate"):
        parse_workflow_runs_page(dup_page)

    # Same-page duplicates set active_jobs_truncated=True without altering the stall verdict.
    dup_replies = _base_replies()
    dup_replies[RUNS_ROUTE] = dup_page
    dup_report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=StubGhClient(dup_replies),
        now=NOW_DT,
        state_dir=tmp_path / "dup-state",
    )
    assert dup_report.verdict == "stall"
    assert dup_report.exit_code == 1
    assert dup_report.alert_emitted is True
    assert dup_report.active_jobs_truncated is True
    validate_report_dict(dup_report.to_dict())

    # Cross-status race (same run ID in both in_progress and queued pages) deduplicates cleanly.
    race_replies = _base_replies()
    race_replies[RUNS_ROUTE] = {"total_count": 1, "workflow_runs": [{"id": 101}]}
    race_replies[QUEUED_RUNS_ROUTE] = {"total_count": 1, "workflow_runs": [{"id": 101}]}
    race_report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=StubGhClient(race_replies),
        now=NOW_DT,
        state_dir=tmp_path / "race-state",
    )
    assert race_report.verdict == "stall"
    assert race_report.exit_code == 1
    assert race_report.active_jobs_truncated is False
    assert race_report.runner_pools == {"macos-latest": 5}
    validate_report_dict(race_report.to_dict())


def test_public_plugin_files_contain_no_internal_review_identifiers() -> None:
    """Public plugin files must not embed local review filenames or reviewer-named identifiers."""
    plugin_root = Path(__file__).resolve().parent.parent
    forbidden = re.compile(
        r"\b[a-z0-9]+-review-[a-z0-9-]+\.md\b"
        r"|\b_?[a-z]+_46_cases\b"
        r"|\btest_[a-z]+_46_scenarios\b",
        re.IGNORECASE,
    )
    scan_roots = (
        plugin_root / "src",
        plugin_root / "tests",
        plugin_root / "schema",
        plugin_root / "examples",
    )
    scan_files = [
        plugin_root / "README.md",
        plugin_root / "CHANGELOG.md",
        plugin_root / "pyproject.toml",
    ]
    for root in scan_roots:
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix in {".py", ".md", ".json", ".yml", ".yaml", ".toml"}:
                scan_files.append(path)

    violations: list[str] = []
    for path in scan_files:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            match = forbidden.search(line)
            if match:
                rel = path.relative_to(plugin_root)
                violations.append(f"{rel}:{lineno}: {match.group(0)}")
    assert violations == [], (
        "Internal review identifiers found in public plugin files:\n" + "\n".join(violations)
    )
