"""Tests for the deterministic pure-stdlib workflow DAG compiler."""

from __future__ import annotations

import json
import unittest

from bernstein_org_kit.compiler import (
    PLAN_SCHEMA,
    CompileError,
    compile_workflow,
    compile_workflow_data,
    ready_stages,
)
from bernstein_org_kit.kit import show_workflow, workflow_rows


class CompilerTests(unittest.TestCase):
    def test_all_packaged_workflows_compile_deterministically(self) -> None:
        for summary in workflow_rows():
            plan1 = compile_workflow(summary.name)
            plan2 = compile_workflow(summary.name)
            self.assertEqual(plan1["schema"], PLAN_SCHEMA)
            self.assertEqual(plan1, plan2)
            self.assertEqual(len(plan1["plan_hash"]), 64)
            self.assertEqual(len(plan1["workflow_sha256"]), 64)

            # Reversed node key ordering produces the exact same plan_hash.
            raw = show_workflow(summary.name).data
            reversed_data = {
                "completion_nodes": list(reversed(raw["completion_nodes"])),
                "nodes": dict(reversed(list(raw["nodes"].items()))),
                "name": raw["name"],
            }
            plan3 = compile_workflow_data(reversed_data, roles_by_name={})
            plan_no_roles = compile_workflow_data(raw, roles_by_name={})
            self.assertEqual(plan3["plan_hash"], plan_no_roles["plan_hash"])

    def test_code_review_merge_waves_metadata_and_ready_stages(self) -> None:
        plan = compile_workflow("code-review-merge")
        self.assertEqual(
            plan["waves"],
            [
                ["draft-change"],
                ["review-correctness", "review-security", "run-tests"],
                ["merge"],
            ],
        )
        by_id = {s["id"]: s for s in plan["stages"]}
        self.assertTrue(by_id["draft-change"]["role_known"])
        self.assertEqual(by_id["draft-change"]["reports_to"], "cto-tech")
        self.assertEqual(by_id["draft-change"]["model_tier"], "tier-2-coding")
        self.assertIn("production-change", by_id["draft-change"]["approval_gates"])

        self.assertEqual(ready_stages(plan, set()), ["draft-change"])
        self.assertEqual(
            ready_stages(plan, {"draft-change"}),
            ["review-correctness", "review-security", "run-tests"],
        )
        self.assertEqual(
            ready_stages(plan, {"draft-change", "review-correctness", "review-security", "run-tests"}),
            ["merge"],
        )
        self.assertEqual(
            ready_stages(plan, set(plan["execution_order"])),
            [],
        )

    def test_document_review_uses_known_roles_and_metadata(self) -> None:
        plan = compile_workflow("document-review")
        self.assertEqual(plan["execution_order"], ["collect-input", "review-packet"])
        by_id = {st["id"]: st for st in plan["stages"]}
        self.assertEqual(by_id["collect-input"]["role"], "tech-automations")
        self.assertEqual(by_id["review-packet"]["role"], "technical-supervisor")
        for st in plan["stages"]:
            self.assertTrue(st["role_known"])
            self.assertEqual(st["reports_to"], "cto-tech")
            self.assertTrue(st["approval_gates"])

    def test_compile_workflow_rejects_stage_role_without_role_file(self) -> None:
        import tempfile
        from pathlib import Path

        bad_wf = {
            "name": "dangling-role-workflow",
            "completion_nodes": ["finish"],
            "nodes": {
                "finish": {
                    "role": "nonexistent-stage-role",
                    "depends_on": [],
                    "timeout_seconds": 30,
                },
            },
        }
        with self.assertRaises(CompileError) as ctx:
            compile_workflow_data(bad_wf, roles_by_name={}, require_known_roles=True)
        self.assertIn("nonexistent-stage-role", str(ctx.exception))
        self.assertIn("no role file found", str(ctx.exception))

        with tempfile.TemporaryDirectory() as td:
            wf_path = Path(td) / "dangling-role-workflow.json"
            wf_path.write_text(json.dumps(bad_wf), encoding="utf-8")
            with self.assertRaises(CompileError) as ctx2:
                compile_workflow(wf_path)
            self.assertIn("nonexistent-stage-role", str(ctx2.exception))
            self.assertIn("finish", str(ctx2.exception))

    def test_rejects_cycles_and_invalid_specs(self) -> None:
        cycle = {
            "name": "bad-cycle",
            "completion_nodes": ["b"],
            "nodes": {
                "a": {"role": "rnd", "depends_on": ["b"], "timeout_seconds": 60},
                "b": {"role": "rnd", "depends_on": ["a"], "timeout_seconds": 60},
            },
        }
        with self.assertRaises(CompileError):
            compile_workflow_data(cycle)

        self_dep = {
            "name": "self-dep",
            "completion_nodes": ["a"],
            "nodes": {"a": {"role": "rnd", "depends_on": ["a"], "timeout_seconds": 60}},
        }
        with self.assertRaises(CompileError):
            compile_workflow_data(self_dep)

        bad_timeout = {
            "name": "bad-timeout",
            "completion_nodes": ["a"],
            "nodes": {"a": {"role": "rnd", "depends_on": [], "timeout_seconds": True}},
        }
        with self.assertRaises(CompileError):
            compile_workflow_data(bad_timeout)

        unknown_dep = {
            "name": "missing-dep",
            "completion_nodes": ["a"],
            "nodes": {"a": {"role": "rnd", "depends_on": ["ghost"], "timeout_seconds": 60}},
        }
        with self.assertRaises(CompileError):
            compile_workflow_data(unknown_dep)

        bad_comp = {
            "name": "missing-comp",
            "completion_nodes": ["ghost"],
            "nodes": {"a": {"role": "rnd", "depends_on": [], "timeout_seconds": 60}},
        }
        with self.assertRaises(CompileError):
            compile_workflow_data(bad_comp)

        # Ensure output is valid strict JSON.
        encoded = json.dumps(compile_workflow("release-pipeline"))
        self.assertIn("release-pipeline", encoded)


if __name__ == "__main__":
    unittest.main()
