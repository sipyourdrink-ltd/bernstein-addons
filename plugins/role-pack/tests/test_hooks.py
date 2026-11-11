"""Hooks over Bernstein's in-process HTTP routes in a trusted workspace."""

from __future__ import annotations

import logging
import secrets
import unittest

from _support import WorkspaceCase
from fastapi.testclient import TestClient

from bernstein.core.persistence.workspace import grant_workspace_trust
from bernstein.core.server.server_app import create_app


class HookCase(WorkspaceCase):
    def setUp(self) -> None:
        super().setUp()
        logging.getLogger("bernstein").setLevel(logging.ERROR)
        (self.root / ".sdd" / "runtime").mkdir(parents=True, exist_ok=True)
        grant_workspace_trust(self.root, granted_by="role-pack-tests")
        token = secrets.token_hex(16)
        app = create_app(jsonl_path=self.root / ".sdd" / "runtime" / "tasks.jsonl", auth_token=token)
        self.http = TestClient(app, headers={"Authorization": f"Bearer {token}"})
        self.http.__enter__()

    def tearDown(self) -> None:
        self.http.__exit__(None, None, None)
        super().tearDown()

    def create(self, title: str, role: str = "worker", description: str = "Do it."):
        return self.http.post("/tasks", json={"title": title, "description": description, "role": role})


class HookTests(HookCase):
    def test_delivered_written_by_hook(self) -> None:
        r = self.create("Write a", description="Do it.\nDeadline: 2026-12-01T12:00:00+02:00")
        self.assertEqual(r.status_code, 201, r.text)
        ts = self.ledger().state(r.json()["id"])
        self.assertEqual((ts.state, ts.role, ts.title), ("delivered", "worker", "Write a"))
        self.assertEqual(ts.deadline, "2026-12-01T10:00:00Z")

    def test_completed_written_by_hook(self) -> None:
        task = self.create("Write a").json()["id"]
        self.assertEqual(self.http.post(f"/tasks/{task}/claim", params={"claimed_by_session": "wrk-1"}).status_code, 200)
        r = self.http.post(f"/tasks/{task}/complete", json={"result_summary": "done"})
        self.assertEqual(r.status_code, 200, r.text)
        ts = self.ledger().state(task)
        self.assertTrue(ts.bernstein_completed)
        self.assertEqual(ts.state, "delivered")

    def test_untracked_role_not_written(self) -> None:
        self.assertEqual(self.create("Other", role="backend").status_code, 201)
        self.assertEqual(self.ledger().tasks(), [])

    def test_replay_of_unknown_task_rejected(self) -> None:
        task = self.create("Write a").json()["id"]
        ledger = self.ledger()
        ledger.acknowledge(task, "wrk-1")
        ledger.mark_unknown(task, "wrk-1", "session lost")
        r = self.create("Write a")
        self.assertEqual(r.status_code, 400)
        self.assertIn(f"task {task}", r.json()["detail"])
        self.assertEqual(len(ledger.tasks()), 1)
        self.assertEqual(self.create("Write a", role="reviewer").status_code, 201)
        ledger.reconcile(task, "req-1", "not_completed", "nothing produced")
        self.assertEqual(self.create("Write a").status_code, 201)

    def test_naive_deadline_rejected(self) -> None:
        r = self.create("Write a", description="Do it.\nDeadline: 2026-12-01T10:00:00")
        self.assertEqual(r.status_code, 400)
        self.assertIn("no offset", r.json()["detail"])
        self.assertEqual(self.ledger().tasks(), [])


class NoConfigHookTests(HookCase):
    write_config = False

    def test_nothing_written_without_roles_toml(self) -> None:
        r = self.create("Write a", description="Deadline: 2026-12-01T10:00:00")
        self.assertEqual(r.status_code, 201, r.text)
        self.assertFalse(self.home.exists())


if __name__ == "__main__":
    unittest.main()
