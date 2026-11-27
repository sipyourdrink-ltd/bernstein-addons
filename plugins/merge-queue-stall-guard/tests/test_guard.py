"""Offline pytest suite for bernstein-merge-queue-stall-guard."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bernstein_merge_queue_stall_guard.cli import main
from bernstein_merge_queue_stall_guard.gh import GhClient, GhQueryError
from bernstein_merge_queue_stall_guard.guard import evaluate_queue

REPO = "example-org/example-repo"
BRANCH = "main"
HEAD_SHA = "a" * 40
TIP_SHA = "b" * 40
LANDED_ISO = "2026-01-14T12:00:00Z"
NOW_ISO = "2026-01-14T15:00:00Z"
NOW_DT = datetime(2026, 1, 14, 15, 0, 0, tzinfo=UTC)

TIP_ROUTE = f"repos/{REPO}/commits/{BRANCH}"
ACTIVITY_ROUTE = f"repos/{REPO}/activity?ref=refs/heads/{BRANCH}&per_page=10"
CHECKS_ROUTE = f"repos/{REPO}/commits/{HEAD_SHA}/check-runs?per_page=100"
CHECKS_PAGE2_ROUTE = f"repos/{REPO}/commits/{HEAD_SHA}/check-runs?per_page=100&page=2"
RUNS_ROUTE = f"repos/{REPO}/actions/runs?status=in_progress&per_page=50"
QUEUED_RUNS_ROUTE = f"repos/{REPO}/actions/runs?status=queued&per_page=50"
JOBS_ROUTE = f"repos/{REPO}/actions/runs/101/jobs?per_page=100"
JOBS_PAGE2_ROUTE = f"repos/{REPO}/actions/runs/101/jobs?per_page=100&page=2"

PLUGIN_ROOT = Path(__file__).parents[1]
SCHEMA_PATH = PLUGIN_ROOT / "schema" / "report.schema.json"
EXAMPLE_WORKFLOW_PATH = PLUGIN_ROOT / "examples" / "stall-guard.yml"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

FAKE_GH_SCRIPT = """#!/usr/bin/env python3
import json
import re
import sys
from pathlib import Path

root = Path(__file__).parent
replies = json.loads((root / "replies.json").read_text(encoding="utf-8"))
args = sys.argv[1:]
route = args[1] if len(args) >= 2 else None
permitted = args[:1] == ["api"] and route in replies
if route == "graphql":
    permitted = (
        permitted
        and len(args) == 4
        and args[2] == "-f"
        and args[3].startswith("query=\\n{ repository(")
        and "mergeQueue(" in args[3]
        and re.search(r"\\bmutation\\b", args[3]) is None
    )
else:
    permitted = permitted and len(args) == 2

