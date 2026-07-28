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

## Candidate Decisions

Create an ADR only when the decision materially constrains architecture or implementation. Current candidates include:

- canonical identity and external-system mapping strategy
- operational-object classification contract
- workflow safety and approval policy
- Monday board and minimum-field design for the first vertical slice
- PostgreSQL persistence and audit model
- Executive Review API contract

Operational discoveries remain under `operating/` until they justify an architectural decision.