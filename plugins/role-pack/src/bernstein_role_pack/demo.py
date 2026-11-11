"""In-process demo over Bernstein's real HTTP routes. No model, no network."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

from bernstein_role_pack.config import HOME_ENV, load_config
from bernstein_role_pack.gate import GATE_NAME
from bernstein_role_pack.ledger import HashMismatch, Ledger, SelfReview, file_sha256

# Same text as examples/roles.toml; a test keeps the two identical.
EXAMPLE_ROLES = """\
[clock]
zone = "UTC"
max_reading_age_s = 60
due_window_s = 3600

[roles.requester]

[roles.worker]
reports_to = "requester"

[roles.reviewer]
reports_to = "requester"
reviews = ["worker"]

[principals]
"req-1" = "requester"
"wrk-1" = "worker"
"rev-1" = "reviewer"
"""

ARTIFACT = "out/summary.md"


class StepFailed(Exception):
    pass


_CURRENT = [0]


def _expect(step: int, condition: bool, what: str) -> None:
    if not condition:
        raise StepFailed(f"step {step} failed: {what}")


def _run(root: Path) -> None:
    from fastapi.testclient import TestClient

    from bernstein.core.persistence.workspace import grant_workspace_trust
    from bernstein.core.quality.gate_pipeline import GatePipelineStep
    from bernstein.core.quality.gate_runner import GateRunner
    from bernstein.core.quality.quality_gates import QualityGatesConfig
    from bernstein.core.server.server_app import create_app

    home = root / ".sdd" / "role-pack"
    home.mkdir(parents=True)
    (root / ".sdd" / "runtime").mkdir(parents=True)
    (home / "roles.toml").write_text(EXAMPLE_ROLES, encoding="ascii")
    os.environ[HOME_ENV] = str(home)
    grant_workspace_trust(root, granted_by="role-pack-demo")
    config = load_config(home)
    ledger = Ledger(home, config)
    runner = GateRunner(QualityGatesConfig(pipeline=[GatePipelineStep(name=GATE_NAME, required=True)]), root)

    token = secrets.token_hex(16)
    app = create_app(jsonl_path=root / ".sdd" / "runtime" / "tasks.jsonl", auth_token=token)

    def gate(task_id: str) -> tuple[bool, list[str], str]:
        task = app.state.store.get_task(task_id)
        report = asyncio.run(runner.run_all(task, root))
        result = next(r for r in report.results if r.name == GATE_NAME)
        return result.blocked, report.changed_files, result.details

    def state(task_id: str) -> str:
        return ledger.state(task_id).state

    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as http:
        _CURRENT[0] = 1
        deadline = (ledger.clock.read().utc + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        r = http.post(
            "/tasks",
            json={
                "title": "Write the summary",
                "description": f"Summarise the findings.\nDeadline: {deadline}",
                "role": "worker",
                "owned_files": [ARTIFACT],
            },
        )
        _expect(1, r.status_code == 201, f"POST /tasks returned {r.status_code}")
        work = r.json()["id"]
        _expect(1, state(work) == "delivered", "hook did not write delivered")
        ts = ledger.state(work)
        print(f"1. requester POST /tasks role=worker -> HTTP {r.status_code}, task {work}; "
              f"pack: {ts.state} (deadline {ts.deadline})")

        _CURRENT[0] = 2
        r = http.post(f"/tasks/{work}/claim", params={"claimed_by_session": "wrk-1"})
        _expect(2, r.status_code == 200, f"claim returned {r.status_code}")
        ledger.acknowledge(work, "wrk-1")
        _expect(2, state(work) == "acknowledged", "not acknowledged")
        print(f"2. wrk-1 POST /tasks/{work}/claim -> HTTP {r.status_code}; ack; pack: {state(work)}")

        _CURRENT[0] = 3
        artifact = root / ARTIFACT
        artifact.parent.mkdir(parents=True)
        original = b"# Summary\n\nThree findings, each with its source.\n"
        artifact.write_bytes(original)
        digest = file_sha256(artifact)
        ledger.report(work, "wrk-1", [ARTIFACT], root)
        _expect(3, state(work) == "reported", "not reported")
        r = http.post(f"/tasks/{work}/complete", json={"result_summary": "summary written"})
        _expect(3, r.status_code == 200, f"complete returned {r.status_code}")
        ts = ledger.state(work)
        _expect(3, ts.bernstein_completed and ts.state == "reported", "completion changed the pack state")
        print(f"3. wrk-1 reports {ARTIFACT} sha256={digest[:12]}..; POST /tasks/{work}/complete -> HTTP "
              f"{r.status_code}; pack: {ts.state} (bernstein_completed=yes, to={ts.reported_to})")

        _CURRENT[0] = 4
        blocked, files, details = gate(work)
        _expect(4, blocked and files == [ARTIFACT], f"gate not blocked over {files}: {details}")
        print(f"4. GateRunner.run_all changed_files={files} -> blocked: {details}")

        _CURRENT[0] = 5
        try:
            ledger.accept(work, "wrk-1", ARTIFACT, digest, root)
            _expect(5, False, "self-acceptance was not refused")
        except SelfReview as exc:
            print(f"5. wrk-1 accepts its own artifact -> refused: {exc}; pack: {state(work)}")

        _CURRENT[0] = 6
        r = http.post("/tasks", json={"title": "Review the summary", "description": "Check it.", "role": "reviewer"})
        _expect(6, r.status_code == 201, f"POST /tasks returned {r.status_code}")
        review = r.json()["id"]
        r = http.post(f"/tasks/{review}/claim", params={"claimed_by_session": "rev-1"})
        _expect(6, r.status_code == 200, f"claim returned {r.status_code}")
        ledger.acknowledge(review, "rev-1")
        wrong = "0" * 64
        try:
            ledger.accept(work, "rev-1", ARTIFACT, wrong, root)
            _expect(6, False, "wrong hash was not refused")
        except HashMismatch as exc:
            print(f"6. rev-1 claims review task {review} (pack: {state(review)}); accepts with a wrong hash "
                  f"-> refused: {exc}; pack: {state(work)}")

        _CURRENT[0] = 7
        ledger.accept(work, "rev-1", ARTIFACT, digest, root)
        _expect(7, state(work) == "done", "not done after valid acceptance")
        blocked, files, details = gate(work)
        _expect(7, not blocked, f"gate blocked: {details}")
        print(f"7. rev-1 accepts sha256={digest[:12]}.. -> pack: {state(work)}; gate -> pass: {details}")

        _CURRENT[0] = 8
        artifact.write_bytes(original + b"Edited after acceptance.\n")
        blocked, files, details = gate(work)
        _expect(8, blocked, "gate passed a changed file")
        print(f"8. {ARTIFACT} edited after acceptance -> gate blocked: {details}")
        artifact.write_bytes(original)
        blocked, files, details = gate(work)
        _expect(8, not blocked, f"gate blocked after restore: {details}")
        print(f"   restored -> gate pass; pack: {state(work)}")

        _CURRENT[0] = 9
        title = "Write the appendix"
        r = http.post("/tasks", json={"title": title, "description": "Add the appendix.", "role": "worker"})
        _expect(9, r.status_code == 201, f"POST /tasks returned {r.status_code}")
        second = r.json()["id"]
        r = http.post(f"/tasks/{second}/claim", params={"claimed_by_session": "wrk-1"})
        _expect(9, r.status_code == 200, f"claim returned {r.status_code}")
        ledger.acknowledge(second, "wrk-1")
        ledger.mark_unknown(second, "req-1", "worker session ended without a result")
        _expect(9, state(second) == "unknown", "not unknown")
        r = http.post("/tasks", json={"title": title, "description": "Add the appendix.", "role": "worker"})
        _expect(9, r.status_code == 400, f"replay under a new id returned {r.status_code}")
        print(f"9. task {second} acknowledged, marked unknown by req-1; POST /tasks same role and title "
              f"-> HTTP {r.status_code}: {r.json()['detail']}")
        ledger.reconcile(second, "req-1", "not_completed", "no result file in the workspace")
        _expect(9, state(second) == "acknowledged", "not back to acknowledged")
        print(f"   req-1 reconciles {second} as not_completed under its own id -> pack: {state(second)}")


def main() -> int:
    logging.getLogger("bernstein").setLevel(logging.ERROR)
    saved = os.environ.get(HOME_ENV)
    cwd = os.getcwd()
    try:
        with tempfile.TemporaryDirectory(prefix="role-pack-demo-") as tmp:
            # Bernstein's task store archives to .sdd/archive relative to the
            # current directory, so the demo runs from inside its workspace.
            os.chdir(tmp)
            try:
                _run(Path(tmp).resolve())
            finally:
                os.chdir(cwd)
    except StepFailed as exc:
        print(exc)
        return 1
    except Exception as exc:
        print(f"step {_CURRENT[0]} failed: {type(exc).__name__}: {exc}")
        return 1
    finally:
        if saved is None:
            os.environ.pop(HOME_ENV, None)
        else:
            os.environ[HOME_ENV] = saved
    print("all steps behaved as stated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
