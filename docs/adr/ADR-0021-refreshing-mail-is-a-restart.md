# ADR-0021 — Refreshing the Mail Is a Restart, and Says So in Data

**Status:** Accepted
**Date:** 2026-08-09
**Depends on:** [ADR-0017](./ADR-0017-a-review-is-an-object-with-a-life.md), which made start, continue and restart three different sentences; [ADR-0020](./ADR-0020-a-session-opens-with-the-playbook.md), which made the opening Admin OS's words rather than the GPT's.

## Context

Brian's review of the day was finished. He asked to refresh his mail. The GPT called `startDailyReview` with `sync: true`, which returned the finished review, and reported that today's review was already complete.

Every part of that is working as built, and the whole of it is a lie about the mailbox. `start` on a finished review does not read Gmail for the answer; it returns the review that exists. There was no check, and new mail was not looked at — but "sync: true" and a returned review read to the caller like a look.

Two faults, one visible, one not:

- **The intent was mapped to the wrong operation.** "Refresh mail" and "check again" are a restart. Only the instructions said so, and only in passing, at the end of a sentence about completion prompts.
- **The way on was prose.** The finished review's `prompt` offered "Review the day again, on refreshed mail" as one of two `choices`. Choices are for Brian to pick between and are presented, not acted on; nothing in the response said, as data, *this is the request that answers "check again"*. Brian also reported his GPT had no `restartDailyReview` tool at all — a stale import, since the route has been published since 0.13.0, but a stale import is indistinguishable from a missing feature from where he sits.

## Decision

**A finished review carries the request that refreshes it.** `restart_available: true` and `restart_action` — name, method, path and body, `{"sync": true, "scope": "inbox"}` — are returned on any response whose review is finished. Acting on "check again" is then a lookup, not an interpretation. `restart_action` names its scope explicitly, including the default, because a request copied from a response should not depend on knowing what is omitted.

**A review under way carries neither.** `restart_available` is false while a review is `not_started`, `in_progress` or `awaiting_actions`. Offering to start over on a morning half worked is offering to throw the morning away; the way on from that is `continueDailyReview`.

**A restart keeps the hour the review was finished at.** Abandoning previously cleared `completed_at`. Setting a completed morning aside to review the day again is a statement about which review is current, not a claim that the first one never ended, and the audit should be able to say when it ended. Everything else was already kept: decisions, actions, and what they did to the mailbox.

**Agreeing the plan makes a review resumed.** `entry_mode` read the run's state alone, so a review whose plan had been begun and whose first group had been presented still opened with the new-review orientation when Brian said "continue". Rows have been shown by then. The opening now reads the plan as well as the state.

**The instructions and the schema are checked against each other.** A contract test collects every operation name the instructions mention and every `operationId` the document publishes, and fails when the first is not a subset of the second. An instruction naming a tool the GPT was never given is a dead end in front of Brian; it is now a failure in CI.

## Alternatives Considered

**Make `startDailyReview` restart a finished review when `sync` is true.** Rejected, and it is the tempting one. "Good morning" said twice would then abandon the morning's preparations, and `sync: true` is the default — the one operation that must be safe to call on entering would become the one that can discard work.

**Return the old run and let the GPT decide from the prose.** Rejected: that is exactly what happened. The `prompt` said everything needed and the caller still reported a check that never occurred.

**Have Admin OS classify "refresh mail" itself.** Rejected. Admin OS is not in the conversation and has no message to classify; mapping phrases onto operations is the GPT's work, and the schema descriptions now name the phrases.

**Leave the finished review `completed` rather than abandoning it.** Rejected. "My review" is the latest review nobody abandoned; two current reviews of one date is the ambiguity ADR-0017 removed, and abandonment is what disarms the scopes prepared in the review being replaced.

## Consequences and Tradeoffs

- A completed review can be told apart from a restartable one without reading English, which is the whole of the fix.
- The restart still abandons: the prior review's `status` becomes `abandoned` though it keeps its decisions, its summary and its `completed_at`. A reader wanting "was this day ever finished?" reads the timestamp, not the status.
- `restart_available` and `restart_action` are additive and nullable: the contract moves to 0.17.0, no request shape changes, and a stale import keeps working without them.
- The contract test constrains how the instructions are written — an operation mentioned in passing must exist — which is the constraint that was missing.

## Affected Documents

- `docs/gpt-action-openapi.yaml` — `RestartAction`, `restart_available`, and what `restartDailyReview` is for.
- `docs/gpt-daily-review-instructions.md` — "refresh mail" and "check again" map to the restart.
- `README.md` — the review lifecycle table.