with (root / "requests.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({"route": route, "permitted": permitted}) + "\\n")

if not permitted:
    sys.exit(2)
reply = replies[route]
if reply is None:
    sys.exit(1)
print(json.dumps(reply))
"""


def _load_fixture(name: str) -> Any:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _base_replies(repo: str = REPO, branch: str = BRANCH) -> dict[str, Any]:
    return {
        "graphql": _load_fixture("queue_stalled.json"),
        f"repos/{repo}/commits/{branch}": _load_fixture("tip_old.json"),
        f"repos/{repo}/activity?ref=refs/heads/{branch}&per_page=10": _load_fixture(
            "activity_old.json"
        ),
        f"repos/{repo}/commits/{HEAD_SHA}/check-runs?per_page=100": _load_fixture(
            "checks_waiting.json"
        ),
        f"repos/{repo}/actions/runs?status=in_progress&per_page=50": _load_fixture(
            "runs_in_progress.json"
        ),
        f"repos/{repo}/actions/runs?status=queued&per_page=50": {
            "total_count": 0,
            "workflow_runs": [],
        },
        f"repos/{repo}/actions/runs/101/jobs?per_page=100": _load_fixture("jobs_101.json"),
    }


def _make_fake_gh(tmp_path: Path, replies: dict[str, Any]) -> Path:
    (tmp_path / "replies.json").write_text(json.dumps(replies), encoding="utf-8")
    gh = tmp_path / "fake-gh"
    gh.write_text(FAKE_GH_SCRIPT, encoding="utf-8")
    gh.chmod(0o700)
    return gh


def _validate_against_schema(instance: Any, schema: dict[str, Any], path: str = "$") -> None:
    """Validate a JSON object against our draft 2020-12 schema subset (no third-party deps)."""
    try:
        import jsonschema  # type: ignore[import-untyped]
    except ImportError:
        jsonschema = None

    if jsonschema is not None and path == "$":
        jsonschema.validate(instance=instance, schema=schema)
        return

    expected_type = schema.get("type")
    if expected_type is not None:
        types = [expected_type] if isinstance(expected_type, str) else list(expected_type)
        ok = False
        for t in types:
            if (
                t == "null"
                and instance is None
                or t == "boolean"
                and isinstance(instance, bool)
                or t == "integer"
                and isinstance(instance, int)
                and not isinstance(instance, bool)
                or (
                    t == "number"
                    and isinstance(instance, (int, float))
                    and not isinstance(instance, bool)
                )
                or t == "string"
                and isinstance(instance, str)
                or t == "object"
                and isinstance(instance, dict)
                or t == "array"
                and isinstance(instance, list)
            ):
                ok = True
        assert ok, f"{path}: expected type {types}, got {type(instance).__name__} ({instance!r})"

    if "const" in schema:
        assert instance == schema["const"], f"{path}: expected const {schema['const']!r}"
    if "enum" in schema:
        assert instance in schema["enum"], f"{path}: {instance!r} not in {schema['enum']!r}"
    if isinstance(instance, str):
        if "minLength" in schema:
            assert len(instance) >= schema["minLength"], f"{path}: shorter than minLength"
        if "pattern" in schema:
            assert re.search(schema["pattern"], instance), f"{path}: {instance!r} failed pattern"
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema:
            assert instance >= schema["minimum"], f"{path}: below minimum"
        if "exclusiveMinimum" in schema:
            assert instance > schema["exclusiveMinimum"], f"{path}: not > exclusiveMinimum"

    if isinstance(instance, dict):
        for req in schema.get("required", []):
            assert req in instance, f"{path}: missing required key {req!r}"
        props = schema.get("properties", {})
        addl = schema.get("additionalProperties", True)
        for key, val in instance.items():
            if key in props:
                _validate_against_schema(val, props[key], f"{path}.{key}")
            elif addl is False:
                raise AssertionError(f"{path}: unexpected property {key!r}")
            elif isinstance(addl, dict):
                _validate_against_schema(val, addl, f"{path}.{key}")

    if isinstance(instance, list) and "items" in schema:
        for idx, elem in enumerate(instance):
            _validate_against_schema(elem, schema["items"], f"{path}[{idx}]")


def _assert_all_requests_permitted(tmp_path: Path) -> list[str]:
    lines = (tmp_path / "requests.jsonl").read_text(encoding="utf-8").splitlines()
    reqs = [json.loads(line) for line in lines]
    assert reqs, "expected at least one gh api call"
    assert all(r["permitted"] for r in reqs), f"disallowed gh call: {reqs}"
    return [r["route"] for r in reqs]


def test_stall_emits_and_throttles_alert_and_matches_schema(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    replies = _base_replies()
    gh = _make_fake_gh(tmp_path, replies)
    state_dir = tmp_path / "state"

    report1 = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        state_dir=state_dir,
    )
    routes = _assert_all_requests_permitted(tmp_path)
    assert routes == [
        "graphql",
        TIP_ROUTE,
        ACTIVITY_ROUTE,
        CHECKS_ROUTE,
        RUNS_ROUTE,
        QUEUED_RUNS_ROUTE,
        JOBS_ROUTE,
    ]
    assert report1.verdict == "stall"
    assert report1.exit_code == 1
    assert report1.alert_emitted is True
    assert report1.last_merge_source == "activity"
    assert report1.queue_truncated is False
    assert report1.active_jobs_truncated is False
    assert report1.head is not None
    assert report1.head.pr_number == 17
    assert report1.head.pending_checks[0].wait_kind == "waiting_for_runner"
    assert report1.head.pending_checks[1].wait_kind == "executing"
    assert report1.head.pending_checks[1].running_minutes == 45
    assert report1.runner_pools == {"macos-latest": 5}
    _validate_against_schema(report1.to_dict(), schema)

    # Second run inside alert_repeat_hours must throttle alerts.jsonl
    report2 = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        state_dir=state_dir,
    )
    assert report2.verdict == "stall"
    assert report2.alert_emitted is False
    alerts = [
        json.loads(line)
        for line in (state_dir / "alerts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(alerts) == 1
    assert alerts[0]["repo"] == REPO
    assert (
        (state_dir / "MERGE-QUEUE-STALL")
        .read_text(encoding="utf-8")
        .startswith("MERGE-QUEUE-STALL")
    )
    assert (state_dir / "ATTENTION").exists()


def test_empty_queue_is_healthy_and_clears_marker(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    replies = _base_replies()
    replies["graphql"] = _load_fixture("queue_empty.json")
    gh = _make_fake_gh(tmp_path, replies)
    state_dir = tmp_path / "state"

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        state_dir=state_dir,
    )
    assert report.verdict == "healthy"
    assert report.exit_code == 0
    assert report.entries_count == 0
    assert report.reason == "queue empty"
    assert (state_dir / "MERGE-QUEUE-STALL").read_text(encoding="utf-8") == ""
    assert not (state_dir / "alerts.jsonl").exists()
    _validate_against_schema(report.to_dict(), schema)


def test_last_merge_uses_activity_feed_not_tip_commit_date(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    replies = _base_replies()
    # Tip commit date is 3h old, but activity feed shows a merge 10m ago.
    replies[ACTIVITY_ROUTE] = _load_fixture("activity_recent.json")
    gh = _make_fake_gh(tmp_path, replies)
    state_dir = tmp_path / "state"

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        state_dir=state_dir,
    )
    assert report.verdict == "healthy"
    assert report.exit_code == 0
    assert report.last_merge_source == "activity"
    assert report.minutes_since_merge == 10
    assert report.last_merge_at == "2026-01-14T14:50:00Z"
    assert not (state_dir / "alerts.jsonl").exists()
    _validate_against_schema(report.to_dict(), schema)


def test_head_red_returns_blocked_red_and_exits_zero(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    replies = _base_replies()
    replies[CHECKS_ROUTE] = _load_fixture("checks_red.json")
    gh = _make_fake_gh(tmp_path, replies)
    state_dir = tmp_path / "state"

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        state_dir=state_dir,
    )
    assert report.verdict == "blocked-red"
    assert report.exit_code == 0
    assert report.head is not None
    assert report.head.red_checks_count == 1
    assert not (state_dir / "alerts.jsonl").exists()
    _validate_against_schema(report.to_dict(), schema)


@pytest.mark.parametrize(
    "conclusion",
    ["failure", "timed_out", "cancelled", "startup_failure", "action_required", "stale"],
)
def test_all_red_conclusions_return_blocked_red(tmp_path: Path, conclusion: str) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    replies = _base_replies()
    replies[CHECKS_ROUTE] = {
        "total_count": 1,
        "check_runs": [
            {
                "name": f"check-{conclusion}",
                "status": "completed",
                "conclusion": conclusion,
                "started_at": "2026-01-14T14:00:00Z",
            }
        ],
    }
    gh = _make_fake_gh(tmp_path, replies)
    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        write_state=False,
    )
    assert report.verdict == "blocked-red"
    assert report.exit_code == 0
    assert report.head is not None
    assert report.head.red_checks_count == 1
    _validate_against_schema(report.to_dict(), schema)


def test_gh_absent_returns_unknown_exit_2(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    state_dir = tmp_path / "state"
    missing_gh = str(tmp_path / "no-such-gh-binary")

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=missing_gh),
        now=NOW_DT,
        state_dir=state_dir,
    )
    assert report.verdict == "unknown"
    assert report.exit_code == 2
    assert "gh CLI not found" in report.reason
    _validate_against_schema(report.to_dict(), schema)


def test_unavailable_queue_query_is_unknown(tmp_path: Path) -> None:
    replies = _base_replies()
    replies["graphql"] = None
    gh = _make_fake_gh(tmp_path, replies)
    state_dir = tmp_path / "state"

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        state_dir=state_dir,
    )
    assert _assert_all_requests_permitted(tmp_path) == ["graphql"]
    assert report.verdict == "unknown"
    assert report.exit_code == 2


def test_graphql_errors_list_with_partial_data_returns_unknown(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    replies = _base_replies()
    replies["graphql"] = {
        **_load_fixture("queue_stalled.json"),
        "errors": [{"message": "Resource not accessible by integration"}],
    }
    gh = _make_fake_gh(tmp_path, replies)

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        write_state=False,
    )
    assert report.verdict == "unknown"
    assert report.exit_code == 2
    _validate_against_schema(report.to_dict(), schema)


def test_null_repository_in_graphql_returns_unknown(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    replies = _base_replies()
    replies["graphql"] = {"data": {"repository": None}}
    gh = _make_fake_gh(tmp_path, replies)

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        write_state=False,
    )
    assert report.verdict == "unknown"
    assert report.exit_code == 2
    assert "mergeQueue" in report.reason
    _validate_against_schema(report.to_dict(), schema)


@pytest.mark.parametrize("bad_count", [None, -1, "2", True])
def test_invalid_entries_total_count_returns_unknown(tmp_path: Path, bad_count: Any) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    replies = _base_replies()
    replies["graphql"] = {
        "data": {
            "repository": {
                "mergeQueue": {
                    "configuration": {"maximumEntriesToBuild": 5},
                    "entries": {"totalCount": bad_count, "nodes": []},
                }
            }
        }
    }
    gh = _make_fake_gh(tmp_path, replies)

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        write_state=False,
    )
    assert report.verdict == "unknown"
    assert report.exit_code == 2
    _validate_against_schema(report.to_dict(), schema)


def test_non_list_entries_nodes_with_positive_count_returns_unknown(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    replies = _base_replies()
    replies["graphql"] = {
        "data": {
            "repository": {
                "mergeQueue": {
                    "configuration": {"maximumEntriesToBuild": 5},
                    "entries": {"totalCount": 1, "nodes": None},
                }
            }
        }
    }
    gh = _make_fake_gh(tmp_path, replies)

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        write_state=False,
    )
    assert report.verdict == "unknown"
    assert report.exit_code == 2
    _validate_against_schema(report.to_dict(), schema)


def test_positive_count_with_empty_nodes_returns_unknown_no_alert(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    replies = _base_replies()
    replies["graphql"] = {
        "data": {
            "repository": {
                "mergeQueue": {
                    "configuration": {"maximumEntriesToBuild": 5},
                    "entries": {"totalCount": 1, "nodes": []},
                }
            }
        }
    }
    gh = _make_fake_gh(tmp_path, replies)
    state_dir = tmp_path / "state"

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        state_dir=state_dir,
    )
    assert report.verdict == "unknown"
    assert report.exit_code == 2
    assert report.alert_emitted is False
    assert report.queue_truncated is True
    assert not (state_dir / "alerts.jsonl").exists()
    assert report.head is not None
    assert report.head.red_checks_count is None
    _validate_against_schema(report.to_dict(), schema)


def test_missing_position_1_does_not_fall_back_to_position_2(tmp_path: Path) -> None:
    """B1: [null, {position: 2, ...}] must return unknown/exit 2, never stall."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    replies = _base_replies()
    replies["graphql"] = {
        "data": {
            "repository": {
                "mergeQueue": {
                    "configuration": {"maximumEntriesToBuild": 5},
                    "entries": {
                        "totalCount": 2,
                        "nodes": [
                            None,
                            {
                                "position": 2,
                                "state": "AWAITING_CHECKS",
                                "enqueuedAt": "2026-01-14T13:00:00Z",
                                "headCommit": {"oid": HEAD_SHA},
                                "pullRequest": {"number": 18, "title": "PR 18"},
                            },
                        ],
                    },
                }
            }
        }
    }
    gh = _make_fake_gh(tmp_path, replies)
    state_dir = tmp_path / "state"

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        state_dir=state_dir,
    )
    routes = _assert_all_requests_permitted(tmp_path)
    assert CHECKS_ROUTE not in routes
    assert report.verdict == "unknown"
    assert report.exit_code == 2
    assert report.alert_emitted is False
    assert not (state_dir / "alerts.jsonl").exists()
    assert report.head is not None
    assert report.head.pr_number is None
    assert report.head.red_checks_count is None
    _validate_against_schema(report.to_dict(), schema)


