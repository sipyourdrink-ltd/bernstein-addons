"""Tests for starter kit catalog, workspace init presets, and roles.toml generation."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from bernstein_role_pack.config import load_config

from bernstein_org_kit import OrgKitError
from bernstein_org_kit.compiler import compile_workflow
from bernstein_org_kit.kit import (
    DEFAULT_PRESET,
    KIT_ROOT,
    MINIMAL_ROLES,
    MINIMAL_WORKFLOWS,
    PRESET_SPECS,
    PRESETS,
    init_workspace,
    role_rows,
    scenario_rows,
    show_role,
    show_scenario,
    show_sop,
    show_workflow,
    sop_rows,
    workflow_rows,
)
from bernstein_org_kit.plugin import CANONICAL_APPROVAL_GATES


class KitCatalogTests(unittest.TestCase):
    def test_presets_and_defaults(self) -> None:
        self.assertEqual(
            PRESETS,
            ("minimal", "engineering", "secops", "product", "compliance", "full"),
        )
        self.assertEqual(DEFAULT_PRESET, "minimal")

    def test_packaged_kit_counts_and_lookups(self) -> None:
        roles = role_rows()
        sops = sop_rows()
        workflows = workflow_rows()
        scenarios = scenario_rows()
        self.assertEqual(len(roles), 36)
        self.assertEqual(len(sops), 16)
        self.assertEqual(len(workflows), 20)
        self.assertEqual(len(scenarios), 32)

        ceo = show_role("ceo")
        self.assertEqual(ceo.role, "ceo")
        self.assertEqual(ceo.reports_to, "owner")
        legacy_word = "gov" + "ernor"
        self.assertNotIn(legacy_word, ceo.title.lower())
        with self.assertRaises(OrgKitError):
            show_role("ceo-" + legacy_word)

        sop = show_sop("SOP-OPS-01")
        self.assertEqual(sop.owner_role, "ceo")
        self.assertEqual(show_sop("sop-gov-01").id, "SOP-GOV-01")
        with self.assertRaises(OrgKitError):
            show_sop("SOP-NOPE-99")

        sc = show_scenario("22-ai-governance-review")
        self.assertEqual(sc.workflow, "ai-governance-review")
        self.assertEqual(sc.sop, "SOP-GOV-01")
        self.assertGreaterEqual(len(scenario_rows(department="security")), 4)
        with self.assertRaises(OrgKitError):
            show_scenario("999-missing")

        wf = show_workflow("code-review-merge")
        self.assertEqual(wf.name, "code-review-merge")
        self.assertIn("draft-change", wf.data["nodes"])
        with self.assertRaises(OrgKitError):
            show_workflow("does-not-exist")

    def test_span_of_control_and_charter_completeness(self) -> None:
        roles = role_rows()
        by_slug = {r.role: r for r in roles}
        direct_reports = Counter(r.reports_to for r in roles)
        for manager, count in direct_reports.items():
            self.assertLessEqual(
                count,
                7,
                f"Manager {manager!r} has {count} direct reports (ORG.md max is 7)",
            )
        allowed_gates = set(CANONICAL_APPROVAL_GATES)
        for role in roles:
            self.assertTrue(set(role.approval_gates).issubset(allowed_gates), role.role)
            self.assertIn("## Mission", role.body, role.role)
            self.assertIn("## Responsibilities", role.body, role.role)
            self.assertIn("## Does not do", role.body, role.role)

        wf_names = {w.name for w in workflow_rows()}
        sop_ids = {s.id for s in sop_rows()}
        for sop in sop_rows():
            self.assertIn(sop.owner_role, by_slug, sop.id)
            for part in sop.participants:
                self.assertIn(part, by_slug, f"{sop.id} unknown participant {part}")
            if sop.workflow != "none":
                self.assertIn(sop.workflow, wf_names, f"{sop.id} unknown workflow {sop.workflow}")

        for sc in scenario_rows():
            self.assertIn(sc.workflow, wf_names, sc.id)
            if sc.sop is not None:
                self.assertIn(sc.sop, sop_ids, sc.id)

        for task_file in sorted((KIT_ROOT / "tasks").glob("*.json")):
            task_data = json.loads(task_file.read_text(encoding="utf-8"))
            self.assertIn(task_data["workflow"], wf_names, task_file.name)

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

    def test_all_domain_presets_initialize_and_compile_cleanly(self) -> None:
        for preset_name, spec in PRESET_SPECS.items():
            if preset_name == "minimal":
                continue
            with self.subTest(preset=preset_name), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                res = init_workspace(root, preset=preset_name)
                self.assertEqual(res.preset, preset_name)
                self.assertEqual(res.role_count, len(spec.roles))
                self.assertEqual(res.workflow_count, len(spec.workflows))
                self.assertLess(res.role_count, 36)
                self.assertEqual(len(sop_rows(root)), len(spec.sops))

                cfg = load_config(root / ".sdd" / "role-pack")
                self.assertIsNotNone(cfg)
                assert cfg is not None
                active_set = set(spec.roles)
                for name in cfg.roles:
                    self.assertFalse(cfg.may_review(name, name))
                    if name != "owner":
                        self.assertEqual(cfg.chain(name)[-1], "owner")

                for wf_summary in workflow_rows(root):
                    plan = compile_workflow(wf_summary.path, workspace=root)
                    if wf_summary.name != "document-review":
                        for stage in plan["stages"]:
                            self.assertIn(
                                stage["role"],
                                active_set,
                                f"Preset {preset_name} workflow {wf_summary.name} uses excluded role {stage['role']}",
                            )

    def test_init_full_preset_and_roles_toml_valid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            res = init_workspace(root, preset="full")
            self.assertEqual(res.preset, "full")
            self.assertEqual(res.role_count, 36)
            self.assertEqual(res.workflow_count, 20)
            self.assertEqual(len(sop_rows(root)), 16)
            self.assertEqual(len(list((root / "templates").iterdir())), 9)
            self.assertEqual(len(list((root / "tasks").glob("*.json"))), 6)
            self.assertTrue((root / "scenarios.json").is_file())

            cfg = load_config(root / ".sdd" / "role-pack")
            self.assertIsNotNone(cfg)
            assert cfg is not None
            self.assertEqual(len(cfg.roles), 37)  # 36 kit roles + owner
            self.assertEqual(len(cfg.principals), 37)
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
