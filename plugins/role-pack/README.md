# bernstein-role-pack

![plugin for Bernstein](../../assets/plugin-for-bernstein.svg)

A plugin for the [Bernstein](https://pypi.org/project/bernstein/) orchestrator that adds three things to a workspace:

1. A registry of roles with reporting lines and review rights, and principals (session names) that hold a role.
2. A receipt ledger with separate states per task: `delivered`, `acknowledged`, `reported`, `done`. A report is not `done`. A task is `done` only when every reported file was accepted by a different principal whose role may review the producer's role, and each acceptance names the exact sha256 of the reported file.
3. Two safety rules. A task whose result is unknown is reconciled under its own id; Bernstein refuses to create a new task with the same role and title while it is unknown. Timestamps follow one clock policy: storage in UTC, fresh readings only, deadlines only with an explicit offset.

## Install

```
python3.12 -m venv .venv
. .venv/bin/activate
pip install "bernstein==3.24.1"
pip install ./plugins/role-pack
bernstein-role-pack demo
```

Run from the repository root. The demo needs no model, no network access and no configuration.

## Demo

`bernstein-role-pack demo` (or `python -m bernstein_role_pack.demo`) creates a temporary workspace, writes the example `roles.toml`, grants workspace trust, builds Bernstein's server app in-process and drives it through the real HTTP routes with FastAPI's `TestClient`. It exits 0 only if every step behaved as stated. Excerpt of a run (task ids and dates differ per run):

```
1. requester POST /tasks role=worker -> HTTP 201, task 71e2a9a89ac0; pack: delivered (deadline 2026-11-13T14:01:33Z)
4. GateRunner.run_all changed_files=['out/summary.md'] -> blocked: not accepted: out/summary.md: no acceptance in task 71e2a9a89ac0
5. wrk-1 accepts its own artifact -> refused: wrk-1 produced out/summary.md and may not accept it; pack: reported
7. rev-1 accepts sha256=087fb1ea6665.. -> pack: done; gate -> pass: 1 file(s) accepted: out/summary.md
8. out/summary.md edited after acceptance -> gate blocked: not accepted: out/summary.md: changed since task 71e2a9a89ac0 reported it
9. task 3d44724268e7 acknowledged, marked unknown by req-1; POST /tasks same role and title -> HTTP 400: Hook 'role_pack' blocked orchestration: task 3d44724268e7 with role worker and title 'Write the appendix' is unknown; reconcile it under its own id instead of creating a new task
   req-1 reconciles 3d44724268e7 as not_completed under its own id -> pack: acknowledged
all steps behaved as stated
```

## Configuration

The pack home is `$BERNSTEIN_ROLE_PACK_HOME` when set, otherwise `<workspace>/.sdd/role-pack/`. The workspace is the current directory for the hooks and the command line, and the gate's `run_dir` for the gate. The home holds `roles.toml` (written by you) and `ledger.jsonl` (written by the pack).

If `roles.toml` is absent the hooks do nothing, so installing the package does not change a workspace that does not use it.

`examples/roles.toml`:

```toml
[clock]
zone = "UTC"
max_reading_age_s = 60
due_window_s = 3600

[roles.requester]

[roles.worker]
reports_to = "requester"

[roles.reviewer]
reports_to = "requester"
reviews = ["worker"]

[principals]
"req-1" = "requester"
"wrk-1" = "worker"
"rev-1" = "reviewer"
```

`bernstein-role-pack check` rejects: a `reports_to` naming an unknown role, a reporting cycle, a registry with no root role, a role that reviews itself or an unknown role, a principal mapped to an unknown role, and an unknown zone. Every ledger event records the sha256 of the `roles.toml` bytes as `policy`.

The zone is used for local display only; the ledger stores UTC (`YYYY-MM-DDTHH:MM:SSZ`). A clock reading older than `max_reading_age_s` is refused. A deadline is `overdue`, `due_now` (within `due_window_s`) or `future`.

## Ledger rules

| Operation | Who | Requires | Result |
|---|---|---|---|
| delivered | the `on_task_created` hook | task role is in the registry | `delivered` |
| `ack` | a principal of the task's role | `delivered` | `acknowledged`; that principal is the holder |
| `report` | the holder | `acknowledged` or later | `reported`; sha256 of each file, `to` = the role the holder's role reports to |
| `accept` | a principal other than the producer, whose role may review the producer's role | the path was reported; the given sha256 equals the reported one and, from the command line, the file on disk | `done` once every reported path has a valid acceptance |
| `unknown` | the holder or a principal in the line above the task's role | `acknowledged` or later | `unknown`; `ack`, `report` and `accept` are refused |
| `reconcile` | the holder or the line above | `unknown` | `completed` (paths required) gives `reported`; `not_completed` gives `acknowledged`; same task id |
| completed | the `on_task_completed` hook | task is tracked | no state change; `status` shows `bernstein_completed=yes` |

A new report replaces the earlier one and drops acceptances whose file hash changed. Each refusal is its own exception class under `bernstein_role_pack.ledger.Refused`.

## Command line

```
bernstein-role-pack check
bernstein-role-pack status [TASK]
bernstein-role-pack ack TASK --as P
bernstein-role-pack report TASK --as P PATH...
bernstein-role-pack accept TASK --as P --path PATH [--sha256 HEX]
bernstein-role-pack unknown TASK --as P --reason TEXT
bernstein-role-pack reconcile TASK --as P --outcome completed|not_completed --evidence TEXT [PATH...]
bernstein-role-pack demo
```

Paths are relative to the current directory. Without `--sha256`, `accept` hashes the file on disk and still compares the result with the reported hash. Any refusal exits 1 with one line on stderr.

## How it plugs into Bernstein

Entry points declared in `pyproject.toml`:

| Group | Name | Object |
|---|---|---|
| `bernstein.plugins` | `role_pack` | `bernstein_role_pack.plugin:RolePackPlugin` |
| `bernstein.gates` | `role_pack_acceptance` | `bernstein_role_pack.gate:RolePackAcceptanceGate` |

Hooks:

- `on_pre_task_create` blocks creation (HTTP 400) when a tracked task with the same role and title is `unknown`, or when the description has a line `Deadline: <timestamp>` whose timestamp has no offset or does not parse. It blocks by raising `bernstein.plugins.manager.HookBlockingError`; Bernstein logs and ignores any other exception from a hook.
- `on_task_created` writes `delivered`, with the deadline from the description in UTC. Bernstein passes no description to this hook and an empty task id to the pre-create hook, so the plugin keeps the parsed deadline in memory between the two calls, keyed by role and title.
- `on_task_completed` writes `completed`.

Bernstein fires plugin hooks only in a trusted workspace:

```
bernstein trust --workdir DIR
```

or `bernstein.core.persistence.workspace.grant_workspace_trust(DIR)`.

The gate `role_pack_acceptance` checks every changed file outside `.sdd/`: it hashes the file now and requires a valid acceptance of that path and hash. It re-checks the registry for each acceptance line (the reviewer differs from the producer and may review the producer's role), so a line appended to the ledger by hand has to pass the same checks. It fails when the owning task is `unknown`, and fails with `blocked=True` when the pack is not configured. Select it in Python:

```python
from bernstein.core.quality.gate_pipeline import GatePipelineStep
from bernstein.core.quality.gate_runner import GateRunner
from bernstein.core.quality.quality_gates import QualityGatesConfig

config = QualityGatesConfig(pipeline=[GatePipelineStep(name="role_pack_acceptance", required=True)])
report = await GateRunner(config, workdir).run_all(task, run_dir)
```

`GateRunner.run_all` decides `changed_files` itself: the task's `owned_files` that exist under `run_dir`; if there are none, the files in `git diff --name-only <base>..HEAD` (committed changes only, base `main` or the current branch when `main` does not exist); otherwise an empty list. With nothing to check the gate passes and says `0 files` in its details.

## Limits

- Principals are names the caller supplies. The pack does not authenticate them.
- The ledger is a plain file. Whoever can write it can alter it; the gate re-checks registry rules but cannot detect a consistent forgery by a writer who names valid principals.
- The gate must be selected in Python. Bernstein 3.24.1 does not accept a plugin gate from `bernstein.yaml`.
- Hooks fire only for tasks created through the HTTP task routes in a trusted workspace. Tasks created another way are not tracked.
- The hooks find the pack home from the server process's current directory or `$BERNSTEIN_ROLE_PACK_HOME`; Bernstein does not pass the workspace to them.
- When `GateRunner` resolves no changed files, the gate has nothing to check and passes.
- If the ledger or `roles.toml` cannot be read, the pre-create rules are skipped, the task is created and a warning goes to stderr.
- A deadline is matched to its task by role and title, so two tasks with the same role and title created at the same moment can swap deadlines.
- Each event is one append to the ledger file. Several processes writing at once were not tested.
- Tested only against Bernstein 3.24.1 on Python 3.12.

## Tests

```
cd plugins/role-pack
python -m unittest discover -s tests
```

## Licence

Apache-2.0. See `LICENSE` and `NOTICE` at the repository root.
