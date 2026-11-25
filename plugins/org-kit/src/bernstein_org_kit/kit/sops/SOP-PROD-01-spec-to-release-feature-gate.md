---
id: SOP-PROD-01
title: Specification to release feature gate
owner_role: vp-product
participants: [product-manager, ux-design-reviewer, tech-automations, qa-eval-engineer, security-review, vp-product]
workflow: spec-to-release
approval_gates: [production-change, public-post, external-send, personal-data]
---
# SOP-PROD-01 Specification to release feature gate

## Purpose
Move a user-facing feature from problem statement through UX/accessibility, implementation, QA evals and security review without skipped gates.

## Scope
Applies to net-new product features and user-visible workflow changes.

## Roles
| Role | Responsibility | Does not |
|---|---|---|
| `product-manager` | Author PRD with non-goals, state matrix and acceptance criteria | Publish unreviewed announcements |
| `ux-design-reviewer` | Audit interaction states, WCAG contrast and copy clarity | Alter backend auth logic |
| `tech-automations` | Implement feature and unit tests against the PRD | Self-accept implementation |
| `qa-eval-engineer` | Run end-to-end functional and regression verification | Approve own code |
| `security-review` | Check permissions, input validation and data exposure | Bypass failing tests |
| `vp-product` | Accept launch readiness packet ahead of human deploy/post gates | Deploy without human approval |

## Procedure
1. `product-manager` writes the PRD and acceptance checklist.
2. `ux-design-reviewer` checks state completeness and contrast while `tech-automations` builds the implementation.
3. `qa-eval-engineer` and `security-review` independently test behaviour, accessibility states and security boundaries.
4. `vp-product` reviews all accepted SHA-256 artifacts and prepares the launch gate request.

## Gates and acceptance
- Human approval gates required before external effect: `production-change, public-post, external-send, personal-data`.
- Every stage artifact is accepted by an independent reviewer role against its exact SHA-256 in the `bernstein-role-pack` ledger.

## Exceptions and escalation
- An attempt whose outcome is `UNKNOWN` is reconciled under its original attempt ID and never replayed under a new ID.
- Any missing evidence, failing check or unapproved gate halts the workflow fail-closed (`BLOCKED`).

## Records
PRD SHA-256, UX/contrast audit, implementation diff SHA-256, QA scorecard, security verdict and launch packet.
