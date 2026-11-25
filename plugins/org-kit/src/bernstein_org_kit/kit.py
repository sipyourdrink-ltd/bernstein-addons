"""Starter kit catalog, frontmatter parsing, and workspace initialization."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bernstein_org_kit import OrgKitError
from bernstein_org_kit.roles_toml import write_roles_toml

PRESETS: tuple[str, ...] = ("minimal", "full")
DEFAULT_PRESET = "minimal"

# Smallest self-contained developer reporting tree back to owner covering
# code-review-merge.json, release-pipeline.json, and document-review.json.
MINIMAL_ROLES: tuple[str, ...] = (
    "ceo",
    "cto-tech",
    "technical-supervisor",
    "tech-automations",
    "rnd",
    "security-review",
)

MINIMAL_WORKFLOWS: tuple[str, ...] = (
    "code-review-merge.json",
    "document-review.json",
    "release-pipeline.json",
)

KIT_ROOT = Path(__file__).resolve().parent / "kit"
_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)


@dataclass(frozen=True)
class RoleDoc:
    role: str
    title: str
    department: str
    reports_to: str
    level: str
    model_tier: str
    approval_gates: tuple[str, ...]
    skills: tuple[str, ...]
    path: Path
    body: str


@dataclass(frozen=True)
class SopDoc:
    id: str
    title: str
    owner_role: str
    participants: tuple[str, ...]
    workflow: str
    approval_gates: tuple[str, ...]
    path: Path
    body: str


@dataclass(frozen=True)
class WorkflowSummary:
    name: str
    stages: int
    completion_nodes: tuple[str, ...]
    roles: tuple[str, ...]
    path: Path


@dataclass(frozen=True)
class WorkflowDoc:
    name: str
    path: Path
    data: dict[str, Any]
    raw_text: str


@dataclass(frozen=True)
class InitResult:
    target: Path
    preset: str
    written: tuple[str, ...]
    skipped: tuple[str, ...]
    roles_toml: Path
    role_count: int
    workflow_count: int
    policy_sha256: str


def _parse_scalar(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_list(raw: str) -> tuple[str, ...]:
    inner = raw.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1].strip()
    if not inner:
        return ()
    return tuple(_parse_scalar(part) for part in inner.split(",") if part.strip())


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse simple YAML-subset frontmatter without third-party dependencies."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    meta: dict[str, Any] = {}
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, raw_val = stripped.split(":", 1)
        key = key.strip()
        val = raw_val.strip()
        if val.startswith("[") and val.endswith("]"):
            meta[key] = _parse_list(val)
        else:
            meta[key] = _parse_scalar(val)
    return meta, text[match.end() :]


def _as_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, tuple):
        return tuple(str(x) for x in value)
    if isinstance(value, list):
        return tuple(str(x) for x in value)
    if isinstance(value, str) and value:
        return (value,)
    return ()


def _subdir(kind: str, workspace: Path | None) -> Path:
    if workspace is not None:
        candidate = Path(workspace) / kind
        if candidate.is_dir():
            return candidate
    return KIT_ROOT / kind


def _parse_role_file(path: Path) -> RoleDoc:
    text = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    slug = str(meta.get("role") or path.stem)
    return RoleDoc(
        role=slug,
        title=str(meta.get("title") or slug),
        department=str(meta.get("department") or ""),
        reports_to=str(meta.get("reports_to") or "owner"),
        level=str(meta.get("level") or ""),
        model_tier=str(meta.get("model_tier") or ""),
        approval_gates=_as_tuple(meta.get("approval_gates")),
        skills=_as_tuple(meta.get("skills")),
        path=path,
        body=body,
    )


def _parse_sop_file(path: Path) -> SopDoc:
    text = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    sop_id = str(meta.get("id") or path.stem)
    return SopDoc(
        id=sop_id,
        title=str(meta.get("title") or sop_id),
        owner_role=str(meta.get("owner_role") or ""),
        participants=_as_tuple(meta.get("participants")),
        workflow=str(meta.get("workflow") or "none"),
        approval_gates=_as_tuple(meta.get("approval_gates")),
        path=path,
        body=body,
    )


def role_rows(workspace: Path | None = None) -> list[RoleDoc]:
    """Return sorted role definitions from workspace (if present) or the packaged kit."""
    directory = _subdir("roles", workspace)
    if not directory.is_dir():
        return []
    docs = [_parse_role_file(p) for p in sorted(directory.glob("*.md"))]
    return sorted(docs, key=lambda r: r.role)


def sop_rows(workspace: Path | None = None) -> list[SopDoc]:
    """Return sorted SOP definitions from workspace (if present) or the packaged kit."""
    directory = _subdir("sops", workspace)
    if not directory.is_dir():
        return []
    docs = [_parse_sop_file(p) for p in sorted(directory.glob("*.md"))]
    return sorted(docs, key=lambda s: s.id)


def workflow_rows(workspace: Path | None = None) -> list[WorkflowSummary]:
    """Return sorted workflow summaries from workspace (if present) or the packaged kit."""
    directory = _subdir("workflows", workspace)
    if not directory.is_dir():
        return []
    rows: list[WorkflowSummary] = []
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        nodes = data.get("nodes", {})
        roles = sorted({str(node.get("role", "")) for node in nodes.values() if isinstance(node, dict)})
        rows.append(
            WorkflowSummary(
                name=str(data.get("name") or path.stem),
                stages=len(nodes),
                completion_nodes=tuple(str(n) for n in data.get("completion_nodes", ())),
                roles=tuple(r for r in roles if r),
                path=path,
            )
        )
    return sorted(rows, key=lambda w: w.name)


def show_role(slug: str, workspace: Path | None = None) -> RoleDoc:
    """Return one role definition by slug, searching workspace then packaged kit."""
    target = slug.removesuffix(".md").strip()
    search_dirs: list[Path] = []
    if workspace is not None and (Path(workspace) / "roles").is_dir():
        search_dirs.append(Path(workspace) / "roles")
    search_dirs.append(KIT_ROOT / "roles")

    for directory in search_dirs:
        candidate = directory / f"{target}.md"
        if candidate.is_file():
            return _parse_role_file(candidate)
        for path in sorted(directory.glob("*.md")):
            doc = _parse_role_file(path)
            if doc.role == target:
                return doc
    raise OrgKitError(f"unknown role {slug!r}")


def show_sop(sop_id: str, workspace: Path | None = None) -> SopDoc:
    """Return one SOP by ID (case-insensitive) or filename stem."""
    needle = sop_id.removesuffix(".md").strip().lower()
    search_dirs: list[Path] = []
    if workspace is not None and (Path(workspace) / "sops").is_dir():
        search_dirs.append(Path(workspace) / "sops")
    search_dirs.append(KIT_ROOT / "sops")

    for directory in search_dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            doc = _parse_sop_file(path)
            if doc.id.lower() == needle or path.stem.lower() == needle:
                return doc
    raise OrgKitError(f"unknown SOP {sop_id!r}")


def show_workflow(name: str, workspace: Path | None = None) -> WorkflowDoc:
    """Return one workflow JSON document by name or filesystem path."""
    maybe_path = Path(name)
    if maybe_path.is_file():
        raw = maybe_path.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OrgKitError(f"workflow {maybe_path} is not valid JSON: {exc}") from None
        if not isinstance(data, dict):
            raise OrgKitError(f"workflow {maybe_path} must be a JSON object")
        return WorkflowDoc(
            name=str(data.get("name") or maybe_path.stem),
            path=maybe_path.resolve(),
            data=data,
            raw_text=raw,
        )

    slug = name.removesuffix(".json").strip()
    search_dirs: list[Path] = []
    if workspace is not None and (Path(workspace) / "workflows").is_dir():
        search_dirs.append(Path(workspace) / "workflows")
    search_dirs.append(KIT_ROOT / "workflows")

    for directory in search_dirs:
        if not directory.is_dir():
            continue
        candidate = directory / f"{slug}.json"
        if candidate.is_file():
            raw = candidate.read_text(encoding="utf-8")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise OrgKitError(f"workflow {candidate} is not valid JSON: {exc}") from None
            return WorkflowDoc(
                name=str(data.get("name") or slug),
                path=candidate,
                data=data,
                raw_text=raw,
            )
    raise OrgKitError(f"unknown workflow {name!r}")


def _copy_file(src: Path, dst: Path, *, force: bool) -> bool:
    if dst.exists() and not force:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    os.chmod(dst, 0o644)
    return True


def init_workspace(
    target: Path,
    *,
    preset: str = DEFAULT_PRESET,
    force: bool = False,
) -> InitResult:
    """Initialize a workspace with the chosen starter kit preset and roles.toml."""
    if preset not in PRESETS:
        raise OrgKitError(f"unknown preset {preset!r}; expected one of {', '.join(PRESETS)}")

    root = Path(target).resolve()
    root.mkdir(parents=True, exist_ok=True)

    plan: list[tuple[Path, Path]] = []

    # Always copy org/ (3 files) and tasks/review-demo.json.
    for src in sorted((KIT_ROOT / "org").iterdir()):
        if src.is_file():
            plan.append((src, root / "org" / src.name))
    plan.append((KIT_ROOT / "tasks" / "review-demo.json", root / "tasks" / "review-demo.json"))

    if preset == "minimal":
        for slug in MINIMAL_ROLES:
            plan.append((KIT_ROOT / "roles" / f"{slug}.md", root / "roles" / f"{slug}.md"))
        for wf_name in MINIMAL_WORKFLOWS:
            plan.append((KIT_ROOT / "workflows" / wf_name, root / "workflows" / wf_name))
    else:
        for sub in ("roles", "sops", "workflows", "templates"):
            for src in sorted((KIT_ROOT / sub).iterdir()):
                if src.is_file():
                    plan.append((src, root / sub / src.name))
        plan.append((KIT_ROOT / "scenarios.json", root / "scenarios.json"))

    written: list[str] = []
    skipped: list[str] = []
    for src, dst in plan:
        rel = dst.relative_to(root).as_posix()
        if _copy_file(src, dst, force=force):
            written.append(rel)
        else:
            skipped.append(rel)

    active_roles = role_rows(root)
    active_workflows = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((root / "workflows").glob("*.json"))
    ]
    roles_toml_path, config, toml_written = write_roles_toml(
        root,
        active_roles,
        active_workflows,
        force=force,
    )
    rel_toml = roles_toml_path.relative_to(root).as_posix()
    if toml_written:
        written.append(rel_toml)
    else:
        skipped.append(rel_toml)

    return InitResult(
        target=root,
        preset=preset,
        written=tuple(sorted(written)),
        skipped=tuple(sorted(skipped)),
        roles_toml=roles_toml_path,
        role_count=len(active_roles),
        workflow_count=len(active_workflows),
        policy_sha256=config.sha256,
    )
