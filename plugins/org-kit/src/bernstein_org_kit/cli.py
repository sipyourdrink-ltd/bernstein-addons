"""Command line interface: bernstein-org-kit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bernstein_org_kit import OrgKitError, __version__
from bernstein_org_kit.compiler import compile_workflow
from bernstein_org_kit.kit import (
    DEFAULT_PRESET,
    PRESETS,
    init_workspace,
    role_rows,
    show_role,
    show_sop,
    show_workflow,
    sop_rows,
    workflow_rows,
)
from bernstein_org_kit.runner import list_adapters, run_stage


def _workspace_arg(parser: argparse.ArgumentParser, *, suppress_default: bool = False) -> None:
    kwargs: dict[str, object] = {
        "type": Path,
        "help": "Path to an initialized org-kit workspace (defaults to current directory if it has org/ORG.md, else packaged starter kit).",
    }
    kwargs["default"] = argparse.SUPPRESS if suppress_default else None
    parser.add_argument("--workspace", **kwargs)


def _json_arg(parser: argparse.ArgumentParser, *, help_text: str, suppress_default: bool = False) -> None:
    kwargs: dict[str, object] = {"action": "store_true", "help": help_text}
    if suppress_default:
        kwargs["default"] = argparse.SUPPRESS
    parser.add_argument("--json", **kwargs)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bernstein-org-kit",
        description="Starter org kit, deterministic workflow compiler, and Bernstein adapter runner.",
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show package version and exit.",
    )
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND", help="Subcommand to run.")

    # init
    s_init = sub.add_parser(
        "init",
        help="Initialize an org-kit workspace with roles, workflows, org docs, and roles.toml.",
    )
    s_init.add_argument(
        "target",
        nargs="?",
        default=".",
        help="Target directory to initialize (default: current directory).",
    )
    s_init.add_argument(
        "--preset",
        choices=PRESETS,
        default=DEFAULT_PRESET,
        help=f"Starter kit preset to install: minimal (6 developer roles, 3 workflows) or full (24 roles, 7 SOPs, 11 workflows) (default: {DEFAULT_PRESET}).",
    )
    s_init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files in the target directory.",
    )

    # demo
    sub.add_parser(
        "demo",
        help="Run the zero-config in-process demo over Bernstein and bernstein-role-pack.",
    )

    # roles
    s_roles = sub.add_parser("roles", help="List or inspect starter kit role definitions.")
    _workspace_arg(s_roles)
    _json_arg(s_roles, help_text="Emit machine-readable JSON output.")
    roles_sub = s_roles.add_subparsers(dest="roles_cmd", metavar="ACTION", help="Optional roles subcommand.")
    s_roles_list = roles_sub.add_parser("list", help="List available roles in a summary table.")
    _workspace_arg(s_roles_list, suppress_default=True)
    _json_arg(s_roles_list, help_text="Emit machine-readable JSON output.", suppress_default=True)
    s_roles_show = roles_sub.add_parser("show", help="Display one role charter by slug.")
    s_roles_show.add_argument("slug", help="Role slug to display (for example: ceo, cto-tech, security-review).")
    _workspace_arg(s_roles_show, suppress_default=True)

    # sops
    s_sops = sub.add_parser("sops", help="List or inspect standard operating procedures.")
    _workspace_arg(s_sops)
    _json_arg(s_sops, help_text="Emit machine-readable JSON output.")
    sops_sub = s_sops.add_subparsers(dest="sops_cmd", metavar="ACTION", help="Optional SOP subcommand.")
    s_sops_list = sops_sub.add_parser("list", help="List available SOPs in a summary table.")
    _workspace_arg(s_sops_list, suppress_default=True)
    _json_arg(s_sops_list, help_text="Emit machine-readable JSON output.", suppress_default=True)
    s_sops_show = sops_sub.add_parser("show", help="Display one SOP by ID or filename stem.")
    s_sops_show.add_argument("sop_id", help="SOP identifier to display (for example: SOP-OPS-01).")
    _workspace_arg(s_sops_show, suppress_default=True)

    # workflows
    s_wf = sub.add_parser("workflows", help="List, inspect, or compile workflow DAG definitions.")
    _workspace_arg(s_wf)
    _json_arg(s_wf, help_text="Emit machine-readable JSON output.")
    wf_sub = s_wf.add_subparsers(dest="wf_cmd", metavar="ACTION", help="Optional workflow subcommand.")
    s_wf_list = wf_sub.add_parser("list", help="List available workflows in a summary table.")
    _workspace_arg(s_wf_list, suppress_default=True)
    _json_arg(s_wf_list, help_text="Emit machine-readable JSON output.", suppress_default=True)
    s_wf_show = wf_sub.add_parser("show", help="Display a workflow DAG, its stages, and dependencies.")
    s_wf_show.add_argument("name", help="Workflow name (for example: code-review-merge) or path to a JSON file.")
    _workspace_arg(s_wf_show, suppress_default=True)
    _json_arg(s_wf_show, help_text="Emit raw workflow JSON.", suppress_default=True)
    s_wf_compile = wf_sub.add_parser(
        "compile",
        help="Validate and compile a workflow DAG into a deterministic plan with SHA-256 hashes.",
    )
    s_wf_compile.add_argument("workflow", help="Workflow name or path to a workflow JSON file.")
    _workspace_arg(s_wf_compile, suppress_default=True)
    s_wf_compile.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional file path to write the compiled plan JSON.",
    )

    # adapters
    s_ad = sub.add_parser(
        "adapters",
        help="List Bernstein's registered CLI adapters and local binary availability.",
    )
    s_ad.add_argument(
        "--available-only",
        action="store_true",
        help="Show only adapters whose CLI binary is currently found on PATH.",
    )
    s_ad.add_argument(
        "--include-mock",
        action="store_true",
        help="Include Bernstein's built-in offline mock adapter in the listing.",
    )
    s_ad.add_argument("--json", action="store_true", help="Emit adapter records as JSON.")

    # run
    s_run = sub.add_parser(
        "run",
        help="Preview (dry-run default) or execute one workflow stage via a Bernstein CLI adapter.",
    )
    s_run.add_argument("workflow", help="Workflow name or path to a workflow JSON file.")
    s_run.add_argument("--stage", required=True, help="Workflow stage ID to prepare or execute.")
    s_run.add_argument("--objective", required=True, help="Task objective passed into the stage prompt.")
    s_run.add_argument(
        "--adapter",
        default="claude",
        help="Registered Bernstein CLI adapter name (default: claude; use mock for offline testing).",
    )
    s_run.add_argument("--model", default=None, help="Optional model name override for Bernstein ModelConfig.")
    s_run.add_argument(
        "--effort",
        default=None,
        choices=("low", "normal", "medium", "high", "max"),
        help="Optional reasoning effort override for Bernstein ModelConfig.",
    )
    _workspace_arg(s_run)
    s_run.add_argument(
        "--predecessor",
        action="append",
        default=[],
        metavar="STAGE=SHA256",
        help="Accepted predecessor stage digest in STAGE=SHA256 form (repeatable).",
    )
    s_run.add_argument(
        "--approve-gate",
        action="append",
        default=[],
        metavar="GATE",
        help="Explicitly approve a required human approval gate for this stage (repeatable).",
    )
    s_run.add_argument(
        "--execute",
        action="store_true",
        help="Actually spawn the Bernstein adapter subprocess (omitting this flag performs a safe dry-run preview).",
    )
    s_run.add_argument(
        "--session-id",
        default=None,
        help="Optional session ID passed to the Bernstein adapter.",
    )
    s_run.add_argument("--json", action="store_true", help="Emit the dry-run plan or execution result as JSON.")

    return p


def _resolve_workspace(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit.resolve()
    cwd = Path.cwd()
    if (cwd / "org" / "ORG.md").is_file():
        return cwd
    return None


def _parse_predecessors(raw_items: list[str]) -> dict[str, str]:
    preds: dict[str, str] = {}
    for item in raw_items:
        if "=" not in item:
            raise OrgKitError(f"invalid --predecessor value {item!r}; expected STAGE=SHA256")
        key, val = item.split("=", 1)
        key, val = key.strip(), val.strip()
        if not key or not val:
            raise OrgKitError(f"invalid --predecessor value {item!r}; both stage and sha256 are required")
        preds[key] = val
    return preds


def _cmd_init(args: argparse.Namespace) -> int:
    res = init_workspace(Path(args.target), preset=args.preset, force=args.force)
    print(
        f"initialized {res.target} (preset={res.preset}): "
        f"{len(res.written)} written, {len(res.skipped)} skipped; "
        f"{res.role_count} roles, {res.workflow_count} workflows; "
        f"roles.toml validated (policy sha256={res.policy_sha256[:12]}..)"
    )
    return 0


def _cmd_roles(args: argparse.Namespace) -> int:
    ws = _resolve_workspace(args.workspace)
    if args.roles_cmd == "show":
        doc = show_role(args.slug, workspace=ws)
        print(doc.path.read_text(encoding="utf-8"), end="")
        return 0
    rows = role_rows(ws)
    if args.json:
        payload = [
            {
                "role": r.role,
                "title": r.title,
                "department": r.department,
                "reports_to": r.reports_to,
                "level": r.level,
                "model_tier": r.model_tier,
                "approval_gates": list(r.approval_gates),
            }
            for r in rows
        ]
        print(json.dumps(payload, indent=2))
        return 0
    print(f"{'ROLE':28s} {'REPORTS TO':22s} {'TIER':18s} GATES")
    for r in rows:
        gates = ", ".join(r.approval_gates) if r.approval_gates else "-"
        print(f"{r.role:28s} {r.reports_to:22s} {r.model_tier:18s} {gates}")
    return 0


def _cmd_sops(args: argparse.Namespace) -> int:
    ws = _resolve_workspace(args.workspace)
    if args.sops_cmd == "show":
        doc = show_sop(args.sop_id, workspace=ws)
        print(doc.path.read_text(encoding="utf-8"), end="")
        return 0
    rows = sop_rows(ws)
    if args.json:
        payload = [
            {
                "id": s.id,
                "title": s.title,
                "owner_role": s.owner_role,
                "workflow": s.workflow,
                "approval_gates": list(s.approval_gates),
            }
            for s in rows
        ]
        print(json.dumps(payload, indent=2))
        return 0
    print(f"{'ID':16s} {'OWNER':18s} {'WORKFLOW':26s} TITLE")
    for s in rows:
        print(f"{s.id:16s} {s.owner_role:18s} {s.workflow:26s} {s.title}")
    return 0


def _cmd_workflows(args: argparse.Namespace) -> int:
    ws = _resolve_workspace(args.workspace)
    if args.wf_cmd == "show":
        doc = show_workflow(args.name, workspace=ws)
        if args.json:
            print(json.dumps(doc.data, indent=2))
            return 0
        plan = compile_workflow(doc.path, workspace=ws)
        print(f"workflow: {plan['workflow']}  plan_hash={plan['plan_hash']}")
        print(f"completion_nodes: {', '.join(plan['completion_nodes'])}")
        print(f"waves: {' -> '.join(', '.join(w) for w in plan['waves'])}")
        print("")
        print(f"{'STAGE':24s} {'ROLE':26s} {'TIMEOUT':8s} DEPENDS ON")
        for st in plan["stages"]:
            deps = ", ".join(st["depends_on"]) if st["depends_on"] else "-"
            print(f"{st['id']:24s} {st['role']:26s} {st['timeout_seconds']:>6d}s  {deps}")
        return 0
    if args.wf_cmd == "compile":
        plan = compile_workflow(args.workflow, workspace=ws)
        rendered = json.dumps(plan, indent=2) + "\n"
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(rendered, encoding="utf-8")
            print(f"compiled {plan['workflow']} -> {args.out} (plan_hash={plan['plan_hash']})")
        else:
            print(rendered, end="")
        return 0
    rows = workflow_rows(ws)
    if args.json:
        payload = [
            {
                "name": w.name,
                "stages": w.stages,
                "completion_nodes": list(w.completion_nodes),
                "roles": list(w.roles),
            }
            for w in rows
        ]
        print(json.dumps(payload, indent=2))
        return 0
    print(f"{'WORKFLOW':28s} {'STAGES':6s} {'COMPLETION':24s} ROLES")
    for w in rows:
        comp = ", ".join(w.completion_nodes)
        print(f"{w.name:28s} {w.stages:>6d} {comp:24s} {', '.join(w.roles)}")
    return 0


def _cmd_adapters(args: argparse.Namespace) -> int:
    rows = list_adapters(include_mock=args.include_mock)
    if args.available_only:
        rows = [r for r in rows if r.available]
    if args.json:
        print(json.dumps([r.to_dict() for r in rows], indent=2))
        return 0
    print(f"{'ADAPTER':22s} {'CLASS':28s} {'BINARY':12s} STATUS")
    for r in rows:
        status = f"found ({r.binary_path})" if r.binary_path else ("built-in" if r.available else "not on PATH")
        print(f"{r.name:22s} {r.adapter_class:28s} {(r.binary or '-'):12s} {status}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    ws = _resolve_workspace(args.workspace)
    preds = _parse_predecessors(args.predecessor)
    result = run_stage(
        args.workflow,
        stage_id=args.stage,
        objective=args.objective,
        adapter=args.adapter,
        workspace=ws,
        predecessor_hashes=preds,
        approved_gates=args.approve_gate,
        model=args.model,
        effort=args.effort,
        session_id=args.session_id,
        dry_run=not args.execute,
    )
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
        return 0
    mode = "DRY-RUN PREVIEW" if result.dry_run else "EXECUTED"
    print(
        f"[{mode}] workflow={result.workflow} stage={result.stage_id} role={result.role} "
        f"adapter={result.adapter} ({result.adapter_class}) model={result.model} effort={result.effort}"
    )
    print(f"plan_hash={result.plan_hash} session_id={result.session_id}")
    if not result.dry_run:
        print(f"pid={result.pid} returncode={result.returncode} log_path={result.log_path}")
    print("--- prompt ---")
    print(result.prompt, end="")
    return 0 if (result.dry_run or result.returncode in (None, 0)) else 1


def run(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    if args.cmd == "demo":
        from bernstein_org_kit.demo import main as demo_main

        return demo_main()
    if args.cmd == "init":
        return _cmd_init(args)
    if args.cmd == "roles":
        return _cmd_roles(args)
    if args.cmd == "sops":
        return _cmd_sops(args)
    if args.cmd == "workflows":
        return _cmd_workflows(args)
    if args.cmd == "adapters":
        return _cmd_adapters(args)
    if args.cmd == "run":
        return _cmd_run(args)
    raise OrgKitError(f"unknown command {args.cmd!r}")


def main(argv: list[str] | None = None) -> int:
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except (OrgKitError, OSError) as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
