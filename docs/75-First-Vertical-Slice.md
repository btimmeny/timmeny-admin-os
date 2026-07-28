# 75 — First Vertical Slice

**Status:** Foundation implemented; completion and Gmail-disposition loop remain  
**Version:** 0.2  
**Purpose:** Records the bounded implementation contract, achieved increments, and remaining work for the first Timmeny Admin OS end-to-end workflow.  
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

The implementation extends the existing repository and preserves working Railway and Monday capabilities.

## Current Repository State

The following increments are implemented on `main`:

1. PostgreSQL baseline and migrations for durable coordination state.
2. Gmail OAuth status and intake-label resolution.
3. Gmail inbox-plus-label synchronization into thread-level evidence.
4. Idempotent evidence updates and safe pruning behavior.
5. Explicit classifier boundary using `v1-review-all`, which assigns no inferred meaning and routes every item to review.
6. Human review queue.
7. Monday To Do List board reads.
8. Duplicate scoring against existing Monday items.
9. Approval-gated Monday task creation from evidence.
10. Reserved Admin OS identity, retry adoption, write verification, and workflow audit.

This means the repository can ingest real Gmail evidence, expose it for review, inspect Monday for duplicates, and create a verified Monday task after explicit confirmation. It does not yet complete the full loop through Monday completion and Gmail disposition.

## Implemented Minimum Domain Records

The runtime now uses the minimum records necessary to preserve identity, state, provenance, and audit, including:

- operational objects;
- evidence;
- classifications;
- external mappings;
- workflow runs;
- workflow steps or equivalent audit events.

Physical schema remains an implementation concern, but stable IDs, timestamps, source identifiers, confidence, lifecycle state, and idempotency are required invariants.

## Implemented Interfaces

### Gmail adapter

Implemented:

- authenticate using OAuth refresh credentials;
- resolve the configured intake label;
- synchronize inbox threads carrying that label;
- preserve Gmail thread identity;
- retry without duplicate evidence;
- prune only when the scan is complete enough to do so safely.

Not yet complete:

- generalized deterministic label application;
- completion-conditioned archive or other disposition;
- granular Gmail write policies and read-back verification for each write type.

### Monday adapter

Implemented:

- read board items;
- inspect completion-related state;
- score possible duplicate tasks;
- create a task from evidence after the approval gate;
- preserve board, item, and Admin OS IDs;
- adopt an existing write after a retry;
- verify resulting state after creation.

Not yet complete:

- update a mapped task through the coordination workflow;
- synchronize task completion into workflow state;
- drive completion-conditioned Gmail disposition.

### Classification service

Implemented:

- one classification per evidence item and classifier version;
- explicit `needs_review` disposition;
- zero confidence and undetermined relationship for classifier v1;
- no hidden inference and no automatic task creation.

Next:

- add the narrow deterministic and grouped-review capability defined in [78 — Advisor and Expert Calls Capability](./78-Advisor-Expert-Calls-Capability.md);
- preserve separate label and recommendation confidence;
- retain Brian's corrections as structured learning.

### Workflow service

Implemented:

- approval gate for Monday task creation;
- duplicate review before creation;
- stable identity reservation;
- idempotent retry adoption;
- verified write and audit.

Remaining:

- mapped task update;
- completion synchronization;
- safe Gmail disposition;
- grouped review and bulk-decision workflow.

### Executive Review API

Current:

- unresolved evidence is exposed through the review queue;
- Monday board and duplicate information can be retrieved separately.

Next:

Return one assembled view containing:

- capability groups;
- active relevant operational objects;
- open and recently completed mapped Monday tasks;
- unresolved Gmail evidence;
- waiting, blocked, or failed workflows;
- recommended next actions;
- confidence, source freshness, provenance, and rule/model versions.

## Safety and Invariants

- An email is evidence, not automatically a task.
- No Monday task is created until classification selects that disposition and the approval gate allows it.
- No Gmail thread is archived before the workflow completion condition is verified.
- Every external write is idempotent.
- Every external record has a stable mapping to the Admin OS object or workflow.
- Ambiguous classification requires review.
- Confidence supports review but does not independently authorize execution.
- Every workflow can explain what it read, inferred, changed, and verified.
- Logs and audit data must not expose secrets or unnecessary full sensitive message content.

## Current Test Scenarios

The implemented foundation should continue to test:

1. Gmail status and label resolution.
2. Repeated intake creates no duplicate evidence.
3. Archived or out-of-scope threads are not treated as active intake.
4. Truncated scans cannot prune unseen evidence.
5. Classifier reruns are idempotent by evidence and version.
6. Uncertain evidence remains in review.
7. Duplicate Monday candidates are surfaced before creation.
8. Unconfirmed uncertain creation is refused.
9. Confirmed creation writes one task and verifies it.
10. A retry adopts the existing Monday item rather than duplicating it.

Remaining end-to-end tests:

11. Monday completion is synchronized before Gmail archive.
12. A failed Monday update leaves Gmail unchanged.
13. A failed Gmail disposition can be retried without duplicating prior work.
14. Executive Review reports grouped, stale, blocked, and unresolved state accurately.

## Completion Criteria

The original vertical slice is complete when it runs repeatedly on real Gmail and Monday records, preserves provenance, avoids duplicate tasks, synchronizes mapped task completion, prevents premature Gmail disposition, exposes verified workflow state, and produces a useful Executive Review.

The advisor/expert-call capability may be developed on top of the implemented foundation before the final completion loop, provided it does not bypass these invariants or broaden autonomous execution.