def test_duplicate_position_1_nodes_returns_unknown(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    replies = _base_replies()
    node = {
        "position": 1,
        "state": "AWAITING_CHECKS",
        "enqueuedAt": "2026-01-14T13:00:00Z",
        "headCommit": {"oid": HEAD_SHA},
        "pullRequest": {"number": 17, "title": "PR 17"},
    }
    replies["graphql"] = {
        "data": {
            "repository": {
                "mergeQueue": {
                    "configuration": {"maximumEntriesToBuild": 5},
                    "entries": {
                        "totalCount": 2,
                        "nodes": [
                            node,
                            {**node, "pullRequest": {"number": 18, "title": "PR 18"}},
                        ],
                    },
                }
            }
        }
    }
    gh = _make_fake_gh(tmp_path, replies)

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        write_state=False,
    )
    assert report.verdict == "unknown"
    assert report.exit_code == 2
    _validate_against_schema(report.to_dict(), schema)


@pytest.mark.parametrize("bad_commit", [None, {}, {"oid": ""}, {"oid": "../evil"}])
def test_missing_or_invalid_head_sha_returns_unknown_no_alert(
    tmp_path: Path, bad_commit: Any
) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    replies = _base_replies()
    replies["graphql"] = {
        "data": {
            "repository": {
                "mergeQueue": {
                    "configuration": {"maximumEntriesToBuild": 5},
                    "entries": {
                        "totalCount": 1,
                        "nodes": [
                            {
                                "position": 1,
                                "state": "AWAITING_CHECKS",
                                "enqueuedAt": "2026-01-14T13:00:00Z",
                                "headCommit": bad_commit,
                                "pullRequest": {"number": 17, "title": "PR 17"},
                            }
                        ],
                    },
                }
            }
        }
    }
    gh = _make_fake_gh(tmp_path, replies)
    state_dir = tmp_path / "state"

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        state_dir=state_dir,
    )
    assert report.verdict == "unknown"
    assert report.exit_code == 2
    assert report.alert_emitted is False
    assert not (state_dir / "alerts.jsonl").exists()
    assert report.head is not None
    assert report.head.head_sha is None
    assert report.head.checks_error == "head entry missing or invalid headCommit.oid"
    _validate_against_schema(report.to_dict(), schema)


