# ADR-0010 — The Action Lifecycle and How a Correction Becomes a Rule

**Status:** Accepted
**Date:** 2026-07-29
**Depends on:** [ADR-0002](./ADR-0002-monday-identity-and-idempotency.md), [ADR-0003](./ADR-0003-gmail-access-and-retention.md), [ADR-0006](./ADR-0006-approval-gate-and-verified-writes.md), [ADR-0008](./ADR-0008-label-scoped-daily-action-loop.md), [ADR-0009](./ADR-0009-review-engine-implementation.md)

ADR-0009 built a review that decides and then stops. This ADR decides how an approval becomes a change to the mailbox, and how a repeated correction becomes a rule without ever doing so by itself.

## Context

Until now nothing Admin OS does to Gmail can be wrong, because it does nothing to Gmail. That property is about to be given up, and it is worth being precise about what replaces it.

Two failures are specific to writing to a mailbox. The first is the interrupted write: a request is sent, the connection drops, and nothing on this side knows whether Gmail acted. Retrying blindly archives twice — harmless — or sends twice, which is not. The second is the unconfirmed write: Gmail returns 200 and the effect is not there, because a label id was stale or a filter moved the thread back. A system that reports what it *asked for* rather than what *happened* is worse than one that does nothing, because it is trusted.

Sending is different in kind from the rest. A label can be removed, an archive undone, a draft deleted. A sent message is in someone else's mailbox. So "the account owner approved a reply" and "the account owner approved *this* reply, as it now reads" cannot be the same fact.

The second problem is learning. Overriding the same recommendation for the twentieth time is a signal, and ignoring it means the review never gets better. But the obvious response — notice the pattern, start applying it — is the dangerous one: behaviour would change without anyone deciding it should, and the first anyone knows of a new rule is mail disappearing. The account owner's instruction was explicit, and it is the constraint this ADR is built around: *a correction must never silently become a rule.*

## Decision

**An action is a persisted object with a lifecycle, not a function call.**

```text
approved → prepared → executed → verified → completed
                                          ↘ failed
```

Each state is durable, so at any moment there is an answer to what was intended, what was attempted, and what Gmail actually shows. `approved` is a decision and nothing more. `prepared` resolves the exact parameters that would be sent and writes nothing. `executed` means Gmail has been called. `verified` and `completed` mean Gmail was read back and agrees. `failed` records the error and stays, because a failure that is retried until it disappears is a failure nobody learns from.

**Preparation is separate from execution because the plan should be readable before it happens.** Preparation resolves label names to ids, refuses a label the mailbox does not have, and rejects a `gmail.label` action that manipulates `INBOX` by hand — leaving the inbox is an archive, and it is recorded as one so the audit trail does not have two names for the same act.

**Identity is derived, not generated.** The idempotency key is a hash of the item, the action, and its parameters, in the spirit of ADR-0002. The same approval prepared twice is the same action; a retry cannot become a second write. Where the external effect is discoverable — a draft on a thread — a retry looks for it before acting, and adopts it rather than creating another.

**Permission is rechecked at execution, never inherited from approval.** Four gates stand in front of a mailbox change: the capability must be *allowed* the action, separately *permitted to execute* it, `GMAIL_WRITE_ENABLED` must be true, and the request must say `confirm: true`. The two capability grants are deliberately distinct: `allowed_actions` governs what may be approved, `execution.permitted_actions` what may reach Gmail. Revoking either stops work that has already been approved, which is what a kill switch is for.

**Execution and verification are different claims.** Every action reads Gmail back after acting and compares against what it intended. An archive Gmail still lists in the inbox is `failed`, not `completed`. A write that cannot be confirmed is not a write that happened.

**Sending requires approval of the exact draft.** Creating a draft never sends it — they are separate actions with separate permissions. Approving a send names both the draft id and the message id the draft carried when it was read back, and approving still does not send: it creates a `gmail.send_draft` action that must be executed like any other, and that refuses if the draft has changed since it was approved. Editing a draft therefore invalidates its approval, which is the intended behaviour.

**Permanent deletion is not implemented.** `gmail.trash` exists in the vocabulary; no capability is granted it and no code performs it. Archiving is reversible and sufficient. *(Superseded in part by [ADR-0012](./ADR-0012-gmail-dispositions.md): `gmail.trash` is now implemented and granted to two capabilities. Permanent deletion remains unimplemented and unreachable.)*

**A correction is evidence.** Every decision that answers a recommendation writes a learning event: what was recommended, what was chosen, the retained metadata the decision turned on, the actor, the policy or rule version, and the provenance. Never message content — ADR-0003 is not relaxed for learning. An event changes nothing about what the review recommends.

**A rule becomes autonomous only by being walked through five states, each an explicit act:**

```text
observed → proposed → confirmed → automatable → retired
```

`observed` is what a correction produces: the same correction seen again increments a count, and that is all it does. `proposed` is a rule written down in full and still inactive. `confirmed` recommends. `automatable` may approve without being asked. `retired` does neither, permanently, and is terminal.

