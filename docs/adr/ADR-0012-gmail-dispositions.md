# ADR-0012 — Archive and Trash Are the Two Dispositions, and Neither Deletes

**Status:** Accepted
**Date:** 2026-07-31
**Depends on:** [ADR-0003](./ADR-0003-gmail-access-and-retention.md), [ADR-0010](./ADR-0010-action-lifecycle-and-learning.md), [ADR-0011](./ADR-0011-presentation-contracts.md)
**Supersedes in part:** [ADR-0010](./ADR-0010-action-lifecycle-and-learning.md), which recorded that `gmail.trash` was named but not implemented.

## Context

The review exists to empty a list. Until now it could only archive, and the thing Brian actually says to a screen of eleven newsletters is "delete all 11".

That sentence is the whole problem. "Delete" in a mailbox means three different operations — leave the inbox, move to Trash, destroy — and only the third is irreversible. A system that hears "delete" and picks the wrong one is either useless (it archived when asked to bin) or unrecoverable (it destroyed mail on a spoken instruction with no undo). The mapping cannot be left to a language model to infer per utterance, because the utterance is identical in all three cases.

There is also a permission question that archiving alone did not force. Archiving tax correspondence is annoying; binning it is not. The two dispositions are not equally safe, and treating them as one grant would make the safer one the ceiling for the riskier.

## Decision

**"Delete", "remove" and "trash" all mean `threads.trash`, and nothing means permanent deletion.** Trash is reversible for thirty days, which makes a misheard instruction recoverable rather than final. `messages.delete` and `threads.delete` are not implemented, not gated: the Gmail client has no method that could call them, so no capability, rule, prompt, or request can reach them. Absence is the only permission control that cannot be misconfigured.

**Both dispositions are exposed under canonical spoken names — `archive_gmail_thread` and `move_gmail_thread_to_trash` — that map onto the stored `gmail.archive` and `gmail.trash`.** The stored vocabulary is what every audit row since the first execution already uses; renaming it would rewrite history to improve an API. Instead the API accepts either name and records one, and each eligible row carries the canonical ids in its `actions` array, so a renderer never invents a permission or guesses a route (ADR-0011).

**Archive removes `INBOX` and nothing else; Trash calls `threads.trash`.** Both operate on the whole thread the review item stands for, because a row is a thread and acting on part of one would leave a mailbox in a state the review cannot describe.

**Both are verified against Gmail's own answer, and a thread already in the requested state is completed without a write.** Archive requires `INBOX` to be absent on read-back; Trash requires `TRASH` to be present. An already-trashed thread is `completed` with an `already_applied` event rather than failed, which is what makes "delete all 11" safe to repeat after a dropped connection.

**A bulk request is shorthand for many decisions, not a decision of its own.** Each row gets its own decision row and audit trail. If any selected row refuses the action, the whole request is refused with `409` and *every* offending row is named with its reason — because "trash 2, 4 and 7" is answered usefully only by saying which of them cannot be trashed, not by stopping at the first.

**Trash is granted to Admin and Career, and withheld from Financial/Taxes.** Tax mail may be archived and never binned. This is a configuration line, not a code path, so widening it later is a reviewed edit.

**Neither disposition may run unattended by default.** No shipped capability auto-approves either. The only route to an unattended disposition is a rule that has been separately confirmed *and* promoted to `automatable` (ADR-0010), and even then execution still passes the capability's execution grant, the kill switch, and `confirm: true`.

**Shipped screens show only unresolved rows.** A trashed thread leaves the table while remaining in the run's record. Otherwise the second review of the day offers to bin mail that is already binned.

## Alternatives Considered

**Implement permanent deletion behind a strong gate.** Rejected. Every gate is a configuration away from being open, and no amount of confirmation makes a destroyed thread recoverable. The gap between Trash and deletion is thirty days of undo, and it is worth more than the disk it saves.

**Rename the stored action kinds to the canonical names.** Rejected. It would either invalidate existing audit rows or require a migration that rewrites them, and an audit trail that has been rewritten to look tidy is worth less than one that has not.

**Let the GPT map "delete" to an action.** Rejected, and this is the ADR's central point. The mapping is a domain decision with a safety consequence; it belongs in configuration that can be read, versioned, and tested, not in a prompt that varies per rendering.

**Apply a bulk decision to whatever rows accept it, and report the rest.** Rejected. "Trash all of these" then means something different every time it is said, and the reader has to reconstruct what happened from a partial result. All-or-nothing keeps the sentence's meaning fixed.

## Consequences and Tradeoffs

- A thread trashed in error is recoverable from Gmail's Trash for thirty days, and not after. That is the actual safety boundary, and it is Gmail's, not ours.
- Refusing a whole bulk request is more annoying than a partial application; that is the price of the request meaning one thing.
- Career and Admin can now empty an inbox quickly. The kill switch, the execution grant, and `confirm: true` are the three things standing in front of that, and all three are checked at execution rather than inherited from approval.
- Evidence pruning (ADR-0003) will retire a trashed thread's evidence on the next sync; the review item and its audit record are copies and survive it.

## Affected Documents

- [README](../../README.md) — Actions, Presentation
- [ADR-0010](./ADR-0010-action-lifecycle-and-learning.md) — the lifecycle these two actions run through
- [81 — Action Execution Runbook](../81-Action-Execution-Runbook.md)
- [82 — Daily Review GPT Instructions](../gpt-daily-review-instructions.md)
- [docs/gpt-action-openapi.yaml](../gpt-action-openapi.yaml)

## Validation

- Tests assert that an eligible row offers both canonical ids, and that a capability without the Trash grant offers neither the row action nor the screen action.
- Tests assert that archive removes `INBOX` and leaves every other label, and that Trash calls `threads.trash`.
- Tests assert that "delete all 11" — one bulk decision, one preparation, one confirmed execution — trashes eleven threads, and that preparation wrote nothing.
- Tests assert that execution without `confirm: true` is refused and writes nothing.
- Tests assert that an already-archived or already-trashed thread completes without a write, and that executing the same trash twice writes once.
- Tests assert that a bulk request naming one ineligible row records nothing and names that row with a reason.
- Tests assert that a confirmed rule recommending Trash does not act, and that only promotion lets it approve.
- Tests assert that the Gmail client has no method that could permanently delete, that no shipped capability auto-approves a disposition, and that the GPT contract contains no permanent-deletion language.
