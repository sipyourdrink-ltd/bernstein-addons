from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest


class DemoTests(unittest.TestCase):
    def test_demo_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            proc = subprocess.run(
                [sys.executable, "-m", "bernstein_role_pack.demo"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=300,
                env={k: v for k, v in os.environ.items() if k != "BERNSTEIN_ROLE_PACK_HOME"},
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("all steps behaved as stated", proc.stdout)
            self.assertEqual(os.listdir(cwd), [])


if __name__ == "__main__":
    unittest.main()
