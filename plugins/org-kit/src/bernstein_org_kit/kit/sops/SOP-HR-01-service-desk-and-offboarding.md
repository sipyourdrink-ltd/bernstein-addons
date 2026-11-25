---
id: SOP-HR-01
title: Service desk and offboarding
owner_role: hr-director
participants: [hr-director, legal-compliance, legal-contracts, cfo-finance, cto-tech, security-review, tech-automations, technical-duty]
workflow: it-service-desk-request
approval_gates: [personal-data, credentials, deletion, legal-commitment, money]
---
# SOP-HR-01 Service desk and offboarding

## Purpose
Handle internal people requests in one traceable path: access requests, equipment, role changes and the end of a person's engagement. Make sure access is removed on time, records are kept, and nothing that creates a legal or financial obligation happens without a person approving it.

## Scope
Covers requests from staff and contractors to the service desk, and the offboarding checklist.

Does not cover recruitment, performance management, disciplinary matters or compensation changes. Those need their own owner and human decisions.

All legal output is a draft for qualified human counsel. It is not legal advice. A licensed person decides, signs and sends.

Does not decide any legal entitlement. Entitlements come from the contract and the governing law as confirmed by `legal-compliance`.

## Roles
| Role | Responsibility | Does not |
|---|---|---|
| `hr-director` | Owns the procedure and the request queue. Checks completeness. | Approve its own legal conclusions. |
| `legal-compliance` | Confirms which rules apply, from primary sources. | Sign or send documents. |
| `legal-contracts` | Reviews release and separation wording. | Waive anyone's rights. |
| `cfo-finance` | Independently checks calculations and final payments. | Calculate and approve the same figure. |
| `cto-tech` | Owns access removal and device return. | Keep access "just in case". |
| `security-review` | Verifies that access was actually removed. | Approve exceptions alone. |
| `tech-automations` | Prepares the routine access or equipment change. | Apply a change before it is reviewed. |
| `technical-duty` | Closes the routine request and records the result. | Close a request without the approval record. |

## Procedure
1. Log each request with a requester, a category, the date and the data it touches. One request is one record.
2. Classify the request: access, equipment, information, role change, offboarding. Check the requester's identity against the directory before acting.
3. Route routine requests to the workflow `it-service-desk-request`. The executor works inside the scope of the request only.
4. For access requests, apply least privilege. Grant the minimum that the stated task needs. Record the approver.
5. For offboarding, open a checklist the day the end date is confirmed in writing.
6. Collect the facts: contract, dates, role, records held. Record the source of each fact.
7. `legal-compliance` states which rules apply and cites the primary source and version. A title of a law is not a citation.
8. `cfo-finance` independently recomputes any figure from the records. Differences are listed and explained.
9. `legal-contracts` drafts release or confirmation wording from the confirmed rules only. Drafting does not authorise signing.
10. `hr-director` checks that the package is complete. The reviewer accepts it against the exact SHA-256 of each document.
11. On the end date, `cto-tech` removes access, revokes keys and collects devices. `security-review` reads back the identity system and confirms removal.
12. Archive the case. Deleting any personal record needs a recorded deletion gate and a retention check.

## Gates and acceptance
Approval gates:
- `personal-data` before personal data is shared outside the case team.
- `credentials` before any credential is issued, reset or revoked outside standard automation.
- `deletion` before records or accounts are removed.
- `legal-commitment` before any signed release, waiver or settlement.
- `money` before any final payment.

Acceptance: the independent reviewer accepts each document against its exact hash. Any later edit voids the acceptance.

Deadlines come from a verified source and a confirmed trigger date. A generic rule of thumb is not a deadline.

## Exceptions and escalation
- Records disagree: preserve both, escalate to `legal-compliance` and `cfo-finance`. Do not pick one silently.
- Missing records or disputed rights: stop drafting, preserve evidence, escalate to `legal-director` with the facts.
- Access removal fails or is partial: escalate to `cto-tech` the same day with the system, account and error.
- Request outcome UNKNOWN: read the system state under the original request id. Do not open a duplicate.
- A provider refuses a task: that attempt ends. Record it. Do not reword or reroute.
- Fail closed. If authority is unclear, the action waits.

## Records
- The request log with identity check result and approver.
- Source facts with locators and hashes.
- Rule citations with versions.
- The calculation worksheet and the independent recomputation.
- Final documents, review records and acceptance hashes.
- The access-removal read-back and the archive entry.

Keep records only as long as policy requires. Personal data in records is limited to what the case needs.
