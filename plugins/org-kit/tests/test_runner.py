"""Tests for Bernstein adapter discovery, prompt building, gate enforcement, and mock execution."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bernstein_org_kit import OrgKitError
from bernstein_org_kit.kit import init_workspace
from bernstein_org_kit.runner import (
    GateBlockedError,
    build_stage_prompt,
    list_adapters,
    run_stage,
)


class RunnerTests(unittest.TestCase):
    def test_list_adapters_sees_live_bernstein_registry(self) -> None:
        adapters = list_adapters()
        names = {a.name for a in adapters}
        self.assertGreaterEqual(len(adapters), 50)
        for core in ("claude", "codex", "gemini"):
            self.assertIn(core, names)
        self.assertNotIn("mock", names)

        with_mock = list_adapters(include_mock=True)
        mock_row = next(a for a in with_mock if a.name == "mock")
        self.assertTrue(mock_row.available)

    def test_build_stage_prompt_includes_charter_and_predecessor_hashes(self) -> None:
        prompt = build_stage_prompt(
            "code-review-merge",
            "review-correctness",
            objective="Verify patch correctness and edge cases",
            predecessor_hashes={"draft-change": "a" * 64},
        )
        self.assertIn("code-review-merge", prompt)
        self.assertIn("review-correctness", prompt)
        self.assertIn("Verify patch correctness and edge cases", prompt)
        self.assertIn("a" * 64, prompt)
        self.assertIn("## Role Charter", prompt)
        self.assertIn("independent reviewer", prompt)

    def test_run_stage_blocks_missing_human_gates_and_supports_dry_run(self) -> None:
        # draft-change role (tech-automations) has gates: production-change, credentials, deletion
        with self.assertRaises(GateBlockedError) as ctx:
            run_stage(
                "code-review-merge",
                stage_id="draft-change",
                objective="Add health route",
                adapter="claude",
                dry_run=True,
            )
        self.assertEqual(
            set(ctx.exception.missing_gates),
            {"production-change", "credentials", "deletion"},
        )

        # Partial approval still fails closed.
        with self.assertRaises(GateBlockedError) as ctx2:
            run_stage(
                "code-review-merge",
                stage_id="draft-change",
                objective="Add health route",
                adapter="claude",
                approved_gates=["production-change"],
                dry_run=True,
            )
        self.assertEqual(set(ctx2.exception.missing_gates), {"credentials", "deletion"})

        # All required gates explicitly approved -> dry-run preview succeeds without spawning.
        res = run_stage(
            "code-review-merge",
            stage_id="draft-change",
            objective="Add health route",
            adapter="claude",
            approved_gates=["production-change", "credentials", "deletion"],
            dry_run=True,
        )
        self.assertTrue(res.dry_run)
        self.assertEqual(res.adapter, "claude")
        self.assertEqual(res.adapter_class, "ClaudeCodeAdapter")
        self.assertIsNone(res.pid)
        self.assertIsNone(res.log_path)
        self.assertIn("Add health route", res.prompt)

    def test_run_stage_unknown_adapter_fails(self) -> None:
        with self.assertRaises(OrgKitError):
            run_stage(
                "document-review",
                stage_id="collect-input",
                objective="Extract headings",
                adapter="not-a-real-adapter",
                dry_run=True,
            )

    def test_run_stage_execute_with_bernstein_mock_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            init_workspace(ws, preset="minimal")
            res = run_stage(
                "document-review",
                stage_id="collect-input",
                objective="Extract headings from README",
                adapter="mock",
                workspace=ws,
                dry_run=False,
                wait=True,
            )
            self.assertFalse(res.dry_run)
            self.assertEqual(res.adapter_class, "MockAgentAdapter")
            self.assertIsNotNone(res.pid)
            self.assertGreater(res.pid or 0, 0)
            self.assertEqual(res.returncode, 0)
            self.assertIsNotNone(res.log_path)
            assert res.log_path is not None
            self.assertTrue(Path(res.log_path).is_file())


if __name__ == "__main__":
    unittest.main()
