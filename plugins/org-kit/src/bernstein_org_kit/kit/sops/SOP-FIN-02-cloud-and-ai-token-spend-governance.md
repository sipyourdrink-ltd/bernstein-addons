---
id: SOP-FIN-02
title: Cloud and AI token spend governance
owner_role: cfo-finance
participants: [bi-automations, finops-controller, runtime-reliability-engineer, security-review, cfo-finance]
workflow: finops-spend-audit
approval_gates: [money, production-change]
---
# SOP-FIN-02 Cloud and AI token spend governance

## Purpose
Detect runaway agent retry loops, misrouted frontier-model spend and idle cloud resources while preserving SLOs and security review floors.

## Scope
Applies to monthly FinOps reviews and anomalous spend alerts across cloud infrastructure and AI model providers.

## Roles
| Role | Responsibility | Does not |
|---|---|---|
| `bi-automations` | Extract normalized billing and token-ledger tables | Change routing configs |
| `finops-controller` | Attribute unit cost per workflow and propose rightsizing | Lower security review floors |
| `runtime-reliability-engineer` | Check latency, retry and SLO impact of proposed changes | Approve budget changes |
| `security-review` | Verify compliance and review-tier floors remain intact | Authorize vendor spend |
| `cfo-finance` | Assemble budget remediation package for human money/prod gates | Execute unapproved plan changes |

## Procedure
1. `bi-automations` compiles period-over-period cloud and token spend by workflow and role.
2. `finops-controller` isolates cost spikes, context bloat and model-tier routing inefficiencies.
3. `runtime-reliability-engineer` and `security-review` verify proposed caps or tier shifts do not degrade SLOs or governance floors.
4. `cfo-finance` signs off the remediation plan and escalates any spend/production change to the human owner.

## Gates and acceptance
- Human approval gates required before external effect: `money, production-change`.
- Every stage artifact is accepted by an independent reviewer role against its exact SHA-256 in the `bernstein-role-pack` ledger.

## Exceptions and escalation
- An attempt whose outcome is `UNKNOWN` is reconciled under its original attempt ID and never replayed under a new ID.
- Any missing evidence, failing check or unapproved gate halts the workflow fail-closed (`BLOCKED`).

## Records
Normalized spend ledger, anomaly decomposition, SLO/security check receipts and approved budget remediation plan.
