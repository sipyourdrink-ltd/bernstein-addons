"""Read-only GitHub CLI (gh api) adapter using subprocess."""

from __future__ import annotations

import json
import subprocess
from typing import Any


class GhUnavailableError(RuntimeError):
    """Raised when the gh binary is missing or cannot be executed."""


class GhQueryError(RuntimeError):
    """Raised when a read-only gh api call fails or returns invalid JSON."""


class GhClient:
    """Thin read-only wrapper around ``gh api`` via ``subprocess.run``."""

    def __init__(self, gh_bin: str = "gh", timeout_seconds: float = 30.0) -> None:
        self._gh_bin = gh_bin
        self._timeout = timeout_seconds

    def _run_json(self, args: list[str]) -> Any:
        cmd = [self._gh_bin, "api", *args]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise GhUnavailableError(f"gh CLI not found: {self._gh_bin}") from exc
        except PermissionError as exc:
            raise GhUnavailableError(f"gh CLI not executable: {self._gh_bin}") from exc
        except subprocess.TimeoutExpired as exc:
            raise GhQueryError(f"gh api timed out after {self._timeout:.0f}s") from exc

        if proc.returncode != 0:
            detail = proc.stderr.strip() or f"exit status {proc.returncode}"
            raise GhQueryError(f"gh api failed: {detail}")

        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise GhQueryError(f"gh api returned invalid JSON: {exc}") from exc

    def graphql(self, query: str) -> dict[str, Any]:
        """Run a read-only GraphQL query through ``gh api graphql``."""
        data = self._run_json(["graphql", "-f", f"query={query}"])
        if not isinstance(data, dict):
            raise GhQueryError("gh api graphql returned non-object payload")
        return data

    def get(self, route: str) -> Any:
        """Run a read-only REST GET through ``gh api <route>``."""
        return self._run_json([route])