@pytest.mark.parametrize(
    "bad_tip",
    [
        ["not-a-dict"],
        {"sha": TIP_SHA, "commit": {"committer": {"date": "not-an-iso-date"}}},
    ],
)
def test_malformed_tip_payload_or_date_returns_unknown(tmp_path: Path, bad_tip: Any) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    replies = _base_replies()
    replies[TIP_ROUTE] = bad_tip
    replies[ACTIVITY_ROUTE] = None
    gh = _make_fake_gh(tmp_path, replies)

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        write_state=False,
    )
    assert report.verdict == "unknown"
    assert report.exit_code == 2
    _validate_against_schema(report.to_dict(), schema)


def test_unavailable_head_checks_return_unknown_no_alert(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    replies = _base_replies()
    replies[CHECKS_ROUTE] = None
    gh = _make_fake_gh(tmp_path, replies)
    state_dir = tmp_path / "state"

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        state_dir=state_dir,
    )
    assert report.verdict == "unknown"
    assert report.exit_code == 2
    assert report.alert_emitted is False
    assert not (state_dir / "alerts.jsonl").exists()
    assert report.head is not None
    assert report.head.red_checks_count is None
    assert report.head.checks_error is not None
    assert report.head.checks_error.startswith("check-runs query failed")
    assert "red=?" in report.summary
    _validate_against_schema(report.to_dict(), schema)


