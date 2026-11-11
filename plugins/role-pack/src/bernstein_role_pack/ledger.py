"""Append-only receipt ledger. Task state is derived by replaying the file."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from bernstein_role_pack.clock import Clock
from bernstein_role_pack.config import Config, RolePackError


class LedgerCorrupt(RolePackError):
    """A ledger line could not be read."""


class Refused(RolePackError):
    """Base class for refused ledger operations."""


class UnknownTask(Refused):
    pass


class UnknownPrincipal(Refused):
    pass


class WrongRole(Refused):
    pass


class WrongState(Refused):
    pass


class NotHolder(Refused):
    pass


class NotInChain(Refused):
    pass


class TaskIsUnknown(Refused):
    pass


class SelfReview(Refused):
    pass


class ReviewNotAllowed(Refused):
    pass


class HashMismatch(Refused):
    pass


class PathNotReported(Refused):
    pass


class FileChanged(Refused):
    pass


class BadPath(Refused):
    pass


@dataclass
class Acceptance:
    by: str
    path: str
    sha256: str
    at: str


@dataclass
class TaskState:
    task: str
    role: str
    title: str
    state: str = "delivered"
    holder: str | None = None
    producer: str | None = None
    reported: dict[str, str] = field(default_factory=dict)
    reported_to: str | None = None
    acceptances: list[Acceptance] = field(default_factory=list)
    unknown: bool = False
    unknown_reason: str | None = None
    bernstein_completed: bool = False
    deadline: str | None = None


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def acceptance_problem(config: Config, ts: TaskState, acc: Acceptance) -> str | None:
    """Why *acc* does not count for *ts* under the registry, or None when it does."""
    if acc.path not in ts.reported:
        return "path is not in the current report"
    if acc.sha256 != ts.reported[acc.path]:
        return "accepted hash differs from the reported hash"
    if acc.by == ts.producer:
        return f"reviewer {acc.by} is the producer"
    reviewer_role = config.role_of(acc.by)
    producer_role = config.role_of(ts.producer or "")
    if not config.may_review(reviewer_role, producer_role):
        return f"role {reviewer_role} may not review role {producer_role}"
    return None


def _settle(config: Config, ts: TaskState) -> None:
    if ts.state not in ("reported", "done"):
        return
    accepted = {a.path for a in ts.acceptances if acceptance_problem(config, ts, a) is None}
    ts.state = "done" if ts.reported and set(ts.reported) <= accepted else "reported"


def _apply_report(ts: TaskState, producer: str, files: dict[str, str], to: str | None) -> None:
    ts.producer = producer
    ts.reported = dict(files)
    ts.reported_to = to
    ts.acceptances = [a for a in ts.acceptances if files.get(a.path) == a.sha256]
    ts.state = "reported"


class Ledger:
    def __init__(self, home: Path, config: Config) -> None:
        self.home = home
        self.path = home / "ledger.jsonl"
        self.config = config
        self.clock = Clock(config.zone, config.max_reading_age_s)

    # -- reading -------------------------------------------------------

    def events(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                raise LedgerCorrupt(f"{self.path.name} line {number} is not JSON") from None
            if not isinstance(event, dict) or "event" not in event or "task" not in event:
                raise LedgerCorrupt(f"{self.path.name} line {number} is not a ledger event")
            out.append(event)
        return out

    def replay(self) -> dict[str, TaskState]:
        states: dict[str, TaskState] = {}
        for ev in self.events():
            kind, task = ev["event"], ev["task"]
            if kind == "delivered":
                states.setdefault(task, TaskState(task, ev["role"], ev.get("title", ""), deadline=ev.get("deadline")))
                continue
            ts = states.get(task)
            if ts is None:
                continue
            if kind == "acknowledged":
                ts.holder = ev["by"]
                ts.state = "acknowledged"
            elif kind == "reported":
                _apply_report(ts, ev["by"], ev["files"], ev.get("to"))
            elif kind == "accepted":
                ts.acceptances.append(Acceptance(ev["by"], ev["path"], ev["sha256"], ev["at"]))
            elif kind == "unknown":
                ts.state = "unknown"
                ts.unknown = True
                ts.unknown_reason = ev.get("reason")
            elif kind == "reconciled":
                ts.unknown = False
                ts.unknown_reason = None
                if ev["outcome"] == "completed":
                    _apply_report(ts, ts.holder or ev["by"], ev["files"], ev.get("to"))
                else:
                    ts.state = "acknowledged"
                    ts.producer = None
                    ts.reported = {}
                    ts.reported_to = None
                    ts.acceptances = []
            elif kind == "completed":
                ts.bernstein_completed = True
            _settle(self.config, ts)
        return states

    def state(self, task: str) -> TaskState:
        ts = self.replay().get(task)
        if ts is None:
            raise UnknownTask(f"task {task} is not tracked")
        return ts

    def tasks(self) -> list[TaskState]:
        return list(self.replay().values())

    # -- writing -------------------------------------------------------

    def _write(self, kind: str, task: str, **fields: object) -> dict:
        event = {"event": kind, "task": task, "at": self.clock.stamp(), "policy": self.config.sha256, **fields}
        line = (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
        os.makedirs(self.home, mode=0o700, exist_ok=True)
        os.chmod(self.home, 0o700)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
        return event

    def _principal_role(self, by: str) -> str:
        role = self.config.role_of(by)
        if role is None:
            raise UnknownPrincipal(f"principal {by} is not in the registry")
        return role

    def _live(self, task: str) -> TaskState:
        ts = self.state(task)
        if ts.unknown:
            raise TaskIsUnknown(f"task {task} is unknown; reconcile it under its own id first")
        return ts

    def _holder_or_chain(self, ts: TaskState, by: str) -> None:
        role = self._principal_role(by)
        if by != ts.holder and role not in self.config.chain(ts.role):
            raise NotInChain(f"{by} is neither the holder nor in the line above role {ts.role}")

    def _hash_paths(self, paths: list[str], root: Path) -> dict[str, str]:
        if not paths:
            raise BadPath("no paths given")
        base = root.resolve()
        files: dict[str, str] = {}
        for raw in paths:
            full = (base / raw).resolve()
            try:
                rel = full.relative_to(base).as_posix()
            except ValueError:
                raise BadPath(f"{raw} is outside the workspace") from None
            if not full.is_file():
                raise BadPath(f"{rel} is not a file")
            files[rel] = file_sha256(full)
        return files

    def deliver(self, task: str, role: str, title: str, deadline: str | None = None) -> bool:
        """Record delivery; return False when the role is not in the registry."""
        if role not in self.config.roles:
            return False
        if task in self.replay():
            raise WrongState(f"task {task} is already tracked")
        self._write("delivered", task, role=role, title=title, deadline=deadline)
        return True

    def acknowledge(self, task: str, by: str) -> TaskState:
        ts = self._live(task)
        if self._principal_role(by) != ts.role:
            raise WrongRole(f"{by} does not hold role {ts.role}")
        if ts.state != "delivered":
            raise WrongState(f"task {task} is {ts.state}, not delivered")
        self._write("acknowledged", task, by=by)
        return self.state(task)

    def report(self, task: str, by: str, paths: list[str], root: Path) -> TaskState:
        ts = self._live(task)
        self._principal_role(by)
        if by != ts.holder:
            raise NotHolder(f"{by} is not the holder of task {task}")
        if ts.state not in ("acknowledged", "reported", "done"):
            raise WrongState(f"task {task} is {ts.state}, not acknowledged")
        files = self._hash_paths(paths, root)
        self._write("reported", task, by=by, files=files, to=self.config.reports_to(ts.role))
        return self.state(task)

    def accept(self, task: str, by: str, path: str, sha256: str, root: Path | None = None) -> TaskState:
        ts = self._live(task)
        reviewer_role = self._principal_role(by)
        if not ts.reported:
            raise WrongState(f"task {task} has no report")
        if path not in ts.reported:
            raise PathNotReported(f"{path} was not reported for task {task}")
        if by == ts.producer:
            raise SelfReview(f"{by} produced {path} and may not accept it")
        producer_role = self.config.role_of(ts.producer or "")
        if not self.config.may_review(reviewer_role, producer_role):
            raise ReviewNotAllowed(f"role {reviewer_role} may not review role {producer_role}")
        if sha256 != ts.reported[path]:
            raise HashMismatch(f"sha256 {sha256[:12]} does not match the reported {ts.reported[path][:12]}")
        if root is not None:
            full = root / path
            if not full.is_file() or file_sha256(full) != sha256:
                raise FileChanged(f"{path} on disk no longer hashes to the reported value")
        self._write("accepted", task, by=by, path=path, sha256=sha256)
        return self.state(task)

    def mark_unknown(self, task: str, by: str, reason: str) -> TaskState:
        ts = self._live(task)
        self._holder_or_chain(ts, by)
        if ts.state == "delivered":
            raise WrongState(f"task {task} has not been acknowledged; nothing is held that could be unknown")
        self._write("unknown", task, by=by, reason=reason)
        return self.state(task)

    def reconcile(
        self,
        task: str,
        by: str,
        outcome: str,
        evidence: str,
        paths: list[str] | None = None,
        root: Path | None = None,
    ) -> TaskState:
        ts = self.state(task)
        if not ts.unknown:
            raise WrongState(f"task {task} is not unknown")
        self._holder_or_chain(ts, by)
        if outcome == "completed":
            if not paths or root is None:
                raise BadPath("outcome completed needs the result paths")
            files = self._hash_paths(paths, root)
            self._write(
                "reconciled", task, by=by, outcome=outcome, evidence=evidence, files=files,
                to=self.config.reports_to(ts.role),
            )
        elif outcome == "not_completed":
            self._write("reconciled", task, by=by, outcome=outcome, evidence=evidence)
        else:
            raise WrongState(f"outcome must be completed or not_completed, not {outcome!r}")
        return self.state(task)

    def record_completed(self, task: str, summary: str) -> bool:
        """Note that Bernstein closed the task; no effect on pack state."""
        if task not in self.replay():
            return False
        self._write("completed", task, summary=summary[:500])
        return True

