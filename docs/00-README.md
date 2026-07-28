# 00 — Timmeny Admin OS Documentation

**Status:** Active  
**Version:** 1.3  
**Stability:** High  
**Purpose:** Provides the reading order, current documentation status, and navigation for the Timmeny Admin OS architecture library.

## Reading Order

The documentation is ordered from most enduring to most changeable:

1. [10 — North Star](./10-NorthStar.md) — why Timmeny Admin OS exists
2. [20 — Design Process](./20-Design-Process.md) — how the product is discovered and evolved
3. [30 — Architecture Principles](./30-Architecture-Principles.md) — the rules every design must satisfy
4. [40 — Product](./40-Product.md) — what the product is and what outcomes it creates
5. [50 — Architecture](./50-Architecture.md) — how the system is organized
6. [60 — Domain Model](./60-Domain-Model.md) — the canonical business language
7. [70 — Implementation Strategy](./70-Implementation-Strategy.md) — how capabilities are built incrementally
8. [75 — First Vertical Slice](./75-First-Vertical-Slice.md) — the current bounded implementation contract and achieved increments
9. [78 — Advisor and Expert Calls Capability](./78-Advisor-Expert-Calls-Capability.md) — deterministic expert-call labeling and grouped review
10. [79 — Daily Assistant Review](./79-Daily-Assistant-Review.md) — the daily conversational review, approval, execution, and learning loop
11. [80 — Monday.com Architecture](./80-Monday-Architecture.md) — the execution-system implementation design
12. [85 — Operating Model](./85-Operating-Model.md) — the current operating-object specification
13. [90 — Roadmap](./90-Roadmap.md) — the current sequence of work
14. [95 — Governance](./95-Governance.md) — how operational learning changes the model and architecture

Supporting material:

- [76 — Repository Assessment](./76-Repository-Assessment.md) — current-state assessment and staged plan for the first slice
- [77 — First Slice Setup Runbook](./77-First-Slice-Setup.md) — the manual Monday, Railway, and Google steps
- [Architecture Decision Records](./adr/README.md)
- [Product Discoveries](./discovery/Product-Discoveries.md)
- [Diagrams](./diagrams/README.md)

## Stability Model

Lower-numbered documents are more foundational and should change less frequently. Higher-numbered documents are progressively more tactical and may change as operational evidence grows.

| Document | Expected Stability |
|---|---|
| North Star | Very high |
| Design Process | Very high |
| Architecture Principles | Very high |
| Product | High |
| Architecture | Medium |
| Domain Model | Medium |
| Implementation Strategy | Medium to low |
| First Vertical Slice | Low |
| Capability specifications | Low |
| Monday.com Architecture | Low |
| Operating Model | Medium to low |
| Roadmap | Low |
| Governance | Medium |

## Documentation Rules

- Git is the architectural source of truth.
- The daily Timmeny Admin OS and Timmeny Career chats generate operational evidence.
- The Architecture chat interprets that evidence and converts significant decisions into ADRs.
- Architecture is discovered through use rather than invented in isolation.
- Documents should reference one another instead of duplicating content.
- Significant decisions are recorded as ADRs.
- Capability documents define bounded business behavior for implementation.
- Only documents affected by a decision should be updated.
- The library should remain concise and usable through progressive disclosure.

## Current State

[ADR-0001](./adr/ADR-0001-admin-os-coordination-layer.md) establishes the architectural boundary: ChatGPT is the reasoning layer; Timmeny Admin OS is the coordination layer; Monday.com owns task execution; PostgreSQL persists canonical operational state; Gmail owns communication; and Calendar owns scheduling.

The repository has working Gmail evidence intake, an explicit classification and review boundary, Monday board reads and duplicate checks, and approval-gated verified Monday task creation. The current product increment is the label-scoped daily action loop in [79 — Daily Assistant Review](./79-Daily-Assistant-Review.md), beginning with [78 — Advisor and Expert Calls Capability](./78-Advisor-Expert-Calls-Capability.md) and governed by [ADR-0007](./adr/ADR-0007-deterministic-labeling-and-grouped-review.md) and [ADR-0008](./adr/ADR-0008-label-scoped-daily-action-loop.md).
