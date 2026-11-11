"""Command line: bernstein-role-pack."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bernstein_role_pack.clock import deadline_state, parse_deadline
from bernstein_role_pack.config import ConfigError, RolePackError, home_for, load_config
from bernstein_role_pack.ledger import Ledger, TaskState, acceptance_problem, file_sha256


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bernstein-role-pack", description="Role registry and receipt ledger.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="validate roles.toml")
    s = sub.add_parser("status", help="show tracked tasks")
    s.add_argument("task", nargs="?")
    s = sub.add_parser("ack", help="acknowledge a delivered task")
    s.add_argument("task")
    s.add_argument("--as", dest="by", required=True)
    s = sub.add_parser("report", help="report result files")
    s.add_argument("task")
    s.add_argument("--as", dest="by", required=True)
    s.add_argument("paths", nargs="+")
    s = sub.add_parser("accept", help="accept one reported file")
    s.add_argument("task")
    s.add_argument("--as", dest="by", required=True)
    s.add_argument("--path", required=True)
    s.add_argument("--sha256")
    s = sub.add_parser("unknown", help="mark a task's result as unknown")
    s.add_argument("task")
    s.add_argument("--as", dest="by", required=True)
    s.add_argument("--reason", required=True)
    s = sub.add_parser("reconcile", help="settle an unknown task under its own id")
    s.add_argument("task")
    s.add_argument("--as", dest="by", required=True)
    s.add_argument("--outcome", required=True, choices=["completed", "not_completed"])
    s.add_argument("--evidence", required=True)
    s.add_argument("paths", nargs="*")
    sub.add_parser("demo", help="run the in-process demo")
    return p


def _summary(ledger: Ledger, ts: TaskState) -> str:
    valid = {a.path for a in ts.acceptances if acceptance_problem(ledger.config, ts, a) is None}
    parts = [
        ts.task,
        f"state={ts.state}",
        f"role={ts.role}",
        f"holder={ts.holder or '-'}",
        f"files={len(ts.reported)}",
        f"accepted={len(valid)}",
        f"bernstein_completed={'yes' if ts.bernstein_completed else 'no'}",
    ]
    if ts.deadline:
        reading = ledger.clock.read()
        state = deadline_state(parse_deadline(ts.deadline), reading, ledger.config.due_window_s)
        parts.append(f"deadline={ts.deadline}({state})")
    return " ".join(parts)


def _status(ledger: Ledger, task: str | None) -> None:
    if task is None:
        for ts in ledger.tasks():
            print(_summary(ledger, ts))
        return
    ts = ledger.state(task)
    print(_summary(ledger, ts))
    print(f"title: {ts.title}")
    if ts.unknown:
        print(f"unknown: {ts.unknown_reason}")
    for path, digest in sorted(ts.reported.items()):
        print(f"reported: {path} sha256={digest} by={ts.producer} to={ts.reported_to}")
    for acc in ts.acceptances:
        print(f"accepted: {acc.path} sha256={acc.sha256} by={acc.by} at={acc.at}")


def run(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    if args.cmd == "demo":
        from bernstein_role_pack.demo import main as demo_main

        return demo_main()
    root = Path.cwd()
    home = home_for(root)
    config = load_config(home)
    if config is None:
        raise ConfigError(f"no roles.toml in {home}")
    if args.cmd == "check":
        print(f"ok: {len(config.roles)} roles, {len(config.principals)} principals, policy sha256={config.sha256}")
        return 0
    ledger = Ledger(home, config)
    if args.cmd == "status":
        _status(ledger, args.task)
        return 0
    if args.cmd == "ack":
        ts = ledger.acknowledge(args.task, args.by)
    elif args.cmd == "report":
        ts = ledger.report(args.task, args.by, args.paths, root)
    elif args.cmd == "accept":
        path = Path(args.path).as_posix()
        digest = args.sha256 or file_sha256(root / path)
        ts = ledger.accept(args.task, args.by, path, digest, root)
    elif args.cmd == "unknown":
        ts = ledger.mark_unknown(args.task, args.by, args.reason)
    else:
        ts = ledger.reconcile(args.task, args.by, args.outcome, args.evidence, args.paths or None, root)
    print(_summary(ledger, ts))
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except (RolePackError, OSError) as exc:
        print(f"refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
