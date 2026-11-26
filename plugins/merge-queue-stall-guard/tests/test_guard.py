"""Offline pytest suite for bernstein-merge-queue-stall-guard."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bernstein_merge_queue_stall_guard.cli import main
from bernstein_merge_queue_stall_guard.gh import GhClient
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
RUNS_ROUTE = f"repos/{REPO}/actions/runs?status=in_progress&per_page=50"
JOBS_ROUTE = f"repos/{REPO}/actions/runs/101/jobs?per_page=100"

SCHEMA_PATH = Path(__file__).parents[1] / "schema" / "report.schema.json"
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
    prefix = 'query=\\n{ repository(owner:"example-org", name:"example-repo")'
    permitted = (
        permitted
        and len(args) == 4
        and args[2] == "-f"
        and args[3].startswith(prefix)
        and 'mergeQueue(branch:"main")' in args[3]
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


def _base_replies() -> dict[str, Any]:
    return {
        "graphql": _load_fixture("queue_stalled.json"),
        TIP_ROUTE: _load_fixture("tip_old.json"),
        ACTIVITY_ROUTE: _load_fixture("activity_old.json"),
        CHECKS_ROUTE: _load_fixture("checks_waiting.json"),
        RUNS_ROUTE: _load_fixture("runs_in_progress.json"),
        JOBS_ROUTE: _load_fixture("jobs_101.json"),
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
            if t == "null" and instance is None or t == "boolean" and isinstance(instance, bool) or t == "integer" and isinstance(instance, int) and not isinstance(instance, bool) or (
                t == "number"
                and isinstance(instance, (int, float))
                and not isinstance(instance, bool)
            ) or t == "string" and isinstance(instance, str) or t == "object" and isinstance(instance, dict) or t == "array" and isinstance(instance, list):
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
    assert routes == ["graphql", TIP_ROUTE, ACTIVITY_ROUTE, CHECKS_ROUTE, RUNS_ROUTE, JOBS_ROUTE]
    assert report1.verdict == "stall"
    assert report1.exit_code == 1
    assert report1.alert_emitted is True
    assert report1.last_merge_source == "activity"
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
    assert (state_dir / "MERGE-QUEUE-STALL").read_text(encoding="utf-8").startswith(
        "MERGE-QUEUE-STALL"
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


def test_unavailable_head_checks_remain_unknown_in_stall(tmp_path: Path) -> None:
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
    assert report.verdict == "stall"
    assert report.exit_code == 1
    assert report.head is not None
    assert report.head.red_checks_count is None
    assert report.head.checks_error == "check-runs query failed"
    assert "red=?" in report.summary
    _validate_against_schema(report.to_dict(), schema)


def test_unavailable_activity_falls_back_to_tip_date(tmp_path: Path) -> None:
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
    assert report.verdict == "stall"
    assert report.last_merge_source == "commit-date"
    assert "src=commit-date" in report.summary


def test_cli_json_format_and_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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


def test_cli_help_runs_cleanly() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "bernstein_merge_queue_stall_guard.cli", "--help"],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "--stall-hours" in proc.stdout
    assert "--state-dir" in proc.stdout
