---
id: SOP-FIN-01
title: Procure-to-pay with three-way match
owner_role: cfo-finance
participants: [cfo-finance, bi-automations, tech-automations, legal-contracts, security-review]
workflow: procure-to-pay-3way
approval_gates: [money, legal-commitment, credentials]
---
# SOP-FIN-01 Procure-to-pay with three-way match

## Purpose
Pay a supplier invoice only when it matches what was ordered and what was received. Stop duplicate, inflated and redirected payments before money moves. Every payment decision is traceable to documents with recorded hashes.

## Scope
Applies to invoices for goods, services and software that are backed by a purchase order.

It covers extraction, matching, fraud checks, approval and the read-back of the payment result.

It does not cover payroll, tax filings, refunds to customers or expense claims. Those have their own procedures.

It does not execute payments. A person with payment authority does that, using the payment system of record.

## Roles
| Role | Responsibility | Does not |
|---|---|---|
| `cfo-finance` | Owns the procedure. Decides on the approval packet. Signs variance waivers. | Pay without a recorded money gate. Review its own packet. |
| `bi-automations` | Builds the line ledger and runs the deterministic match. | Approve, waive or pay. |
| `tech-automations` | Maintains the extraction and match tooling. | Change match rules without CFO review. |
| `legal-contracts` | Reviews contract terms when an invoice conflicts with the agreement. | Decide on payment. |
| `security-review` | Reviews fraud signals and changed bank details. | Contact the supplier on its own initiative. |

The reviewer of a packet must differ from the role that produced it.

## Procedure
1. Collect the purchase order, the goods receipt or service-activation record, and the invoice. Record the SHA-256 of each file.
2. Extract the lines into one ledger. Each line keeps a pointer to its source page. Use one explicit currency. An exchange rate needs a dated source and a human authorisation. The extraction step must not infer a rate.
3. Run the deterministic match per line.
   - Previously invoiced quantity plus invoiced quantity must not exceed received quantity.
   - Received quantity must not exceed ordered quantity.
   - Invoice unit price must equal the order unit price, or be inside a written tolerance.
   - Invoice total plus committed spend must stay under the budget limit.
4. Treat any of these as a hold, not a pass: missing input, negative or non-numeric amount, duplicate invoice number, changed bank details, unexplained variance, cap breach.
5. Compare the supplier's bank details with the last verified record. A change triggers a security hold. Confirm the change through a channel already on file, never through contact details printed on the invoice.
6. Check for split invoices: several invoices from one supplier just under an approval threshold.
7. Assemble the approval packet: ledger, match result, fraud result and the three source hashes.
8. An independent reviewer checks the packet against the exact hashes. The reviewer records pass or hold.
9. `cfo-finance` decides. The decision names the invoice, amount, currency, beneficiary and settlement date.
10. A person with payment authority executes the payment through the approved system. The money gate is recorded before execution.
11. Read back the payment status from the payment system. Reconcile it to the ledger and attach the proof of payment to the record.

## Gates and acceptance
Approval gates: `money` before any disbursement. `legal-commitment` when a variance waiver changes contract terms. `credentials` when payment-system access is needed. An agent never holds payment credentials.

Acceptance: the independent reviewer accepts the packet against the exact SHA-256 of the invoice, the ledger and the order. If any hash changes, review starts again.

Zero variance is the default. A variance needs a written waiver from `cfo-finance` that names the amount and the reason.

A retry of a payment reuses the original idempotency key. Never create a second payment for the same invoice.

## Exceptions and escalation
- Changed bank details: freeze the invoice. Alert `cfo-finance` and `security-review`. Resume only after out-of-band confirmation is recorded.
- Payment status UNKNOWN: reconcile under the original payment id by reading the system of record. Never submit a new payment.
- Matching tool failure: fix the tool or hold the invoice. Do not approve by hand to meet a date.
- Invoice unmatched past its due date: alert `legal-contracts` so the supplier relationship is handled deliberately.
- A role cannot reach a conclusion: it records the blocker and the exact missing input, then stops. It does not guess.
- Fail closed. When evidence is missing, the answer is hold.

## Records
Keep for every invoice, as written records:
- source files with SHA-256 hashes
- the line ledger and the match result
- the fraud review result and the reviewer identity
- the CFO decision, any waiver and its reason
- the payment id, proof of payment and the reconciliation entry

Records are append-only. A correction is a new entry that points to the old one.
