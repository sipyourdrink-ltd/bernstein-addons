---
id: SOP-OPS-05
title: Disaster recovery and isolated backup restore drill
owner_role: cloud-infra
participants: [cloud-infra, runtime-reliability-engineer, data-platform-engineer, security-review, cto-tech]
workflow: disaster-recovery-drill
approval_gates: [production-change, credentials, deletion, personal-data]
---
# SOP-OPS-05 Disaster recovery and isolated backup restore drill

## Purpose
Prove backups actually restore within stated RTO/RPO bounds inside an isolated environment without touching live production.

## Scope
Applies to scheduled backup verification drills for datastores, task ledgers and configuration snapshots.

## Roles
| Role | Responsibility | Does not |
|---|---|---|
| `cloud-infra` | Provision isolated restore sandbox and inventory snapshot metadata | Overwrite live production volumes |
| `runtime-reliability-engineer` | Execute restore runbook and measure elapsed RTO/RPO | Skip health/smoke checks |
| `data-platform-engineer` | Verify restored table checksums, WAL continuity and schema integrity | Export raw PII outside sandbox |
| `security-review` | Confirm sandbox network isolation, secret redaction and clean teardown | Retain unencrypted copies |
| `cto-tech` | Accept DR readiness receipt and escalate any RPO/RTO gap | Self-review own infra scripts |

## Procedure
1. `cloud-infra` records latest snapshot timestamps and stands up a network-isolated restore target.
2. `runtime-reliability-engineer` runs the restore procedure from documentation alone and measures wall-clock RTO and RPO.
3. `data-platform-engineer` validates data integrity and hash-chain continuity; `security-review` verifies isolation and clean disposal.
4. `cto-tech` signs off the DR readiness receipt or files blocking remediation tasks.

## Gates and acceptance
- Human approval gates required before external effect: `production-change, credentials, deletion, personal-data`.
- Every stage artifact is accepted by an independent reviewer role against its exact SHA-256 in the `bernstein-role-pack` ledger.

## Exceptions and escalation
- An attempt whose outcome is `UNKNOWN` is reconciled under its original attempt ID and never replayed under a new ID.
- Any missing evidence, failing check or unapproved gate halts the workflow fail-closed (`BLOCKED`).

## Records
Snapshot manifest, measured RTO/RPO timings, integrity checksum report and sandbox disposal receipt.
