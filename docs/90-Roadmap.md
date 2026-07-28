# 90 — Roadmap

**Status:** Active  
**Version:** 0.2  
**Stability:** Low  
**Purpose:** Defines the current implementation sequence for Timmeny Admin OS.  
**Depends on:** [70 — Implementation Strategy](./70-Implementation-Strategy.md), [ADR-0001](./adr/ADR-0001-admin-os-coordination-layer.md)

## Now — Establish the Build Baseline

- Accept ADR-0001 as the current architectural boundary.
- Inventory the existing FastAPI, Railway, Monday connector, authentication, and deployment capabilities.
- Define the minimum PostgreSQL schema for operational objects, evidence, mappings, workflow runs, and audit.
- Define connector interfaces and idempotency rules.
- Select the existing Monday board and minimum fields for the first vertical slice.
- Define the Executive Review response contract.

## First Vertical Slice

Build and validate:

```text
Gmail
  -> evidence and operational-object classification
  -> Monday task creation or update
  -> task completion synchronization
  -> Gmail archive or disposition
  -> verification and audit
```

Required implementation components:

1. PostgreSQL and migrations
2. Gmail synchronization adapter
3. Monday synchronization adapter
4. operational-object and evidence services
5. workflow orchestration and audit
6. Executive Review API
7. ChatGPT-facing API contract
8. tests covering retries, duplicate prevention, ambiguous classification, completion, and archive safety

## Exit Gate

Do not add additional integrations or broad workflow types until the first slice:

- runs end-to-end on real records
- is idempotent
- preserves source identity and provenance
- creates no duplicate tasks
- does not archive prematurely
- exposes explainable verification and audit history
- produces a useful Executive Review

## Next

After the exit gate:

- add Calendar synchronization to the Executive Review
- expand classification dispositions beyond task creation
- add recurring obligations and lifecycle workflows
- add decision and waiting-state workflows
- improve entity resolution and duplicate cleanup
- evaluate broader Monday board redesign based on observed requirements

## Later

- goal and life-health intelligence
- broader finance, travel, household, family, and relationship administration
- additional communication channels and documents
- proactive safe automation governed by explicit policy

The roadmap is intentionally narrow. Working operational loops take precedence over architectural breadth.