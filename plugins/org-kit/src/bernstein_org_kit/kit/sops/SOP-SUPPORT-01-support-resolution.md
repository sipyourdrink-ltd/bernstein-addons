---
id: SOP-SUPPORT-01
title: Support resolution
owner_role: cto-tech
participants: [cto-tech, technical-duty, tech-automations, security-review, legal-counsel, technical-supervisor, bi-automations]
workflow: support-resolution
approval_gates: [deletion, money, external-send, personal-data, production-change]
---
# SOP-SUPPORT-01 Support resolution

## Purpose
Resolve customer problems quickly, correctly and within a safe action list. Triage fast. Fix only what is pre-approved and reversible. Prove that the original symptom is gone before telling the customer it is fixed.

## Scope
Covers inbound customer requests and incident tickets on supported channels.

Does not cover legal disputes, claims of financial loss or security incidents. Those go straight to the owning role and never enter automated remediation.

Does not allow destructive actions without a person's approval.

## Roles
| Role | Responsibility | Does not |
|---|---|---|
| `cto-tech` | Owns the procedure and the action allowlist. | Approve its own allowlist changes alone. |
| `technical-duty` | Triages, applies allowlisted actions, writes replies. | Act outside the allowlist. |
| `tech-automations` | Maintains classifiers, probes and the allowlist tooling. | Widen the allowlist alone. |
| `security-review` | Takes suspected security issues. | Reply to the customer unprompted. |
| `legal-counsel` | Takes disputes, legal threats and loss claims. | Decide technical fixes. |
| `technical-supervisor` | Takes escalations after the attempt budget is spent. | Repeat a side effect. |
| `bi-automations` | Retrieves matching runbooks and past solutions. | Take an action on the account. |

## Procedure
1. Record the ticket with its received time. Verify the customer's identity against the account on file before any account-specific action.
2. Triage within the target of 60 seconds from receipt. Record severity, sentiment, risk and the assigned role. Sentiment sets urgency. It never sets a legal conclusion.
3. Late triage is a service failure. The ticket stays with the manager. It is never dropped.
4. Route by kind. Standard questions are answered from the knowledge base. A dispute, legal threat or loss claim goes to `legal-counsel`. An outage or suspected security issue goes to `cto-tech` and `security-review`.
5. Gather read-only evidence: logs and metrics for the customer over the relevant window, and matching runbooks.
6. Decide if an allowlisted action fits. All of these must be true: identity verified, action is on the allowlist, risk is standard, reversible, and confidence is high.
7. Execute the action once, with an idempotency key. Record it.
8. Probe for the original symptom with a check that is independent of the action. An API success response is not a resolution.
9. Reply in plain, short sentences. State what happened, what was done and what to expect. Include no internal credentials, stack traces or other customers' data.
10. Close only with the probe result attached. Otherwise escalate.
11. Log the time to triage, the time to resolve and any follow-up needed.

## Gates and acceptance
Approval gates: `deletion` for any permanent removal. `money` for any refund or credit. `external-send` when a reply goes beyond the template set approved for the channel. `personal-data` when data of one customer is shared with anyone else. `production-change` for any change beyond the allowlist.

Acceptance: an independent probe result, bound by SHA-256 to the evidence file, shows that the original symptom is gone. A second role reviews the ticket record when severity is high.

Response targets by severity are set by `cto-tech` in writing. Missing a target is reported, not hidden.

## Exceptions and escalation
- At most three distinct technical attempts in total, inside the stage deadline. After that, send the full packet to `technical-supervisor`: ticket, attempts, evidence and the next proposed step. Do not repeat a side effect.
- Non-technical holds such as missing authority escalate at once. They do not consume attempts.
- Action outcome UNKNOWN: read the system state under the original action id. Never run the action again with a new key.
- A provider refuses a task: that attempt ends. Do not reword, split or reroute. Send the ticket to a person.
- Hostile or distressed customer: hand the ticket to a person with the full timeline.
- Fail closed. If identity, scope or authority is unclear, escalate.

## Records
- The ticket with received, triaged and closed times.
- Classification result and routing decision.
- Evidence: queries, results and hashes.
- The action taken, its idempotency key and the probe result.
- The customer reply as sent.
- Escalation packets and their outcomes.

Customer data in records is limited to what the case needs and follows the retention policy.
