"""Bernstein plugin entry point contributing org-kit governance to AGENTS.md."""

from __future__ import annotations

from pathlib import Path

from bernstein.core.knowledge.agents_md_generator import AgentsMdSection
from bernstein.plugins import hookimpl

from bernstein_org_kit.kit import role_rows, workflow_rows

CANONICAL_APPROVAL_GATES: tuple[str, ...] = (
    "money",
    "external-send",
    "public-post",
    "deletion",
    "credentials",
    "legal-commitment",
    "production-change",
    "personal-data",
)


class OrgKitPlugin:
    """Contributes an AGENTS.md governance section when org/ORG.md exists in the repo."""

    @hookimpl
    def provide_agents_md_section(self, repo_path: Path) -> AgentsMdSection | None:
        root = Path(repo_path)
        org_file = root / "org" / "ORG.md"
        if not org_file.is_file():
            return None

        roles = role_rows(root) if (root / "roles").is_dir() else []
        workflows = workflow_rows(root) if (root / "workflows").is_dir() else []

        active_gates = sorted({g for role in roles for g in role.approval_gates})
        gates_display = active_gates or list(CANONICAL_APPROVAL_GATES)
        roles_toml_present = (root / ".sdd" / "role-pack" / "roles.toml").is_file()

        lines: list[str] = [
            "This workspace uses `bernstein-org-kit` on top of `bernstein-role-pack`.",
            "Source charter: `org/ORG.md` and `org/HOW-WE-WORK.md`.",
            "",
            "### Governance rules",
            "- **Independent SHA-256 acceptance:** A producer never accepts its own artifact. An independent reviewer role accepts each reported file against its exact SHA-256 in the `bernstein-role-pack` receipt ledger.",
            "- **Fail-closed human approval gates:** Agents prepare drafts only. Never perform actions covered by `"
            + ", ".join(gates_display)
            + "` without explicit written human approval.",
            "- **Unknown attempt reconciliation:** Reconcile `UNKNOWN` attempts under their original task ID; never replay under a new ID.",
            "- **Role-pack policy:** `.sdd/role-pack/roles.toml` is "
            + ("present." if roles_toml_present else "not yet generated (run `bernstein-org-kit init`)."),
        ]

        if roles:
            lines.extend(
                [
                    "",
                    f"### Active roles ({len(roles)})",
                    "| Role | Reports to | Tier | Human gates |",
                    "|---|---|---|---|",
                ]
            )
            for role in roles:
                gates_cell = ", ".join(role.approval_gates) if role.approval_gates else "none"
                lines.append(
                    f"| `{role.role}` | `{role.reports_to}` | `{role.model_tier or '-'}` | {gates_cell} |"
                )

        if workflows:
            wf_names = ", ".join(f"`{wf.name}`" for wf in workflows)
            lines.extend(["", f"### Active workflows ({len(workflows)})", wf_names])

        return AgentsMdSection(
            key="org-kit-governance",
            title="Organisation and Governance (bernstein-org-kit)",
            body="\n".join(lines),
            kind="roles",
            target_globs=(),
            always_apply=True,
        )
