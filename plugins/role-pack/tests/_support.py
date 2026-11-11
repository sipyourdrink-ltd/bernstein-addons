"""Shared fixtures for the unittest suite."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from bernstein_role_pack.config import HOME_ENV, load_config
from bernstein_role_pack.demo import EXAMPLE_ROLES
from bernstein_role_pack.ledger import Ledger


class WorkspaceCase(unittest.TestCase):
    """A temporary workspace with the example roles.toml at the default home."""

    write_config = True

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="role-pack-test-")
        self.root = Path(self._tmp.name).resolve()
        self.home = self.root / ".sdd" / "role-pack"
        self._saved_env = os.environ.pop(HOME_ENV, None)
        self._saved_cwd = os.getcwd()
        os.chdir(self.root)
        if self.write_config:
            self.home.mkdir(parents=True)
            (self.home / "roles.toml").write_text(EXAMPLE_ROLES, encoding="ascii")

    def tearDown(self) -> None:
        os.chdir(self._saved_cwd)
        if self._saved_env is not None:
            os.environ[HOME_ENV] = self._saved_env
        self._tmp.cleanup()

    def ledger(self) -> Ledger:
        return Ledger(self.home, load_config(self.home))

    def write(self, rel: str, data: bytes) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def reported_task(self, task: str = "t1") -> Ledger:
        """A worker task reported by wrk-1 with one file, out/a.txt."""
        ledger = self.ledger()
        ledger.deliver(task, "worker", "Write a")
        ledger.acknowledge(task, "wrk-1")
        self.write("out/a.txt", b"alpha\n")
        ledger.report(task, "wrk-1", ["out/a.txt"], self.root)
        return ledger
