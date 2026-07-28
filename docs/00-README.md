# 00 — Timmeny Admin OS Documentation

**Status:** Active  
**Version:** 1.1  
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
8. [75 — First Vertical Slice](./75-First-Vertical-Slice.md) — the current bounded implementation contract
9. [80 — Monday.com Architecture](./80-Monday-Architecture.md) — the execution-system implementation design
10. [85 — Operating Model](./85-Operating-Model.md) — the current operating-object specification
11. [90 — Roadmap](./90-Roadmap.md) — the current sequence of work
12. [95 — Governance](./95-Governance.md) — how operational learning changes the model and architecture

Supporting material:

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
- Only documents affected by a decision should be updated.
- The library should remain concise and usable through progressive disclosure.

## Current State

[ADR-0001](./adr/ADR-0001-admin-os-coordination-layer.md) establishes the current architectural boundary: ChatGPT is the reasoning layer; Timmeny Admin OS is the coordination layer; Monday.com owns task execution; PostgreSQL persists canonical operational state; Gmail owns communication; and Calendar owns scheduling.

Implementation should now begin with the repository assessment and bounded workflow in [75 — First Vertical Slice](./75-First-Vertical-Slice.md).