"""Core behavioral, CLI, schema, and transport tests for merge-queue-stall-guard."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bernstein_merge_queue_stall_guard.cli import main
from bernstein_merge_queue_stall_guard.gh import GhClient, GhQueryError
from bernstein_merge_queue_stall_guard.guard import evaluate_queue
from bernstein_merge_queue_stall_guard.schema_check import (
    ReportSchemaError,
    validate_report_dict,
)

REPO = "example-org/example-repo"
BRANCH = "main"
HEAD_SHA = "a" * 40
TIP_SHA = "b" * 40
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
BUNDLED_SCHEMA_PATH = (
    PLUGIN_ROOT / "src" / "bernstein_merge_queue_stall_guard" / "report.schema.json"
)
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


def _assert_all_requests_permitted(tmp_path: Path) -> list[str]:
    lines = (tmp_path / "requests.jsonl").read_text(encoding="utf-8").splitlines()
    reqs = [json.loads(line) for line in lines]
    assert reqs, "expected at least one gh api call"
    assert all(r["permitted"] for r in reqs), f"disallowed gh call: {reqs}"
    return [r["route"] for r in reqs]


def test_bundled_schema_matches_published_schema_file() -> None:
    assert BUNDLED_SCHEMA_PATH.read_bytes() == SCHEMA_PATH.read_bytes()


def test_schema_check_rejects_negative_build_depth_and_cli_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    gh = _make_fake_gh(tmp_path, _base_replies())
    valid = evaluate_queue(
        repo=REPO,
        branch=BRANCH,
        client=GhClient(gh_bin=str(gh)),
        now=NOW_DT,
        write_state=False,
    ).to_dict()
    validate_report_dict(valid)

    bad = dict(valid)
    bad["build_depth"] = -2
    with pytest.raises(ReportSchemaError):
        validate_report_dict(bad)

    # Verify CLI turns ReportSchemaError into exit 2 with no JSON output on stdout.
    def _broken_validate(_payload: dict[str, Any]) -> None:
        raise ReportSchemaError("synthetic schema violation")

    monkeypatch.setattr(
        "bernstein_merge_queue_stall_guard.cli.validate_report_dict",
        _broken_validate,
    )
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
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out == ""
    assert "schema validation" in captured.err


def test_stall_emits_and_throttles_alert_and_matches_schema(tmp_path: Path) -> None:
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
        CHECKS_ROUTE,
        TIP_ROUTE,
        ACTIVITY_ROUTE,
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
    validate_report_dict(report1.to_dict())

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
    validate_report_dict(report.to_dict())


def test_last_merge_uses_activity_feed_not_tip_commit_date(tmp_path: Path) -> None:
    replies = _base_replies()
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
    validate_report_dict(report.to_dict())


def test_head_red_returns_blocked_red_and_exits_zero(tmp_path: Path) -> None:
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
    validate_report_dict(report.to_dict())


@pytest.mark.parametrize(
    "conclusion",
    ["failure", "timed_out", "cancelled", "startup_failure", "action_required", "stale"],
)
def test_all_red_conclusions_return_blocked_red(tmp_path: Path, conclusion: str) -> None:
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
    validate_report_dict(report.to_dict())


def test_gh_absent_returns_unknown_exit_2(tmp_path: Path) -> None:
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
    validate_report_dict(report.to_dict())


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


def test_unavailable_head_checks_return_unknown_no_alert(tmp_path: Path) -> None:
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
    validate_report_dict(report.to_dict())


def test_unavailable_activity_does_not_assert_stall(tmp_path: Path) -> None:
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
    validate_report_dict(report.to_dict())


def test_unavailable_activity_with_recent_commit_date_and_non_red_head_is_healthy(
    tmp_path: Path,
) -> None:
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
    validate_report_dict(report.to_dict())


def test_paginated_check_runs_finds_red_on_page_2(tmp_path: Path) -> None:
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
    validate_report_dict(report.to_dict())


def test_queued_workflow_runs_are_counted_in_active_jobs(tmp_path: Path) -> None:
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
    validate_report_dict(report.to_dict())


def test_active_jobs_pagination_across_two_job_pages(tmp_path: Path) -> None:
    replies = _base_replies()
    replies[RUNS_ROUTE] = {"total_count": 1, "workflow_runs": [{"id": 101}]}
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
    assert report.active_jobs_truncated is False
    assert report.runner_pools == {"ubuntu-latest": 100, "macos-latest": 2}
    validate_report_dict(report.to_dict())


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

    replies_b_empty = _base_replies(repo=repo_b, branch="main")
    replies_b_empty["graphql"] = _load_fixture("queue_empty.json")
    gh_b_empty = _make_fake_gh(dir_b, replies_b_empty)
    rep_b2 = evaluate_queue(
        repo=repo_b, branch="main", client=GhClient(gh_bin=str(gh_b_empty)), now=NOW_DT
    )
    assert rep_b2.verdict == "healthy"

    rep_a2 = evaluate_queue(
        repo=repo_a, branch="main", client=GhClient(gh_bin=str(gh_a)), now=NOW_DT
    )
    assert rep_a2.verdict == "stall" and rep_a2.alert_emitted is False


def test_sub_minute_stall_hours_uses_timedelta_and_rejects_out_of_range(tmp_path: Path) -> None:
    replies = _base_replies()
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
        assert "stall_hours" in capsys.readouterr().err


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
    validate_report_dict(payload)


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
    validate_report_dict(payload)


def test_cli_subprocess_exits_2_on_missing_gh(tmp_path: Path) -> None:
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
    validate_report_dict(payload)


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
