# ADR-0009 — Capability Configuration and the Persisted Review State Machine

**Status:** Accepted
**Date:** 2026-07-28
**Depends on:** [ADR-0001](./ADR-0001-admin-os-coordination-layer.md), [ADR-0003](./ADR-0003-gmail-access-and-retention.md), [ADR-0004](./ADR-0004-classification-boundary-and-review.md), [ADR-0006](./ADR-0006-approval-gate-and-verified-writes.md), [ADR-0007](./ADR-0007-deterministic-labeling-and-grouped-review.md), [ADR-0008](./ADR-0008-label-scoped-daily-action-loop.md)

ADR-0007 and ADR-0008 decide *what* the daily review is: deterministic labelling separated from contextual recommendation, and a persisted label-scoped review and action loop. This ADR decides *how* that is built — where capability policy lives, what is persisted, and where the boundary between the model and the state machine falls in code.

## Context

Everything built so far is a set of administrative endpoints: sync, classify, list the queue, check for duplicates, create one task. Each is a step an operator runs. None of them is what the account owner actually asks for, which is *"good morning — what's in my inbox?"*

That phrase implies things the current endpoints do not provide. It implies a session with a beginning and an end, which can be resumed after lunch without starting over or asking twice about the same thread. It implies mail arriving grouped by the kind of attention it needs — a request for an advisory call is a different act of judgement from a tax notice — rather than as one undifferentiated list. And it implies that a decision, once made, is remembered.

The intake also has to widen past one label: three kinds of mail now, more later. The naive way to widen it is a branch per label, and each branch then acquires its own conditions about what may be done to that mail. Behaviour that matters — which actions a kind of mail permits, when a human must be asked — ends up spread across functions where it cannot be read as a whole, reviewed before it takes effect, or explained afterwards.

The last constraint is the interesting one. A model can read a thread and say something useful about it. It cannot be the thing that decides an action was approved, because a model's output is a probability and an approval is a fact. Yet the model's reading has to reach the review, or the review is no better than the deterministic classifier of ADR-0004 — which asserts nothing at all.

## Decision

**A capability is data, not a branch.** `config/capabilities.yaml` defines each capability: its Gmail labels, its playbook steps, its recommendation policy, the actions it may take, its approval and completion rules, its learning scope, and the objectives it serves. Code iterates capabilities generically; no function names a label. Adding a capability, reordering the review, or narrowing what a capability may do is an edit to one reviewable file.

The configuration is validated on load and refused if inconsistent, rather than degraded into a partly working review. Several refusals encode rules that would otherwise live in code and be violated quietly:

- A policy rule may not recommend an action the capability is not granted, so permission cannot be widened by recommending around it.
- The default recommendation may not be an action, so mail that matches nothing is never acted on by omission.
- A capability may not auto-approve an action it is not allowed to take.
- `record_message_content: true` is refused outright: ADR-0003's retention rule is not a capability's to opt out of.
- Two capabilities may not share a position, because position is what orders the review.

**A review run is persisted and identified by its date.** `review_runs → review_groups → review_items → review_decisions`. "Start my daily review" is one call that creates the run or resumes today's. Resumption is the normal case: decisions already made are kept, mail that has arrived since is added, and nothing is asked twice.

Groups are presented one at a time, in configured order, because grouping by the kind of attention required is the point rather than a display detail.

**A review item copies its evidence rather than referencing it.** ADR-0003 makes evidence non-permanent: archiving a thread prunes its evidence row. A decision must remain explicable after that, so the subject, participants, and dates a decision was made on are copied onto the item. The audit record cannot be hollowed out by later mailbox activity.

**An item is identified by thread and content.** A thread settled in an earlier review does not come back; a thread whose content hash has changed does. A reply reopens a conversation, sitting in the inbox does not, and the same distinction that keeps the daily review short is the one that catches a genuinely new message.

A group waiting only on execution does not block the one behind it. Sequencing exists to keep attention on one kind of work at a time, not to make an unexecuted action stall the rest of the morning, so the review advances to the next group needing a decision and returns to outstanding actions at the end.

Deferral is deliberately not a settlement. It clears an item out of today's review and lets the same thread return tomorrow, which is what "not now" means and what a dismissal must not be allowed to mean by accident.

**The model interprets; the state machine decides.** An assessment carries a category from the capability's own vocabulary, a confidence, a rationale, a model version, and at most a *suggested* action. It is schema-validated on arrival: an unknown category is refused, and so is a suggestion the capability is not allowed to act on. A suggestion is adopted as the item's recommendation only above the capability's `min_ai_confidence`, and below it is recorded as unadopted with the reason. No assessment can move an item to approved or executed. Every transition to an action runs through `record_decision`, which is deterministic and refuses what configuration does not permit.

This is the boundary of ADR-0004 restated at a point where the model is now allowed to be useful. The earlier decision was to record no inference at all rather than a weak one; this decision is to record inference explicitly as inference, separated from the human act that authorises anything.