def test_unavailable_activity_does_not_assert_stall(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    replies = _base_replies()
    replies[ACTIVITY_ROUTE] = None
    gh = _make_fake_gh(tmp_path, replies)
    state_dir = tmp_path / "state"

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        state_dir=state_dir,
    )
    assert report.verdict == "unknown"
    assert report.exit_code == 2
    assert report.last_merge_source == "commit-date"
    assert report.alert_emitted is False
    assert not (state_dir / "alerts.jsonl").exists()
    assert "src=commit-date" in report.summary
    _validate_against_schema(report.to_dict(), schema)


def test_unavailable_activity_with_red_head_returns_blocked_red(tmp_path: Path) -> None:
    """S1: unavailable /activity must not hide a definitively red head."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    replies = _base_replies()
    replies[ACTIVITY_ROUTE] = None
    replies[CHECKS_ROUTE] = _load_fixture("checks_red.json")
    gh = _make_fake_gh(tmp_path, replies)
    state_dir = tmp_path / "state"

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        state_dir=state_dir,
    )
    assert report.verdict == "blocked-red"
    assert report.exit_code == 0
    assert report.last_merge_source == "commit-date"
    assert report.head is not None
    assert report.head.red_checks_count == 1
    assert not (state_dir / "alerts.jsonl").exists()
    _validate_against_schema(report.to_dict(), schema)


@pytest.mark.parametrize(
    "bad_activity",
    [
        {"message": "Not Found"},
        [None, {"activity_type": "push", "timestamp": "bad-ts"}],
    ],
)
def test_malformed_activity_payload_does_not_assert_stall(
    tmp_path: Path, bad_activity: Any
) -> None:
    replies = _base_replies()
    replies[ACTIVITY_ROUTE] = bad_activity
    gh = _make_fake_gh(tmp_path, replies)

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        write_state=False,
    )
    assert report.verdict == "unknown"
    assert report.exit_code == 2


def test_unavailable_activity_with_recent_commit_date_is_healthy(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    replies = _base_replies()
    replies[ACTIVITY_ROUTE] = None
    replies[TIP_ROUTE] = {
        "sha": TIP_SHA,
        "commit": {"committer": {"date": "2026-01-14T14:50:00Z"}},
    }
    gh = _make_fake_gh(tmp_path, replies)

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        write_state=False,
    )
    assert report.verdict == "healthy"
    assert report.exit_code == 0
    assert report.last_merge_source == "commit-date"
    _validate_against_schema(report.to_dict(), schema)


def test_paginated_check_runs_finds_red_on_page_2(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    replies = _base_replies()
    page1_runs = [
        {
            "name": f"pass-{i}",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-01-14T14:00:00Z",
        }
        for i in range(100)
    ]
    replies[CHECKS_ROUTE] = {"total_count": 101, "check_runs": page1_runs}
    replies[CHECKS_PAGE2_ROUTE] = {
        "total_count": 101,
        "check_runs": [
            {
                "name": "late-red-check",
                "status": "completed",
                "conclusion": "failure",
                "started_at": "2026-01-14T14:10:00Z",
            }
        ],
    }
    gh = _make_fake_gh(tmp_path, replies)

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        write_state=False,
    )
    routes = _assert_all_requests_permitted(tmp_path)
    assert CHECKS_PAGE2_ROUTE in routes
    assert report.verdict == "blocked-red"
    assert report.exit_code == 0
    assert report.head is not None
    assert report.head.red_checks_count == 1
    _validate_against_schema(report.to_dict(), schema)


def test_malformed_entry_on_full_check_runs_page_returns_unknown(tmp_path: Path) -> None:
    """B2: 99 valid dicts + null on page 1 with total_count=101 must fail closed."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    replies = _base_replies()
    page1_runs: list[Any] = [
        {
            "name": f"pass-{i}",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-01-14T14:00:00Z",
        }
        for i in range(99)
    ]
    page1_runs.append(None)
    replies[CHECKS_ROUTE] = {"total_count": 101, "check_runs": page1_runs}
    replies[CHECKS_PAGE2_ROUTE] = {
        "total_count": 101,
        "check_runs": [{"name": "late-red", "status": "completed", "conclusion": "failure"}],
    }
    gh = _make_fake_gh(tmp_path, replies)
    state_dir = tmp_path / "state"

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        state_dir=state_dir,
    )
    assert report.verdict == "unknown"
    assert report.exit_code == 2
    assert report.alert_emitted is False
    assert not (state_dir / "alerts.jsonl").exists()
    assert report.head is not None
    assert report.head.red_checks_count is None
    _validate_against_schema(report.to_dict(), schema)


