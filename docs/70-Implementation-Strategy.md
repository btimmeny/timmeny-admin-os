# 70 — Implementation Strategy

**Status:** Active draft  
**Version:** 0.4  
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
- Introduce Gmail capabilities one label at a time.
- Keep recommendation approval separate from action execution.
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

The current classifier intentionally asserts no business meaning and assigns zero confidence. The next increments should add narrow deterministic capabilities and a persisted daily-review contract rather than a broad general classifier.

## Current Product Increment — Daily Assistant Review

Implement [79 — Daily Assistant Review](./79-Daily-Assistant-Review.md) as the user-facing operating loop:

```text
Brian requests the morning review
  -> Admin OS creates or resumes today's review run
  -> enabled Gmail capability groups refresh
  -> each item receives a recommendation and confidence
  -> Brian approves, corrects, defers, or rejects
  -> approved actions are prepared, executed, and verified
  -> unresolved state remains visible
  -> Monday.com execution and waiting state join the same review
```

The GPT should converse over one assembled Admin OS review contract. It should not reconstruct workflow state by independently chaining raw Gmail and Monday calls.

## Initial Gmail Capabilities

Build a shared capability framework and instantiate it narrowly for:

1. `Career - Advisor/Expert Calls`;
2. `financial/taxes`;
3. a configurable administrative-mail label pending confirmation of its exact Gmail name.

Each capability has independent:

- deterministic intake rules;
- reasoning and recommendation policy;
- allowed dispositions;
- execution permissions;
- confidence thresholds;
- completion conditions;
- learning and rule history.

Shared implementation must not imply shared business rules.

## Advisor and Expert Calls

Implement [78 — Advisor and Expert Calls Capability](./78-Advisor-Expert-Calls-Capability.md):

```text
Gmail refresh
  -> deterministic advisor/expert-call detection
  -> apply or preserve Career - Advisor/Expert Calls
  -> synchronize labeled inbox threads as evidence
  -> grouped review with confidence and exceptions
  -> Brian confirms or corrects recommendations
  -> approved actions pass through execution gates
  -> feedback is retained for explicit rule promotion
```

Required behaviors:

- version sender, domain, and phrase rules;
- record rule provenance and label-write result;
- distinguish label confidence from recommendation confidence;
- group opportunities without merging distinct engagements;
- return exactly one proposed disposition per item;
- support homogeneous bulk approval with item-level audit;
- draft replies before sending;
- prevent labels or confidence from authorizing replies, archive, Trash, or Monday writes;
- preserve corrections as structured learning.

## Action Execution Framework

The daily-review framework must model:

```text
recommended
  -> approved or corrected
  -> prepared
  -> executed
  -> verified
  -> complete or failed
```

Initially supported actions should include record only, wait, defer, label, archive, move to Trash, draft reply, send approved reply, create or update an operational object, and create a Monday task through the existing gate.

Every action must expose:

- stable action ID;
- review-item ID;
- capability and policy version;
- requested parameters;
- approval identity and timestamp;
- execution attempts;
- verification result;
- retry state;
- failure detail without secrets.

## Executive Review API

The Executive Review should evolve from a flat unresolved-evidence list into one persisted assembled view containing:

- review date and run state;
- enabled capability groups;
- group counts, confidence ranges, and exceptions;
- stable item IDs and allowed actions;
- current recommendations and approvals;
- prepared, executed, verified, and failed actions;
- active outcomes and decisions relevant to the review;
- open and recently completed Monday tasks;
- blocked and waiting workflows;
- confidence, provenance, freshness, rule version, and model version.

Minimum API behavior:

- start or refresh today's review;
- retrieve the full review or one capability group;
- record item and bulk decisions;
- prepare actions;
- execute only approved actions;
- retrieve verification and failures;
- resume an incomplete review.

## Learning Strategy

The system learns through explicit corrections rather than silent observation.

Each reviewed recommendation preserves:

- original classification and recommendation;
- final confirmed classification and disposition;
- action actually executed and observable outcome;
- supporting evidence and operational-object links;
- candidate preference or deterministic rule;
- capability scope and promotion status.

Reusable behavior progresses through `observed`, `proposed`, `confirmed`, `automatable`, and `retired`. A single accepted recommendation does not become a permanent rule, and a rule does not propagate across capabilities without explicit confirmation.

## Monday.com Expansion

After the Gmail action loop is usable, join Monday state into the same daily review:

- work Brian must do;
- decisions Brian must make;
- waiting-on-others items;
- blocked tasks and dependencies;
- overdue and upcoming commitments;
- recently completed tasks that satisfy Gmail workflow conditions;
- Gmail evidence that should create or update a Monday item.

Monday.com remains authoritative for native task execution fields; Admin OS owns the relationship between evidence, operational objects, workflows, and Monday items.

## Exit Criteria for the Current Increment

The daily action loop is ready for regular use when:

- one conversational request starts or resumes the current review;
- the initial Gmail groups refresh independently and idempotently;
- advisor/expert calls appear as one coherent group with visible exceptions;
- Brian can approve, correct, reject, or defer recommendations;
- draft, send, archive, Trash, label, and Monday actions are separately permissioned and verified;
- bulk review preserves item-level decisions;
- failures remain retry-safe and visible;
- review state survives conversation boundaries;
- corrections are retained as capability-scoped structured learning;
- Monday state can be added without creating a disconnected second review.
