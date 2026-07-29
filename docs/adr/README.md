# Architecture Decision Records

Architecture Decision Records capture significant decisions that should remain understandable over time.

Each ADR includes:

- status
- context
- decision
- alternatives considered
- consequences and tradeoffs
- affected documents
- validation approach

ADRs are append-only historical records. Superseded decisions are marked as superseded and linked to the replacement ADR rather than silently rewritten.

## Accepted ADRs

- [ADR-0001 — Admin OS Is the Coordination Layer](./ADR-0001-admin-os-coordination-layer.md)
- [ADR-0002 — Monday Identity and Write Idempotency](./ADR-0002-monday-identity-and-idempotency.md)
- [ADR-0003 — Gmail Access, Intake Scope, and Retention](./ADR-0003-gmail-access-and-retention.md)
- [ADR-0004 — Classification Boundary and Review State](./ADR-0004-classification-boundary-and-review.md)
- [ADR-0005 — Duplicate Review Before Monday Writes](./ADR-0005-duplicate-review-before-monday-writes.md)
- [ADR-0006 — Approval Gate and Verified Monday Writes](./ADR-0006-approval-gate-and-verified-writes.md)
- [ADR-0007 — Deterministic Labeling and Grouped Review](./ADR-0007-deterministic-labeling-and-grouped-review.md)
- [ADR-0008 — Label-Scoped Daily Action Loop](./ADR-0008-label-scoped-daily-action-loop.md)
- [ADR-0009 — Capability Configuration and the Persisted Review State Machine](./ADR-0009-review-engine-implementation.md)
- [ADR-0010 — The Action Lifecycle and How a Correction Becomes a Rule](./ADR-0010-action-lifecycle-and-learning.md)
- [ADR-0011 — Presentation Is a Versioned Contract Owned by Admin OS](./ADR-0011-presentation-contracts.md)
- [ADR-0012 — Archive and Trash Are the Two Dispositions, and Neither Deletes](./ADR-0012-gmail-dispositions.md)
- [ADR-0013 — A Recommendation to File Mail Names the Folder](./ADR-0013-filing-mail-in-a-named-folder.md)
- [ADR-0014 — An Execution Runs the Rows That Were Selected, and No Others](./ADR-0014-execution-scope-integrity.md)
- [ADR-0015 — The Review Is of the Inbox, and Says So](./ADR-0015-review-mailbox-scope.md)
- [ADR-0016 — The Contract the GPT Holds Is the One This Service Serves](./ADR-0016-a-contract-that-cannot-go-stale.md)
- [ADR-0017 — A Review Is an Object With a Life, and Ending One Is a Decision](./ADR-0017-a-review-is-an-object-with-a-life.md)
- [ADR-0018 — A Decision Is Not a Done Thing, and the Review Says So](./ADR-0018-a-decision-is-not-a-done-thing.md)
- [ADR-0019 — A Review States Its Plan Before It Works](./ADR-0019-a-review-states-its-plan.md)
- [ADR-0020 — A Session Opens With the Playbook, in Admin OS's Words](./ADR-0020-a-session-opens-with-the-playbook.md)
- [ADR-0021 — Refreshing the Mail Is a Restart, and Says So in Data](./ADR-0021-refreshing-mail-is-a-restart.md)

## Candidate Decisions

Create an ADR only when the decision materially constrains architecture or implementation. Current candidates include:

- sync scheduling and trigger model;
- presentation contracts for the action lifecycle and rule-learning views;
- Monday task execution inside the review's action lifecycle;
- automatic retry policy for failed actions;
- model-provider and prompt-version governance.

Operational discoveries remain under `operating/` until they justify an architectural decision.