@pytest.mark.parametrize(
    ("page1", "page2"),
    [
        ({"total_count": -1, "check_runs": []}, None),
        ({"total_count": "1", "check_runs": []}, None),
        (
            {
                "total_count": 5,
                "check_runs": [
                    {"name": "ok", "status": "completed", "conclusion": "success"},
                ],
            },
            None,
        ),
        (
            {
                "total_count": 1,
                "check_runs": [
                    {"name": "ok1", "status": "completed", "conclusion": "success"},
                    {"name": "ok2", "status": "completed", "conclusion": "success"},
                ],
            },
            None,
        ),
        (
            {
                "total_count": 101,
                "check_runs": [
                    {"name": f"ok-{i}", "status": "completed", "conclusion": "success"}
                    for i in range(100)
                ],
            },
            {
                "total_count": 102,
                "check_runs": [
                    {"name": "ok-100", "status": "completed", "conclusion": "success"},
                ],
            },
        ),
    ],
)
def test_inconsistent_check_runs_total_count_returns_unknown(
    tmp_path: Path, page1: dict[str, Any], page2: dict[str, Any] | None
) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    replies = _base_replies()
    replies[CHECKS_ROUTE] = page1
    if page2 is not None:
        replies[CHECKS_PAGE2_ROUTE] = page2
    gh = _make_fake_gh(tmp_path, replies)

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        write_state=False,
    )
    assert report.verdict == "unknown"
    assert report.exit_code == 2
    assert report.head is not None
    assert report.head.red_checks_count is None
    _validate_against_schema(report.to_dict(), schema)


@pytest.mark.parametrize(
    "bad_check",
    [
        {"name": "mystery", "status": "completed", "conclusion": None},
        {"name": "mystery", "status": "completed", "conclusion": "unexpected_state"},
        {"name": "mystery", "status": None, "conclusion": None},
        {"name": "mystery", "status": "weird_status", "conclusion": None},
    ],
)
def test_unrecognized_check_status_or_conclusion_returns_unknown(
    tmp_path: Path, bad_check: dict[str, Any]
) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    replies = _base_replies()
    replies[CHECKS_ROUTE] = {"total_count": 1, "check_runs": [bad_check]}
    gh = _make_fake_gh(tmp_path, replies)

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        write_state=False,
    )
    assert report.verdict == "unknown"
    assert report.exit_code == 2
    assert report.head is not None
    assert report.head.red_checks_count is None
    _validate_against_schema(report.to_dict(), schema)


