# 70 — Implementation Strategy

**Status:** Active draft  
**Version:** 0.3  
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
- Separate deterministic detection from contextual reasoning.
- Treat confidence as review metadata, not execution authority.
- Expand only after the current capability works repeatedly in real use.

## MVP Boundary

Build only:

1. PostgreSQL persistence
2. Gmail connector
3. Monday.com connector
4. Executive Review API
5. ChatGPT interface

Calendar synchronization remains part of the target architecture and success condition, but it is not required to complete the first operational capabilities unless existing calendar access can be reused without delaying them.

## Achieved Foundation

The repository now contains the core safety and execution foundation:

- PostgreSQL-backed evidence, classification, mappings, workflow runs, and migrations;
- Gmail authentication, label-scoped inbox intake, thread-level idempotency, and evidence pruning safeguards;
- an explicit classifier boundary that routes uncertain evidence to review;
- a human review queue;
- Monday board reads and duplicate scoring;
- approval-gated Monday task creation;
- reserved Admin OS identity, retry adoption, read-back verification, and audit.

The current classifier intentionally asserts no business meaning and assigns zero confidence. The next increment should add a narrow deterministic capability rather than a broad general classifier.

## Current Capability — Advisor and Expert Calls

Implement the bounded capability defined in [78 — Advisor and Expert Calls Capability](./78-Advisor-Expert-Calls-Capability.md):

```text
Morning Gmail refresh
  -> deterministic advisor/expert-call detection
  -> apply or preserve Career - Advisor/Expert Calls
  -> synchronize labeled inbox threads as evidence
  -> grouped review with confidence and exceptions
  -> Brian confirms or corrects recommendations in bulk
  -> approved actions pass through existing execution gates
  -> feedback is retained for explicit rule promotion
```

### Required behaviors

- Preserve Gmail thread and message identifiers.
- Version deterministic sender, domain, and phrase rules.
- Record which rule matched and whether a label write occurred or was a no-op.
- Distinguish label confidence from recommendation confidence.
- Group opportunities without merging distinct engagements.
- Return exactly one proposed disposition per item.
- Allow bulk confirmation only for homogeneous dispositions above configured thresholds.
- Preserve item-level decisions and batch audit.
- Prevent bulk review from bypassing Gmail or Monday safety gates.
- Record corrections as structured learning events.

## Executive Review API

The Executive Review should evolve from a flat unresolved-evidence list into one assembled operational view containing:

- capability groups, beginning with `Career - Advisor/Expert Calls`;
- group counts, confidence ranges, and exception counts;
- active outcomes and decisions relevant to the review;
- open and recently completed Monday tasks;
- new or unresolved Gmail evidence;
- blocked or waiting workflows;
- recommended next actions;
- confidence, provenance, freshness, rule version, and model version indicators.

## Learning Strategy

The system learns through explicit corrections rather than silent observation.

Each reviewed recommendation preserves:

- original classification and recommendation;
- final confirmed classification and disposition;
- supporting evidence and operational-object links;
- candidate preference or deterministic rule;
- promotion status.

Reusable behavior progresses through `observed`, `proposed`, `confirmed`, `automatable`, and `retired`. A single accepted recommendation does not become a permanent rule.

## Exit Criteria for the Current Capability

The advisor/expert-call capability is ready for daily use when it operates repeatedly on real Gmail records with:

- acceptable false-positive and false-negative rates;
- no duplicate evidence or repeated label writes;
- one coherent grouped morning review;
- visible low-confidence exceptions;
- auditable bulk confirmation;
- no unauthorized replies, archive actions, deletions, or Monday writes;
- structured retention of Brian's corrections and confirmed preferences.