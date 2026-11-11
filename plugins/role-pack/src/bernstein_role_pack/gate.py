"""Quality gate ``role_pack_acceptance`` (entry-point group ``bernstein.gates``)."""

from __future__ import annotations

from pathlib import Path

from bernstein.core.quality.gate_plugins import GatePlugin
from bernstein.core.quality.gate_runner import GateResult

from bernstein_role_pack.config import RolePackError, home_for, load_config
from bernstein_role_pack.ledger import Ledger, acceptance_problem, file_sha256

GATE_NAME = "role_pack_acceptance"


def _result(status: str, details: str, metadata: dict | None = None) -> GateResult:
    return GateResult(
        name=GATE_NAME,
        status=status,
        required=True,
        blocked=status != "pass",
        cached=False,
        duration_ms=0,
        details=details,
        metadata=metadata or {},
    )


def check_files(ledger: Ledger, changed_files: list[str], root: Path) -> tuple[list[str], list[str]]:
    """Return (checked paths, problems); each problem names one file and its reason."""
    tasks = ledger.tasks()
    checked: list[str] = []
    problems: list[str] = []
    for rel in sorted({Path(p).as_posix() for p in changed_files}):
        if rel == ".sdd" or rel.startswith(".sdd/"):
            continue
        checked.append(rel)
        full = root / rel
        if not full.is_file():
            problems.append(f"{rel}: file not found")
            continue
        current = file_sha256(full)
        owners = [ts for ts in tasks if rel in ts.reported]
        if not owners:
            problems.append(f"{rel}: not reported by any tracked task")
            continue
        unknown = [ts.task for ts in owners if ts.unknown]
        if unknown:
            problems.append(f"{rel}: owning task {', '.join(unknown)} is unknown")
            continue
        reasons: list[str] = []
        ok = False
        for ts in owners:
            if ts.reported[rel] != current:
                reasons.append(f"changed since task {ts.task} reported it")
                continue
            candidates = [a for a in ts.acceptances if a.path == rel]
            if not candidates:
                reasons.append(f"no acceptance in task {ts.task}")
            for acc in candidates:
                problem = acceptance_problem(ledger.config, ts, acc)
                if problem is None:
                    ok = True
                else:
                    reasons.append(f"acceptance by {acc.by} in task {ts.task} does not count: {problem}")
        if not ok:
            problems.append(f"{rel}: " + "; ".join(reasons))
    return checked, problems


class RolePackAcceptanceGate(GatePlugin):
    @property
    def name(self) -> str:
        return GATE_NAME

    @property
    def required(self) -> bool:
        return True

    def run(self, changed_files: list[str], run_dir: Path, task_title: str, task_description: str) -> GateResult:
        home = home_for(run_dir)
        try:
            config = load_config(home)
            if config is None:
                return _result("fail", "role pack is not configured: roles.toml not found in the pack home")
            checked, problems = check_files(Ledger(home, config), changed_files, run_dir)
        except RolePackError as exc:
            return _result("fail", f"role pack error: {exc}")
        meta = {"checked": checked, "policy": config.sha256}
        if problems:
            return _result("fail", "not accepted: " + " | ".join(problems), meta)
        if not checked:
            return _result("pass", "no changed files to check outside .sdd/ (0 files)", meta)
        return _result("pass", f"{len(checked)} file(s) accepted: " + ", ".join(checked), meta)
