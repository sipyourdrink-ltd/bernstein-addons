"""Zero-config in-process end-to-end demo over Bernstein and bernstein-role-pack."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import sys
import tempfile
from pathlib import Path

from bernstein_role_pack.config import HOME_ENV, load_config
from bernstein_role_pack.gate import GATE_NAME
from bernstein_role_pack.ledger import HashMismatch, Ledger, SelfReview, file_sha256

from bernstein_org_kit.compiler import compile_workflow, ready_stages
from bernstein_org_kit.kit import init_workspace
from bernstein_org_kit.runner import list_adapters

ARTIFACT = "out/change.patch"


class StepFailed(Exception):
    """Raised when a demo verification assertion does not hold."""


_CURRENT = [0]


def _expect(step: int, condition: bool, detail: str) -> None:
    if not condition:
        raise StepFailed(f"step {step} failed: {detail}")


def _run(root: Path) -> None:
    from fastapi.testclient import TestClient

    from bernstein.core.persistence.workspace import grant_workspace_trust
    from bernstein.core.quality.gate_pipeline import GatePipelineStep
    from bernstein.core.quality.gate_runner import GateRunner
    from bernstein.core.quality.quality_gates import QualityGatesConfig
    from bernstein.core.server.server_app import create_app

    # 1. Initialize minimal org-kit workspace and validate generated roles.toml.
    _CURRENT[0] = 1
    init_res = init_workspace(root, preset="minimal")
    home = root / ".sdd" / "role-pack"
    config = load_config(home)
    _expect(1, config is not None, "roles.toml was not loaded by bernstein-role-pack")
    assert config is not None
    _expect(1, init_res.role_count == 6, f"expected 6 minimal roles, got {init_res.role_count}")
    _expect(1, init_res.workflow_count == 3, f"expected 3 minimal workflows, got {init_res.workflow_count}")
    print(
        f"1. init minimal -> {init_res.role_count} roles, {init_res.workflow_count} workflows; "
        f"roles.toml valid ({len(config.roles)} roles, {len(config.principals)} principals, "
        f"policy sha256={config.sha256[:12]}..)"
    )

    # 2. Compile code-review-merge workflow DAG deterministically.
    _CURRENT[0] = 2
    plan = compile_workflow("code-review-merge", workspace=root)
    initial_ready = ready_stages(plan, set())
    _expect(2, initial_ready == ["draft-change"], f"expected initial ready ['draft-change'], got {initial_ready}")
    wave_str = " -> ".join(", ".join(wave) for wave in plan["waves"])
    print(f"2. compile code-review-merge -> plan_hash={plan['plan_hash'][:12]}.., order: {wave_str}")

    # 3. Inspect live Bernstein adapter registry.
    _CURRENT[0] = 3
    adapters = list_adapters()
    adapter_names = {a.name for a in adapters}
    highlights = [name for name in ("claude", "codex", "gemini") if name in adapter_names]
    _expect(3, len(highlights) == 3, f"expected claude/codex/gemini adapters, got {highlights}")
    print(f"3. bernstein adapters -> {len(adapters)} registered ({', '.join(highlights)}, ...)")

    # 4. Boot real Bernstein FastAPI app + RolePackPlugin + RolePackAcceptanceGate in-process.
    _CURRENT[0] = 4
    os.environ[HOME_ENV] = str(home)
    (root / ".sdd" / "runtime").mkdir(parents=True, exist_ok=True)
    grant_workspace_trust(root, granted_by="org-kit-demo")
    ledger = Ledger(home, config)
    runner = GateRunner(
        QualityGatesConfig(pipeline=[GatePipelineStep(name=GATE_NAME, required=True)]),
        root,
    )
    token = secrets.token_hex(16)
    app = create_app(jsonl_path=root / ".sdd" / "runtime" / "tasks.jsonl", auth_token=token)

    def run_gate(task_id: str) -> tuple[bool, list[str], str]:
        task = app.state.store.get_task(task_id)
        report = asyncio.run(runner.run_all(task, root))
        result = next(r for r in report.results if r.name == GATE_NAME)
        return result.blocked, report.changed_files, result.details

    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as http:
        stage1 = plan["stages"][0]
        r = http.post(
            "/tasks",
            json={
                "title": f"{plan['workflow']}:{stage1['id']}",
                "description": f"Stage {stage1['id']} for plan {plan['plan_hash'][:12]}",
                "role": stage1["role"],
                "owned_files": [ARTIFACT],
            },
        )
        _expect(4, r.status_code == 201, f"POST /tasks returned {r.status_code}: {r.text}")
        task_id = r.json()["id"]
        _expect(4, ledger.state(task_id).state == "delivered", "RolePackPlugin hook did not write delivered")

        producer = f"{stage1['role']}-1"
        r = http.post(f"/tasks/{task_id}/claim", params={"claimed_by_session": producer})
        _expect(4, r.status_code == 200, f"claim returned {r.status_code}")
        ledger.acknowledge(task_id, producer)

        artifact = root / ARTIFACT
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"diff --git a/app.py b/app.py\n+def health() -> str:\n+    return 'ok'\n")
        digest = file_sha256(artifact)
        ledger.report(task_id, producer, [ARTIFACT], root)

        r = http.post(f"/tasks/{task_id}/complete", json={"result_summary": "draft patch written"})
        _expect(4, r.status_code == 200, f"complete returned {r.status_code}")
        ts = ledger.state(task_id)
        _expect(4, ts.bernstein_completed and ts.state == "reported", "complete should leave pack state reported")
        print(
            f"4. stage 1 [{stage1['id']}] role={stage1['role']} -> HTTP 201 task {task_id}; "
            f"{producer} reports {ARTIFACT} sha256={digest[:12]}..; pack: {ts.state}"
        )

        # 5. Gate blocks before independent acceptance.
        _CURRENT[0] = 5
        blocked, files, details = run_gate(task_id)
        _expect(5, blocked and files == [ARTIFACT], f"expected gate block over {[ARTIFACT]}, got {blocked} {files}")
        print(f"5. gate before acceptance -> blocked: {details}")

        # 6. Producer self-acceptance is refused.
        _CURRENT[0] = 6
        try:
            ledger.accept(task_id, producer, ARTIFACT, digest, root)
            _expect(6, False, "self-acceptance was not refused")
        except SelfReview as exc:
            print(f"6. self-accept by {producer} -> refused (SelfReview): {exc}")

        # 7. Independent reviewer with wrong SHA-256 is refused.
        _CURRENT[0] = 7
        reviewer = "rnd-1"
        wrong_sha = "0" * 64
        try:
            ledger.accept(task_id, reviewer, ARTIFACT, wrong_sha, root)
            _expect(7, False, "wrong SHA-256 acceptance was not refused")
        except HashMismatch as exc:
            print(f"7. wrong-sha256 accept by {reviewer} ({wrong_sha[:12]}..) -> refused (HashMismatch): {exc}")

        # 8. Independent reviewer with exact SHA-256 succeeds and passes the gate.
        _CURRENT[0] = 8
        ledger.accept(task_id, reviewer, ARTIFACT, digest, root)
        _expect(8, ledger.state(task_id).state == "done", "task did not reach done after valid acceptance")
        blocked, files, details = run_gate(task_id)
        _expect(8, not blocked, f"gate remained blocked after acceptance: {details}")
        print(f"8. independent accept by {reviewer} sha256={digest[:12]}.. -> pack: done; gate -> pass: {details}")

        # 9. Acceptance unblocks Stage 2 in the compiled workflow DAG.
        _CURRENT[0] = 9
        accepted_stages = {stage1["id"]}
        unblocked = ready_stages(plan, accepted_stages)
        _expect(
            9,
            unblocked == ["review-correctness", "review-security", "run-tests"],
            f"unexpected unblocked stages: {unblocked}",
        )
        print(f"9. dag advance -> accepted={sorted(accepted_stages)} unblocks: {unblocked}")


def main() -> int:
    logging.getLogger("bernstein").setLevel(logging.ERROR)
    saved_home = os.environ.get(HOME_ENV)
    cwd = os.getcwd()
    try:
        with tempfile.TemporaryDirectory(prefix="org-kit-demo-") as tmp:
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
        if saved_home is None:
            os.environ.pop(HOME_ENV, None)
        else:
            os.environ[HOME_ENV] = saved_home
    print("all steps behaved as stated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