The separations matter individually. Observing is not proposing, so a pattern in the data is not a suggestion. Proposing is not confirming, so the exact conditions and the single action can be read before agreeing to them. And confirming is not promoting: agreeing that a rule gives good advice is not licensing it to act unattended, which is the distinction that most systems collapse. Promotion is the narrowest grant in the system and needs its own confirmation.

Even a promoted rule only *approves*. It creates an action in `approved`, recorded as `approval_kind: automatable_rule` with the rule that did it, and every execution gate above still stands between it and the mailbox.

## Alternatives Considered

1. **Execute on approval.** Rejected: it merges "this should happen" with "this happened", and leaves no state in which the exact parameters can be read before they are sent.
2. **A client-supplied idempotency key.** Rejected: correctness would depend on the caller. Deriving the key from the content of the action makes duplicate suppression a property of the system.
3. **Trust Gmail's 200.** Rejected: the failure this is meant to catch — a stale label id, a filter putting a thread back — returns 200.
4. **Delete failed actions on retry.** Rejected: the failure record is the only evidence that a write was ever ambiguous, which is exactly what an audit needs.
5. **Approve sending by item rather than by draft.** Rejected: it would let an edited draft ride an old approval. The draft's own identity is the only thing worth approving.
6. **Support Trash behind a permission.** Rejected for now: the reversible action covers the need, and a destructive one available "behind a flag" is available.
7. **Promote a rule automatically after N consistent corrections.** Rejected outright, and this is the decision the rest follows from. A threshold is a rule about making rules, and it changes behaviour without anyone choosing to.
8. **Let confirming a rule also make it automatable.** Rejected: agreement about advice is not consent to unattended action, and one route for both leaves no way to express the first without the second.
9. **Learn across capabilities.** Rejected: a pattern that is true of administrative mail is not thereby true of tax correspondence. Rules are capability-scoped.

## Consequences

- Admin OS can now change a live mailbox. `GMAIL_WRITE_ENABLED` stays `false` in production until each action class has been watched on real mail.
- Actions accumulate as durable rows, including failures, and no cleanup process removes them. That is a deliberate cost of auditability.
- A retry after an interrupted draft creation depends on Gmail's own state to avoid duplication, so a draft the account owner deletes between attempts will be recreated.
- Learning events are written for every answered recommendation, including agreements, so the record shows what the review got right as well as what it got wrong.
- Observed candidate rules will accumulate without doing anything. A queue of unproposed observations is the expected steady state, not a backlog.
- Two Admin rules now recommend `gmail.archive` where before everything was `needs_review`. Both are narrow, both name a specific sender, and neither approves anything.
- The action vocabulary is now larger than what any capability may execute, which stays true as new actions are added: naming an action is not granting it.

## Not Yet Implemented

- Monday actions in the lifecycle. `monday.create_task` remains approvable and continues to run through the ADR-0006 path; it has no executor here.
- Automatic retry. Retries are asked for; nothing retries on a schedule.
- Bulk send approval. Sends are approved one draft at a time, deliberately.
- Suggested rules from observations. Observations record what was corrected; proposing a rule from them is a human act with no assistance yet.
- The urgent-first pass and deterministic labelling from ADR-0007 remain outstanding, as recorded in ADR-0009.

## Affected Documents

- [README](../../README.md) — Actions, Learning, Capabilities
- [ADR-0009](./ADR-0009-review-engine-implementation.md) — its "execution is the next increment" gap is closed here
- [81 — Action Execution Runbook](../81-Action-Execution-Runbook.md) — the operational sequence
- [docs/gpt-action-openapi.yaml](../gpt-action-openapi.yaml)
- [79 — Daily Assistant Review](../79-Daily-Assistant-Review.md)

## Validation

- Tests assert that approving writes nothing, that preparing writes nothing, and that only execution reaches Gmail.
- Tests assert that the kill switch and each capability grant are rechecked at execution, and that an action is refused when either is withdrawn after approval.
- Tests assert that preparing twice yields one action, that executing twice writes once, and that a retry after a failure adopts an existing draft instead of creating a second.
- Tests assert that an unverifiable effect leaves the action `failed` with the discrepancy recorded, rather than `completed`.
- Tests assert that creating a draft does not send it, that sending requires the exact draft and message id, and that an edited draft is refused.
- Tests assert that no executor exists for permanent deletion and that no shipped capability is granted it.
- Tests assert that every override writes exactly one learning event, that events carry metadata only, and that a capability with learning off records nothing.
- Tests assert each rule transition and each illegal one: that observing does not propose, proposing does not activate, confirming does not promote, promotion cannot skip confirmation, and retirement is terminal.
- Tests assert that only an `automatable` rule approves without being asked, that it cannot exceed the capability's permissions, and that a retired rule neither recommends nor acts.
- A contract test asserts that every route documented in `docs/gpt-action-openapi.yaml` exists in the application, and that every review and learning route is documented.
