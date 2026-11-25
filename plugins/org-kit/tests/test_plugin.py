"""Tests for OrgKitPlugin AGENTS.md hook and Bernstein plugin entry point."""

from __future__ import annotations

import importlib.metadata
import tempfile
import unittest
from pathlib import Path

from bernstein.core.knowledge.agents_md_generator import AgentsMdSection, render_canonical

from bernstein_org_kit.kit import init_workspace
from bernstein_org_kit.plugin import OrgKitPlugin


class PluginTests(unittest.TestCase):
    def test_returns_none_without_org_md(self) -> None:
        plugin = OrgKitPlugin()
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(plugin.provide_agents_md_section(Path(td)))

    def test_provides_section_for_initialized_workspace(self) -> None:
        plugin = OrgKitPlugin()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            init_workspace(root, preset="minimal")
            section = plugin.provide_agents_md_section(root)
            self.assertIsInstance(section, AgentsMdSection)
            assert section is not None
            self.assertEqual(section.key, "org-kit-governance")
            self.assertEqual(section.kind, "roles")
            self.assertIn("tech-automations", section.body)
            self.assertIn("Independent SHA-256 acceptance", section.body)
            self.assertIn("production-change", section.body)
            self.assertIn(".sdd/role-pack/roles.toml` is present", section.body)

            rendered = render_canonical([section], repo_name="demo-repo")
            self.assertIn("## Organisation and Governance (bernstein-org-kit)", rendered)
            self.assertIn("code-review-merge", rendered)

    def test_entry_point_registered(self) -> None:
        eps = importlib.metadata.entry_points(group="bernstein.plugins")
        matches = [ep for ep in eps if ep.name == "org_kit"]
        self.assertEqual(len(matches), 1, f"expected org_kit entry point, got {list(eps)}")
        self.assertIs(matches[0].load(), OrgKitPlugin)


if __name__ == "__main__":
    unittest.main()
