---
id: SOP-SALES-01
title: Enterprise RFP and customer security questionnaire pack
owner_role: vp-sales
participants: [legal-assistant, solutions-architect, security-review, legal-contracts, legal-compliance, vp-sales]
workflow: vendor-security-rfp
approval_gates: [external-send, legal-commitment, money, personal-data]
---
# SOP-SALES-01 Enterprise RFP and customer security questionnaire pack

## Purpose
Answer enterprise security questionnaires and RFPs accurately from cited evidence with legal and adversarial verification before external send.

## Scope
Covers inbound enterprise RFPs, CAIQ/SIG-lite security questionnaires and architecture due-diligence requests.

## Roles
| Role | Responsibility | Does not |
|---|---|---|
| `legal-assistant` | Parse questionnaire rows and map prior approved evidence | Send drafts to prospects |
| `solutions-architect` | Draft technical architecture answers citing exact docs/code | Promise unshipped roadmap items |
| `security-review` | Verify security, encryption and audit-trail claims | Sign legal representations |
| `legal-contracts` | Review SLA, liability, DPA and commercial redlines | Commit pricing unilaterally |
| `legal-compliance` | Challenge unsupported or overbroad claims before release | Waive legal review |
| `vp-sales` | Assemble final outbound response pack for human external-send gate | Transmit binding terms without owner yes |

## Procedure
1. `legal-assistant` extracts every question into a structured ledger with required evidence fields.
2. `solutions-architect` and `security-review` draft technical and security answers backed by current repository facts.
3. `legal-contracts` and `legal-compliance` review contractual commitments and strip any unverified claim.
4. `vp-sales` packages the verified response and pauses at `external-send` and `legal-commitment` human gates.

## Gates and acceptance
- Human approval gates required before external effect: `external-send, legal-commitment, money, personal-data`.
- Every stage artifact is accepted by an independent reviewer role against its exact SHA-256 in the `bernstein-role-pack` ledger.

## Exceptions and escalation
- An attempt whose outcome is `UNKNOWN` is reconciled under its original attempt ID and never replayed under a new ID.
- Any missing evidence, failing check or unapproved gate halts the workflow fail-closed (`BLOCKED`).

## Records
Parsed question matrix, cited evidence map, legal/compliance redline log and approved response bundle.
