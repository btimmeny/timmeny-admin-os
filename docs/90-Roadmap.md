# 90 — Roadmap

**Status:** Active  
**Version:** 0.3  
**Stability:** Low  
**Purpose:** Defines the current implementation sequence for Timmeny Admin OS.  
**Depends on:** [70 — Implementation Strategy](./70-Implementation-Strategy.md), [75 — First Vertical Slice](./75-First-Vertical-Slice.md), [ADR-0001](./adr/ADR-0001-admin-os-coordination-layer.md)

## Completed Foundation

The repository now has:

- PostgreSQL persistence and migrations;
- Gmail OAuth configuration and status checks;
- Gmail inbox-plus-label evidence synchronization;
- thread-level idempotency and safe evidence pruning;
- explicit classification and human-review boundaries;
- Monday board reads;
- duplicate scoring;
- approval-gated, idempotent, verified Monday task creation;
- stable Admin OS identity and workflow audit.

The remaining portions of the original vertical slice are mapped task updates, Monday completion synchronization, completion-conditioned Gmail disposition, and one assembled Executive Review.

## Now — Advisor and Expert Calls Morning Review

Build [78 — Advisor and Expert Calls Capability](./78-Advisor-Expert-Calls-Capability.md):

1. Resolve or create the Gmail label `Career - Advisor/Expert Calls`.
2. Define versioned deterministic sender, domain, and phrase rules.
3. Detect matching inbox threads and record rule provenance.
4. Apply or preserve the label idempotently behind a dedicated Gmail-write control.
5. Synchronize labeled threads as evidence.
6. Present them as one morning-review group.
7. Expose separate label-confidence and recommendation-confidence values.
8. Score capability fit, strategic fit, urgency, effort, compensation, and relationship value when evidence exists.
9. Produce exactly one proposed disposition per opportunity.
10. Support auditable bulk confirmation for homogeneous high-confidence recommendations.
11. Retain Brian's corrections as structured learning events and candidate preferences.
12. Route any approved Monday creation through the existing duplicate and approval gates.

## Current Capability Exit Gate

Do not broaden deterministic Gmail labeling or bulk action to other categories until this capability:

- runs repeatedly on real records;
- avoids duplicate evidence and repeated label writes;
- demonstrates acceptable false-positive and false-negative rates;
- separates classification confidence from recommendation confidence;
- keeps low-confidence exceptions visible;
- records item-level decisions for every bulk confirmation;
- performs no unauthorized reply, archive, deletion, or Monday write;
- retains explainable feedback and rule provenance.

## Complete the Original Vertical Slice

After or alongside the current capability, complete:

```text
Gmail evidence
  -> approved Monday task
  -> mapped task update and completion synchronization
  -> completion-conditioned Gmail disposition
  -> verification and audit
```

Required work:

- mapped Monday task update;
- completion-state synchronization;
- granular Gmail write policies;
- verified archive or other approved disposition;
- retry-safe completion workflow;
- assembled Executive Review API.

## Next

After both exit gates:

- schedule the morning Executive Review;
- add Calendar context for meetings, deadlines, preparation, and availability;
- expand classification dispositions beyond task creation;
- add recurring obligations and lifecycle workflows;
- add decision and waiting-state workflows;
- improve entity resolution and duplicate cleanup;
- add additional bounded mail capabilities based on observed review volume;
- evaluate broader Monday board redesign based on demonstrated requirements.

## Later

- goal and life-health intelligence;
- broader finance, travel, household, family, and relationship administration;
- additional communication channels and documents;
- proactive safe automation governed by explicit, versioned policy.

The roadmap remains intentionally narrow. Working, reviewable operational capabilities take precedence over architectural breadth.