**Approval records an action; it does not perform one.** An approved item is `approved`, not `executed`, and a run holding approved actions reports `awaiting_actions` rather than completion. Execution is the next increment, and until it exists the state machine says so instead of implying the work happened.

**Bulk decisions are all or nothing.** "Archive all of these" is one intent, so it is validated across every selected item before any of them changes. A capability may withhold bulk decisions entirely, which is how mail that deserves individual attention is prevented from being cleared in a gesture.

**Every run records the configuration that produced it** — the version and a digest of the file. A decision made months ago can then be explained against the exact rules in force at the time, rather than against today's.

## Alternatives Considered

1. **A branch per label.** Rejected: it scatters the rules that matter most across the code, and each new capability makes the previous ones harder to reason about. Configuration makes the whole policy readable at once, and reviewable before it takes effect.
2. **Hardcode the three capabilities now and generalise later.** Rejected: the generalisation never happens under pressure, and by then the branches have acquired exceptions.
3. **Let the model return actions to execute.** Rejected: it makes "was this approved?" a question about a probability. The model may suggest; only a decision authorises.
4. **Let the model's confidence auto-approve.** Deferred, not refused: the mechanism exists (`min_confidence_for_auto`), and every capability currently sets `auto_approve: []`. Auto-approval should be enabled on evidence of a pattern behaving well, not on the day it becomes possible.
5. **Keep the review in memory for the duration of a conversation.** Rejected: it cannot be resumed, cannot be audited, and cannot answer why something was archived three weeks ago.
6. **Reference evidence by foreign key from a review item.** Rejected: pruning would then delete the audit trail, or pruning would have to stop — and pruning is what keeps the intake set honest.
7. **One review per capability per day.** Rejected: three runs a day is three sessions to resume and no single answer to "am I done?".
8. **Treat an item as settled forever once decided.** Rejected: a reply to a dismissed thread is new information, and content hashing already distinguishes the two cases.

## Consequences

- Behaviour now depends on a file that is not code. A malformed or missing configuration fails the review with `503` rather than silently reviewing nothing — the loud failure is deliberate.
- Rules match on retained metadata only: subject, participants, dates. Anything requiring the body of a message has to come from an assessment, because there is no body stored to match against.
- The shipped configuration carries no rules and no auto-approvals, so every thread arrives as `needs_review`. This is ADR-0004's position, now expressed as configuration rather than as a classifier version.
- A thread carrying two capabilities' labels appears in both groups. Deduplicating it would mean deciding which kind of attention it deserves, which is the account owner's judgement rather than the system's.
- Approved actions accumulate until the execution increment exists. Runs will sit in `awaiting_actions`, which is an accurate description of the system rather than a defect.
- The Gmail action vocabulary (`gmail.trash`, `gmail.send_draft`) is defined here but granted to nothing yet. Naming an action in the enum is not granting it.

## Not Yet Implemented

ADR-0007 and ADR-0008 ask for more than this increment delivers, and the gaps are deliberate rather than overlooked:

- Execution. Prepared actions, Gmail label, archive, Trash, draft, and send, the granular write policy, idempotency keys, read-back verification, and retry state are the next increment. Until then a capability's `allowed_actions` govern what may be *approved*, not what may happen.
- Deterministic labelling. Threads are grouped by labels already present; Admin OS does not yet apply the `Career - Advisor/Expert Calls` label itself, so ADR-0007's label-confidence value has nothing to report and is not persisted separately from recommendation confidence.
- The urgent-first pass. Document 79 opens the review with items needing immediate action across all groups; groups are currently presented in configured order only, since urgency has no source until rules or assessments supply one.
- Learning events. Decisions are recorded with the recommendation they agreed or disagreed with, which is the raw material; promoting a correction into a candidate rule is not built.
- Monday reconciliation inside the review. `monday.create_task` is an approvable action, but execution state, waiting state, and dependencies from Monday are not yet joined into the run.

## Affected Documents

- [README](../../README.md) — Daily review, Capabilities
- [ADR-0003](./ADR-0003-gmail-access-and-retention.md) — intake scope is now capability configuration
- [75 — First Vertical Slice](../75-First-Vertical-Slice.md)
- [79 — Daily Assistant Review](../79-Daily-Assistant-Review.md)

## Validation

- The shipped configuration is parsed by the test suite, so a broken file fails before deployment rather than at the first "good morning".
- Every configuration refusal above is tested for, including the retention rule and the rule that a policy may not recommend an unpermitted action.
- Tests assert that starting twice in a day resumes one run, that a new day starts a new one, that mail arriving mid-day joins the open review, and that a settled thread returns only when its content has changed.
- Tests assert that an item survives the deletion of the evidence it came from, with its subject and decision intact.
- Tests assert that an assessment cannot approve or execute anything, that an unknown category and an unpermitted suggestion are refused, and that a low-confidence suggestion is recorded without being adopted.
- Tests assert that an unpermitted action is refused however it is requested, that a capability without an `execute_approved` step cannot approve actions at all, and that a bulk decision refused for one item changes none of them.
