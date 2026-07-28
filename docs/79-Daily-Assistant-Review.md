# 79 — Daily Assistant Review

**Status:** Approved for implementation  
**Version:** 0.2  
**Stability:** Low  
**Purpose:** Defines how Brian begins each day with Timmeny Admin OS, reviews bounded Gmail capability groups, approves actions, incorporates Monday.com execution state, and connects daily work to objectives and progress over time.  
**Depends on:** [70 — Implementation Strategy](./70-Implementation-Strategy.md), [78 — Advisor and Expert Calls Capability](./78-Advisor-Expert-Calls-Capability.md), [ADR-0007](./adr/ADR-0007-deterministic-labeling-and-grouped-review.md), [ADR-0008](./adr/ADR-0008-label-scoped-daily-action-loop.md)

## User Experience

Brian begins the day in the Timmeny Admin OS chat and requests the daily review. The chat is the conversational interface; Timmeny Admin OS assembles current state, recommendations, approvals, verified execution, and objective-progress context.

```text
Brian opens the Admin OS chat
  -> requests the morning review
  -> Admin OS refreshes configured Gmail capability labels
  -> review presents one capability group at a time
  -> Brian accepts, rejects, edits, or defers proposed actions
  -> approved actions execute and are verified
  -> unresolved items remain visible
  -> Monday.com work, waiting state, and dependencies are reconciled
  -> objective alignment, progress, drift, and risks are summarized
```

The daily review is pull-based initially: Brian starts it by talking to the GPT. Scheduling or proactive delivery may be added later, but must call the same review service and produce the same persisted review state.

## Initial Capability Groups

The first daily-review release supports three bounded Gmail groups:

1. `Career - Advisory/Expert Calls`
2. `Financial/Taxes`
3. `Admin`

Each group is an independent capability with its own:

- intake label;
- deterministic rules;
- reasoning policy;
- proposed dispositions;
- execution permissions;
- confidence thresholds;
- learning history;
- completion conditions.

A shared framework may implement these capabilities, but no group inherits another group's action rules implicitly.

## Review Sequence

The service should create one persisted daily-review run and process groups in configured order. Initial order:

1. items requiring immediate action across all enabled groups;
2. advisor and expert calls;
3. taxes and financial administration;
4. `Admin` mail;
5. unresolved exceptions and waiting items;
6. Monday.com actions, decisions, blockers, and waiting state;
7. objective progress, drift, risks, and recommended focus.

Brian may ask to review only one group. Partial review must update the same daily-review run rather than create conflicting review state.

## Review Item Contract

Each item must expose:

- stable review-item ID;
- Gmail thread and evidence identity;
- capability group and source label;
- concise summary;
- affected operational object, if known;
- linked objective, outcome, obligation, or responsibility, when known;
- proposed disposition;
- proposed external actions;
- classification confidence;
- recommendation confidence;
- objective-link confidence;
- rationale;
- urgency and relevant dates;
- entities, relationships, and dependencies;
- approval requirement;
- current execution state;
- rule, prompt, model, and workflow versions;
- provenance and freshness.

The review should compress repeated items into groups, but every action remains item-addressable and auditable.

## Objective Alignment

Email and Monday.com are evidence and execution systems, not the organizing model. Each meaningful review item should be linked, where possible, to one of:

- an objective or outcome;
- an obligation or enduring responsibility;
- a decision;
- a risk or dependency;
- administrative overhead.

Unknown alignment must remain explicit. The system must not fabricate objective links merely to improve coverage.

Each capability returns standardized progress signals:

```text
evidence
operational-object effect
objective or responsibility links
proposed actions or decisions
waiting state and dependencies
progress signals
risk signals
confidence and provenance
```

The daily review should identify:

- objectives progressing;
- objectives drifting or deteriorating;
- important objectives with no supporting activity;
- operational overhead consuming attention;
- decisions, dependencies, or waiting states blocking progress;
- the smallest recommended intervention.

## Interaction Commands

The conversational interface should support clear operations such as:

- show today's review;
- show the advisor-call group;
- show the Admin group;
- approve items 1, 3, and 5;
- approve all high-confidence declines;
- change item 4 to ask for more information;
- draft the response but do not send it;
- archive after the response is sent;
- move these confirmed low-value solicitations to Trash;
- create a Monday task for this item;
- defer this until Friday;
- mark this as waiting on the sender;
- link this item to objective X;
- mark this as administrative overhead;
- explain why this recommendation was made;
- explain why an objective score or trajectory changed.

Natural language is converted into explicit item IDs, dispositions, objective links, and action requests before execution. Ambiguous references require clarification and must not trigger writes.

