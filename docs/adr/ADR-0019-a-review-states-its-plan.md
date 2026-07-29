# ADR-0019 — A Review States Its Plan Before It Works

**Status:** Accepted
**Date:** 2026-08-08
**Depends on:** [ADR-0017](./ADR-0017-a-review-is-an-object-with-a-life.md), which made a review an object with a life; [ADR-0018](./ADR-0018-a-decision-is-not-a-done-thing.md), which made the gap between deciding and doing visible on the row.

## Context

A review used to open with a table. Whatever it consisted of — how many groups, in what order, how much was in them, what would happen to a row that was approved — was learned by working through it. That is an implicit contract, and the incident behind ADR-0018 is what an implicit contract costs: three rows were decided, the next group appeared, and the morning read as finished with work that had not started.

Brian also had no way to say what a morning should be. "Do Financial first", "only Admin today", "skip that one" were things to be asked for and then honoured by a GPT choosing which routes to call — which is to say, a plan held in the conversation rather than by the service. A plan nobody has written down cannot be resumed, cannot be quoted back, and cannot be checked against what actually happened.

## Decision

**A review has a plan, and the plan comes before the first group.** `startDailyReview` returns `plan` with `current_group` absent. The plan carries the ordered groups with their item counts, which are empty, the mailboxes excluded, whether this review is new or resumed, the steps working a group consists of, and what the review already owes the mailbox. Presenting rows is what `beginReviewPlan` does.

That costs a turn every morning. It buys the only version of "show the plan before the first group" that is a fact rather than a hope: the first group does not exist to be shown until the plan has been agreed, so no configuration, no screen and no GPT can skip it.

**The plan is persisted and versioned.** `review_plans` holds the sequence, the groups set aside, the configuration version it was drawn from, its status and when it was begun. Ordering is not a query parameter re-sent on every read; it is what this review agreed to, and a resumed review works the order it was given yesterday evening rather than the configured one.

**Ordering and filtering are arguments to beginning, not new capabilities.** `order` brings named groups forward, `only` works those and sets the rest aside, `skip` sets named ones aside. All three are about this review: `sequence` keeps the whole order including what was set aside, and `skipped` says which of those are not for today, so "skip Admin today" is recorded as a decision about today rather than as a group that never existed. A named group that is not in the review is `422`, and so is a plan with nothing left to work.

**Deciding a row begins the plan.** Working the review is a stronger statement than agreeing to work it, and asking afterwards whether to begin is a question whose answer has been given. A review under way when this shipped is `active` on its first read, so nobody is asked to agree to a morning they are halfway through.

**What a review owes is counted three ways, from rows and actions rather than from what was said.** `decided_not_executed` counts rows decided and not yet prepared — the state the three Admin rows were in. `prepared_awaiting_confirmation` counts actions prepared and waiting. `failed_or_unverified` counts actions attempted with no verified result. They are disjoint, so a row cannot be owed twice, and they appear per group and per review.

**The end-of-review summary counts verified execution and nothing else.** `summary.done` is keyed by what happened — `archived`, `filed`, `moved_to_trash`, `replies_drafted`, `replies_sent`, `tasks_created` — and only a `completed` action, one whose effect has been read back from Gmail, raises a count. A decision raises nothing, which is the whole point.

## Alternatives Considered

**Return the plan and the first group together.** Rejected, and it is the tempting one: it costs no turn and shows the plan. It also shows a table, and a table is what gets worked. "Before" would mean "above", enforced by nothing, and the first morning someone scrolled past it the guarantee would be gone.

**Make the plan a field the GPT is told to render first.** Rejected for the reason ADR-0018 gives: the instructions already said not to claim completion, and the GPT read the table instead.

**Keep the order in configuration only, and let "do Financial first" be the GPT calling that group directly.** Rejected. It works, and it leaves no record: the review cannot say what it is working, resuming forgets it, and progress is counted against an order nobody is following.

**Order groups by size, or by what looks urgent.** Rejected. The configured order is a decision already made, and a review that rearranges itself is a review whose shape has to be re-learned each morning.

**Count a set-aside group as remaining, at the end of the order.** Rejected. Counting something nobody will look at among what is left is how "two groups to go" stops meaning anything.

**Count `executed` as done.** Rejected. Executed is attempted; completed is read back from Gmail. The distinction is the one this whole system exists to keep, and a summary that blurs it is a summary that reports the incident as a success.

## Consequences and Tradeoffs

- Every morning takes one extra call. Existing callers that read `current_group` from `startDailyReview` see `null` until they begin the plan; the tests moved with it, and the contract says so in `plan.status`.
- The plan is created on first read of a review, including reviews that predate it. One already under way is `active` immediately, so nothing in flight is interrupted.
- `plan.excluded` is derived from the review's scope rather than written down per review, so a scope gaining a mailbox changes what a plan says it excluded. That is right: the plan describes the review that ran.
- Group standing is computed per read from rows and actions rather than stored. It is a small query against a review's own rows, and it cannot go stale, which a stored counter can.
- The contract is 0.15.0: `beginReviewPlan` is a new request shape, and the fingerprint moves with it.

## Affected Documents

- [ADR-0017](./ADR-0017-a-review-is-an-object-with-a-life.md) — the review object this plan belongs to
- [ADR-0018](./ADR-0018-a-decision-is-not-a-done-thing.md) — the decided-not-done distinction these counts carry
- [ADR-0015](./ADR-0015-review-mailbox-scope.md) — the scope the plan reports as excluded
- [Daily Review GPT instructions](../gpt-daily-review-instructions.md)
- [GPT Action contract](../gpt-action-openapi.yaml)
