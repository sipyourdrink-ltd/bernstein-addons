import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bernstein_sandcastle import adapter
from bernstein_sandcastle.adapter import Hold, run_attempt


class AttemptContract(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        self.git("init", "--initial-branch=main")
        for message in ("Initial", "Base"):
            self.git("commit", "--allow-empty", "-m", message)
        self.base = self.git("rev-parse", "HEAD")
        self.target = "refs/heads/main"

        runtime = self.root / "runtime"
        runtime.mkdir()
        shutil.copyfile(adapter.RUNTIME / "runner.mjs", runtime / "runner.mjs")
        fake_package = runtime / "node_modules" / "bernstein-sandcastle-api"
        fake_package.mkdir(parents=True)
        (fake_package / "package.json").write_text('{"exports":"./stub.mjs"}')
        shutil.copyfile(Path(__file__).with_name("stub.mjs"), fake_package / "stub.mjs")
        self.enterContext(patch.object(adapter, "RUNTIME", runtime))

        self.config = self.root / "config.mjs"
        self.config.write_text("export default {agent: {}, sandbox: {}, output: {maxRetries: 42}}")
        self.invocations = self.root / "invocations"
        self.scenario = self.root / "scenario.json"
        self.enterContext(patch.dict(os.environ, {"TEST_SCENARIO": str(self.scenario)}))
        self.evidence = self.root / "evidence"

    def git(self, *args, repo=None):
        return subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
             "-C", str(repo or self.source), *args],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def attempt(self, mode="ok", attempt_id="attempt-1", base=None, timeout=10):
        self.scenario.write_text(json.dumps({
            "mode": mode, "invocations": str(self.invocations), "source": str(self.source),
        }))
        receipt = run_attempt(
            repo_dir=self.source, evidence_dir=self.evidence, task_id="task-1",
            attempt_id=attempt_id, base_sha=self.base if base is None else base,
            target_ref=self.target, config=self.config, prompt="task input", timeout=timeout,
        )
        attempt_dir = Path(receipt["repository"]).parent
        self.assertEqual(json.loads((attempt_dir / "evidence.json").read_bytes()), receipt)
        self.assertEqual(list(attempt_dir.glob("*.json")), [attempt_dir / "evidence.json"])
        return receipt

    def test_verified_receipt_and_one_call_per_attempt(self):
        first = self.attempt()
        second = self.attempt(attempt_id="attempt-2")
        self.assertNotEqual(first["branch"], second["branch"])
        self.assertNotEqual(first["repository"], second["repository"])
        self.assertEqual(self.invocations.read_text().splitlines(), [first["branch"], second["branch"]])
        self.assertEqual(set(first), {
            "schema_version", "task_id", "attempt_id", "status", "reason", "repository",
            "base_sha", "target_ref", "branch", "tip_sha", "commits", "log_path",
            "log_sha256", "output",
        })
        self.assertEqual(first["schema_version"], 1)
        self.assertEqual((first["task_id"], first["attempt_id"]), ("task-1", "attempt-1"))
        self.assertEqual(first["status"], "verified")
        self.assertIsNone(first["reason"])
        self.assertEqual(first["base_sha"], self.base)
        self.assertEqual(first["target_ref"], self.target)
        self.assertEqual(first["output"], {"summary": "from stub"})
        self.assertEqual(first["log_sha256"], hashlib.sha256(b"stub run log\n").hexdigest())
        self.assertEqual(Path(first["log_path"]).read_text(), "stub run log\n")
        self.assertEqual(first["tip_sha"], first["commits"][-1]["sha"])
        self.assertEqual(len(first["commits"]), 2)

    def test_attempt_cannot_be_replayed(self):
        self.attempt()
        with self.assertRaisesRegex(Hold, "attempt_already_reserved"):
            self.attempt()
        self.assertEqual(len(self.invocations.read_text().splitlines()), 1)

    def test_mismatches_and_failures_hold(self):
        cases = {
            "branch": "branch_mismatch", "log": "log_mismatch", "commits": "commits_mismatch",
            "empty": "empty_commits", "target": "target_ref_changed",
            "source-target": "target_ref_changed", "error": "subprocess_failed",
        }
        for mode, reason in cases.items():
            with self.subTest(mode=mode):
                self.git("update-ref", self.target, self.base)
                receipt = self.attempt(mode, attempt_id=mode)
                self.assertEqual(receipt["status"], "held")
                self.assertEqual(receipt["reason"], reason)
                self.assertIsNone(receipt["tip_sha"])
                self.assertEqual(receipt["commits"], [])
                self.assertNotIn("output", receipt)
                self.assertIsNotNone(receipt["log_sha256"])

    def test_bad_base_prevents_invocation(self):
        receipt = self.attempt(base=self.git("rev-parse", "HEAD^"))
        self.assertEqual(receipt["reason"], "base_mismatch")
        self.assertFalse(self.invocations.exists())

    def test_configured_hooks_are_rejected_before_invocation(self):
        for idx, value in enumerate(("{host: {onSandboxReady: ['never']}}", "undefined", "{}")):
            with self.subTest(value=value):
                self.config.write_text(f"export default {{agent: {{}}, sandbox: {{}}, hooks: {value}}}")
                receipt = self.attempt(attempt_id=str(idx))
                self.assertEqual(receipt["status"], "held")
                self.assertEqual(receipt["reason"], "subprocess_failed")
                self.assertFalse(self.invocations.exists())

    def test_timeout_is_held_without_replay(self):
        receipt = self.attempt("timeout", timeout=1)
        self.assertEqual(receipt["status"], "held")
        self.assertEqual(receipt["reason"], "timeout")
        with self.assertRaisesRegex(Hold, "attempt_already_reserved"):
            self.attempt()

    def test_adapter_never_invokes_merge_and_leaves_targets_at_base(self):
        with patch.object(adapter, "_git", wraps=adapter._git) as git:
            receipt = self.attempt()
        self.assertEqual(receipt["status"], "verified")
        self.assertFalse(any(call.args[1] in {"merge", "cherry-pick", "rebase"} for call in git.call_args_list))
        self.assertEqual(self.git("rev-parse", self.target), self.base)
        copy = Path(receipt["repository"])
        self.assertEqual(self.git("rev-parse", self.target, repo=copy), self.base)
        self.assertEqual(self.git("rev-parse", receipt["branch"], repo=copy), receipt["tip_sha"])


if __name__ == "__main__":
    unittest.main()
