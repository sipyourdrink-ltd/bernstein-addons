"""Read-only GitHub CLI (gh api) adapter using subprocess."""

from __future__ import annotations

import contextlib
import json
import subprocess
import threading
from typing import IO, Any

DEFAULT_MAX_STDOUT_BYTES = 10 * 1024 * 1024  # 10 MiB
DEFAULT_MAX_STDERR_BYTES = 64 * 1024  # 64 KiB
MAX_ERROR_DIAGNOSTIC_CHARS = 500
_READ_CHUNK_SIZE = 8192


class GhUnavailableError(RuntimeError):
    """Raised when the gh binary is missing or cannot be executed."""


class GhQueryError(RuntimeError):
    """Raised when a read-only gh api call fails or returns invalid JSON."""


def _read_bounded(
    stream: IO[bytes] | None,
    limit: int,
    buf: bytearray,
    overflow_flag: list[bool],
    on_overflow: threading.Event | None = None,
) -> None:
    if stream is None:
        return
    try:
        while True:
            chunk = stream.read(_READ_CHUNK_SIZE)
            if not chunk:
                break
            remaining = limit - len(buf)
            if remaining > 0:
                buf.extend(chunk[:remaining])
            if len(chunk) > remaining:
                overflow_flag[0] = True
                if on_overflow is not None:
                    on_overflow.set()
                    return
    finally:
        with contextlib.suppress(OSError):
            stream.close()


class GhClient:
    """Thin read-only wrapper around ``gh api`` via ``subprocess.Popen``."""

    def __init__(
        self,
        gh_bin: str = "gh",
        timeout_seconds: float = 30.0,
        max_stdout_bytes: int = DEFAULT_MAX_STDOUT_BYTES,
        max_stderr_bytes: int = DEFAULT_MAX_STDERR_BYTES,
    ) -> None:
        self._gh_bin = gh_bin
        self._timeout = timeout_seconds
        self._max_stdout_bytes = max_stdout_bytes
        self._max_stderr_bytes = max_stderr_bytes

    def _run_json(self, args: list[str]) -> Any:
        cmd = [self._gh_bin, "api", *args]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise GhUnavailableError(f"gh CLI not found: {self._gh_bin}") from exc
        except PermissionError as exc:
            raise GhUnavailableError(f"gh CLI not executable: {self._gh_bin}") from exc

        stdout_buf = bytearray()
        stderr_buf = bytearray()
        stdout_overflow = [False]
        stderr_overflow = [False]
        abort_event = threading.Event()

        t_out = threading.Thread(
            target=_read_bounded,
            args=(proc.stdout, self._max_stdout_bytes, stdout_buf, stdout_overflow, abort_event),
            daemon=True,
        )
        t_err = threading.Thread(
            target=_read_bounded,
            args=(proc.stderr, self._max_stderr_bytes, stderr_buf, stderr_overflow, None),
            daemon=True,
        )
        t_out.start()
        t_err.start()

        # Wait for either process exit, stdout overflow abort, or timeout.
        deadline = self._timeout
        poll_step = 0.05
        elapsed = 0.0
        timed_out = False
        while True:
            if abort_event.is_set():
                proc.kill()
                break
            if proc.poll() is not None:
                break
            if elapsed >= deadline:
                timed_out = True
                proc.kill()
                break
            wait_for = min(poll_step, deadline - elapsed)
            try:
                proc.wait(timeout=wait_for)
                break
            except subprocess.TimeoutExpired:
                elapsed += wait_for

        if proc.poll() is None:
            proc.wait(timeout=5.0)
        t_out.join(timeout=2.0)
        t_err.join(timeout=2.0)

        if stdout_overflow[0]:
            raise GhQueryError(
                f"gh api stdout exceeded {self._max_stdout_bytes} byte limit"
            )
        if timed_out:
            raise GhQueryError(f"gh api timed out after {self._timeout:.0f}s")

        if proc.returncode != 0:
            raw_err = stderr_buf.decode("utf-8", errors="replace").strip()
            if len(raw_err) > MAX_ERROR_DIAGNOSTIC_CHARS:
                raw_err = raw_err[:MAX_ERROR_DIAGNOSTIC_CHARS] + "... [truncated]"
            elif stderr_overflow[0] and raw_err:
                raw_err = raw_err + "... [truncated]"
            detail = raw_err or f"exit status {proc.returncode}"
            raise GhQueryError(f"gh api failed: {detail}")

        try:
            stdout_text = stdout_buf.decode("utf-8")
            return json.loads(stdout_text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
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
