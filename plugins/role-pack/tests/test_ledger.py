from __future__ import annotations

import hashlib
import json
import stat
import unittest

from _support import WorkspaceCase

from bernstein_role_pack.ledger import (
    FileChanged,
    HashMismatch,
    NotHolder,
    NotInChain,
    PathNotReported,
    Refused,
    ReviewNotAllowed,
    SelfReview,
    TaskIsUnknown,
    WrongRole,
    WrongState,
)

ALPHA = hashlib.sha256(b"alpha\n").hexdigest()


class LedgerTests(WorkspaceCase):
    def test_state_sequence(self) -> None:
        ledger = self.ledger()
        self.assertTrue(ledger.deliver("t1", "worker", "Write a", "2026-12-01T10:00:00Z"))
        self.assertEqual(ledger.state("t1").state, "delivered")
        self.assertEqual(ledger.acknowledge("t1", "wrk-1").holder, "wrk-1")
        self.assertEqual(ledger.state("t1").state, "acknowledged")
        self.write("out/a.txt", b"alpha\n")
        ts = ledger.report("t1", "wrk-1", ["out/a.txt"], self.root)
        self.assertEqual(ts.state, "reported")
        self.assertEqual(ts.reported, {"out/a.txt": ALPHA})
        self.assertEqual(ts.reported_to, "requester")
        ledger.record_completed("t1", "closed by bernstein")
        ts = ledger.state("t1")
        self.assertEqual(ts.state, "reported")
        self.assertTrue(ts.bernstein_completed)
        ts = ledger.accept("t1", "rev-1", "out/a.txt", ALPHA, self.root)
        self.assertEqual(ts.state, "done")
        self.assertEqual(ts.deadline, "2026-12-01T10:00:00Z")
        self.assertEqual([t.task for t in ledger.tasks()], ["t1"])

    def test_events_carry_policy_and_utc(self) -> None:
        ledger = self.reported_task()
        lines = ledger.path.read_text(encoding="ascii").splitlines()
        self.assertEqual(len(lines), 3)
        for line in lines:
            event = json.loads(line)
            self.assertEqual(event["policy"], ledger.config.sha256)
            self.assertRegex(event["at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_file_modes(self) -> None:
        ledger = self.reported_task()
        self.assertEqual(stat.S_IMODE(ledger.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(ledger.home.stat().st_mode), 0o700)

    def test_untracked_role(self) -> None:
        ledger = self.ledger()
        self.assertFalse(ledger.deliver("t9", "backend", "Other"))
        self.assertFalse(ledger.path.exists())

    def test_acknowledge_rules(self) -> None:
        ledger = self.ledger()
        ledger.deliver("t1", "worker", "Write a")
        with self.assertRaises(WrongRole):
            ledger.acknowledge("t1", "rev-1")
        ledger.acknowledge("t1", "wrk-1")
        with self.assertRaises(WrongState):
            ledger.acknowledge("t1", "wrk-1")

    def test_report_only_by_holder(self) -> None:
        ledger = self.ledger()
        ledger.deliver("t1", "worker", "Write a")
        self.write("out/a.txt", b"alpha\n")
        with self.assertRaises(NotHolder):
            ledger.report("t1", "wrk-1", ["out/a.txt"], self.root)
        ledger.acknowledge("t1", "wrk-1")
        with self.assertRaises(NotHolder):
            ledger.report("t1", "req-1", ["out/a.txt"], self.root)

    def test_accept_refused_when_reviewer_is_producer(self) -> None:
        ledger = self.reported_task()
        with self.assertRaises(SelfReview):
            ledger.accept("t1", "wrk-1", "out/a.txt", ALPHA, self.root)
        self.assertEqual(ledger.state("t1").state, "reported")

    def test_accept_refused_when_hash_differs(self) -> None:
        ledger = self.reported_task()
        with self.assertRaises(HashMismatch):
            ledger.accept("t1", "rev-1", "out/a.txt", "0" * 64)
        with self.assertRaises(HashMismatch):
            ledger.accept("t1", "rev-1", "out/a.txt", "0" * 64, self.root)
        self.assertEqual(ledger.state("t1").acceptances, [])

    def test_accept_refused_for_role_not_allowed_to_review(self) -> None:
        ledger = self.reported_task()
        with self.assertRaises(ReviewNotAllowed):
            ledger.accept("t1", "req-1", "out/a.txt", ALPHA, self.root)

    def test_accept_refused_for_unreported_path(self) -> None:
        ledger = self.reported_task()
        with self.assertRaises(PathNotReported):
            ledger.accept("t1", "rev-1", "out/b.txt", ALPHA)

    def test_accept_refused_when_disk_changed(self) -> None:
        ledger = self.reported_task()
        self.write("out/a.txt", b"edited\n")
        with self.assertRaises(FileChanged):
            ledger.accept("t1", "rev-1", "out/a.txt", ALPHA, self.root)

    def test_new_report_voids_changed_acceptances(self) -> None:
        ledger = self.reported_task()
        self.write("out/b.txt", b"beta\n")
        ledger.report("t1", "wrk-1", ["out/a.txt", "out/b.txt"], self.root)
        ledger.accept("t1", "rev-1", "out/a.txt", ALPHA, self.root)
        beta = hashlib.sha256(b"beta\n").hexdigest()
        self.assertEqual(ledger.accept("t1", "rev-1", "out/b.txt", beta, self.root).state, "done")
        self.write("out/b.txt", b"beta two\n")
        ts = ledger.report("t1", "wrk-1", ["out/a.txt", "out/b.txt"], self.root)
        self.assertEqual(ts.state, "reported")
        self.assertEqual([a.path for a in ts.acceptances], ["out/a.txt"])

    def test_unknown_blocks_until_reconciled(self) -> None:
        ledger = self.reported_task()
        with self.assertRaises(NotInChain):
            ledger.mark_unknown("t1", "rev-1", "reviewer is not in the line")
        ts = ledger.mark_unknown("t1", "req-1", "session lost")
        self.assertTrue(ts.unknown)
        self.assertEqual(ts.state, "unknown")
        with self.assertRaises(TaskIsUnknown):
            ledger.accept("t1", "rev-1", "out/a.txt", ALPHA, self.root)
        with self.assertRaises(TaskIsUnknown):
            ledger.report("t1", "wrk-1", ["out/a.txt"], self.root)
        with self.assertRaises(TaskIsUnknown):
            ledger.acknowledge("t1", "wrk-1")
        with self.assertRaises(Refused):
            ledger.reconcile("t1", "wrk-1", "completed", "file present")
        ts = ledger.reconcile("t1", "wrk-1", "completed", "file present", ["out/a.txt"], self.root)
        self.assertEqual(ts.state, "reported")
        self.assertFalse(ts.unknown)
        self.assertEqual(ledger.accept("t1", "rev-1", "out/a.txt", ALPHA, self.root).state, "done")

    def test_reconcile_not_completed_returns_to_acknowledged(self) -> None:
        ledger = self.reported_task()
        ledger.mark_unknown("t1", "wrk-1", "result unclear")
        ts = ledger.reconcile("t1", "req-1", "not_completed", "no output")
        self.assertEqual((ts.task, ts.state, ts.holder, ts.reported), ("t1", "acknowledged", "wrk-1", {}))


if __name__ == "__main__":
    unittest.main()
