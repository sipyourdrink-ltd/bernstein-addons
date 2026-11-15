"""Run an explicitly selected task attempt and write its evidence receipt."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

RUNTIME = Path(__file__).resolve().with_name("runtime")
OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


class Hold(Exception):
    """The attempt has no candidate tip eligible for Bernstein's gates."""


def _git(repo: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    return subprocess.run(
        ["git", "-c", f"core.hooksPath={os.devnull}", "-C", str(repo), *args],
        env=env,
        capture_output=True,
        check=True,
        text=True,
        timeout=60,
    ).stdout.strip()


def _tip(repo: Path, ref: str) -> str:
    return _git(repo, "show-ref", "--verify", "--hash", ref)


def _prepare_copy(source: Path, repo: Path, target_ref: str, base: str, branch: str) -> None:
    if not OBJECT_ID.fullmatch(base):
        raise Hold("invalid_base")
    if not target_ref.startswith("refs/heads/"):
        raise Hold("invalid_target_ref")
    if Path(_git(source, "rev-parse", "--show-toplevel")).resolve() != source:
        raise Hold("source_not_repository_root")
    _git(source, "check-ref-format", target_ref)
    if _tip(source, target_ref) != base or _git(source, "cat-file", "-t", base) != "commit":
        raise Hold("base_mismatch")

    repo.mkdir(mode=0o700)
    object_format = _git(source, "rev-parse", "--show-object-format")
    _git(repo, "init", "--template=", f"--object-format={object_format}")
    # Fetch the exact object into a standalone copy with no configured remote.
    _git(repo, "fetch", "--no-tags", "--no-write-fetch-head", "--", str(source), base)
    _git(repo, "update-ref", target_ref, base)
    _git(repo, "symbolic-ref", "HEAD", target_ref)
    _git(repo, "reset", "--hard", base)
    _git(repo, "branch", "--", branch, base)
    if _tip(repo, f"refs/heads/{branch}") != base or _tip(repo, target_ref) != base:
        raise Hold("copy_base_mismatch")


def _verify_result(
    source: Path, repo: Path, receipt: dict[str, Any], result: Any, log: Path,
) -> dict[str, Any]:
    base, branch, target_ref = (receipt[key] for key in ("base_sha", "branch", "target_ref"))
    if not isinstance(result, dict) or result.get("branch") != branch:
        raise Hold("branch_mismatch")
    if result.get("logFilePath") != str(log) or not log.is_file() or log.is_symlink():
        raise Hold("log_mismatch")
    commits = result.get("commits")
    if commits == []:
        raise Hold("empty_commits")
    if not isinstance(commits, list) or any(
        not isinstance(commit, dict)
        or not isinstance(commit.get("sha"), str)
        or not OBJECT_ID.fullmatch(commit["sha"])
        for commit in commits
    ):
        raise Hold("invalid_commits")

    if _tip(repo, target_ref) != base or _tip(source, target_ref) != base:
        raise Hold("target_ref_changed")
    if _git(repo, "symbolic-ref", "HEAD") != target_ref:
        raise Hold("target_checkout_changed")

    tip = _tip(repo, f"refs/heads/{branch}")
    try:
        _git(repo, "merge-base", "--is-ancestor", base, tip)
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 1:
            raise Hold("base_not_ancestor") from exc
        raise
    actual = _git(repo, "rev-list", f"{base}..{tip}", "--reverse").splitlines()
    if actual != [commit["sha"] for commit in commits]:
        raise Hold("commits_mismatch")
    verified = {"tip_sha": tip, "commits": [{"sha": sha} for sha in actual]}
    if "output" in result:
        verified["output"] = result["output"]
    return verified


def run_attempt(
    *,
    repo_dir: Path,
    evidence_dir: Path,
    task_id: str,
    attempt_id: str,
    base_sha: str,
    target_ref: str,
    config: Path,
    prompt: str,
    timeout: int,
) -> dict[str, Any]:
    """Return one receipt. The caller supplies a trusted local runtime config."""
    if not task_id or not attempt_id:
        raise ValueError("Task and attempt IDs are required")
    if type(timeout) is not int or not 1 <= timeout <= 2_147_482:
        raise ValueError("Timeout must be a positive integer in seconds")
    key = hashlib.sha256(json.dumps([task_id, attempt_id]).encode()).hexdigest()
    source = repo_dir.resolve(strict=True)
    evidence_dir = evidence_dir.resolve()
    if evidence_dir.is_relative_to(source):
        raise ValueError("Evidence directory must be outside the source repository")
    evidence_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    attempt_dir = evidence_dir / key
    # Atomic reservation. A repeated or interrupted attempt cannot be replayed.
    try:
        attempt_dir.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise Hold("attempt_already_reserved") from exc

    repo = attempt_dir / "repository"
    log = attempt_dir / "run.log"
    result_file = attempt_dir / "result.json"
    receipt = {
        "schema_version": 1,
        "task_id": task_id,
        "attempt_id": attempt_id,
        "status": "held",
        "reason": None,
        "repository": str(repo),
        "base_sha": base_sha,
        "target_ref": target_ref,
        "branch": f"bernstein-attempt/{uuid.uuid4().hex}",
        "tip_sha": None,
        "commits": [],
        "log_path": None,
        "log_sha256": None,
    }

    try:
        _prepare_copy(source, repo, target_ref, base_sha, receipt["branch"])
        request = {
            "config": str(config.resolve(strict=True)),
            "prompt": prompt,
            "branch": receipt["branch"],
            "logFilePath": str(log),
            "resultPath": str(result_file),
            "timeoutMs": timeout * 1000,
        }
        node_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        subprocess.run(
            ["node", str(RUNTIME / "runner.mjs")],
            cwd=repo,
            env=node_env,
            input=json.dumps(request),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            check=True,
            timeout=timeout + 1,
        )
        with result_file.open(encoding="utf-8") as file:
            result = json.load(file)
        verified = _verify_result(source, repo, receipt, result, log)
        receipt.update(verified, status="verified")
    except Hold as exc:
        receipt["reason"] = str(exc)
    except subprocess.TimeoutExpired:
        receipt["reason"] = "timeout"
    except subprocess.CalledProcessError:
        receipt["reason"] = "subprocess_failed"
    except (OSError, UnicodeError, ValueError):
        receipt["reason"] = "artifact_error"

    try:
        if log.is_file() and not log.is_symlink():
            with log.open("rb") as file:
                receipt["log_sha256"] = hashlib.file_digest(file, "sha256").hexdigest()
            receipt["log_path"] = str(log)
        elif receipt["status"] == "verified":
            raise OSError("Expected a log for verified evidence")
    except OSError:
        receipt.update(status="held", reason="log_unreadable", tip_sha=None, commits=[])
        receipt.pop("output", None)

    try:
        result_file.unlink(missing_ok=True)
    except OSError:
        receipt.update(status="held", reason="result_cleanup_failed", tip_sha=None, commits=[])
        receipt.pop("output", None)
    evidence_file = attempt_dir / "evidence.json"
    draft = evidence_file.with_suffix(".tmp")
    with draft.open("x", encoding="utf-8") as file:
        json.dump(receipt, file, allow_nan=False, indent=2)
        file.write("\n")
    draft.replace(evidence_file)
    return receipt
