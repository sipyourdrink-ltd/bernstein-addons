"""Tests for the zero-config demo and complete CLI help and command behavior."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from bernstein_org_kit import cli


def _collect_missing_help(parser: argparse.ArgumentParser, trail: str = "bernstein-org-kit") -> list[str]:
    missing: list[str] = []
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        if isinstance(action, argparse._SubParsersAction):
            if not action.help:
                missing.append(f"{trail}: subparsers action missing help")
            choice_help = {ca.dest: ca.help for ca in action._choices_actions}
            for name, subparser in action.choices.items():
                if not choice_help.get(name):
                    missing.append(f"{trail} {name}: subcommand missing help")
                missing.extend(_collect_missing_help(subparser, f"{trail} {name}"))
            continue
        if not action.help or action.help is argparse.SUPPRESS:
            label = "/".join(action.option_strings) if action.option_strings else action.dest
            missing.append(f"{trail} [{label}]: argument missing help")
    return missing


class DemoAndCliTests(unittest.TestCase):
    def test_demo_exits_zero_and_leaves_cwd_clean(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            env = {k: v for k, v in os.environ.items() if k != "BERNSTEIN_ROLE_PACK_HOME"}
            proc = subprocess.run(
                [sys.executable, "-m", "bernstein_org_kit.demo"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("all steps behaved as stated", proc.stdout)
            self.assertIn("SelfReview", proc.stdout)
            self.assertIn("HashMismatch", proc.stdout)
            self.assertIn("gate -> pass", proc.stdout)
            self.assertIn("unblocks:", proc.stdout)
            self.assertEqual(os.listdir(cwd), [])

    def test_every_cli_subcommand_and_flag_has_help(self) -> None:
        missing = _collect_missing_help(cli._parser())
        self.assertEqual(missing, [], f"Missing CLI help strings: {missing}")

    def test_cli_init_catalog_compile_adapters_and_run(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "team"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(["init", str(ws)])
            self.assertEqual(rc, 0)
            self.assertIn("preset=minimal", buf.getvalue())

            # roles list & show
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(["roles", "--workspace", str(ws), "--json"])
            self.assertEqual(rc, 0)
            roles_data = json.loads(buf.getvalue())
            self.assertEqual(len(roles_data), 6)

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(["roles", "show", "ceo", "--workspace", str(ws)])
            self.assertEqual(rc, 0)
            self.assertIn("# CEO", buf.getvalue())

            # sops list & show
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(["sops", "--json"])
            self.assertEqual(rc, 0)
            self.assertEqual(len(json.loads(buf.getvalue())), 16)

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(["sops", "show", "SOP-OPS-01"])
            self.assertEqual(rc, 0)
            self.assertIn("Executive teammate", buf.getvalue())

            # scenarios list, filter & show
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(["scenarios", "--json"])
            self.assertEqual(rc, 0)
            self.assertEqual(len(json.loads(buf.getvalue())), 32)

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(["scenarios", "--department", "security", "--json"])
            self.assertEqual(rc, 0)
            sec_scenarios = json.loads(buf.getvalue())
            self.assertGreaterEqual(len(sec_scenarios), 4)

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(["scenarios", "show", "22-ai-governance-review"])
            self.assertEqual(rc, 0)
            self.assertIn("ai-governance-review", buf.getvalue())

            # domain preset init (secops)
            sec_ws = Path(td) / "secops-team"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(["init", str(sec_ws), "--preset", "secops"])
            self.assertEqual(rc, 0)
            self.assertIn("preset=secops", buf.getvalue())

            # workflows show & compile
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(["workflows", "show", "code-review-merge", "--workspace", str(ws)])
            self.assertEqual(rc, 0)
            self.assertIn("draft-change", buf.getvalue())

            plan_out = ws / "compiled.json"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(["workflows", "compile", "code-review-merge", "--workspace", str(ws), "--out", str(plan_out)])
            self.assertEqual(rc, 0)
            self.assertTrue(plan_out.is_file())
            compiled = json.loads(plan_out.read_text(encoding="utf-8"))
            self.assertEqual(compiled["workflow"], "code-review-merge")

            # adapters --json
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(["adapters", "--json"])
            self.assertEqual(rc, 0)
            adapters = json.loads(buf.getvalue())
            self.assertGreaterEqual(len(adapters), 50)

            # run gate refusal returns exit 1
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = cli.main(
                    [
                        "run",
                        "code-review-merge",
                        "--stage",
                        "draft-change",
                        "--objective",
                        "Ship feature",
                        "--workspace",
                        str(ws),
                    ]
                )
            self.assertEqual(rc, 1)
            self.assertIn("GateBlockedError", err.getvalue())

            # run dry-run with approvals returns exit 0
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(
                    [
                        "run",
                        "code-review-merge",
                        "--stage",
                        "draft-change",
                        "--objective",
                        "Ship feature",
                        "--adapter",
                        "claude",
                        "--workspace",
                        str(ws),
                        "--approve-gate",
                        "production-change",
                        "--approve-gate",
                        "credentials",
                        "--approve-gate",
                        "deletion",
                        "--json",
                    ]
                )
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["adapter"], "claude")

            # run execute with mock adapter
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(
                    [
                        "run",
                        "document-review",
                        "--stage",
                        "collect-input",
                        "--objective",
                        "Extract headings",
                        "--adapter",
                        "mock",
                        "--workspace",
                        str(ws),
                        "--execute",
                        "--json",
                    ]
                )
            self.assertEqual(rc, 0)
            exec_payload = json.loads(buf.getvalue())
            self.assertFalse(exec_payload["dry_run"])
            self.assertEqual(exec_payload["returncode"], 0)


if __name__ == "__main__":
    unittest.main()
