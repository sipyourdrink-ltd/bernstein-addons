# bernstein-org-kit

<a href="https://github.com/sipyourdrink-ltd/bernstein"><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/sipyourdrink-ltd/bernstein/main/assets/brand/badge-plugin-for-dark.svg"><source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/sipyourdrink-ltd/bernstein/main/assets/brand/badge-plugin-for-light.svg"><img alt="plugin for Bernstein" src="https://raw.githubusercontent.com/sipyourdrink-ltd/bernstein/main/assets/brand/badge-plugin-for-light.svg"></picture></a>

Role charters, standard operating procedures, domain-scoped organisation presets, a deterministic workflow DAG compiler, and a fail-closed stage runner built on [Bernstein](https://pypi.org/project/bernstein/) and [`bernstein-role-pack`](https://github.com/sipyourdrink-ltd/bernstein-addons/blob/main/plugins/role-pack/README.md).

`bernstein-org-kit` initializes a governed team workspace across **6 presets** (`minimal` by default: 6 developer roles, 3 workflows, and a validated `.sdd/role-pack/roles.toml`; domain presets `engineering`, `secops`, `product`, `compliance`; or `full`: 36 roles, 16 SOPs, 20 workflows, 32 scenarios, 9 authoring templates, and 6 starter tasks), compiles workflow JSON into SHA-256-hashed execution plans, invokes Bernstein's native CLI adapters (`claude`, `codex`, `gemini`, and 50+ others) with human approval-gate checks, and contributes an `AGENTS.md` governance section via Bernstein's plugin hook.

## Proof in one command

`bernstein-org-kit demo` runs in a temporary directory in ~2 seconds with no model, no API keys, and no network access. It boots Bernstein's real FastAPI server with `RolePackPlugin` and `RolePackAcceptanceGate`, proves self-acceptance and wrong-SHA-256 acceptance are refused, then proves independent acceptance with the exact SHA-256 passes the gate and unblocks Stage 2 in the compiled DAG:

```text
1. init minimal -> 6 roles, 3 workflows; roles.toml valid (7 roles, 7 principals, policy sha256=1e5221e1ede5..)
2. compile code-review-merge -> plan_hash=43e52c28a23b.., order: draft-change -> review-correctness, review-security, run-tests -> merge
3. bernstein adapters -> 55 registered (claude, codex, gemini, ...)
4. stage 1 [draft-change] role=tech-automations -> HTTP 201 task 1c058e6bbe8e; tech-automations-1 reports out/change.patch sha256=3013d0f7cad8..; pack: reported
5. gate before acceptance -> blocked: not accepted: out/change.patch: no acceptance in task 1c058e6bbe8e
6. self-accept by tech-automations-1 -> refused (SelfReview): tech-automations-1 produced out/change.patch and may not accept it
7. wrong-sha256 accept by rnd-1 (000000000000..) -> refused (HashMismatch): sha256 000000000000 does not match the reported 3013d0f7cad8
8. independent accept by rnd-1 sha256=3013d0f7cad8.. -> pack: done; gate -> pass: 1 file(s) accepted: out/change.patch
9. dag advance -> accepted=['draft-change'] unblocks: ['review-correctness', 'review-security', 'run-tests']
all steps behaved as stated
```

Task IDs vary per run; adapter counts reflect the installed Bernstein version (54 on Bernstein 3.24.1, 55 on 4.0.x).

## Install

From the repository root, with Python 3.12 or newer (installs the latest Bernstein `4.x` release):

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install "bernstein>=4.0.1,<5" ./plugins/role-pack ./plugins/org-kit
bernstein-org-kit demo
```

## Presets & Quickstart

Choose a preset sized to your team so you do not load unused departments into `roles.toml` or `AGENTS.md`:

| Preset | Roles | SOPs | Workflows | Focus |
|---|---:|---:|---:|---|
| `minimal` *(default)* | 6 | 0 | 3 | Core PR review, release pipeline, and document review |
| `engineering` | 14 | 3 | 8 | Full SDLC, zero-downtime DB migrations, incident postmortems, DR drills, deprecations |
| `secops` | 12 | 3 | 5 | Vulnerability/CVE hotfixes, MCP & plugin supply-chain vetting, OWASP Agentic & ISO 42001 review |
| `product` | 14 | 2 | 6 | Spec-to-release PRD/UX/a11y gates, deprecations, incident follow-through |
| `compliance` | 19 | 5 | 6 | AI governance, SOC2 audit packs, FinOps cloud + token spend, 3-way P2P, legal review, enterprise RFPs |
| `full` | 36 | 16 | 20 | Complete multi-department organisation (+ 32 scenarios, 9 authoring templates, 6 starter tasks) |

```sh
# Minimal developer workspace (default)
bernstein-org-kit init my-team
cd my-team
bernstein-role-pack check

# Domain-scoped workspace (for example: SecOps + AI governance)
bernstein-org-kit init my-secops --preset secops

# Full multi-department organisation
bernstein-org-kit init my-org --preset full
```

Inspect roles, SOPs, scenarios, and compiled workflow DAGs:

```sh
bernstein-org-kit roles
bernstein-org-kit sops
bernstein-org-kit scenarios --department security
bernstein-org-kit workflows show ai-governance-review
bernstein-org-kit workflows compile zero-downtime-migration
```

Check which Bernstein CLI adapters are registered and found on `PATH`:

```sh
bernstein-org-kit adapters
bernstein-org-kit adapters --available-only
```

Preview a stage prompt (safe dry-run default - spawns no subprocess) or execute a stage through a Bernstein CLI adapter:

```sh
# Stages whose role lists human approval gates fail closed unless explicitly approved:
bernstein-org-kit run code-review-merge \
  --stage draft-change \
  --objective "Implement health endpoint and unit tests" \
  --adapter claude \
  --approve-gate production-change \
  --approve-gate credentials \
  --approve-gate deletion

# Add --execute to invoke adapter.spawn(...) through Bernstein's native CLI adapter:
bernstein-org-kit run document-review \
  --stage collect-input \
  --objective "Extract changed headings from docs/" \
  --adapter mock \
  --execute
```

## How adapter execution works

`bernstein-org-kit run` does not implement its own model client or proxy. It resolves adapters directly from `bernstein.adapters.registry.get_adapter(...)` and calls `adapter.spawn(...)` using Bernstein's native `ModelConfig`.

- **Default is dry-run.** Without `--execute`, `run` validates the workflow DAG, checks human approval gates, verifies the adapter exists in Bernstein's registry, and prints the grounded prompt without starting a child process.
- **Fail-closed human gates.** If the stage's role frontmatter declares `approval_gates` (such as `production-change`, `credentials`, `deletion`, `money`, or `external-send`), `run` refuses to proceed unless each gate is explicitly acknowledged via `--approve-gate <gate>`.
- **Provider terms.** Each CLI adapter (`claude`, `codex`, `gemini`, `aider`, `opencode`, `goose`, and 50+ others registered in Bernstein) runs under the operator's own local installation, credentials, and provider terms. Use `--adapter mock` for offline tests without network calls or API keys.

## How it plugs into Bernstein

Entry points declared in `pyproject.toml`:

| Group | Name | Object |
|---|---|---|
| `bernstein.plugins` | `org_kit` | `bernstein_org_kit.plugin:OrgKitPlugin` |

- `OrgKitPlugin.provide_agents_md_section(repo_path)` returns `None` when `org/ORG.md` is absent, leaving unrelated repositories untouched.
- When `org/ORG.md` exists, it returns an `AgentsMdSection` (`kind="roles"`) summarizing active roles, reporting lines, human approval gates, and independent SHA-256 acceptance rules for `bernstein agents-md`.
- Receipt enforcement and quality gating remain in [`bernstein-role-pack`](https://github.com/sipyourdrink-ltd/bernstein-addons/blob/main/plugins/role-pack/README.md) (`RolePackPlugin` and `RolePackAcceptanceGate`); `org-kit init` generates `.sdd/role-pack/roles.toml` directly from active role frontmatter and validates it with `bernstein_role_pack.config.load_config`.

## Command line

```text
bernstein-org-kit init [TARGET] [--preset minimal|engineering|secops|product|compliance|full] [--force]
bernstein-org-kit demo
bernstein-org-kit roles [list|show SLUG] [--workspace DIR] [--json]
bernstein-org-kit sops [list|show SOP_ID] [--workspace DIR] [--json]
bernstein-org-kit scenarios [list|show SCENARIO_ID] [--department DEPT] [--workspace DIR] [--json]
bernstein-org-kit workflows [list|show NAME|compile WORKFLOW] [--workspace DIR] [--json] [--out PATH]
bernstein-org-kit adapters [--available-only] [--include-mock] [--json]
bernstein-org-kit run WORKFLOW --stage STAGE --objective TEXT [--adapter NAME] [--approve-gate GATE ...] [--execute]
```

## Starter kit catalog

<details>
<summary>Full catalog: 36 roles, 16 SOPs, 20 workflows, and 32 scenarios (<code>--preset full</code>)</summary>

### All 36 roles (`--preset full`, every manager ≤ 7 direct reports)

| Role | Reports to | Tier | Human approval gates |
|---|---|---|---|
| `advisor` | `owner` | `tier-1-reasoning` | `money`, `external-send`, `public-post`, `deletion`, `credentials`, `legal-commitment`, `production-change`, `personal-data` |
| `ai-governance-officer` | `ceo` | `tier-1-reasoning` | `legal-commitment`, `personal-data`, `production-change`, `public-post` |
| `ai-red-teamer` | `security-review` | `tier-1-reasoning` | `credentials`, `production-change`, `personal-data` |
| `bi-automations` | `cfo-finance` | `tier-3-extraction` | `personal-data`, `external-send`, `deletion` |
| `ceo` | `owner` | `tier-1-reasoning` | `money`, `external-send`, `public-post`, `deletion`, `credentials`, `legal-commitment`, `production-change`, `personal-data` |
| `cfo-finance` | `owner` | `tier-1-reasoning` | `money`, `legal-commitment`, `personal-data`, `external-send` |
| `chief-of-staff` | `ceo` | `tier-1-reasoning` | `external-send`, `money`, `personal-data` |
| `cloud-infra` | `cto-tech` | `tier-2-coding` | `money`, `production-change`, `credentials`, `deletion` |
| `communications-controller` | `ceo` | `tier-1-reasoning` | `external-send`, `public-post`, `personal-data` |
| `cto-tech` | `owner` | `tier-1-reasoning` | `money`, `deletion`, `credentials`, `production-change`, `personal-data` |
| `data-platform-engineer` | `technical-supervisor` | `tier-2-coding` | `production-change`, `deletion`, `personal-data`, `credentials` |
| `deep-research` | `ceo` | `tier-1-reasoning` | `external-send`, `personal-data`, `money` |
| `finops-controller` | `cfo-finance` | `tier-2-coding` | `money`, `production-change` |
| `hr-director` | `owner` | `tier-1-reasoning` | `personal-data`, `credentials`, `deletion`, `legal-commitment`, `external-send` |
| `legal-assistant` | `legal-director` | `tier-3-extraction` | `external-send`, `personal-data`, `deletion` |
| `legal-compliance` | `legal-director` | `tier-1-reasoning` | `legal-commitment`, `personal-data`, `public-post` |
| `legal-contracts` | `legal-director` | `tier-1-reasoning` | `legal-commitment`, `money`, `external-send`, `personal-data` |
| `legal-counsel` | `legal-director` | `tier-1-reasoning` | `legal-commitment`, `external-send`, `personal-data` |
| `legal-director` | `owner` | `tier-1-reasoning` | `legal-commitment`, `external-send`, `personal-data`, `public-post` |
| `product-manager` | `vp-product` | `tier-1-reasoning` | `external-send`, `public-post`, `personal-data` |
| `qa-eval-engineer` | `technical-supervisor` | `tier-2-coding` | `production-change`, `personal-data` |
| `release-manager` | `technical-supervisor` | `tier-2-coding` | `production-change`, `public-post`, `credentials` |
| `rnd` | `cto-tech` | `tier-1-reasoning` | `money`, `personal-data` |
| `runtime-reliability-engineer` | `technical-supervisor` | `tier-2-coding` | `production-change`, `deletion`, `credentials` |
| `secops-responder` | `security-review` | `tier-2-coding` | `credentials`, `production-change`, `external-send`, `public-post` |
| `security-review` | `cto-tech` | `tier-1-reasoning` | `credentials`, `production-change`, `personal-data` |
| `solutions-architect` | `vp-sales` | `tier-1-reasoning` | `external-send`, `legal-commitment`, `money`, `personal-data` |
| `swarm-coordinator` | `cto-tech` | `tier-1-reasoning` | `money`, `production-change` |
| `tech-automations` | `cto-tech` | `tier-2-coding` | `production-change`, `credentials`, `deletion` |
| `technical-duty` | `technical-supervisor` | `tier-2-coding` | `production-change`, `deletion`, `credentials` |
| `technical-supervisor` | `cto-tech` | `tier-1-reasoning` | `production-change`, `deletion`, `credentials` |
| `upstream` | `cto-tech` | `tier-2-coding` | `public-post`, `external-send`, `legal-commitment` |
| `ux-design-reviewer` | `vp-product` | `tier-2-coding` | `public-post` |
| `vp-product` | `ceo` | `tier-1-reasoning` | `public-post`, `external-send`, `money`, `personal-data` |
| `vp-sales` | `owner` | `tier-1-reasoning` | `external-send`, `public-post`, `legal-commitment`, `money`, `personal-data` |
| `writer-prompter` | `ceo` | `tier-2-coding` | `external-send`, `public-post` |

### All 16 SOPs (`--preset full`)

| ID | Owner | Workflow | Title |
|---|---|---|---|
| `SOP-ENG-01` | `technical-supervisor` | `zero-downtime-migration` | Zero-downtime schema and data migration |
| `SOP-FIN-01` | `cfo-finance` | `procure-to-pay-3way` | Procure-to-pay with three-way match |
| `SOP-FIN-02` | `cfo-finance` | `finops-spend-audit` | Cloud and AI token spend governance |
| `SOP-GOV-01` | `ai-governance-officer` | `ai-governance-review` | AI system risk, OWASP agentic threat and ISO 42001 review |
| `SOP-HR-01` | `hr-director` | `it-service-desk-request` | Service desk and offboarding |
| `SOP-LEGAL-01` | `legal-director` | `legal-adversarial-review` | Adversarial review of external communications |
| `SOP-OPS-01` | `ceo` | `executive-teammate` | Executive teammate |
| `SOP-OPS-02` | `ceo` | `plan-then-execute` | Workflow contract |
| `SOP-OPS-03` | `ceo` | `none` | Surface and attention routing |
| `SOP-OPS-04` | `technical-supervisor` | `incident-postmortem` | Incident response and blameless postmortem |
| `SOP-OPS-05` | `cloud-infra` | `disaster-recovery-drill` | Disaster recovery and isolated backup restore drill |
| `SOP-PROD-01` | `vp-product` | `spec-to-release` | Specification to release feature gate |
| `SOP-SALES-01` | `vp-sales` | `vendor-security-rfp` | Enterprise RFP and customer security questionnaire pack |
| `SOP-SEC-01` | `security-review` | `security-advisory-hotfix` | Vulnerability disclosure and CVE hotfix |
| `SOP-SEC-02` | `security-review` | `mcp-plugin-vetting` | MCP server and third-party plugin security vetting |
| `SOP-SUPPORT-01` | `cto-tech` | `support-resolution` | Support resolution |

### All 20 workflows (`--preset full`)

| Workflow | Stages | Completion node |
|---|---:|---|
| `ai-governance-review` | 6 | `aims-register-entry` |
| `audit-evidence-pack` | 7 | `review-package` |
| `code-review-merge` | 5 | `merge` |
| `deprecation-rollout` | 7 | `remove-old-surface` |
| `disaster-recovery-drill` | 5 | `dr-readiness-receipt` |
| `document-review` | 2 | `review-packet` |
| `executive-teammate` | 6 | `attention-digest` |
| `finops-spend-audit` | 5 | `budget-remediation-plan` |
| `incident-postmortem` | 6 | `postmortem-signoff` |
| `it-service-desk-request` | 6 | `close-request` |
| `legal-adversarial-review` | 10 | `counsel-review-package` |
| `mcp-plugin-vetting` | 5 | `vetting-verdict` |
| `plan-then-execute` | 5 | `final-review` |
| `procure-to-pay-3way` | 6 | `payment-decision` |
| `release-pipeline` | 6 | `deploy-decision` |
| `security-advisory-hotfix` | 6 | `prepare-advisory-release` |
| `spec-to-release` | 6 | `launch-readiness` |
| `support-resolution` | 6 | `close-ticket` |
| `vendor-security-rfp` | 6 | `approved-response-pack` |
| `zero-downtime-migration` | 6 | `migration-signoff` |

</details>

## Tests

```sh
python -m unittest discover -s plugins/org-kit/tests -v
```

Tested on Python 3.12 against Bernstein `3.24.1` and `4.0.x`.

## Licence

Apache-2.0. See `LICENSE` at the repository root and `NOTICE`.
