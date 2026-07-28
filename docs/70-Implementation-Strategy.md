# 70 — Implementation Strategy

**Status:** Active draft  
**Version:** 0.2  
**Stability:** Medium to low  
**Purpose:** Defines how the target product and architecture will be delivered incrementally without overbuilding or disrupting working capabilities.  
**Depends on:** [20 — Design Process](./20-Design-Process.md), [50 — Architecture](./50-Architecture.md), [60 — Domain Model](./60-Domain-Model.md), [ADR-0001](./adr/ADR-0001-admin-os-coordination-layer.md)

## Delivery Principles

- Preserve existing working Monday.com and Railway capabilities.
- Build from validated operational evidence.
- Prefer one complete operational loop over disconnected infrastructure.
- Introduce the smallest useful canonical abstraction.
- Expose business operations rather than vendor-specific fields.
- Keep classification, workflow execution, verification, and audit explicit.
- Require idempotent connector writes and deterministic external mappings.
- Expand only after the current slice works end-to-end in real use.

## MVP Boundary

Build only:

1. PostgreSQL persistence
2. Gmail connector
3. Monday.com connector
4. Executive Review API
5. ChatGPT interface

Calendar synchronization remains part of the target architecture and success condition, but it is not required to complete the first vertical slice unless existing calendar access can be reused without delaying it.

## First Vertical Slice

Implement exactly one workflow:

```text
Gmail thread synchronized
  -> evidence classified against an operational object
  -> disposition selected
  -> Monday task created or updated when required
  -> task completion synchronized
  -> email archived or otherwise dispositioned
  -> evidence, verification, and audit recorded
```

### Required behaviors

- Preserve Gmail thread and message identifiers.
- Resolve or create the affected operational object before task creation.
- Prevent duplicate Monday tasks on retries.
- Preserve the mapping among evidence, operational object, workflow run, and Monday item.
- Do not archive an email until the workflow's completion condition is satisfied.
- Require review for ambiguous classifications or unsafe writes.
- Make every action explainable: what was read, what was inferred, what changed, and how it was verified.

## Executive Review API

The first review endpoint should return a single assembled operational view containing:

- active outcomes and decisions relevant to the review
- open and recently completed Monday tasks
- new or unresolved Gmail evidence
- blocked or waiting workflows
- recommended next actions
- confidence, provenance, and freshness indicators

## Exit Criteria

The first slice is complete when it operates repeatedly on real Gmail and Monday records without duplicate tasks, lost evidence, premature archive actions, or unexplained state changes. Only then should additional workflows or integrations be added.