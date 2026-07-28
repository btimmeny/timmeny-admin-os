# 90 — Roadmap

**Status:** Active  
**Version:** 0.4  
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

## Now — Daily Assistant Review Foundation

Build [79 — Daily Assistant Review](./79-Daily-Assistant-Review.md):

1. Add persisted daily-review runs that can be started or resumed from the Admin OS chat.
2. Add stable review-item and review-action identities.
3. Add enabled capability-group configuration and ordered refresh.
4. Return one assembled review contract with group summaries, item details, confidence, provenance, allowed actions, and execution state.
5. Record approve, reject, correct, and defer decisions.
6. Separate recommendation, preparation, execution, verification, completion, and failure states.
7. Support resumable partial reviews without losing prior decisions.

## Now — Advisor and Expert Calls

Build [78 — Advisor and Expert Calls Capability](./78-Advisor-Expert-Calls-Capability.md) as the first active group:

1. Resolve or create the Gmail label `Career - Advisor/Expert Calls`.
2. Define versioned deterministic sender, domain, and phrase rules.
3. Detect matching inbox threads and record rule provenance.
4. Apply or preserve the label idempotently behind a dedicated Gmail-write policy.
5. Synchronize labeled threads as evidence.
6. Present them as one morning-review group.
7. Expose separate label-confidence and recommendation-confidence values.
8. Score capability fit, strategic fit, urgency, effort, compensation, and relationship value when evidence exists.
9. Produce exactly one proposed disposition per opportunity.
10. Support auditable bulk approval for homogeneous high-confidence recommendations.
11. Retain Brian's corrections as structured learning events and candidate preferences.
12. Support approved archive, Trash, wait, draft-response, send-response, and Monday-task workflows through granular gates.

## Parallel Bounded Groups

Reuse the daily-review framework for two additional groups without generalizing their business rules:

### Taxes

- Continue using `financial/taxes` as the configured intake label.
- Define tax-specific recommendations and allowed actions.
- Preserve existing financial-mail obligations and dependencies.
- Require explicit approval for archive, Trash, response, or Monday creation.

### Administrative Mail

- Confirm the exact Gmail label name before activation.
- Define deterministic intake and admin-specific dispositions.
- Begin with review and explicitly approved actions only.
- Do not inherit expert-call or tax rules.

## Gmail Action Execution

Implement granular, verified actions in this order:

1. label apply/remove;
2. archive;
3. move to Trash;
4. defer/remind and waiting state;
5. draft reply;
6. send explicitly approved reply;
7. create or update Monday work through existing gates.

Permanent deletion remains out of scope. Draft and send must remain separate operations. Every action must be idempotent, auditable, and read-back verified where supported.

## Daily Gmail Exit Gate

Do not broaden to general inbox automation until:

- one conversational request starts or resumes the current review;
- all three enabled groups refresh independently without duplicate evidence or repeated writes;
- expert calls appear as one coherent group with confidence and exceptions;
- Brian can approve, correct, reject, and defer recommendations;
- approved Gmail actions execute and verify safely;
- bulk decisions retain item-level audit;
- failed actions remain visible and retry-safe;
- review and learning state survive conversation boundaries;
- no rule silently propagates across capabilities.

## Next — Monday.com as Daily Execution Context

After the Gmail action loop is usable, add Monday.com to the same review:

1. Read open, upcoming, overdue, recently completed, and waiting items.
2. Classify Brian actions, decisions, blockers, dependencies, and waiting-on-others state.
3. Join Gmail evidence and Monday items through Admin OS operational-object identity.
4. Detect when Monday completion satisfies a Gmail workflow condition.
5. Support mapped task updates and completion synchronization.
6. Trigger completion-conditioned Gmail disposition only after verification.
7. Present email and task state as one ordered administrative review rather than separate summaries.

## Later

- schedule or proactively deliver the same persisted morning review;
- add Calendar context for meetings, deadlines, preparation, and availability;
- add recurring obligations and lifecycle workflows;
- improve entity resolution and duplicate cleanup;
- evaluate broader Monday board redesign based on demonstrated requirements;
- expand into finance, travel, household, family, and relationship administration;
- add proactive safe automation governed by explicit, versioned policy.

The roadmap remains intentionally narrow. Working, reviewable, action-capable operating loops take precedence over architectural breadth.
