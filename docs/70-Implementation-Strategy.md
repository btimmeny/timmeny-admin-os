# 70 — Implementation Strategy

**Status:** Draft scaffold  
**Version:** 0.1  
**Stability:** Medium to low  
**Purpose:** Defines how the target product and architecture will be delivered incrementally without overbuilding or disrupting working capabilities.  
**Depends on:** [20 — Design Process](./20-Design-Process.md), [50 — Architecture](./50-Architecture.md), [60 — Domain Model](./60-Domain-Model.md)

## Initial Delivery Principles

- Preserve existing working Monday.com and Railway capabilities.
- Build from validated operational discoveries.
- Introduce the smallest useful canonical abstraction.
- Prefer end-to-end operational loops over isolated infrastructure.
- Expose business operations rather than SQL, GraphQL, or vendor-specific fields.
- Keep important actions reviewable, explainable, and auditable.
- Validate each capability through the Timmeny Admin OS or Timmeny Career operational workspace.
- Expand capabilities only when further use demonstrates value.

This document will be completed after the canonical principles, architecture, and domain model are approved.