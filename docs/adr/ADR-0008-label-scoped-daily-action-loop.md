# ADR-0008 — Label-Scoped Daily Action Loop

**Status:** Accepted  
**Date:** 2026-07-28

## Context

Timmeny Admin OS now has Gmail evidence intake, a review boundary, Monday duplicate detection, and approval-gated verified Monday task creation. The next product step is not another isolated connector. Brian needs a repeatable daily interaction in which he opens the Admin OS chat, reviews current inbox groups, approves recommended actions, and sees those actions executed and verified.

The first Gmail capability is `Career - Advisor/Expert Calls`. Taxes already have a configured intake label, and administrative mail is the next bounded category. These categories have different business meaning and different safe actions. A broad inbox classifier or one global Gmail-write policy would couple them prematurely and make behavior difficult to explain.

## Decision

Timmeny Admin OS will implement the daily assistant as a persisted, label-scoped review and action loop.

1. Brian initially starts the daily review through the Admin OS conversational interface.
2. Admin OS creates or resumes one persisted daily-review run for the day.
3. Gmail capabilities are introduced one label at a time. Initial groups are advisor/expert calls, taxes, and a configurable administrative-mail label.
4. Each capability owns its deterministic intake rules, contextual recommendation policy, allowed dispositions, execution permissions, completion conditions, and learning history.
5. The review API assembles capability groups and stable review-item IDs. The GPT does not independently reconstruct workflow state from raw Gmail calls.
6. Recommendation approval is separate from external execution. External actions progress through prepared, executed, and verified states.
7. Replies are drafted before sending. Sending requires explicit approval during the initial phase.
8. Archive, Trash, label changes, and Monday writes are granular, idempotent, verified, and individually auditable.
9. Bulk confirmation is permitted only for homogeneous recommendations that meet capability-specific confidence and exception rules; each item still receives its own decision and action records.
10. Learning is capability-scoped and based on explicit corrections. No preference silently propagates across unrelated Gmail groups.
11. Monday.com is incorporated into the same daily review as execution state, waiting state, dependencies, and commitments. Gmail and Monday are joined through Admin OS operational objects rather than shown as disconnected lists.
12. Scheduled or proactive morning delivery may be added later, but it must invoke the same persisted review service and policy gates as the conversational flow.

## Alternatives Considered

### Let the GPT directly search Gmail and execute ad hoc actions

Rejected. This would make state, approvals, retries, and audit dependent on a conversation and would bypass the coordination-layer architecture.

### Build one general-purpose inbox classifier before taking action

Rejected. It delays value, broadens risk, and prevents capability-specific learning. Label-scoped capabilities provide a bounded path to real inbox impact.

### Use Gmail labels only as manual folders

Rejected as the target. Manual labels are useful evidence, but deterministic labeling and controlled action are required to reduce repetitive work.

### Schedule the review before the interactive flow works

Rejected for the first release. A scheduled trigger does not solve review state, approvals, or execution safety. The interactive daily loop must work first.

### Treat Gmail and Monday as separate morning summaries

Rejected. The product objective is one administrative operating view that connects communication evidence to commitments, waiting state, and outcomes.

## Consequences and Tradeoffs

- The daily experience becomes a first-class product capability rather than a prompt convention.
- A persisted daily-review model and item-level action state are required.
- Gmail permissions must become more granular than the current global write switch.
- Three bounded capabilities can share infrastructure while retaining separate policy.
- Draft, send, archive, Trash, label, and Monday actions require explicit execution contracts and verification.
- The GPT becomes simpler and safer because it receives stable IDs and allowed actions from Admin OS.
- Scheduling is deferred until the same flow is reliable interactively.
- The exact administrative Gmail label remains an explicit configuration decision rather than an architectural assumption.

## Affected Documents

- `docs/00-README.md`
- `docs/70-Implementation-Strategy.md`
- `docs/79-Daily-Assistant-Review.md`
- `docs/90-Roadmap.md`
- `docs/adr/README.md`
- `operating/review/daily-review.md`
- `operating/learning/brian-preferences.md`

## Validation

Validate through repeated real daily reviews:

- one conversational request starts or resumes the current review;
- each enabled Gmail group refreshes without duplicates;
- Brian can approve, correct, and defer recommendations;
- approved actions execute only through capability policy;
- draft and send remain distinct;
- archive and Trash are distinct and verified;
- failed writes remain retry-safe and visible;
- review state survives conversation boundaries;
- corrections create structured learning events;
- Monday state can be joined to email evidence without duplicate or conflicting execution state.
