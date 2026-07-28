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

## Candidate Decisions

Create an ADR only when the decision materially constrains architecture or implementation. Current candidates include:

- PostgreSQL persistence and audit model
- Executive Review API contract
- sync scheduling and trigger model

Operational discoveries remain under `operating/` until they justify an architectural decision.