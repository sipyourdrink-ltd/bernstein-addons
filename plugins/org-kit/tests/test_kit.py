"""Tests for starter kit catalog, workspace init presets, and roles.toml generation."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from bernstein_role_pack.config import load_config

from bernstein_org_kit import OrgKitError
from bernstein_org_kit.kit import (
    DEFAULT_PRESET,
    KIT_ROOT,
    MINIMAL_ROLES,
    MINIMAL_WORKFLOWS,
    PRESETS,
    init_workspace,
    role_rows,
    show_role,
    show_sop,
    show_workflow,
    sop_rows,
    workflow_rows,
)


class KitCatalogTests(unittest.TestCase):
    def test_presets_and_defaults(self) -> None:
        self.assertEqual(PRESETS, ("minimal", "full"))
        self.assertEqual(DEFAULT_PRESET, "minimal")

    def test_packaged_kit_counts_and_lookups(self) -> None:
        roles = role_rows()
        sops = sop_rows()
        workflows = workflow_rows()
        self.assertEqual(len(roles), 24)
        self.assertEqual(len(sops), 7)
        self.assertEqual(len(workflows), 11)

        ceo = show_role("ceo")
        self.assertEqual(ceo.role, "ceo")
        self.assertEqual(ceo.reports_to, "owner")
        legacy_word = "gov" + "ernor"
        self.assertNotIn(legacy_word, ceo.title.lower())
        with self.assertRaises(OrgKitError):
            show_role("ceo-" + legacy_word)

        sop = show_sop("SOP-OPS-01")
        self.assertEqual(sop.owner_role, "ceo")
        self.assertEqual(show_sop("sop-ops-01").id, "SOP-OPS-01")
        with self.assertRaises(OrgKitError):
            show_sop("SOP-NOPE-99")

        wf = show_workflow("code-review-merge")
        self.assertEqual(wf.name, "code-review-merge")
        self.assertIn("draft-change", wf.data["nodes"])
        with self.assertRaises(OrgKitError):
            show_workflow("does-not-exist")

    def test_packaged_kit_has_no_legacy_or_private_leaks(self) -> None:
        forbidden = (
            "gov" + "ernor",
            "org" + "anico",
            "company-" + "task-server",
            "dispatch-" + "ctl",
            "/Use" + "rs/",
        )
        for path in KIT_ROOT.rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            lower = text.lower()
            for needle in forbidden:
                self.assertNotIn(needle.lower(), lower, f"{needle!r} leaked in {path}")
            mode = stat.S_IMODE(os.stat(path).st_mode)
            self.assertNotEqual(mode, 0o600, f"file {path} has 0600 permissions")

    def test_init_minimal_default_and_roles_toml_valid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            res = init_workspace(root)
            self.assertEqual(res.preset, "minimal")
            self.assertEqual(res.role_count, len(MINIMAL_ROLES))
            self.assertEqual(res.workflow_count, len(MINIMAL_WORKFLOWS))
            self.assertEqual({r.role for r in role_rows(root)}, set(MINIMAL_ROLES))
            self.assertEqual(
                {w.path.name for w in workflow_rows(root)},
                set(MINIMAL_WORKFLOWS),
            )
            self.assertTrue((root / "org" / "ORG.md").is_file())
            self.assertTrue((root / "org" / "HOW-WE-WORK.md").is_file())
            self.assertTrue((root / "org" / "routing-matrix.example.yaml").is_file())
            self.assertTrue((root / "tasks" / "review-demo.json").is_file())
            self.assertFalse((root / "sops").exists())

            cfg = load_config(root / ".sdd" / "role-pack")
            self.assertIsNotNone(cfg)
            assert cfg is not None
            self.assertEqual(len(cfg.roles), len(MINIMAL_ROLES) + 1)
            self.assertEqual(len(cfg.principals), len(MINIMAL_ROLES) + 1)
            self.assertTrue(cfg.may_review("rnd", "tech-automations"))
            self.assertTrue(cfg.may_review("security-review", "tech-automations"))
            self.assertTrue(cfg.may_review("technical-supervisor", "rnd"))
            self.assertFalse(cfg.may_review("tech-automations", "tech-automations"))

            for p in root.rglob("*"):
                if p.is_file():
                    self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o644)

            # Second init without force skips existing files.
            res2 = init_workspace(root)
            self.assertEqual(res2.written, ())
            self.assertGreater(len(res2.skipped), 0)

            # With force=True overwrites cleanly.
            res3 = init_workspace(root, force=True)
            self.assertEqual(res3.skipped, ())
            self.assertGreater(len(res3.written), 0)

    def test_init_full_preset_and_roles_toml_valid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            res = init_workspace(root, preset="full")
            self.assertEqual(res.preset, "full")
            self.assertEqual(res.role_count, 24)
            self.assertEqual(res.workflow_count, 11)
            self.assertEqual(len(sop_rows(root)), 7)
            self.assertEqual(len(list((root / "templates").iterdir())), 6)
            self.assertTrue((root / "scenarios.json").is_file())

            cfg = load_config(root / ".sdd" / "role-pack")
            self.assertIsNotNone(cfg)
            assert cfg is not None
            self.assertEqual(len(cfg.roles), 25)  # 24 kit roles + owner
            self.assertEqual(len(cfg.principals), 25)
            for name in cfg.roles:
                self.assertFalse(cfg.may_review(name, name))
                if name != "owner":
                    self.assertEqual(cfg.chain(name)[-1], "owner")

    def test_build_roles_toml_rejects_cycles_and_dangling_managers(self) -> None:
        from dataclasses import dataclass

        from bernstein_org_kit.roles_toml import build_roles_toml

        @dataclass(frozen=True)
        class _FakeRole:
            role: str
            reports_to: str
            approval_gates: tuple[str, ...] = ()

        with self.assertRaises(OrgKitError):
            build_roles_toml([_FakeRole("worker", "missing-manager")])
        with self.assertRaises(OrgKitError):
            build_roles_toml([_FakeRole("a", "b"), _FakeRole("b", "a")])
        with self.assertRaises(OrgKitError):
            build_roles_toml([_FakeRole("owner", "owner")])


if __name__ == "__main__":
    unittest.main()
