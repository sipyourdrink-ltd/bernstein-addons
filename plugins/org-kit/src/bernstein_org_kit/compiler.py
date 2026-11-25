"""Deterministic pure-stdlib workflow DAG compiler."""

from __future__ import annotations

import hashlib
import heapq
import json
import re
from pathlib import Path
from typing import Any

from bernstein_org_kit import OrgKitError
from bernstein_org_kit.kit import RoleDoc, role_rows, show_workflow

PLAN_SCHEMA = "bernstein.org-kit.workflow-plan.v1"
_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,79}$")
_MAX_NODES = 20
_MAX_TIMEOUT_S = 3600


class CompileError(OrgKitError):
    """Workflow JSON is invalid or contains a dependency cycle."""


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_json(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("ascii")).hexdigest()


def _validate_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise CompileError(f"{label} must match ^[a-z][a-z0-9-]{{0,79}}$, got {value!r}")
    return value


def _validate_workflow(data: Any) -> tuple[str, dict[str, dict[str, Any]], list[str]]:
    if not isinstance(data, dict):
        raise CompileError("workflow must be a JSON object")
    extra_top = set(data) - {"name", "nodes", "completion_nodes"}
    if extra_top:
        raise CompileError(f"unexpected top-level workflow keys: {sorted(extra_top)}")

    name = _validate_id(data.get("name"), "workflow name")
    raw_nodes = data.get("nodes")
    if not isinstance(raw_nodes, dict) or not raw_nodes:
        raise CompileError("workflow 'nodes' must be a non-empty object")
    if len(raw_nodes) > _MAX_NODES:
        raise CompileError(f"workflow has {len(raw_nodes)} nodes (max {_MAX_NODES})")

    nodes: dict[str, dict[str, Any]] = {}
    for raw_id, raw_spec in raw_nodes.items():
        node_id = _validate_id(raw_id, "node id")
        if not isinstance(raw_spec, dict):
            raise CompileError(f"node {node_id!r} must be an object")
        extra_node = set(raw_spec) - {"role", "depends_on", "timeout_seconds"}
        if extra_node:
            raise CompileError(f"node {node_id!r} has unexpected keys: {sorted(extra_node)}")
        role = _validate_id(raw_spec.get("role"), f"node {node_id!r} role")
        deps = raw_spec.get("depends_on")
        if not isinstance(deps, list):
            raise CompileError(f"node {node_id!r} depends_on must be a list")
        cleaned_deps: list[str] = []
        seen_deps: set[str] = set()
        for dep in deps:
            dep_id = _validate_id(dep, f"node {node_id!r} dependency")
            if dep_id == node_id:
                raise CompileError(f"node {node_id!r} cannot depend on itself")
            if dep_id in seen_deps:
                raise CompileError(f"node {node_id!r} lists duplicate dependency {dep_id!r}")
            seen_deps.add(dep_id)
            cleaned_deps.append(dep_id)
        timeout = raw_spec.get("timeout_seconds")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not (1 <= timeout <= _MAX_TIMEOUT_S):
            raise CompileError(
                f"node {node_id!r} timeout_seconds must be an integer in 1..{_MAX_TIMEOUT_S}"
            )
        nodes[node_id] = {
            "role": role,
            "depends_on": sorted(cleaned_deps),
            "timeout_seconds": timeout,
        }

    for node_id, spec in nodes.items():
        for dep_id in spec["depends_on"]:
            if dep_id not in nodes:
                raise CompileError(f"node {node_id!r} depends on unknown node {dep_id!r}")

    raw_completion = data.get("completion_nodes")
    if not isinstance(raw_completion, list) or not raw_completion:
        raise CompileError("workflow 'completion_nodes' must be a non-empty list")
    completion_nodes: list[str] = []
    seen_comp: set[str] = set()
    for item in raw_completion:
        comp_id = _validate_id(item, "completion node")
        if comp_id not in nodes:
            raise CompileError(f"completion_node {comp_id!r} does not exist in nodes")
        if comp_id in seen_comp:
            raise CompileError(f"duplicate completion_node {comp_id!r}")
        seen_comp.add(comp_id)
        completion_nodes.append(comp_id)

    return name, nodes, sorted(completion_nodes)


