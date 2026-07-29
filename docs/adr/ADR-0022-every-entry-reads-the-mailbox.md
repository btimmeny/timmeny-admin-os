# ADR-0022 — Every Entry Reads the Mailbox, and Reviews What It Says Now

**Status:** Accepted
**Date:** 2026-08-09
**Supersedes part of:** [ADR-0017](./ADR-0017-a-review-is-an-object-with-a-life.md), where `start` created or resumed; [ADR-0021](./ADR-0021-refreshing-mail-is-a-restart.md), which rejected exactly this decision.

## Context

Brian says hello at ten. The review he finished at nine comes back, and with it a sentence about a mailbox nobody has looked at since. He asks for his inbox and is answered with a record of an hour ago.

ADR-0021 considered making `start` refresh and refused, on the grounds that "good morning" said twice would then discard the morning's preparations. That reasoning held while the alternative was a review handed back with a `prompt` on it. It stopped holding the moment the question was put plainly: which is worse, losing a preparation nobody confirmed, or reporting an inbox nobody read? The preparation can be made again in one call. The inbox cannot be un-misreported.

The mailbox does not hold still. Mail arrives while a review is being worked. A review is a snapshot of a mailbox, and the thing Brian asks for when he says "check my inbox" is the mailbox, not the snapshot.

## Decision

**Every entry into admin takes a fresh snapshot.** `POST /review/start` reads Gmail and opens a review of what it says now, every time: a new `review_id`, a new `snapshot_at`, and the review that existed set aside rather than topped up or handed back. A finished review no longer stands between Brian and his inbox, and neither does a half-worked one.

**`POST /review/restart` is the same operation under the name for asking.** "Refresh mail" and "check again" are what it is called; the behaviour is not different, because there is no longer a version of entering admin that fails to read Gmail. Both are exposed, because the two things Brian says are two things, and a tool layer that only publishes one of them makes the other a guess.

**Resuming is a sentence Brian has to say.** `POST /review/continue` is the only way back into an unfinished review, returning the same `review_id` with its progress: "where was I?", "continue", "pick up where we left off". Nothing carries on a morning it was not asked to carry on.

**A review says which mailbox it is of.** `snapshot_at` records when a review's evidence was first read and does not move; `evidence_refresh_at` moves whenever Gmail is read again. "Is this current?" is answered against the first.

**A replaced review is named, and says what it was left holding.** `supersedes_review_id` is the audit chain. `superseded` is returned only where the review set aside was holding decisions the mailbox never saw — the id, the standing, and a sentence — because those rows are real, they are in a review nobody will open again, and the fresh review is the last place they can be said.

**Setting a review aside disarms what was prepared in it.** Unchanged from ADR-0017, and now reached far more often: a confirmation given against the old snapshot cannot run against the new one. A prepared scope belongs to one run, and a superseded run executes nothing.

## Alternatives Considered

**Ask before replacing a review under way.** Rejected, for now, and it is the closest call here. A prompt on every "hello" makes the common case two turns to protect the rare one, and what is lost is a preparation, not a decision — decisions, actions and audit all survive in the review set aside, which is why `superseded` reports them rather than letting them go quietly. Brian has been told this is the behaviour and can ask for the prompt.

**A separate `startFreshDailyReview` operation, leaving `startDailyReview` as it was.** Rejected. Two entrances, one of which reports stale mail, is the bug with a second door; the conversational layer would have to be right about which to use every single morning.

**Keep resuming automatically when a review is under way, and refresh only finished ones.** Rejected. "Hello" at eleven on a morning half worked is still a question about the inbox at eleven, and the half-worked review is exactly the one whose snapshot is most out of date.

**Reconcile a fresh review against the previous one's decisions.** Rejected. A decision is about the mail as it was; carrying it forward makes a new review that quietly agrees with an old one. Gmail is the source of truth — a thread archived or trashed has left the inbox and is absent on those grounds, and a thread deferred is still in the inbox and is asked about again, which is what deferring meant.

## Consequences and Tradeoffs

- Reviews are created far more often: one per entry rather than one per date. Revision numbers climb, and `review_runs` grows with them. This is a record of when Brian looked at his mail, which is worth its rows.
- A preparation made and not confirmed is lost to the next "hello". The rows behind it are not, and `superseded` says so.
- "Good morning" costs a Gmail read every time. That is the point of it.
- The contract moves to 0.18.0: `snapshot_at`, `supersedes_review_id` and `superseded` are additive and nullable, no request shape changes, and a stale import keeps working without them.
- `restart_available` and `restart_action` stay as ADR-0021 left them, and matter less: a finished review is now something Brian reads back, rather than something handed to him when he asked for his inbox.

## Affected Documents

- `docs/gpt-action-openapi.yaml` — what `startDailyReview` does, what `continueDailyReview` alone does, `Superseded`.
- `docs/gpt-daily-review-instructions.md` — every entry is a snapshot; only explicit continue resumes.
- `README.md` — the review lifecycle table, and what calling start twice in a day means.
