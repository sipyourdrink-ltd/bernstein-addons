"""Real Bernstein CLI adapter discovery, prompt builder, and fail-closed stage runner."""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from bernstein.adapters import registry as adapter_registry
from bernstein.core.models import ModelConfig

from bernstein_org_kit import OrgKitError
from bernstein_org_kit.compiler import compile_workflow
from bernstein_org_kit.kit import RoleDoc, show_role

KNOWN_BINARIES: dict[str, str] = {
    "agy": "agy",
    "aichat": "aichat",
    "aider": "aider",
    "amp": "amp",
    "antigravity": "agy",
    "auggie": "auggie",
    "claude": "claude",
    "cline": "cline",
    "codex": "codex",
    "cody": "cody",
    "continue": "cn",
    "copilot": "copilot",
    "cursor": "agent",
    "droid": "droid",
    "forge": "forge",
    "gemini": "gemini",
    "goose": "goose",
    "gptme": "gptme",
    "kiro": "kiro-cli",
    "mistral": "vibe",
    "ollama": "ollama",
    "opencode": "opencode",
    "pi": "pi",
    "plandex": "plandex",
    "q_dev": "q",
    "qwen": "qwen",
    "rovo": "acli",
}

TIER_EFFORT: dict[str, str] = {
    "tier-1-reasoning": "high",
    "tier-2-coding": "normal",
    "tier-3-extraction": "low",
}


class GateBlockedError(OrgKitError):
    """Raised when a workflow stage requires human approval gates not explicitly passed."""

    def __init__(self, stage_id: str, role: str, missing_gates: Sequence[str]) -> None:
        self.stage_id = stage_id
        self.role = role
        self.missing_gates = tuple(missing_gates)
        gate_list = ", ".join(self.missing_gates)
        flags = " ".join(f"--approve-gate {g}" for g in self.missing_gates)
        super().__init__(
            f"stage {stage_id!r} (role {role!r}) blocked on human approval gate(s): "
            f"{gate_list}; obtain written human approval and pass {flags}"
        )


@dataclass(frozen=True)
class AdapterInfo:
    name: str
    adapter_class: str
    binary: str | None
    available: bool
    binary_path: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StageRunResult:
    workflow: str
    plan_hash: str
    stage_id: str
    role: str
    adapter: str
    adapter_class: str
    model: str
    effort: str
    timeout_seconds: int
    dry_run: bool
    approved_gates: tuple[str, ...]
    session_id: str
    prompt: str
    pid: int | None = None
    log_path: str | None = None
    returncode: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _adapter_class_map() -> dict[str, str]:
    classes: dict[str, str] = {}
    iter_specs = getattr(adapter_registry, "iter_adapter_specs", None)
    if callable(iter_specs):
        for name, cls in iter_specs():
            classes[str(name)] = getattr(cls, "__name__", str(cls))
    return classes


def list_adapters(*, include_mock: bool = False) -> list[AdapterInfo]:
    """List Bernstein's registered CLI adapters and whether each binary is on PATH."""
    selectable_fn = getattr(adapter_registry, "selectable_adapter_names", None)
    if callable(selectable_fn):
        names = sorted(str(n) for n in selectable_fn())
    else:
        names = sorted(_adapter_class_map())

    if include_mock and "mock" not in names:
        names.append("mock")
        names.sort()

    class_map = _adapter_class_map()
    rows: list[AdapterInfo] = []
    for name in names:
        if name == "mock":
            rows.append(
                AdapterInfo(
                    name="mock",
                    adapter_class=class_map.get("mock", "MockAgentAdapter"),
                    binary=None,
                    available=True,
                    binary_path=None,
                )
            )
            continue
        binary = KNOWN_BINARIES.get(name, name)
        found = shutil.which(binary) if binary else None
        rows.append(
            AdapterInfo(
                name=name,
                adapter_class=class_map.get(name, "CLIAdapter"),
                binary=binary,
                available=found is not None,
                binary_path=found,
            )
        )
    return rows


def _find_stage(plan: Mapping[str, Any], stage_id: str) -> Mapping[str, Any]:
    for stage in plan.get("stages", ()):
        if stage.get("id") == stage_id:
            return stage
    valid = [str(s.get("id")) for s in plan.get("stages", ())]
    raise OrgKitError(
        f"unknown stage {stage_id!r} in workflow {plan.get('workflow')!r}; "
        f"available stages: {', '.join(valid)}"
    )


def _load_role_optional(role_slug: str, workspace: Path | None) -> RoleDoc | None:
    try:
        return show_role(role_slug, workspace=workspace)
    except OrgKitError:
        return None


