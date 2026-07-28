# 78 — Advisor and Expert Calls Capability

**Status:** Approved for implementation  
**Version:** 0.1  
**Stability:** Low  
**Purpose:** Defines the first repeatable morning-review capability: deterministic Gmail labeling and grouped review of advisor and expert-call opportunities with confidence scores.  
**Depends on:** [70 — Implementation Strategy](./70-Implementation-Strategy.md), [75 — First Vertical Slice](./75-First-Vertical-Slice.md), [ADR-0004](./adr/ADR-0004-classification-boundary-and-review.md), [ADR-0007](./adr/ADR-0007-deterministic-labeling-and-grouped-review.md)

## Objective

Each morning, identify advisor and expert-call messages, apply or preserve the Gmail label `Career - Advisor/Expert Calls`, and present the resulting threads as one review group rather than as unrelated inbox items.

The first release is intentionally review-oriented. It may label messages deterministically, but it must not send replies, accept engagements, decline opportunities, archive threads, or create Monday tasks without an approved workflow action.

## Capability Flow

```text
Gmail inbox refresh
  -> deterministic candidate detection
  -> apply or preserve Career - Advisor/Expert Calls label
  -> synchronize labeled threads as evidence
  -> group related threads for morning review
  -> score classification and recommendation confidence
  -> Brian bulk-confirms or corrects proposed dispositions
  -> approved actions execute through existing safety gates
  -> feedback is retained as operating-model learning
```

## Deterministic Labeling

The Gmail label is an intake and grouping signal, not a final business classification.

A thread may be labeled deterministically when one or more configured rules match, including:

- a confirmed expert-network sender or sender domain;
- a confirmed sender pattern associated with advisory consultations;
- a confirmed subject or body phrase that unambiguously indicates an expert call, consultation request, advisory project, or paid expert interview;
- an explicit manual label already applied by Brian.

Initial sender and phrase rules must be stored as versioned configuration with provenance. Rules may be expanded only after review of false positives and false negatives.

A deterministic label match records:

- rule identifier and version;
- matched sender, domain, or phrase category;
- match result;
- labeling action taken or no-op result;
- timestamp and Gmail thread identifier.

## Grouped Morning Review

The Executive Review should expose one group named `Career - Advisor/Expert Calls` with:

- total new and unresolved threads;
- proposed bulk disposition counts;
- average and minimum confidence;
- exceptions requiring individual review;
- each opportunity's network, client or topic when available;
- capability-fit score;
- strategic-fit score;
- urgency or response deadline;
- estimated effort and compensation when stated;
- one recommended next action;
- confidence for both classification and recommendation;
- provenance and rule version.

The default review unit is one opportunity or meaningful thread. Multiple messages in the same engagement should remain one review item. Distinct opportunities from the same expert network remain distinct items inside the group.

## Confidence

Confidence values use the range `0.0` to `1.0` and must distinguish:

1. **Label confidence** — confidence that the thread belongs in the advisor/expert-call group.
2. **Recommendation confidence** — confidence in the proposed action such as engage, decline, wait, ask for details, or archive.

Deterministic exact-rule matches may produce high label confidence, but they do not automatically justify high recommendation confidence.

Recommended review thresholds for the first release:

- `>= 0.95`: eligible for bulk confirmation when no exception rule applies;
- `0.70–0.94`: show in the group but require explicit review;
- `< 0.70`: treat as an exception and do not include in automatic bulk action.

These are implementation defaults, not permanent preferences. Actual thresholds should be configurable and revised using observed outcomes.

## Initial Recommendation Dimensions

The reasoning layer should evaluate:

- capability fit;
- strategic fit with Brian's current positioning;
- compensation or commercial value when known;
- time and preparation effort;
- relationship value;
- urgency;
- conflicts, compliance constraints, or missing information.

The first release may leave any dimension unknown. Unknown values must remain explicit rather than being converted to neutral scores.

## Allowed Initial Dispositions

- engage;
- decline;
- ask for more information;
- wait;
- remind;
- record only;
- archive after confirmed action;
- archive now;
- create or update an operational object;
- create a Monday task through the existing approval gate.

Every item receives exactly one proposed disposition.

## Bulk Review Rules

Bulk review is a confirmation mechanism, not unrestricted automation.

- Only items with the same proposed disposition may be confirmed together.
- Every bulk set must show its item count, confidence range, and rule or model version.
- Exceptions remain outside the bulk set.
- A bulk confirmation creates one decision event per item plus a batch reference.
- Bulk confirmation does not authorize email sending unless a separate approved send workflow exists.
- Gmail archive or label changes must be individually auditable and retry-safe.

## Learning

Brian's corrections are stored as structured feedback:

- original classification and recommendation;
- corrected classification or disposition;
- reason when supplied;
- relevant sender, topic, network, entity, and relationship;
- candidate reusable preference;
- confirmation status.

One accepted recommendation does not create a permanent rule. Reusable behavior progresses through `observed`, `proposed`, `confirmed`, `automatable`, and `retired` states.

## Safety and Invariants

- Email remains evidence, not automatically a task.
- The Gmail label does not imply that Brian should accept the opportunity.
- No reply, acceptance, decline, archive, Trash action, or Monday write occurs solely because the label matched.
- Deterministic rules must be versioned and explainable.
- Every external write must be verified and idempotent.
- False positives and false negatives must be measurable.
- Secrets and unnecessary full message content must not appear in logs.

## Implementation Increments

1. Add label configuration and read-only candidate detection.
2. Add safe label creation/resolution and deterministic label application behind a dedicated Gmail-write policy.
3. Synchronize labeled inbox threads as evidence idempotently.
4. Add grouped review response and confidence fields.
5. Add bulk confirmation records without external actions.
6. Connect confirmed dispositions to existing Monday and Gmail safety gates.
7. Promote repeatedly confirmed preferences into versioned deterministic rules.

## Acceptance Criteria

The capability is ready for daily use when:

- the label resolves and is applied consistently to known advisor/expert-call threads;
- repeated syncs do not duplicate evidence or labels;
- the morning review presents one coherent group with confidence scores;
- exceptions remain visible for individual review;
- bulk confirmation is auditable and cannot trigger unauthorized actions;
- Brian's corrections are retained as structured learning;
- false-positive and false-negative rates can be inspected.