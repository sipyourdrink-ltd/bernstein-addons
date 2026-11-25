---
id: SOP-OPS-04
title: Incident response and blameless postmortem
owner_role: technical-supervisor
participants: [technical-duty, runtime-reliability-engineer, qa-eval-engineer, tech-automations, security-review, technical-supervisor]
workflow: incident-postmortem
approval_gates: [production-change, deletion, external-send]
---
# SOP-OPS-04 Incident response and blameless postmortem

## Purpose
Restore service safely, establish a cited timeline from logs and convert root causes into tested engineering guardrails.

## Scope
Covers production outages, SLO error-budget breaches and high-severity operational regressions.

## Roles
| Role | Responsibility | Does not |
|---|---|---|
| `technical-duty` | Contain impact and coordinate safe rollback proposal | Run unapproved destructive commands |
| `runtime-reliability-engineer` | Extract UTC timeline, metrics and deployment diffs | Speculate without log evidence |
| `qa-eval-engineer` | Turn the incident trigger into a deterministic regression test | Close incident without reproduction |
| `tech-automations` | Implement corrective code/config guardrail | Self-accept corrective patch |
| `security-review` | Verify no credential leak or security boundary breach occurred | Send external notifications |
| `technical-supervisor` | Review blameless postmortem and corrective-action receipts | Waive human production gate |

## Procedure
1. `technical-duty` triages blast radius and proposes immediate revert/mitigation (gated on `production-change`).
2. `runtime-reliability-engineer` assembles the exact UTC event timeline while `qa-eval-engineer` reproduces the failure mode.
3. `security-review` checks whether the incident exposed data or credentials.
4. `tech-automations` drafts the permanent fix against the new regression test.
5. `technical-supervisor` reviews and accepts the blameless postmortem and tracked remediation items.

## Gates and acceptance
- Human approval gates required before external effect: `production-change, deletion, external-send`.
- Every stage artifact is accepted by an independent reviewer role against its exact SHA-256 in the `bernstein-role-pack` ledger.

## Exceptions and escalation
- An attempt whose outcome is `UNKNOWN` is reconciled under its original attempt ID and never replayed under a new ID.
- Any missing evidence, failing check or unapproved gate halts the workflow fail-closed (`BLOCKED`).

## Records
UTC timeline log, reproduction test SHA-256, corrective patch SHA-256 and signed postmortem document.
