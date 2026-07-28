# ADR-0006 — Approval Gate and Verified Monday Writes

**Status:** Accepted
**Date:** 2026-07-28
**Depends on:** [ADR-0001](./ADR-0001-admin-os-coordination-layer.md), [ADR-0002](./ADR-0002-monday-identity-and-idempotency.md), [ADR-0004](./ADR-0004-classification-boundary-and-review.md), [ADR-0005](./ADR-0005-duplicate-review-before-monday-writes.md)

## Context

Everything to this point reads. This is the first operation that changes something the account owner can see, on a board holding four years of hand-made work, and a bad write is not a failed request — it is an item sitting on the board that someone has to notice and delete.

The instruction from the account owner was: *"if 100% you can create a task, but anything lower, it will have to confirm from me."* Two numbers in this system could be read as "100%", and they point in opposite directions:

- **Classification confidence** — how certain the classifier is that this evidence warrants a task. Certainty here argues *for* creating.
- **Duplicate score** — how closely a proposed title resembles something already on the board. A score of 1.000 means the task already exists, which argues *against* creating.

Reading "100%" as the duplicate score would invert the rule into "create a second copy whenever a perfect copy already exists".

Separately, ADR-0002 requires that a write be recoverable. Monday has no client-supplied idempotency token, so a create that succeeds at Monday but never returns leaves an item on the board that Admin OS has no record of, and the retry duplicates it.

## Decision

**"100%" means classification confidence, and a duplicate candidate withdraws the permission.** A task is created without asking only when the classification is fully confident *and* the duplicate report is empty at the candidate threshold. Anything else returns `409` with the report attached and creates nothing. `confirmed: true` on the request is a human overriding that refusal, and is recorded as such.

This is deliberately stricter than the literal instruction: full confidence alone is not enough if the board holds something resembling the title. The asymmetry is the point — creating a duplicate costs the owner attention, while asking costs one round trip.

Classifier v1 assigns zero confidence to everything (ADR-0004), so today the gate refuses every task and each one is confirmed by hand. The gate is built now, before a classifier can be certain, so that the certainty rule already exists when one is.

**Identity is reserved before Monday is called.** An operational object and an `external_mappings` row in state `pending` are committed *before* the create. The `admin_os_id` is written into the board's Admin OS ID column, so a retry looks the id up on the board: finding it adopts the item, not finding it creates one. A crash between the two is therefore recoverable, and the reservation is the only durable trace that an item may exist externally.

**The create is verified by a separate read.** Monday echoing an item id proves the mutation was accepted, not that an item exists with the fields asked for. The item is read back and its board, name, and Admin OS ID are checked before the mapping moves to `verified`. A mismatch fails the run and leaves the mapping in `failed` with its `admin_os_id` intact, so a human can find the item rather than a retry making another one.

**Board scope is server-side.** The board id comes from `TODO_BOARD_ID`; no request field can redirect a write to another board.

**Mutation lives in a separate class.** `MondayWriter` extends the read-only `MondayClient`. Every read path — the board listing, the duplicate check — holds an object with no create method on it, so the read-only guarantee of ADR-0005 is structural rather than a convention.

**The workflow run records the outcome; the classification does not.** A verified task takes its thread out of the review queue by way of a succeeded `workflow_runs` row. The classification is left as written. It records what the classifier inferred at a point in time, and editing it to mean "handled" would conflate an inference with an action taken about it.

## Alternatives Considered

1. **Take "100%" literally as confidence alone.** Rejected: it lets a fully confident classifier create a task that visibly already exists on the board.
2. **Read "100%" as the duplicate score.** Rejected: it inverts the rule, auto-creating exactly the duplicates the check was built to catch.
3. **Block only on a *strong* duplicate (≥ 0.75).** Rejected for the auto-create path: at that threshold this board's near-duplicates score 0.6–0.7, so the case most worth a human glance would pass unchallenged. A weaker candidate costs one confirmation; the strong threshold still governs how the report is presented.
4. **Create first, reconcile after.** Rejected: reconciliation cannot distinguish "we created this" from "the owner created something similar" without the stamped id, which is what the reservation provides.
5. **Trust the mutation's echoed id.** Rejected: it verifies the request, not the state, and column values can be silently dropped by Monday if a column id is wrong.
6. **Mark the classification `create_task` when a task is made.** Rejected: it rewrites an inference to mean an outcome, and destroys the record of what v1 actually concluded.
7. **Update an existing item instead of creating.** Out of scope here. Update is a different verification problem — it must not overwrite a field a human changed — and belongs with completion synchronisation.

## Consequences

- Every task creation reads the whole board first (≈11 paged requests) to build the duplicate report the decision is made on. The report is returned with the result, so the audit record shows what the board looked like when the call was made.
- Until a classifier can assert confidence, every task requires `confirmed: true`. That is the intended behaviour, not a limitation to work around.
- A failed run leaves a `pending` or `failed` mapping with a reserved `admin_os_id`. This is intentionally not cleaned up: it is the recovery handle.
- `admin_os_id` is unique in Postgres, and the lookup refuses to proceed if two board items carry the same one — an id identifying two items identifies neither.
- Gmail is untouched. Archiving a thread happens only after verified Monday completion, which is a later increment.

## Affected Documents

- [README](../../README.md) — `POST /admin/monday/tasks`
- [75 — First Vertical Slice](../75-First-Vertical-Slice.md)

## Validation

- The gate is a pure function tested at full confidence, below it, with and without duplicate candidates, and under human confirmation.
- Tests assert the mapping is committed before Monday is called, that a retry adopts the item carrying the reserved id rather than creating a second, and that a wrong board, wrong name, wrong Admin OS ID, or missing item all fail verification.
- Tests assert a refused task creates no run, no mapping, and no operational object, and that a failed run leaves the thread in the review queue.