def _toposort(nodes: dict[str, dict[str, Any]]) -> tuple[list[str], dict[str, list[str]], dict[str, int]]:
    indegree: dict[str, int] = {nid: len(spec["depends_on"]) for nid, spec in nodes.items()}
    dependents: dict[str, list[str]] = {nid: [] for nid in nodes}
    for nid, spec in nodes.items():
        for dep in spec["depends_on"]:
            dependents[dep].append(nid)
    for nid in dependents:
        dependents[nid].sort()

    heap: list[str] = sorted(nid for nid, deg in indegree.items() if deg == 0)
    heapq.heapify(heap)
    order: list[str] = []
    wave_of: dict[str, int] = {}

    while heap:
        current = heapq.heappop(heap)
        order.append(current)
        deps = nodes[current]["depends_on"]
        wave_of[current] = 0 if not deps else 1 + max(wave_of[d] for d in deps)
        for nxt in dependents[current]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                heapq.heappush(heap, nxt)

    if len(order) != len(nodes):
        stuck = sorted(set(nodes) - set(order))
        raise CompileError(f"workflow contains a dependency cycle involving: {', '.join(stuck)}")

    return order, dependents, wave_of


def compile_workflow_data(
    data: dict[str, Any],
    *,
    roles_by_name: dict[str, RoleDoc] | None = None,
) -> dict[str, Any]:
    """Compile a validated workflow dict into a deterministic plan with SHA-256 hashes."""
    name, nodes, completion_nodes = _validate_workflow(data)
    order, dependents, wave_of = _toposort(nodes)

    canonical_workflow = {
        "name": name,
        "completion_nodes": completion_nodes,
        "nodes": {nid: nodes[nid] for nid in sorted(nodes)},
    }
    workflow_sha256 = _sha256_json(canonical_workflow)

    roles_map = roles_by_name or {}
    completion_set = set(completion_nodes)

    max_wave = max(wave_of.values())
    waves: list[list[str]] = [[] for _ in range(max_wave + 1)]
    for nid in order:
        waves[wave_of[nid]].append(nid)
    for wave in waves:
        wave.sort()

    # Order stages by wave then lexicographically for clean wave-by-wave execution.
    execution_order = [nid for wave in waves for nid in wave]

    stages: list[dict[str, Any]] = []
    for idx, nid in enumerate(execution_order, start=1):
        spec = nodes[nid]
        role_doc = roles_map.get(spec["role"])
        stage_core = {
            "id": nid,
            "index": idx,
            "wave": wave_of[nid],
            "role": spec["role"],
            "depends_on": spec["depends_on"],
            "unblocks": dependents[nid],
            "timeout_seconds": spec["timeout_seconds"],
            "is_completion_node": nid in completion_set,
            "role_known": role_doc is not None,
            "reports_to": role_doc.reports_to if role_doc is not None else None,
            "department": role_doc.department if role_doc is not None else None,
            "model_tier": role_doc.model_tier if role_doc is not None else None,
            "approval_gates": list(role_doc.approval_gates) if role_doc is not None else [],
        }
        stage_core["stage_sha256"] = _sha256_json(stage_core)
        stages.append(stage_core)

    plan_body: dict[str, Any] = {
        "schema": PLAN_SCHEMA,
        "workflow": name,
        "workflow_sha256": workflow_sha256,
        "completion_nodes": completion_nodes,
        "execution_order": execution_order,
        "waves": waves,
        "stages": stages,
    }
    plan_body["plan_hash"] = _sha256_json(plan_body)
    return plan_body


def compile_workflow(
    workflow: str | Path,
    *,
    workspace: Path | None = None,
) -> dict[str, Any]:
    """Resolve and compile a workflow name or JSON file path into a deterministic plan."""
    wf_doc = show_workflow(str(workflow), workspace=workspace)
    # Build role metadata map: packaged kit as baseline, workspace roles override.
    roles_map: dict[str, RoleDoc] = {r.role: r for r in role_rows(None)}
    if workspace is not None:
        for r in role_rows(workspace):
            roles_map[r.role] = r
    return compile_workflow_data(wf_doc.data, roles_by_name=roles_map)


def ready_stages(plan: dict[str, Any], accepted: set[str] | frozenset[str]) -> list[str]:
    """Return sorted stage IDs not yet accepted whose dependencies are all accepted."""
    out: list[str] = []
    for stage in plan.get("stages", []):
        sid = stage["id"]
        if sid in accepted:
            continue
        if all(dep in accepted for dep in stage["depends_on"]):
            out.append(sid)
    return sorted(out)