def build_stage_prompt(
    workflow: str | Path | Mapping[str, Any],
    stage_id: str,
    *,
    objective: str,
    predecessor_hashes: Mapping[str, str] | None = None,
    workspace: Path | None = None,
) -> str:
    """Build a grounded execution prompt for one workflow stage."""
    plan = (
        dict(workflow)
        if isinstance(workflow, Mapping)
        else compile_workflow(workflow, workspace=workspace)
    )
    stage = _find_stage(plan, stage_id)
    role_slug = str(stage["role"])
    role_doc = _load_role_optional(role_slug, workspace)

    gates = list(stage.get("approval_gates") or ())
    deps = list(stage.get("depends_on") or ())
    preds = dict(predecessor_hashes or {})

    lines: list[str] = [
        f"# Workflow Stage Brief: {plan['workflow']} / {stage_id}",
        "",
        f"- Workflow: `{plan['workflow']}` (plan_hash: `{plan['plan_hash']}`)",
        f"- Stage: `{stage_id}` (wave {stage['wave']}, timeout {stage['timeout_seconds']}s)",
        f"- Assigned role: `{role_slug}`",
        f"- Reports to: `{stage.get('reports_to') or 'owner'}`",
        f"- Model tier: `{stage.get('model_tier') or 'unspecified'}`",
        f"- Depends on stages: {', '.join(deps) if deps else 'none'}",
        f"- Human approval gates: {', '.join(gates) if gates else 'none'}",
        "",
        "## Objective",
        objective.strip(),
        "",
        "## Accepted Predecessor Artifacts (SHA-256)",
    ]

    if preds:
        for key in sorted(preds):
            lines.append(f"- `{key}`: `{preds[key]}`")
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Governance Contract",
            "- Produce concrete file artifacts and record their exact SHA-256 digests.",
            "- Never accept your own artifact; an independent reviewer must accept against the exact SHA-256.",
            "- Agents prepare drafts; never execute any gated action (money, external-send, public-post, deletion, credentials, legal-commitment, production-change, personal-data) without explicit written human approval.",
            "- Fail closed: if inputs, permissions, or evidence are missing, report BLOCKED instead of guessing.",
        ]
    )

    if role_doc is not None and role_doc.body.strip():
        lines.extend(["", "## Role Charter", role_doc.body.strip()])

    return "\n".join(lines) + "\n"


def _resolve_adapter(adapter_name: str) -> Any:
    selectable_fn = getattr(adapter_registry, "selectable_adapter_names", None)
    if adapter_name != "mock" and callable(selectable_fn):
        valid = set(selectable_fn())
        if adapter_name not in valid:
            raise OrgKitError(
                f"unknown Bernstein adapter {adapter_name!r}; run 'bernstein-org-kit adapters' to list registered adapters"
            )
    try:
        return adapter_registry.get_adapter(adapter_name)
    except Exception as exc:
        raise OrgKitError(f"failed to load Bernstein adapter {adapter_name!r}: {exc}") from exc


def run_stage(
    workflow: str | Path | Mapping[str, Any],
    *,
    stage_id: str,
    objective: str,
    adapter: str = "claude",
    workspace: Path | None = None,
    predecessor_hashes: Mapping[str, str] | None = None,
    approved_gates: Sequence[str] | None = None,
    model: str | None = None,
    effort: str | None = None,
    session_id: str | None = None,
    dry_run: bool = True,
    wait: bool = True,
) -> StageRunResult:
    """Validate human gates, resolve a real Bernstein adapter, and preview or spawn a stage."""
    plan = (
        dict(workflow)
        if isinstance(workflow, Mapping)
        else compile_workflow(workflow, workspace=workspace)
    )
    stage = _find_stage(plan, stage_id)

    required_gates = [str(g) for g in stage.get("approval_gates") or ()]
    approved = {str(g).strip() for g in (approved_gates or ()) if str(g).strip()}
    missing = [g for g in required_gates if g not in approved]
    if missing:
        raise GateBlockedError(stage_id, str(stage["role"]), missing)

    adapter_obj = _resolve_adapter(adapter)
    prompt = build_stage_prompt(
        plan,
        stage_id,
        objective=objective,
        predecessor_hashes=predecessor_hashes,
        workspace=workspace,
    )

    default_model = getattr(adapter_obj, "default_model", None)
    resolved_model = model or (default_model if isinstance(default_model, str) and default_model else "default")
    resolved_effort = effort or TIER_EFFORT.get(str(stage.get("model_tier") or ""), "normal")
    resolved_session = session_id or f"orgkit-{plan['workflow']}-{stage_id}-{uuid.uuid4().hex[:8]}"
    timeout_s = int(stage["timeout_seconds"])

    if dry_run:
        return StageRunResult(
            workflow=str(plan["workflow"]),
            plan_hash=str(plan["plan_hash"]),
            stage_id=stage_id,
            role=str(stage["role"]),
            adapter=adapter,
            adapter_class=type(adapter_obj).__name__,
            model=resolved_model,
            effort=resolved_effort,
            timeout_seconds=timeout_s,
            dry_run=True,
            approved_gates=tuple(sorted(approved)),
            session_id=resolved_session,
            prompt=prompt,
        )

    workdir = Path(workspace).resolve() if workspace is not None else Path.cwd().resolve()
    model_config = ModelConfig(model=resolved_model, effort=resolved_effort)
    spawn_result = adapter_obj.spawn(
        prompt=prompt,
        workdir=workdir,
        model_config=model_config,
        session_id=resolved_session,
        timeout_seconds=timeout_s,
    )

    returncode: int | None = None
    proc = getattr(spawn_result, "proc", None)
    timer = getattr(spawn_result, "timeout_timer", None)
    if wait and proc is not None and hasattr(proc, "wait"):
        try:
            returncode = proc.wait(timeout=timeout_s)
        finally:
            if timer is not None and hasattr(timer, "cancel"):
                timer.cancel()

    return StageRunResult(
        workflow=str(plan["workflow"]),
        plan_hash=str(plan["plan_hash"]),
        stage_id=stage_id,
        role=str(stage["role"]),
        adapter=adapter,
        adapter_class=type(adapter_obj).__name__,
        model=resolved_model,
        effort=resolved_effort,
        timeout_seconds=timeout_s,
        dry_run=False,
        approved_gates=tuple(sorted(approved)),
        session_id=resolved_session,
        prompt=prompt,
        pid=getattr(spawn_result, "pid", None),
        log_path=str(spawn_result.log_path) if getattr(spawn_result, "log_path", None) else None,
        returncode=returncode,
    )
