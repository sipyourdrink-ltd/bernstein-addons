from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bernstein_role_pack.config import ConfigError, load_config
from bernstein_role_pack.demo import EXAMPLE_ROLES

EXAMPLE_FILE = Path(__file__).resolve().parents[1] / "examples" / "roles.toml"


def _load(text: str):
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        (home / "roles.toml").write_text(text, encoding="ascii")
        return load_config(home)


class ConfigTests(unittest.TestCase):
    def test_example_file_matches_demo_text(self) -> None:
        self.assertEqual(EXAMPLE_FILE.read_text(encoding="ascii"), EXAMPLE_ROLES)

    def test_example_loads(self) -> None:
        config = _load(EXAMPLE_ROLES)
        self.assertEqual(config.role_of("wrk-1"), "worker")
        self.assertIsNone(config.role_of("nobody"))
        self.assertEqual(config.reports_to("worker"), "requester")
        self.assertIsNone(config.reports_to("requester"))
        self.assertEqual(config.chain("worker"), ["requester"])
        self.assertEqual(config.chain("requester"), [])
        self.assertTrue(config.may_review("reviewer", "worker"))
        self.assertFalse(config.may_review("worker", "worker"))
        self.assertFalse(config.may_review("requester", "worker"))
        self.assertEqual(len(config.sha256), 64)

    def test_absent_file_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_config(Path(tmp)))

    def test_chain_of_three(self) -> None:
        config = _load('[roles.a]\n[roles.b]\nreports_to = "a"\n[roles.c]\nreports_to = "b"\n')
        self.assertEqual(config.chain("c"), ["b", "a"])

    def assert_invalid(self, text: str, fragment: str) -> None:
        with self.assertRaises(ConfigError) as ctx:
            _load(text)
        self.assertIn(fragment, str(ctx.exception))

    def test_unknown_reports_to(self) -> None:
        self.assert_invalid('[roles.a]\n[roles.b]\nreports_to = "z"\n', "unknown role 'z'")

    def test_cycle(self) -> None:
        self.assert_invalid(
            '[roles.root]\n[roles.a]\nreports_to = "b"\n[roles.b]\nreports_to = "a"\n', "cycle"
        )

    def test_no_root(self) -> None:
        self.assert_invalid('[roles.a]\nreports_to = "b"\n[roles.b]\nreports_to = "a"\n', "no root")

    def test_reviews_itself(self) -> None:
        self.assert_invalid('[roles.a]\nreviews = ["a"]\n', "may not review itself")

    def test_reviews_unknown(self) -> None:
        self.assert_invalid('[roles.a]\nreviews = ["z"]\n', "reviews unknown role")

    def test_principal_unknown_role(self) -> None:
        self.assert_invalid('[roles.a]\n[principals]\n"p-1" = "z"\n', "unknown role 'z'")

    def test_unknown_zone(self) -> None:
        self.assert_invalid('[clock]\nzone = "Nowhere/Nothing"\n[roles.a]\n', "unknown zone")

    def test_unparseable(self) -> None:
        self.assert_invalid("[roles.a\n", "cannot parse")


if __name__ == "__main__":
    unittest.main()
