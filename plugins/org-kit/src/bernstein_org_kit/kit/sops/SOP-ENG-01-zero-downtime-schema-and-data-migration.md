---
id: SOP-ENG-01
title: Zero-downtime schema and data migration
owner_role: technical-supervisor
participants: [data-platform-engineer, runtime-reliability-engineer, qa-eval-engineer, security-review, release-manager, technical-supervisor]
workflow: zero-downtime-migration
approval_gates: [production-change, deletion, personal-data, credentials]
---
# SOP-ENG-01 Zero-downtime schema and data migration

## Purpose
Execute database schema changes and data backfills using expand-contract phases with verified parity and rehearsed rollback.

## Scope
Covers relational/document schema migrations, index builds and ETL backfills touching stateful services.

## Roles
| Role | Responsibility | Does not |
|---|---|---|
| `data-platform-engineer` | Author expand/backfill/contract scripts and down-migration | Run destructive drops in prod |
| `runtime-reliability-engineer` | Rehearse rollback timing and lock/replication impact | Self-approve migration scripts |
| `qa-eval-engineer` | Verify row counts, checksums and dual-read compatibility | Bypass parity mismatches |
| `security-review` | Check access controls, PII handling and backup encryption | Approve production cutover |
| `release-manager` | Sequence deployment waves around migration phases | Execute prod cutover without gate |
| `technical-supervisor` | Accept complete migration package before human prod gate | Skip rollback rehearsal |

## Procedure
1. `data-platform-engineer` writes additive expand migration, idempotent backfill and explicit rollback script.
2. `runtime-reliability-engineer`, `qa-eval-engineer` and `security-review` verify rollback rehearsal, row-hash parity and data-protection rules in staging.
3. `release-manager` sequences application and schema rollout steps with explicit abort thresholds.
4. `technical-supervisor` verifies all SHA-256 receipts and pauses at `production-change` (and `deletion` before any contract/drop phase).

## Gates and acceptance
- Human approval gates required before external effect: `production-change, deletion, personal-data, credentials`.
- Every stage artifact is accepted by an independent reviewer role against its exact SHA-256 in the `bernstein-role-pack` ledger.

## Exceptions and escalation
- An attempt whose outcome is `UNKNOWN` is reconciled under its original attempt ID and never replayed under a new ID.
- Any missing evidence, failing check or unapproved gate halts the workflow fail-closed (`BLOCKED`).

## Records
Migration SQL/code SHA-256, staging rollback timing log, row-parity receipt and cutover runbook.
