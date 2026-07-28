# ADR-0007 — Deterministic Labeling and Grouped Review

**Status:** Accepted  
**Date:** 2026-07-28

## Context

Timmeny Admin OS now has working Gmail evidence intake, an explicit classification boundary and review queue, Monday duplicate checking, and an approval-gated verified Monday task write. The next operational objective is a daily morning mail review that can reduce repetitive inbox work without silently converting messages into tasks or autonomous actions.

Advisor and expert-call requests are a suitable first repeatable capability. They arrive from recurring networks and recognizable patterns, but the business decision to engage, decline, wait, or ask for more information still requires contextual judgment.

A single undifferentiated review queue would force Brian to inspect each thread separately and would not create a controlled path for learning reusable preferences. Conversely, allowing an AI model to label and execute actions without explicit rule provenance would make behavior opaque and unsafe.

## Decision

Timmeny Admin OS will separate deterministic intake classification from contextual recommendation.

1. Versioned deterministic rules may apply or preserve the Gmail label `Career - Advisor/Expert Calls` when a confirmed sender, domain, phrase pattern, or explicit manual label matches.
2. The label is an intake and grouping signal only. It does not imply acceptance, task creation, archive, deletion, or response.
3. Labeled threads are synchronized as evidence and presented as one grouped morning-review capability.
4. Each item exposes separate label-confidence and recommendation-confidence values with provenance and rule or model versions.
5. Items sharing the same proposed disposition and satisfying configured confidence and exception rules may be confirmed in bulk.
6. Bulk confirmation records an individual decision for every item and does not bypass Gmail or Monday write gates.
7. Brian's corrections are retained as structured feedback. Reusable rules are proposed explicitly and promoted only after confirmation.
8. External actions remain idempotent, verified, auditable, and bounded by specific execution policy.

## Alternatives Considered

### Use AI classification for all labeling

Rejected for the first capability. It would make a straightforward sender- and pattern-based intake decision less predictable and harder to audit.

### Require Brian to apply the Gmail label manually

Rejected as the target behavior because it preserves repetitive work and prevents the system from demonstrating controlled deterministic automation. Manual labeling remains valid evidence and a fallback.

### Treat every labeled thread as a task

Rejected because email is evidence, not automatically work. Many expert-call messages should be declined, archived, retained for awareness, or linked to an existing opportunity rather than turned into Monday tasks.

### Execute recommendations automatically above a confidence threshold

Rejected for the initial release. Confidence enables review compression and bulk confirmation, but does not by itself authorize consequential actions.

## Consequences and Tradeoffs

- The system gains a clear first capability for deterministic automation and grouped review.
- Label rules require configuration, versioning, provenance, and quality measurement.
- Two confidence values are required because classification certainty and action certainty are materially different.
- Bulk review reduces cognitive load while preserving item-level audit.
- The implementation must add explicit learning-event and preference-promotion concepts before autonomous execution can expand safely.
- Gmail writes must be enabled through a narrower policy than a single global write switch over time.

## Affected Documents

- `docs/00-README.md`
- `docs/70-Implementation-Strategy.md`
- `docs/75-First-Vertical-Slice.md`
- `docs/78-Advisor-Expert-Calls-Capability.md`
- `docs/90-Roadmap.md`
- `docs/adr/README.md`
- `operating/review/daily-review.md`
- `operating/learning/brian-preferences.md`

## Validation

Validate with real Gmail threads over repeated morning reviews:

- known advisor/expert-call messages are labeled;
- unrelated messages are not labeled;
- repeated runs are idempotent;
- grouped review shows correct counts and confidence ranges;
- uncertain exceptions remain outside bulk confirmation;
- Brian's corrections are retained and can produce explicit candidate rules;
- no reply, archive, deletion, or Monday task occurs without its required approval and verification.