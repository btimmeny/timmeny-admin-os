# 75 — First Vertical Slice

**Status:** Ready for implementation discovery  
**Version:** 0.1  
**Purpose:** Provides the bounded implementation contract for the first Timmeny Admin OS end-to-end workflow.  
**Depends on:** [50 — Architecture](./50-Architecture.md), [60 — Domain Model](./60-Domain-Model.md), [70 — Implementation Strategy](./70-Implementation-Strategy.md), [ADR-0001](./adr/ADR-0001-admin-os-coordination-layer.md)

## Objective

Implement one reliable operational loop:

```text
Gmail evidence
  -> operational-object classification
  -> Monday task creation or update
  -> task completion synchronization
  -> Gmail disposition
  -> verification and audit
```

The implementation must extend the existing repository and preserve working Railway and Monday capabilities.

## First Codex Task: Repository Assessment

Before changing code, inspect and document:

- current application entry points and module structure
- current FastAPI routes and authentication
- current Monday connector and supported operations
- current Railway deployment and environment configuration
- existing persistence, if any
- tests and local development workflow
- existing ChatGPT Action or API contracts
- gaps against this vertical slice

Produce a concise implementation plan grounded in the repository. Do not redesign the entire system.

## Minimum Domain Records

The first slice requires only the records necessary to preserve identity, state, provenance, and audit:

- `operational_objects`
- `evidence`
- `external_mappings`
- `workflow_runs`
- `workflow_steps` or equivalent audit events
- `decisions` only if required to represent ambiguous classification

The exact physical schema is an implementation decision, but it must support stable IDs, timestamps, source-system identifiers, lifecycle state, confidence, and idempotency.

## Required Interfaces

### Gmail adapter

- synchronize a thread and messages
- preserve Gmail thread and message IDs
- read and apply labels
- archive a thread only after an approved completion condition
- support retry without duplicate state

### Monday adapter

- create a task
- update a mapped task
- read task state and completion
- preserve Monday board and item IDs
- verify resulting state after a write
- support retry without duplicate tasks

### Classification service

Given evidence and current operational context, return:

- affected or proposed operational object
- relationship: creates, updates, completes, blocks, contradicts, or supports
- confidence
- recommended disposition
- whether human review is required

The first implementation may use deterministic rules and an explicit AI classification boundary. It must not hide uncertain classification as fact.

### Workflow service

- execute one approved Gmail-to-Monday workflow
- maintain explicit workflow state
- prevent duplicate execution
- stop safely on ambiguous or failed steps
- verify each external write
- preserve an audit trail

### Executive Review API

Return one assembled view containing:

- active relevant operational objects
- open and recently completed mapped Monday tasks
- unresolved Gmail evidence
- waiting, blocked, or failed workflows
- recommended next actions
- source freshness and confidence

## Safety and Invariants

- An email is evidence, not automatically a task.
- No Monday task is created until classification selects that disposition.
- No Gmail thread is archived before the workflow completion condition is verified.
- Every external write is idempotent.
- Every external record has a stable mapping to the Admin OS object or workflow.
- Ambiguous classification requires review.
- Every workflow can explain what it read, inferred, changed, and verified.
- Logs and audit data must not expose secrets or full sensitive message content unnecessarily.

## Out of Scope

- broad Monday workspace redesign
- Calendar synchronization unless already available and trivial to reuse
- recurring obligations
- financial integrations
- life-health scoring
- broad autonomous execution
- multiple new workflow types
- sophisticated UI

## Test Scenarios

At minimum, test:

1. New actionable email creates one Monday task.
2. Retrying the same input does not create a duplicate task.
3. Non-actionable evidence is recorded without task creation.
4. Ambiguous evidence pauses for review.
5. Monday completion is synchronized before Gmail archive.
6. A failed Monday write leaves the Gmail thread unarchived.
7. A failed archive can be retried without duplicating prior work.
8. Executive Review reports stale, blocked, and unresolved state accurately.

## Completion Criteria

The slice is complete when it runs repeatedly on real Gmail and Monday records, preserves provenance, avoids duplicate tasks, prevents premature archive, exposes verified workflow state, and produces a useful Executive Review.