## Action Model

Approval of a recommendation and execution of external effects are separate states.

```text
recommended
  -> approved or corrected
  -> prepared
  -> executed
  -> verified
  -> complete or failed
```

An item may require several ordered actions, for example:

```text
draft response
  -> Brian approves text
  -> send response
  -> verify sent message
  -> archive thread
  -> verify thread left inbox
```

The system must not collapse this into one opaque operation.

## Initially Supported Actions

The action framework should support, subject to capability policy:

- review;
- record only;
- keep in inbox;
- wait;
- remind or defer;
- apply or remove a Gmail label;
- archive;
- move to Trash;
- draft a reply;
- send an approved reply;
- create or update an operational object;
- link to an objective, outcome, obligation, or responsibility;
- create a Monday task through the existing duplicate and approval gates;
- update a mapped Monday task;
- mark an item waiting on another person;
- close a review item after verified completion.

`Review` is a first-class action when the required next step is to examine, interpret, or decide what newly supplied information means before any external execution is justified.

Permanent deletion is not an initial action. Gmail Trash is reversible and must remain distinct from archive.

## Approval Policy

For the initial daily-use phase:

- classification and recommendations may be generated automatically;
- deterministic label application may be enabled by label-specific policy;
- bulk approval may be used for homogeneous recommendations meeting the configured threshold;
- replies are drafted first and sent only after explicit approval;
- archive and Trash require explicit approval unless Brian later promotes a narrow rule to automatable status;
- Monday writes continue through duplicate detection, confirmation, stable identity, and verification;
- every external write records requested, attempted, verified, and failure states.

## Progress and Scoring

The first implementation must preserve the data required for scoring, but should not invent a sophisticated scoring formula before enough real operating evidence exists.

Persist dated, explainable assessment snapshots for objectives and administrative health. Candidate measures include:

- progress;
- trajectory;
- execution;
- attention or alignment;
- risk;
- assessment confidence;
- inbox control;
- execution integrity;
- evidence freshness.

Every score or trajectory change must retain its drivers, source evidence, assessment version, and confidence. Scores summarize evidence; they do not replace it.

## Learning

Each correction creates a structured learning event containing:

- original recommendation;
- Brian's final decision;
- action actually executed;
- outcome and later response, when observable;
- sender, domain, topic, capability, and entities;
- objective or responsibility link and any correction;
- candidate reusable rule;
- rule status and provenance.

Learning is capability-scoped by default. A rule confirmed for expert calls does not automatically apply to taxes or administrative mail. Objective-linking patterns may be promoted only after explicit confirmation or repeated validated use.

## Monday.com Expansion

The second stage of the daily review adds Monday.com as execution context rather than as a separate review:

- actions Brian must perform;
- decisions Brian must make;
- work waiting on others;
- blocked tasks and unmet dependencies;
- overdue and upcoming commitments;
- recently completed work that may complete an email workflow;
- email evidence that should create or update a Monday item;
- Monday actions with no objective, obligation, or responsibility context;
- objectives with insufficient supporting execution.

The review should join Gmail evidence and Monday execution through Admin OS operational-object identity. It must not present them as two disconnected lists.

## API Direction

The implementation should add an assembled daily-review contract rather than require the GPT to orchestrate many low-level calls itself.

Minimum operations:

- start or refresh today's review;
- list enabled capability groups;
- retrieve one group or the whole review;
- record corrections, objective links, and approvals;
- prepare an action batch;
- execute an approved action batch;
- retrieve verification and failures;
- retrieve objective-progress and administrative-health assessments;
- resume an incomplete review.

The API returns stable IDs and explicit allowed actions so the GPT can converse naturally without inventing execution semantics.

## Acceptance Criteria

The daily assistant review is ready for regular use when:

- Brian can start the review from the Admin OS chat with one request;
- the three configured Gmail groups refresh independently and idempotently;
- expert calls are reviewed as one coherent group with confidence scores;
- each reviewed item has exactly one recommended disposition;
- `review` can be selected as the explicit next action when interpretation is required before execution;
- Brian can approve, correct, or defer individual and eligible bulk recommendations;
- approved archive, Trash, label, draft, send, and Monday actions are permission-gated and verified;
- failures remain visible and retry-safe;
- the review can be resumed without losing prior decisions;
- corrections are retained as structured learning;
- objective or responsibility alignment is explicit, including unknown and administrative-overhead states;
- Monday.com state can be added without creating a second, disconnected operating model;
- progress and administrative-health snapshots can be stored and explained over time.