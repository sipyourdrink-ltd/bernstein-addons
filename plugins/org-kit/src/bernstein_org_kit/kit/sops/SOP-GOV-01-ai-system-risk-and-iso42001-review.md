---
id: SOP-GOV-01
title: AI system risk, OWASP agentic threat and ISO 42001 review
owner_role: ai-governance-officer
participants: [ai-governance-officer, ai-red-teamer, qa-eval-engineer, legal-compliance, security-review]
workflow: ai-governance-review
approval_gates: [legal-commitment, personal-data, production-change, public-post]
---
# SOP-GOV-01 AI system risk, OWASP agentic threat and ISO 42001 review

## Purpose
Ensure autonomous and multi-agent workflows maintain an auditable AIMS register entry, risk classification, eval baseline and human-oversight controls.

## Scope
Covers new agentic workflows, model-tier routing changes and periodic ISO/IEC 42001 and EU AI Act readiness reviews.

## Roles
| Role | Responsibility | Does not |
|---|---|---|
| `ai-governance-officer` | Catalog workflow scope, data classes and human approval gates | Issue legal conformity sign-off |
| `ai-red-teamer` | Map OWASP Agentic Top 10 risks and containment controls | Waive security findings |
| `qa-eval-engineer` | Verify held-out outcome evals and repeatability metrics | Approve production rollout |
| `legal-compliance` | Map transparency and data-protection obligations | Commit the company externally |
| `security-review` | Accept technical risk controls against artifact SHA-256 | Self-review authored prompts |

## Procedure
1. `ai-governance-officer` records the workflow purpose, inputs, tools, model tiers and human gates.
2. `ai-red-teamer`, `qa-eval-engineer` and `legal-compliance` run parallel threat, eval-coverage and regulatory assessments.
3. `security-review` verifies that every identified high-severity risk has an enforced technical barrier.
4. `ai-governance-officer` compiles the AIMS register entry for independent review and human gate sign-off.

## Gates and acceptance
- Human approval gates required before external effect: `legal-commitment, personal-data, production-change, public-post`.
- Every stage artifact is accepted by an independent reviewer role against its exact SHA-256 in the `bernstein-role-pack` ledger.

## Exceptions and escalation
- An attempt whose outcome is `UNKNOWN` is reconciled under its original attempt ID and never replayed under a new ID.
- Any missing evidence, failing check or unapproved gate halts the workflow fail-closed (`BLOCKED`).

## Records
AIMS register JSON/Markdown entry, OWASP agentic risk matrix, eval scorecard and human-oversight attestation.
