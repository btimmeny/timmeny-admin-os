# ADR-0004 — Classification Boundary and Review State

**Status:** Accepted
**Date:** 2026-07-28
**Depends on:** [ADR-0001](./ADR-0001-admin-os-coordination-layer.md), [ADR-0003](./ADR-0003-gmail-access-and-retention.md), [75 — First Vertical Slice](../75-First-Vertical-Slice.md)

## Context

The slice invariant is that an email is evidence, not automatically a task, and that no Monday task is created until classification selects that disposition. Classification is therefore the gate that decides whether anything leaves Admin OS.

Two facts constrain the first classifier.

The evidence set is small and narrow. After the intake scope was reduced to the intersection of `INBOX` and `financial/taxes`, exactly one thread is in scope. There is no corpus to derive rules from, and the account owner has not yet defined what makes a tax thread actionable — a request directed at them, a filing deadline, a specific sender, or something else.

The failure modes are asymmetric. A false positive creates a Monday task the owner did not ask for and, once the archive disposition is enabled, can retire mail that still needed attention. A false negative leaves a filing obligation unnoticed. Both are worse than an explicit "I do not know", and neither is a good trade for saving one human glance at one thread a month.

The domain model already carries the machinery for this: `classifications.requires_review`, a `needs_review` disposition, and the `awaiting_review` workflow state.

## Decision

**Classifier v1 makes no inference.** Version `v1-review-all` classifies every piece of evidence identically: `disposition = needs_review`, `requires_review = true`, `confidence = 0.0`, `relationship_type = undetermined`, and a rationale saying so. It reads no subject, no sender, and no snippet.

**`undetermined` is a real relationship value.** The domain vocabulary lists `creates`, `updates`, `completes`, `blocks`, `contradicts`, and `supports`, all of which assert something. A classifier that derived nothing must not pick one of them; `undetermined` keeps the absence of an inference visible in the data rather than disguised as a weak `supports`.

**A row is written rather than inferred from absence.** "Awaiting review" is represented by a classification record, not by the lack of one. Unclassified evidence and evidence a human must look at are different states, and only an explicit row can carry a version, a timestamp, and a rationale — so when v2 changes the rules, what v1 decided about a given thread is still legible.

**Classification is identified by `(evidence, classifier version)`, enforced by a unique constraint.** Re-running is free, matching the sync. A new version classifies the same evidence again under its own version rather than overwriting the old verdict.

**Review is a queue, not yet a workflow.** `POST /admin/classify` populates it and `GET /admin/review-queue` reads it. Nothing resolves an item yet, because resolving one means creating a Monday task, which is the next increment. The queue is deliberately shipped before the thing that empties it, so intake and classification can be observed against real mail without any write being possible.

**Classification stays deterministic and local.** No model call. ChatGPT reasons *about* the queue through the API; it does not decide what enters it. That boundary is the point of ADR-0001, and it also keeps mailbox metadata out of any third party.

## Alternatives Considered

1. **Keyword rules over subject and sender now** — the obvious v1. Rejected: with one thread in scope, any rule is a guess dressed as a policy, and a wrong one is discovered only when it silently drops a filing.
2. **An LLM classifier.** Rejected for this increment. It moves domain judgment out of Admin OS, requires sending mailbox metadata to a third party, and produces confidence figures that are not calibrated to anything.
3. **Auto-create a Monday task for every intake thread.** Rejected: directly violates the slice invariant, and would have created 131 tasks on the first sync.
4. **Skip classification and let the review queue read evidence directly.** Simpler, but loses versioning and rationale, and leaves no place for v2 to record a decision.
5. **Treat "no classification row" as "needs review".** Rejected: conflates "not yet examined" with "examined, undecided", and the distinction is what makes a stalled classifier visible.

## Consequences

- Every intake thread requires a human glance. Acceptable at one thread; the trigger to build real rules is the queue growing faster than it is read.
- The queue cannot currently be emptied. Until Increment 5 lands, an item stays in it after the owner acts on the mail.
- Because classification carries a version, adding v2 does not invalidate history, but it does mean two rows per thread and a queue that must filter by version.
- Classifications are deleted with their evidence when a thread is pruned; a classification about a thread that no longer exists is meaningless. Anything intended to outlive the thread must live in an operational object or audit record, not here.
- Confidence is stored as `0.0` rather than null, so downstream code can order by it without special-casing. Nothing should read that as "certainly not actionable" — it means no inference was attempted.

## Affected Documents

- [75 — First Vertical Slice](../75-First-Vertical-Slice.md)
- [90 — Roadmap](../90-Roadmap.md)
- [README](../../README.md)

## Validation

- Deterministic tests: every thread routed to review; the relationship recorded as `undetermined` with zero confidence; re-classification a no-op; the unique constraint rejecting a duplicate at the database level; the queue ordered newest first and bounded by `limit`.
- Against production evidence: `POST /admin/classify` followed by `GET /admin/review-queue` returns the in-scope threads and no Monday item is created.
