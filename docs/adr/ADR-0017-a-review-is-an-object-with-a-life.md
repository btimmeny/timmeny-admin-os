# ADR-0017 — A Review Is an Object With a Life, and Ending One Is a Decision

**Status:** Accepted
**Date:** 2026-08-06
**Depends on:** [ADR-0014](./ADR-0014-execution-scope-integrity.md), whose scopes belong to a review; [ADR-0015](./ADR-0015-review-mailbox-scope.md), which made the scope of a review explicit.

## Context

A review run existed already: one row per date, channel and mailbox scope, resumed by starting again. What it had no way to express is finishing. `state` moved between `in_progress`, `awaiting_actions` and `completed`, and a completed run was resumed exactly as an unfinished one was — so "start my daily review", said to begin a fresh morning's work, re-served a review Brian had already worked through, and the rows he had settled were settled still.

The verbs were missing too. Resuming and starting were the same call, so a GPT could not distinguish "where was I?" from "I want to do this again", and Brian had no way to say the second at all. There was no `abandoned`: a review one wants to be rid of could only be worked through or ignored, and an ignored review is a review whose prepared actions are still live.

Nothing recorded when Gmail was last read for a review, either. "How fresh is this?" could only be answered from the clock, which is not the same question.

## Decision

**A review is an object, and it is identified by `(review_date, channel, scope_name, revision)`.** `review_id`, `review_date`, scope, `status`, `started_at`, `completed_at`, `abandoned_at` and `evidence_refresh_at` are persisted and returned on every review response. `run_id` and `state` remain as the older names for the id and the status, because the routes are addressed by run.

**A second review of a date exists only where one was deliberately abandoned.** Revision is not a retry counter and nothing increments it automatically: the day rolls over into revision 1 of the new date, and a restart is the only thing that produces revision 2.

**Three verbs, and they mean different things.** `startDailyReview` creates today's review or resumes the one under way. `continueDailyReview` resumes, and only resumes: no review to continue is a `404`, a finished one is a `409`, and neither is an occasion to open a review Brian did not ask for. `restartDailyReview` abandons the review that exists, refreshes Gmail, and opens the next revision.

**A finished review is not silently re-served; it is reported, with the choice offered.** `startDailyReview` on a completed review returns a prompt naming what a fresh review would cost, and the operations that read it or restart it. Admin OS refuses; it does not decide.

**"Finished" means worked by Brian, not merely marked complete.** A review that completed having settled nothing — an empty inbox at eight, mail at ten — is topped up rather than fenced off, and so is one every row of which was withdrawn because the thread left the inbox: a decision signed `scope:` or `rule:` is Admin OS's own doing, not a morning to protect. Only a completed review carrying a decision Brian made requires a deliberate restart.

**Abandoning disarms, and preserves.** The abandoned review keeps its decisions, its actions and its audit; what it loses is any open preparation, which is superseded at the moment of abandonment and can never execute. Its rows take no further decisions: every mutating route refuses an abandoned review before doing anything.

**Nothing crosses a review boundary.** Progress, counts, rows, prepared scopes and executions are read from one review id. An action scope already belonged to a run (ADR-0014); this makes the run itself a thing that can end.

## Alternatives Considered

**Have `startDailyReview` restart a completed review automatically, after refreshing Gmail.** Rejected, and it was the other half of the request. It is the more convenient reading of "start", and it is a review destroyed by a greeting: "good morning" said twice would abandon the first one's preparations. The refusal costs one sentence and one more turn.

**Keep resuming a completed review, as today.** Rejected. It is the reported behaviour: the day's work re-served as though it were pending, with no way to say otherwise.

**Delete the abandoned review, or overwrite it.** Rejected. What Brian decided about his mail is the record, whether or not he wanted the review that recorded it, and an execution that happened cannot be un-recorded.

**Model the restart as a new run of a synthetic scope, or of the next date.** Rejected. Both lie in the data — a review of Tuesday's inbox filed under Wednesday, or under a mailbox that does not exist — to avoid adding a column.

**Drop `awaiting_actions` to leave exactly the four requested statuses.** Rejected for now. It is a real state of the action lifecycle, distinct from `in_progress` in that the deciding is done and only execution remains, and collapsing it would lose that in the response for the sake of a shorter enum. It is documented in the contract alongside the other four.

**Timestamp the evidence refresh from the sync itself rather than the request.** Deferred. The current value is the moment the review was opened or resumed with `sync` set, which is within a second of the read and never claims freshness the review does not have.

## Consequences and Tradeoffs

- `startDailyReview` can now return a review with no group to render. A prompt is the response, and a GPT that ignores prompts will say something wrong about a finished day.
- Two revisions of a date exist in the data, and any query over reviews that assumed one row per date and scope is now wrong. The unique constraint moved with it.
- A restarted review re-reads Gmail and re-derives rows, so evidence settled in the abandoned review does not reappear: the new review is what is still outstanding, not a copy of the old one.
- `not_started` is a status a review can be returned in, where nothing has been decided yet. It is new, and it is the state most reviews are read in first.
- The GPT can now abandon a review. The contract says restart only where Brian asked, and the mechanism cannot enforce that — what it does enforce is that nothing is lost when it happens.

## Affected Documents

- [ADR-0014](./ADR-0014-execution-scope-integrity.md) — scopes belong to a review, and end with it
- [ADR-0015](./ADR-0015-review-mailbox-scope.md) — the scope half of a review's identity
- [Daily Review GPT instructions](../gpt-daily-review-instructions.md)
- [GPT Action contract](../gpt-action-openapi.yaml)
