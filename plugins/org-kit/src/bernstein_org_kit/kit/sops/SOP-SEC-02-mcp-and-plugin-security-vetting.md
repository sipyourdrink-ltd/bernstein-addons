---
id: SOP-SEC-02
title: MCP server and third-party plugin security vetting
owner_role: security-review
participants: [secops-responder, security-review, ai-red-teamer, legal-compliance, cto-tech]
workflow: mcp-plugin-vetting
approval_gates: [credentials, production-change, personal-data, legal-commitment]
---
# SOP-SEC-02 MCP server and third-party plugin security vetting

## Purpose
Prevent supply-chain compromise, tool privilege abuse and indirect prompt injection before enabling any external MCP server or agent plugin.

## Scope
Applies to every third-party MCP server, CLI plugin, skill bundle or webhook connector prior to workspace or production enablement.

## Roles
| Role | Responsibility | Does not |
|---|---|---|
| `secops-responder` | Pin commit/digest, license and declared environment/network permissions | Install unvetted packages in prod |
| `security-review` | Audit code and dependencies for secret exfiltration or shell injection | Grant production credentials |
| `ai-red-teamer` | Assess OWASP Agentic tool-misuse and prompt-injection surfaces | Probe external vendor hosts |
| `legal-compliance` | Verify license compatibility and personal-data handling terms | Sign vendor agreements |
| `cto-tech` | Issue technical vetting verdict gated on human credential/prod approval | Self-accept own plugin code |

## Procedure
1. `secops-responder` inventories exact package digests, required env vars, filesystem paths and outbound endpoints.
2. `security-review` runs static and dependency inspection on the pinned source tree.
3. `ai-red-teamer` evaluates tool schemas and untrusted-output pathways for goal hijack and privilege escalation.
4. `legal-compliance` checks open-source license obligations and data-processing boundaries.
5. `cto-tech` records the allow/deny verdict with pinned SHA-256 and required sandbox constraints.

## Gates and acceptance
- Human approval gates required before external effect: `credentials, production-change, personal-data, legal-commitment`.
- Every stage artifact is accepted by an independent reviewer role against its exact SHA-256 in the `bernstein-role-pack` ledger.

## Exceptions and escalation
- An attempt whose outcome is `UNKNOWN` is reconciled under its original attempt ID and never replayed under a new ID.
- Any missing evidence, failing check or unapproved gate halts the workflow fail-closed (`BLOCKED`).

## Records
Permission manifest, pinned digest, threat review report, compliance check and signed vetting verdict.
