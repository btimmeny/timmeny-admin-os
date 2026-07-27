# Architecture Decision Records

Architecture Decision Records capture significant decisions that should remain understandable over time.

Each ADR should include:

- status
- context
- decision
- alternatives considered
- consequences and tradeoffs
- affected documents
- validation approach

ADRs are append-only historical records. Superseded decisions should be marked as superseded and linked to the replacement ADR rather than silently rewritten.

## Initial ADR Candidates

- AI reasoning is stateless; Timmeny Admin OS owns durable state.
- Objectives are a primary operational business object.
- Business concepts remain independent of external implementations.
- Monday.com is the operational work system, not the canonical domain model.
- PostgreSQL stores durable context, learning, mappings, and history.
- Product architecture is discovered through operational use.