def test_queued_workflow_runs_are_counted_in_active_jobs(tmp_path: Path) -> None:
    """N2: queued workflow runs are inspected alongside in-progress runs."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    replies = _base_replies()
    replies[RUNS_ROUTE] = {"total_count": 0, "workflow_runs": []}
    replies[QUEUED_RUNS_ROUTE] = {"total_count": 1, "workflow_runs": [{"id": 202}]}
    replies[f"repos/{REPO}/actions/runs/202/jobs?per_page=100"] = {
        "total_count": 1,
        "jobs": [{"status": "queued", "labels": ["ubuntu-latest"]}],
    }
    gh = _make_fake_gh(tmp_path, replies)

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        write_state=False,
    )
    assert report.verdict == "stall"
    assert report.runner_pools == {"ubuntu-latest": 1}
    assert report.active_jobs_truncated is False
    _validate_against_schema(report.to_dict(), schema)


def test_active_jobs_pagination_and_truncation_flag(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    replies = _base_replies()
    replies[RUNS_ROUTE] = {"total_count": 40, "workflow_runs": [{"id": 101}]}
    page1_jobs = [{"status": "in_progress", "labels": ["ubuntu-latest"]} for _ in range(100)]
    replies[JOBS_ROUTE] = {"total_count": 102, "jobs": page1_jobs}
    replies[JOBS_PAGE2_ROUTE] = {
        "total_count": 102,
        "jobs": [
            {"status": "queued", "labels": ["macos-latest"]},
            {"status": "queued", "labels": ["macos-latest"]},
        ],
    }
    gh = _make_fake_gh(tmp_path, replies)

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        write_state=False,
    )
    assert report.verdict == "stall"
    assert report.active_jobs_truncated is True
    assert report.runner_pools == {"ubuntu-latest": 100, "macos-latest": 2}
    _validate_against_schema(report.to_dict(), schema)


def test_malformed_jobs_payload_sets_active_jobs_truncated(tmp_path: Path) -> None:
    replies = _base_replies()
    replies[JOBS_ROUTE] = {
        "total_count": 2,
        "jobs": [None, {"status": "in_progress", "labels": ["macos-latest"]}],
    }
    gh = _make_fake_gh(tmp_path, replies)

    report = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        write_state=False,
    )
    assert report.verdict == "stall"
    assert report.active_jobs_truncated is True
    assert report.runner_pools == {"macos-latest": 1}


def test_default_state_dir_is_isolated_per_repo_and_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    repo_a = "example-org/repo-a"
    repo_b = "example-org/repo-b"

    dir_a = tmp_path / "gh-a"
    dir_a.mkdir()
    gh_a = _make_fake_gh(dir_a, _base_replies(repo=repo_a, branch="main"))

    dir_b = tmp_path / "gh-b"
    dir_b.mkdir()
    gh_b = _make_fake_gh(dir_b, _base_replies(repo=repo_b, branch="main"))

    rep_a = evaluate_queue(
        repo=repo_a, branch="main", client=GhClient(gh_bin=str(gh_a)), now=NOW_DT
    )
    rep_b = evaluate_queue(
        repo=repo_b, branch="main", client=GhClient(gh_bin=str(gh_b)), now=NOW_DT
    )
    assert rep_a.verdict == "stall" and rep_a.alert_emitted is True
    assert rep_b.verdict == "stall" and rep_b.alert_emitted is True

    # A healthy run for repo_b must not clear repo_a's marker/episode.
    replies_b_empty = _base_replies(repo=repo_b, branch="main")
    replies_b_empty["graphql"] = _load_fixture("queue_empty.json")
    gh_b_empty = _make_fake_gh(dir_b, replies_b_empty)
    rep_b2 = evaluate_queue(
        repo=repo_b, branch="main", client=GhClient(gh_bin=str(gh_b_empty)), now=NOW_DT
    )
    assert rep_b2.verdict == "healthy"

    # Second stall check for repo_a still throttles (its own episode survived).
    rep_a2 = evaluate_queue(
        repo=repo_a, branch="main", client=GhClient(gh_bin=str(gh_a)), now=NOW_DT
    )
    assert rep_a2.verdict == "stall" and rep_a2.alert_emitted is False


def test_sub_minute_stall_hours_uses_timedelta_and_rejects_out_of_range(tmp_path: Path) -> None:
    replies = _base_replies()
    # Last merge was 10 seconds ago; stall_hours=0.01 is 36 seconds -> must be healthy.
    replies[ACTIVITY_ROUTE] = [
        {"activity_type": "merge_queue_merge", "timestamp": "2026-01-14T14:59:50Z"}
    ]
    gh = _make_fake_gh(tmp_path, replies)
    report_recent = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        stall_hours=0.01,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        write_state=False,
    )
    assert report_recent.verdict == "healthy"
    assert report_recent.exit_code == 0

    # Last merge was 40 seconds ago; stall_hours=0.01 is 36 seconds -> must be stall.
    replies[ACTIVITY_ROUTE] = [
        {"activity_type": "merge_queue_merge", "timestamp": "2026-01-14T14:59:20Z"}
    ]
    gh2 = _make_fake_gh(tmp_path, replies)
    report_stalled = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        stall_hours=0.01,
        client=GhClient(gh_bin=str(gh2)),
        now=NOW_DT,
        write_state=False,
    )
    assert report_stalled.verdict == "stall"
    assert report_stalled.exit_code == 1

    for bad in (math.inf, -math.inf, math.nan, 0.0, -1.0, 8761.0, 1e20, 1e308):
        with pytest.raises(ValueError):
            evaluate_queue(
                repo=REPO,
                branch=BRANCH,
                stall_hours=bad,
                client=GhClient(gh_bin=str(gh)),
                now=NOW_DT,
                write_state=False,
            )


def test_cli_huge_stall_hours_exits_2_without_overflow_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """N3: huge finite --stall-hours must produce structured exit 2, never OverflowError."""
    gh = _make_fake_gh(tmp_path, _base_replies())
    for raw_val in ("1e20", "1e308"):
        rc = main(
            [
                "--repo",
                REPO,
                "--stall-hours",
                raw_val,
                "--gh-bin",
                str(gh),
                "--no-state",
            ]
        )
        assert rc == 2
        err = capsys.readouterr().err
        assert "stall_hours" in err


def test_gh_client_bounds_stdout_and_stderr(tmp_path: Path) -> None:
    big_out = tmp_path / "big-out-gh"
    big_out.write_text(
        "#!/usr/bin/env python3\nimport sys\nsys.stdout.write('x' * 50000)\n",
        encoding="utf-8",
    )
    big_out.chmod(0o700)
    client_out = GhClient(gh_bin=str(big_out), max_stdout_bytes=1024)
    with pytest.raises(GhQueryError, match="exceeded 1024 byte limit"):
        client_out.get("repos/a/b")

    big_err = tmp_path / "big-err-gh"
    big_err.write_text(
        "#!/usr/bin/env python3\nimport sys\nsys.stderr.write('E' * 5000)\nsys.exit(1)\n",
        encoding="utf-8",
    )
    big_err.chmod(0o700)
    client_err = GhClient(gh_bin=str(big_err), max_stderr_bytes=1024)
    with pytest.raises(GhQueryError) as exc_info:
        client_err.get("repos/a/b")
    assert "[truncated]" in str(exc_info.value)
    assert len(str(exc_info.value)) < 700


def test_example_workflow_grants_required_read_only_permissions() -> None:
    text = EXAMPLE_WORKFLOW_PATH.read_text(encoding="utf-8")
    for perm in ("contents: read", "pull-requests: read", "checks: read", "actions: read"):
        assert perm in text, f"missing permission {perm!r} in {EXAMPLE_WORKFLOW_PATH}"
    assert ": write" not in text


def test_cli_json_format_and_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    gh = _make_fake_gh(tmp_path, _base_replies())
    rc = main(
        [
            "--repo",
            REPO,
            "--branch",
            BRANCH,
            "--gh-bin",
            str(gh),
            "--now",
            NOW_ISO,
            "--no-state",
            "--format",
            "json",
        ]
    )
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    _validate_against_schema(payload, schema)


@pytest.mark.parametrize(
    ("scenario", "stderr_msg"),
    [
        ("unauthenticated", "gh: To get started with GitHub CLI, please run: gh auth login"),
        ("rate_limited", "gh: API rate limit exceeded (HTTP 403)"),
    ],
)
def test_cli_subprocess_exits_2_on_gh_auth_or_rate_limit(
    tmp_path: Path, scenario: str, stderr_msg: str
) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    bad_gh = tmp_path / f"gh-{scenario}"
    bad_gh.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.stderr.write({stderr_msg!r} + '\\n')\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    bad_gh.chmod(0o700)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "bernstein_merge_queue_stall_guard.cli",
            "--repo",
            REPO,
            "--gh-bin",
            str(bad_gh),
            "--no-state",
            "--format",
            "json",
        ],
        env={**os.environ, "PYTHONPATH": str(PLUGIN_ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert payload["verdict"] == "unknown"
    _validate_against_schema(payload, schema)


def test_cli_subprocess_exits_2_on_missing_gh(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "bernstein_merge_queue_stall_guard.cli",
            "--repo",
            REPO,
            "--gh-bin",
            str(tmp_path / "does-not-exist"),
            "--no-state",
            "--format",
            "json",
        ],
        env={**os.environ, "PYTHONPATH": str(PLUGIN_ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert payload["verdict"] == "unknown"
    _validate_against_schema(payload, schema)


def test_cli_help_runs_cleanly() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "bernstein_merge_queue_stall_guard.cli", "--help"],
        env={
            **os.environ,
            "PYTHONPATH": str(PLUGIN_ROOT / "src"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "--stall-hours" in proc.stdout
    assert "--state-dir" in proc.stdout
