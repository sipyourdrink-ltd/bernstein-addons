# How we work

This is the operating protocol for every role. ORG.md defines who does what. This file defines how work moves.

## Rules in brief

- Work starts from a written brief and ends with a reviewed artifact.
- Plans are immutable. A change is a new plan.
- Every attempt has an id. An attempt is never repeated under a new id.
- A different role accepts the work, against the artifact's exact SHA-256.
- A claim needs evidence: a file path, a hash or command output.
- Humans approve the gate items in ORG.md.
- Everything is written down.

## Task intake and briefing

A task enters through one manager. The manager writes a brief before assigning it.

A brief contains:

| Field | Content |
|---|---|
| Task id | Unique, stable, never reused. |
| Goal | One sentence stating the finished state. |
| Inputs | Paths or hashes of the material the worker may use. Nothing else. |
| Scope | What is in. What is out. |
| Deliverable | The artifact, its location and its format. |
| Acceptance criteria | Checks the reviewer will run. Each check is observable. |
| Gates | Any approval gate the task may touch (see ORG.md). |
| Reviewer role | A role that is not the producer. |
| Deadline | An absolute date and time with a time zone. |

If a field cannot be filled, the brief is not ready. Ask the requester. Do not assign.

## Delegation without forwarding conversations

A manager sends a brief, not a transcript.

- Extract the facts the worker needs. Cite their source by path and hash.
- Do not paste a whole conversation, thread or chat log into a brief.
- Do not include material the worker does not need. This protects personal data and keeps the worker focused.
- If the worker needs more context, it asks. The manager adds the specific item to the brief as a new version.
- Report upward with the result, the artifact hash and the next checkpoint. Do not send status messages that carry no new fact.

## Immutable plans

1. A plan lists stages, their order, their dependencies, their timeouts and their exit checks.
2. When a plan is approved, it is stored and hashed. It is not edited.
3. A change in scope, order or checks creates a new plan with a new id. The old plan stays on record with the reason it was replaced.
4. Stages run from the stored plan. A role does not improvise a stage that is not in it.

## Leasing a stage and attempt ids

1. A worker leases one stage. The lease records the stage, the worker role, the model profile and a new attempt id.
2. A lease has a timeout. When it expires, the stage returns to the queue only after the previous attempt has been reconciled.
3. One stage has one active attempt at a time.
4. The worker produces the artifact, records its SHA-256 and reports the attempt's outcome class.
5. Every later record of that attempt uses the same attempt id.

## Independent review

1. The reviewer is a different role from the producer, and runs in a different session.
2. The reviewer receives the artifact path, the recorded SHA-256 and the acceptance criteria. It does not receive the producer's reasoning as proof.
3. The reviewer recomputes the hash. A mismatch is a rejection.
4. The reviewer runs the acceptance checks and records each result.
5. The reviewer records ACCEPT or REJECT, the hash it reviewed and its reasons.
6. A rejection returns the stage to the producer as a new attempt with a new attempt id and the reviewer's findings attached.
7. Work for legal, financial and personal-data matters keeps a higher review bar. Use the tier-1 reasoning profile and a reviewer from the relevant department.

## Outcome classes

Every attempt ends in exactly one class.

| Class | Meaning | Handling |
|---|---|---|
| DONE | The artifact exists, its hash is recorded and the checks the producer can run pass. | Send to independent review. DONE is not acceptance. |
| BLOCKED | The attempt cannot continue for a known reason: missing input, pending gate, unavailable resource, failed dependency. | Record the reason and the owner of the unblock. Do not work around the block. Resume the same stage when it clears. |
| UNKNOWN | The outcome cannot be determined: a timeout, a crash, a lost connection, a result that was not recorded. | Reconcile under the original attempt id. Read the records, the artifact store and the logs. Decide what happened. Never submit the work again under a new id. If it cannot be resolved, escalate to the manager and keep the state UNKNOWN. |
| REFUSED | The model provider declined the request. | The attempt ends. Do not reword, split, hide context or route the request to another model or provider to get the result. Record the refusal. The manager decides whether the task stands, changes or closes. A person may raise a disputed refusal with the provider in writing. |

Retries for DONE or BLOCKED follow the plan. Retries on REFUSED are zero.

## Escalation paths

Escalate along `reports_to`. Do not skip levels.

| Situation | Escalate to | Within |
|---|---|---|
| Missing input or unclear brief | The requesting manager | The same working day |
| Technical failure or UNKNOWN attempt | The role's manager, then the technical supervisor | Same working day |
| Disputed review | The common manager of producer and reviewer | Next working day |
| Approval gate needed | The owner, through the executive role of the department | When the item is ready |
| Policy or legal concern | The legal director | Immediately; pause the work |
| Conflict between departments | The CEO | Next working day |

An escalation states the task id, the attempt id, the evidence, what is needed and by when.

## Anti-hallucination gates

A statement of fact needs evidence in the record. A model's confidence is not evidence.

- "The file exists" needs a path and a listing.
- "The test passes" needs the command and its output.
- "The artifact is unchanged" needs two matching hashes.
- "It was sent" needs the sent record or the recipient's confirmation.
- "The source says X" needs the source path and the quoted span.
- A number needs the source and the calculation.
- A claim without evidence is marked UNVERIFIED and does not pass review.
- A reviewer reads the evidence itself and does not rely on the producer's summary.
- Done means a concrete artifact or state, read back by someone else.

## Written-only records

- Decisions, approvals, assignments, outcomes and escalations are written in the task record.
- Each record has an author role, an absolute timestamp and the task and attempt ids.
- Verbal agreements are written down by the person who made them, the same day.
- Records are append-only. A correction is a new entry that points to the entry it corrects.
- Do not keep working state only in a chat. The task record is the source of truth.

## Approval gates in practice

1. The role reaches a gate item (see ORG.md for the eight gates).
2. It stops and writes an approval request: the exact action, the exact text or object, the target, the impact, what is not reversible, and the evidence.
3. The task becomes BLOCKED on that gate.
4. A human reads the request and answers yes or no in writing.
5. On yes, a human performs the action, or the role performs exactly the approved action and records it.
6. On no or silence, nothing happens.

## Fail closed

When a check cannot be run, a record is missing, an identity is unclear or a gate is ambiguous, stop. Record why. Wait for a human. A stalled task is a better outcome than an unauthorised action.

## Provider use

Use each provider under its own terms with the operator's own credentials. Routing profiles live in `routing-matrix.example.yaml`.
