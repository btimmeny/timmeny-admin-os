# 00 — Timmeny Admin OS Documentation

**Status:** Active  
**Version:** 1.0  
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
8. [90 — Roadmap](./90-Roadmap.md) — the current sequence of work

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
| Roadmap | Low |

## Documentation Rules

- Git is the architectural source of truth.
- The daily Timmeny Admin OS and Timmeny Career chats generate operational evidence.
- The Architecture chat interprets that evidence and converts validated patterns into product and architecture decisions.
- Architecture is discovered through use rather than invented in isolation.
- Documents should reference one another instead of duplicating content.
- Significant decisions should be recorded as ADRs.
- Only documents affected by a decision should be updated.
- The library should remain concise and usable through progressive disclosure.

## Current State

The North Star and Product Definition are established. The next document to review and approve is **20 — Design Process**.

The existing FastAPI, Railway, Monday.com, and GPT Action documentation remains in this directory as implementation-specific reference material.