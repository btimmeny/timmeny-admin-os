# ADR-0014 — An Execution Runs the Rows That Were Selected, and No Others

**Status:** Accepted
**Date:** 2026-08-02
**Depends on:** [ADR-0010](./ADR-0010-action-lifecycle-and-learning.md), [ADR-0012](./ADR-0012-gmail-dispositions.md), [ADR-0013](./ADR-0013-filing-mail-in-a-named-folder.md)
**Extends:** [ADR-0010](./ADR-0010-action-lifecycle-and-learning.md), which recorded the lifecycle this adds a scope to.

## Context

On 2026-07-29 an Admin review held twenty-two approved rows. Nineteen were asked for — rows 1 to 3 and 5 to 20. Twenty-two were trashed, in nine seconds, run `f6bc5a3d-068f-4798-98e3-3c52b9791b25`.

Nothing malfunctioned. `prepare` and `execute` took a `capability_key` and, given one, prepared and ran *every* approved action under it. The selection existed at the decision — each of the nineteen was recorded individually, with its own audit row — and then had nowhere to live. Two requests later it was gone, and the widest reading of the remaining parameters was the one that ran.

That is the shape of the bug worth naming: the caller's intent was expressed once and never carried. Every subsequent step re-derived scope from a capability, which is a superset by construction. A GPT sitting in front of it cannot be the safeguard — it is precisely the component least able to promise it sent the right nineteen ids, and asking it to be careful is not a control.

Trash is recoverable, so the cost this time was three threads and Brian's confidence. The same defect on a send, or on a permanent operation, would not have been recoverable — which is the argument for treating it as a transactional integrity fault rather than a validation nicety.

## Decision

**A selection is a first-class, persisted object: the scope.** `action_scopes` records the run, the capability, whether the whole capability was explicitly asked for, the requested item ids, the prepared item ids, the action ids, every excluded item with its reason, the actor, and — once it has run — the executed and verified item ids. The selection is written down at the moment it is understood, so no later step has to reconstruct it.

**Preparation requires the exact rows.** `item_ids` is the selection. A request naming neither `item_ids` nor `entire_capability` is refused with `400`; it is not read as "all of them". `entire_capability: true` is available and honest — "delete everything in Admin" is a real sentence — but it must be *said*, it requires a `capability_key`, and it cannot be combined with `item_ids`.

**Preparation returns the scope in full.** `scope_id`, `requested_item_ids`, `prepared_item_ids`, `action_ids`, `prepared_items`, `excluded_items` with a reason each, and `scope_matches_request`. Nothing about what would run is left to be inferred, because inference is what this ADR exists to remove. A row selected but not prepared is named and explained rather than silently dropped.

**Execution names a scope, and can reach nothing else.** `execute` takes a `scope_id` and runs that scope's `action_ids`. `capability_key` is not a parameter of execution any more; there is no request that means "everything approved". The caller may restate `item_ids` and `action_ids`, and a restatement that disagrees with the prepared scope stops the request rather than being reconciled with it.

**Preparing again supersedes the earlier scope.** Changing one's mind is preparing a different selection, so the previous `scope_id` stops being executable at that moment. A confirmation given for an older list can never run a newer one, or the reverse.

**Mismatch is `409 ScopeMismatch`, and nothing is written.** Every check — state, restated items, restated actions, the prepared set against the actions about to run, and whether any row was decided again since preparation — happens before the first Gmail call. The response names the difference in both directions, because "these are not the same nineteen" is only useful when it says which.

**One row's changed mind stops the whole scope.** If any prepared item has since been dismissed, deferred, or decided again, the confirmation no longer describes what would happen; executing the remainder would be a third selection nobody made.

**The scope is settled with what actually ran.** Executed and verified item ids are recorded against the scope and the scope is closed. A verified set *smaller* than prepared is a reported failure, not a scope violation: those actions stay visible and retryable. A set *larger* than prepared cannot happen without a bug and is logged as one — the invariant is checked, not assumed.

**A Trash can be undone.** `restore_gmail_thread_from_trash` maps to `gmail.untrash`, removes `TRASH` from the whole thread, and is verified by reading Gmail back. A group response lists what is restorable with the exact request that restores it, so an undo never depends on anyone reconstructing one. It is a permission like any other, granted to the capabilities that may Trash. Permanent deletion remains absent, not gated.

## Alternatives Considered

**Send the item ids on `execute` and check them there.** Rejected as the whole fix. It puts the burden back on the caller to remember its own selection across two requests, and the caller that failed to do so is the one this must be safe against. Restating ids is kept, but as corroboration of a server-held scope, not as its source.

**Make the GPT verify the scope before confirming.** Adopted as instruction, rejected as a control. It is written into the GPT instructions because a good renderer should check, but the server refuses regardless — a safeguard that lives only in a prompt is a safeguard that a rephrasing removes.

**Keep capability-wide execution, with a confirmation count.** Rejected. "Are you sure you want to affect 22?" is a question the failing path answered wrongly once already, and it makes the safe case noisier without making the unsafe case impossible.

**Idempotency keys instead of scopes.** Rejected. They make a repeated request harmless, which is a different problem: the incident was one request that was too wide, not one request sent twice.

**Execute the subset that still matches on mismatch.** Rejected explicitly. Partial execution invents a third selection and is the hardest outcome to reason about afterwards. Refusing everything is recoverable in one step; a partial write is not.

**Restore the three threads automatically once restore existed.** Rejected. Undoing mail movements without being asked is the same class of error as the incident, in the opposite direction. The capability is built; using it stays Brian's instruction.

## Consequences and Tradeoffs

- A caller must send the ids it means. `prepare` with only a `capability_key` now fails; that is a breaking change to a route the GPT uses, and it is intended to break.
- Two round trips are needed before any write, and the scope makes the second one refusable. Slower, and correct.
- A scope is per preparation, so two people working the same run concurrently will supersede each other. Correct for a single-user review; a genuine multi-actor review would want scopes keyed by actor.
- A row decided again invalidates the whole prepared scope, including rows nobody touched. Occasionally annoying, and cheap: prepare again.
- Executed and verified sets are recorded but a shortfall is reported rather than rolled back. Gmail has no transaction; pretending otherwise would be a worse lie than reporting the truth.
- Restore is only as good as Gmail's retention. After Trash is emptied there is nothing to restore, and the review will say so rather than appearing to succeed.

## Affected Documents

- [README](../../README.md) — Actions
- [ADR-0010](./ADR-0010-action-lifecycle-and-learning.md) — the lifecycle this scopes
- [ADR-0012](./ADR-0012-gmail-dispositions.md) — the dispositions, and the absence of deletion
- [81 — Action Execution Runbook](../81-Action-Execution-Runbook.md)
- [Daily Review GPT instructions](../gpt-instructions/daily-review-gpt-instructions.md)
