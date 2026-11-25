---
id: SOP-OPS-02
title: Workflow contract
owner_role: ceo
participants: [ceo, cto-tech, technical-supervisor, hr-director, swarm-coordinator, runtime-reliability-engineer, tech-automations, security-review]
workflow: plan-then-execute
approval_gates: [money, external-send, public-post, deletion, credentials, legal-commitment, production-change, personal-data]
---
# SOP-OPS-02 Workflow contract

## Purpose
Define how any task moves from request to a verified result. Every task has one accountable executor, a stated scope, a recorded result and an independent check. This is the base contract. Other procedures add domain rules on top of it.

## Scope
Applies to every role and every workflow in the company.

Domain procedures for finance, legal and releases stay in force and may add stricter steps. They cannot remove steps from this one.

## Roles
| Role | Responsibility | Does not |
|---|---|---|
| `ceo` | Sets priority. Delegates with a complete brief. Checks returned evidence. | Repeat a specialist's work. |
| `cto-tech` | Owns technical delivery and the tooling behind the contract. | Accept its own team's artifact alone. |
| `technical-supervisor` | Supervises technical executors and recovery. | Change scope. |
| `hr-director` | Keeps roles, scopes and reporting lines current. | Override a gate. |
| `swarm-coordinator` | Runs parallel work with disjoint write sets. | Merge results without the join conditions. |
| `runtime-reliability-engineer` | Keeps the runner, queues and receipts healthy. | Mark a failed run as passed. |
| `tech-automations` | Runs the tests and checks of the implemented step. | Accept the result it tested. |
| `security-review` | Gives the final independent review. | Review its own changes. |

Span of control is at most seven direct reports. Each role has one manager. A task has one accountable executor.

## Procedure
1. Intake. Record the task id, the outcome wanted, input references with hashes, the authority, constraints, budget and deadline. List the unknowns.
2. Resolve facts from files and tools before asking a person. Ask only for a material fact that cannot be found, or for a decision outside delegated scope.
3. Plan only when the work has real dependencies. A plan names the executor, the inputs, the expected output and the check for each step.
4. Preflight. Check only what the next action needs. Record unavailable capabilities as unavailable.
5. Execute. Independent steps with disjoint writes run in parallel on the same evidence snapshot. A join waits for all of its declared inputs.
6. Verify. Check the output against the outcome, the input hashes and the domain rules. An exit code of zero is necessary but not enough without a valid output.
7. Review. An independent reviewer, not the producer, accepts the artifact against its exact SHA-256.
8. Read back. Open the saved file or state. Report what was observed: complete, partial or blocked.
9. Hand off. Name the next owner and the next action. A dispatched task or a scheduled job is not completed work.
10. Record the attempt data and one line on the bottleneck seen and the next owned fix.

## Gates and acceptance
Approval gates: all eight gates apply when the task reaches them. `money`, `external-send`, `public-post`, `deletion`, `credentials`, `legal-commitment`, `production-change` and `personal-data` each need a recorded human approval for the exact action. Approval for one action does not carry to another. Preparing an action is allowed before approval. Executing it is not.

Acceptance: the reviewer binds acceptance to the artifact hash. A changed artifact is a new review.

Done means the artifact or state exists, the checks passed, the review is recorded and the result was read back.

## Exceptions and escalation
Recovery is bounded: three attempts in total, the first plus at most two materially different, supported alternatives. A tighter limit in the brief wins.
1. Attempt one: capture the error, the inputs and the artifact. Classify the failure.
2. Attempt two: change one supported variable such as an input, a schema or a documented equivalent tool.
3. Attempt three: one more evidence-driven change with a predicted result. If it fails, save partial output and escalate the exact dependency.

Rules:
- Repeating the same request or changing wording is not a different attempt.
- An attempt whose outcome is UNKNOWN is reconciled under its original id. It is never resubmitted as new.
- A provider refusal ends that attempt. Do not reword, split, hide context or move the task to another provider to obtain the refused result. Disputes go to the provider in writing.
- A missing authorisation, evidence gap or legal veto is not a technical failure and is not retried.
- Use each provider only under its own terms and with the operator's own credentials.
- Fail closed.

## Records
One task record in the task system. It holds the objective, executor, inputs and hashes, authority, budget, state, check, result and blocker.

Reviews, receipts and source evidence stay as separate artifacts. Records are written, append-only and readable by the reviewer.
