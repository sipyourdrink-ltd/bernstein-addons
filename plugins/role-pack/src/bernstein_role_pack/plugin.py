"""Bernstein lifecycle hooks (entry-point group ``bernstein.plugins``)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from bernstein.plugins import hookimpl
from bernstein.plugins.manager import HookBlockingError

from bernstein_role_pack.clock import ClockError, format_utc, parse_deadline
from bernstein_role_pack.config import home_for, load_config
from bernstein_role_pack.ledger import Ledger

DEADLINE_LINE = re.compile(r"^\s*Deadline:\s*(.+?)\s*$", re.MULTILINE)


def _open_ledger() -> Ledger | None:
    home = home_for(Path.cwd())
    config = load_config(home)
    return None if config is None else Ledger(home, config)


def _warn(hook: str, exc: Exception) -> None:
    print(f"role-pack: {hook}: {type(exc).__name__}: {exc}", file=sys.stderr)


class RolePackPlugin:
    """Writes delivery receipts and blocks replays of unknown tasks.

    Bernstein's pre-create hook receives an empty task id and the created hook
    receives no description, so a parsed deadline is held here between the two
    calls, keyed by (role, title).
    """

    def __init__(self) -> None:
        self._deadlines: dict[tuple[str, str], str] = {}

    @hookimpl
    def on_pre_task_create(self, task_id: str, role: str, title: str, description: str) -> None:
        try:
            ledger = _open_ledger()
            if ledger is None:
                return
            for ts in ledger.tasks():
                if ts.unknown and ts.role == role and ts.title == title:
                    reason = (
                        f"task {ts.task} with role {role} and title {title!r} is unknown; "
                        "reconcile it under its own id instead of creating a new task"
                    )
                    break
            else:
                reason = None
            deadline = None
            match = DEADLINE_LINE.search(description or "")
            if reason is None and match:
                try:
                    deadline = format_utc(parse_deadline(match.group(1)))
                except ClockError as exc:
                    reason = str(exc)
        except Exception as exc:  # never block for anything but the two rules
            _warn("on_pre_task_create", exc)
            return
        if reason is not None:
            raise HookBlockingError("role_pack", reason)
        if deadline is not None:
            self._deadlines[(role, title)] = deadline
        else:
            self._deadlines.pop((role, title), None)

    @hookimpl
    def on_task_created(self, task_id: str, role: str, title: str) -> None:
        deadline = self._deadlines.pop((role, title), None)
        try:
            ledger = _open_ledger()
            if ledger is not None:
                ledger.deliver(task_id, role, title, deadline)
        except Exception as exc:
            _warn("on_task_created", exc)

    @hookimpl
    def on_task_completed(self, task_id: str, role: str, result_summary: str) -> None:
        try:
            ledger = _open_ledger()
            if ledger is not None:
                ledger.record_completed(task_id, result_summary or "")
        except Exception as exc:
            _warn("on_task_completed", exc)
