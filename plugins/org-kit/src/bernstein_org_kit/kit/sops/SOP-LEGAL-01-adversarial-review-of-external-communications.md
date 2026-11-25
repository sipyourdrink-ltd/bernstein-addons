---
id: SOP-LEGAL-01
title: Adversarial review of external communications
owner_role: legal-director
participants: [legal-director, legal-counsel, legal-contracts, legal-compliance, legal-assistant]
workflow: legal-adversarial-review
approval_gates: [external-send, legal-commitment, public-post, personal-data]
---
# SOP-LEGAL-01 Adversarial review of external communications

## Purpose
Make every legally significant external message correct before it leaves the company. Each claim must rest on a checked source. Each draft is attacked by a reviewer whose job is to find what is wrong with it. A person decides whether it is sent.

## Scope
Covers contracts, formal notices, regulatory submissions, dispute letters and any external text that states a legal position or creates an obligation.

Internal advice is also reviewed. A draft label does not permit unsupported legal statements.

All legal output is a draft for qualified human counsel. It is not legal advice. A licensed person decides, signs and sends.

It does not send, file or sign anything. The workflow ends with an approved package that a person may use.

## Roles
| Role | Responsibility | Does not |
|---|---|---|
| `legal-director` | Owns the procedure. Gives the review sign-off on the final package. | Sign off its own draft. |
| `legal-counsel` | Writes the intake, runs the challenge pass, synthesises the verdict. | Act as its own critic. |
| `legal-contracts` | Argues the strongest supported position and drafts wording. | Critique its own work. |
| `legal-compliance` | Validates citations and confirms that rules apply. | Advocate a position. |
| `legal-assistant` | Extracts facts and organises sources. | Decide what the law means. |

Advocates and the critic are different agents or sessions. A role never reviews its own artifact.

## Procedure
1. Open one run per case. Write the intake: question, jurisdiction, as-of date, authorised scope and the list of captured source files. Do not reuse another case's outputs.
2. Extract facts. Each fact gets an id, an exact value, a source file, a SHA-256 hash and a page or section locator. Mark gaps and conflicts. Separate allegations from documented facts.
3. Validate citations. For each legal claim, read the primary source itself. Record the provision, the version and effective date, the event date, a short exact quote and the source hash. A search snippet or a model's memory is not a source.
4. Require independent corroboration. Two copies of the same source are not independent. Where no second source exists, say so.
5. Run the challenge pass. Test source independence, jurisdiction, version, missing exhibits, adverse authority, chronology, amounts and overstatement. Every material finding is resolved before the next step.
6. Run two advocacy branches on the same cleared evidence. One argues rights and remedies. The other argues obligations, exclusions and lawful alternatives. Both must finish.
7. An independent critic attacks both branches. It tests enforceability, waiver, counterclaims, deadlines, jurisdiction and unsupported claims. It lists vetoes with severity and remedy.
8. A timeout, a malformed result or a non-zero exit from the critic is a blocker. It cannot be waived. Repair and review again.
9. `legal-counsel` writes the verdict: approved and rejected claims, alternatives, risk level, confidence and its basis, and conditions.
10. Draft the final text from approved claims only. Map every factual and legal statement to a claim id or exhibit. Check quotes, sums, recipients and attachments.
11. `legal-director` writes the review sign-off. It binds the verdict hash and the final document hash and states residual risk.
12. Prepare the owner package: recipient, channel, timing, the text and the approval needed.

## Gates and acceptance
Approval gates: `external-send` for any send. `public-post` for anything published. `legal-commitment` for signature or settlement. `personal-data` before any attachment or disclosure containing personal data.

Acceptance: the review sign-off is bound to the exact SHA-256 of the final document. Any edit, including translation or attachment changes, requires renewed review.

Estimates of outcomes are labelled as estimates with dated evidence and assumptions. They are never presented as legal certainty. Hypotheses and model inferences must not appear as established fact in the final text.

## Exceptions and escalation
- Missing material fact: record the blocker and the exact fact needed. Finish the unaffected parts.
- Unresolved red risk: keep it as a blocker, or escalate to the human owner with a concrete evidence-backed choice.
- A provider refuses a task: the attempt ends. Do not reword, split or reroute it. Record it and let the human decide.
- Stage outcome UNKNOWN: reconcile under the original run and stage id. Never restart as a new run.
- Deadlines need an applicable provision and a trigger date. A statute title alone is not a citation.
- Fail closed. No review sign-off, no send.

## Records
Per run, in one directory: intake, facts, citations, challenge findings, both advocacy results, critique, verdict, draft, final document, sign-off and a receipt for each stage with input hashes, author role, status and exit code.

Accepted artifacts are immutable. A change creates a new run that references the old one.
