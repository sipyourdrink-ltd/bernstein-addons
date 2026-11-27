"""Command-line entrypoint for merge-queue-stall-guard."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from bernstein_merge_queue_stall_guard.gh import GhClient
from bernstein_merge_queue_stall_guard.guard import (
    EXIT_UNREADABLE,
    evaluate_queue,
    parse_iso_utc,
)
from bernstein_merge_queue_stall_guard.models import StallReport


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="merge-queue-stall-guard",
        description=(
            "Read-only watcher for GitHub's native merge queue. "
            "Exit codes: 0 healthy (or blocked-red), 1 stalled, 2 cannot read."
        ),
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("MQSG_REPO", ""),
        help="Target GitHub repository in owner/name form (or env MQSG_REPO).",
    )
    parser.add_argument(
        "--branch",
        default=os.environ.get("MQSG_BRANCH", "main"),
        help="Protected branch name (default: main).",
    )
    parser.add_argument(
        "--stall-hours",
        type=float,
        default=float(os.environ.get("MQSG_STALL_HOURS", "2")),
        help="Hours without a merge before reporting a stall (default: 2).",
    )
    parser.add_argument(
        "--alert-repeat-hours",
        type=float,
        default=float(os.environ.get("MQSG_ALERT_REPEAT_HOURS", "4")),
        help="Minimum hours between repeated alert rows in alerts.jsonl (default: 4).",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="State directory (default: $XDG_STATE_HOME/merge-queue-stall-guard).",
    )
    parser.add_argument(
        "--no-state",
        action="store_true",
        help="Do not write local state files (useful in CI workflows).",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        dest="output_format",
        help="Output format (default: text).",
    )
    parser.add_argument(
        "--gh-bin",
        default=os.environ.get("GH_BIN", "gh"),
        help="Path to the gh CLI binary (default: gh).",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Override current UTC time (ISO-8601, e.g. 2026-01-14T15:00:00Z).",
    )
    return parser


def format_report_text(report: StallReport) -> str:
    return report.summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.repo:
        parser.error("--repo is required (or set MQSG_REPO)")

    now_dt = None
    if args.now is not None:
        now_dt = parse_iso_utc(args.now)
        if now_dt is None:
            print(f"error: invalid --now timestamp: {args.now}", file=sys.stderr)
            return EXIT_UNREADABLE

    try:
        report = evaluate_queue(
            repo=args.repo,
            branch=args.branch,
            stall_hours=args.stall_hours,
            alert_repeat_hours=args.alert_repeat_hours,
            client=GhClient(gh_bin=args.gh_bin),
            now=now_dt,
            state_dir=args.state_dir,
            write_state=not args.no_state,
        )
    except (ValueError, OverflowError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE

    if args.output_format == "json":
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(format_report_text(report))
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
