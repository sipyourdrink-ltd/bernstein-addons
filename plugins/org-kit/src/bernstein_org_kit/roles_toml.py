"""Compile active org-kit role frontmatter into a validated bernstein-role-pack roles.toml."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from bernstein_role_pack.config import Config, ConfigError, load_config

from bernstein_org_kit import OrgKitError

ROOT_ROLE = "owner"
_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Independent peer-review grants grounded in org/ORG.md and role charters.
# Filtered to active roles and stripped of self-review at compile time.
PEER_REVIEWS: dict[str, tuple[str, ...]] = {
    "advisor": (
        "ceo",
        "cto-tech",
        "cfo-finance",
        "legal-director",
        "vp-sales",
        "hr-director",
    ),
    "ceo": (
        "cto-tech",
        "cfo-finance",
        "legal-director",
        "vp-sales",
        "hr-director",
        "advisor",
    ),
    "security-review": (
        "tech-automations",
        "rnd",
        "cloud-infra",
        "technical-supervisor",
        "swarm-coordinator",
        "upstream",
        "runtime-reliability-engineer",
        "technical-duty",
        "bi-automations",
        "data-platform-engineer",
        "qa-eval-engineer",
        "release-manager",
        "finops-controller",
        "solutions-architect",
    ),
    "technical-supervisor": (
        "tech-automations",
        "rnd",
        "security-review",
        "cloud-infra",
        "swarm-coordinator",
        "upstream",
    ),
    "qa-eval-engineer": (
        "tech-automations",
        "rnd",
        "data-platform-engineer",
        "secops-responder",
    ),
    "ai-governance-officer": (
        "ai-red-teamer",
        "qa-eval-engineer",
        "tech-automations",
        "finops-controller",
    ),
    "ux-design-reviewer": (
        "product-manager",
        "tech-automations",
    ),
    "rnd": ("tech-automations",),
}


class RoleSpec(Protocol):
    role: str
    reports_to: str
    approval_gates: tuple[str, ...]


def _toml_key(name: str) -> str:
    if _BARE_KEY_RE.match(name):
        return name
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_str_list(values: Sequence[str]) -> str:
    return "[" + ", ".join(_toml_str(v) for v in values) + "]"


def build_roles_toml(
    roles: Sequence[RoleSpec],
    workflows: Sequence[Mapping[str, Any]] | None = None,
    *,
    zone: str = "UTC",
    max_reading_age_s: int = 60,
    due_window_s: int = 3600,
) -> str:
    """Compile role metadata into a bernstein-role-pack roles.toml string."""
    if not roles:
        raise OrgKitError("cannot build roles.toml without at least one role")

    by_name: dict[str, RoleSpec] = {}
    for item in roles:
        if item.role == ROOT_ROLE:
            raise OrgKitError(f"reserved root role {ROOT_ROLE!r} must not be declared as a worker role")
        if item.role in by_name:
            raise OrgKitError(f"duplicate role {item.role!r}")
        by_name[item.role] = item

    active = set(by_name) | {ROOT_ROLE}

    # Validate reporting tree reaches ROOT_ROLE without cycles or dangling managers.
    for name, spec in by_name.items():
        seen = {name}
        current = spec.reports_to
        while current != ROOT_ROLE:
            if current not in by_name:
                raise OrgKitError(
                    f"role {name!r} reports_to unknown or excluded role {current!r}"
                )
            if current in seen:
                raise OrgKitError(f"reporting cycle detected through role {current!r}")
            seen.add(current)
            current = by_name[current].reports_to

    reviews: dict[str, set[str]] = {name: set() for name in active}

    # 1. Managers review all direct and transitive reports (up to owner).
    for name, spec in by_name.items():
        manager = spec.reports_to
        while True:
            reviews[manager].add(name)
            if manager == ROOT_ROLE:
                break
            manager = by_name[manager].reports_to

    # 2. Charter-defined independent peer reviews.
    for reviewer, targets in PEER_REVIEWS.items():
        if reviewer not in active:
            continue
        for target in targets:
            if target in active and target != reviewer:
                reviews[reviewer].add(target)

    # 3. Workflow handoff review grants: downstream stage role may review upstream role.
    for wf in workflows or ():
        nodes = wf.get("nodes")
        if not isinstance(nodes, Mapping):
            continue
        for node in nodes.values():
            if not isinstance(node, Mapping):
                continue
            downstream = node.get("role")
            deps = node.get("depends_on", ())
            if not isinstance(downstream, str) or downstream not in active:
                continue
            if not isinstance(deps, Sequence) or isinstance(deps, (str, bytes)):
                continue
            for dep_id in deps:
                dep_node = nodes.get(dep_id)
                if not isinstance(dep_node, Mapping):
                    continue
                upstream = dep_node.get("role")
                if isinstance(upstream, str) and upstream in active and upstream != downstream:
                    reviews[downstream].add(upstream)

    # Enforce strict no-self-review invariant across every role.
    for name in active:
        reviews[name].discard(name)

    lines: list[str] = [
        "# Generated by bernstein-org-kit from active role frontmatter.",
        "# Validated against bernstein-role-pack.",
        "[clock]",
        f"zone = {_toml_str(zone)}",
        f"max_reading_age_s = {int(max_reading_age_s)}",
        f"due_window_s = {int(due_window_s)}",
        "",
        f"[roles.{_toml_key(ROOT_ROLE)}]",
        f"reviews = {_toml_str_list(sorted(reviews[ROOT_ROLE]))}",
    ]

    for name in sorted(by_name):
        spec = by_name[name]
        lines.append("")
        if spec.approval_gates:
            lines.append(f"# human approval gates: {', '.join(spec.approval_gates)}")
        lines.append(f"[roles.{_toml_key(name)}]")
        lines.append(f"reports_to = {_toml_str(spec.reports_to)}")
        role_reviews = sorted(reviews[name])
        if role_reviews:
            lines.append(f"reviews = {_toml_str_list(role_reviews)}")

    lines.append("")
    lines.append("[principals]")
    lines.append(f'{_toml_str(f"{ROOT_ROLE}-1")} = {_toml_str(ROOT_ROLE)}')
    for name in sorted(by_name):
        lines.append(f'{_toml_str(f"{name}-1")} = {_toml_str(name)}')
    lines.append("")

    return "\n".join(lines)


def write_roles_toml(
    workspace: Path,
    roles: Sequence[RoleSpec],
    workflows: Sequence[Mapping[str, Any]] | None = None,
    *,
    force: bool = False,
) -> tuple[Path, Config, bool]:
    """Generate, write, and validate <workspace>/.sdd/role-pack/roles.toml.

    Returns ``(path, validated_config, written)``.
    """
    home = workspace / ".sdd" / "role-pack"
    path = home / "roles.toml"
    written = False
    if force or not path.exists():
        content = build_roles_toml(roles, workflows)
        home.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        os.chmod(path, 0o644)
        written = True

    try:
        config = load_config(home)
    except ConfigError as exc:
        raise OrgKitError(f"generated roles.toml failed bernstein-role-pack validation: {exc}") from exc
    if config is None:
        raise OrgKitError(f"roles.toml missing at {path}")
    return path, config, written
