from __future__ import annotations

import hashlib
import json
import unittest

from _support import WorkspaceCase

from bernstein_role_pack.gate import GATE_NAME, RolePackAcceptanceGate

ALPHA = hashlib.sha256(b"alpha\n").hexdigest()


class GateTests(WorkspaceCase):
    def run_gate(self, files: list[str]):
        result = RolePackAcceptanceGate().run(files, self.root, "title", "description")
        self.assertEqual(result.name, GATE_NAME)
        return result

    def assert_blocked(self, result, fragment: str) -> None:
        self.assertEqual(result.status, "fail")
        self.assertTrue(result.blocked)
        self.assertIn(fragment, result.details)

    def test_passes_on_valid_acceptance(self) -> None:
        ledger = self.reported_task()
        ledger.accept("t1", "rev-1", "out/a.txt", ALPHA, self.root)
        result = self.run_gate(["out/a.txt", ".sdd/role-pack/ledger.jsonl"])
        self.assertEqual(result.status, "pass", result.details)
        self.assertFalse(result.blocked)
        self.assertEqual(result.metadata["checked"], ["out/a.txt"])

    def test_blocks_without_acceptance(self) -> None:
        self.reported_task()
        self.assert_blocked(self.run_gate(["out/a.txt"]), "out/a.txt: no acceptance")

    def test_blocks_unreported_file(self) -> None:
        self.reported_task()
        self.write("other.txt", b"x\n")
        self.assert_blocked(self.run_gate(["other.txt"]), "other.txt: not reported")

    def test_blocks_after_file_changes(self) -> None:
        ledger = self.reported_task()
        ledger.accept("t1", "rev-1", "out/a.txt", ALPHA, self.root)
        self.write("out/a.txt", b"alpha edited\n")
        self.assert_blocked(self.run_gate(["out/a.txt"]), "changed since task t1 reported it")
        self.write("out/a.txt", b"alpha\n")
        self.assertEqual(self.run_gate(["out/a.txt"]).status, "pass")

    def forge_acceptance(self, ledger, by: str, sha256: str = ALPHA) -> None:
        line = {"event": "accepted", "task": "t1", "by": by, "path": "out/a.txt", "sha256": sha256,
                "at": "2026-11-01T00:00:00Z", "policy": ledger.config.sha256}
        with ledger.path.open("a", encoding="ascii") as fh:
            fh.write(json.dumps(line) + "\n")

    def test_blocks_forged_line_where_reviewer_is_producer(self) -> None:
        ledger = self.reported_task()
        self.forge_acceptance(ledger, "wrk-1")
        self.assert_blocked(self.run_gate(["out/a.txt"]), "reviewer wrk-1 is the producer")

    def test_blocks_forged_line_from_role_not_allowed(self) -> None:
        ledger = self.reported_task()
        self.forge_acceptance(ledger, "req-1")
        self.assert_blocked(self.run_gate(["out/a.txt"]), "role requester may not review role worker")

    def test_blocks_forged_line_with_another_hash(self) -> None:
        ledger = self.reported_task()
        self.forge_acceptance(ledger, "rev-1", sha256="0" * 64)
        self.assert_blocked(self.run_gate(["out/a.txt"]), "accepted hash differs from the reported hash")
        self.assertEqual(ledger.state("t1").state, "reported")

    def test_blocks_when_owning_task_unknown(self) -> None:
        ledger = self.reported_task()
        ledger.accept("t1", "rev-1", "out/a.txt", ALPHA, self.root)
        ledger.mark_unknown("t1", "req-1", "session lost")
        self.assert_blocked(self.run_gate(["out/a.txt"]), "owning task t1 is unknown")

    def test_zero_files_says_so(self) -> None:
        result = self.run_gate([".sdd/runtime/tasks.jsonl"])
        self.assertEqual(result.status, "pass")
        self.assertIn("0 files", result.details)


class UnconfiguredGateTests(WorkspaceCase):
    write_config = False

    def test_blocks_when_not_configured(self) -> None:
        result = RolePackAcceptanceGate().run(["a.txt"], self.root, "t", "d")
        self.assertTrue(result.blocked)
        self.assertEqual(result.status, "fail")
        self.assertIn("not configured", result.details)


if __name__ == "__main__":
    unittest.main()
