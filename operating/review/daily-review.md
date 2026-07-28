# Daily Operations Review

## Purpose

The daily review converts current operational evidence into one ordered executive worklist. It is not an email summary. It answers: what requires Brian's attention, what should happen next, what has been approved, what was executed, and how each item connects to the broader operating model.

## Starting the Review

Brian initially starts or resumes the review by talking to the Timmeny Admin OS chat. A simple request such as "review my morning" or "start today's review" should invoke one persisted Admin OS daily-review run.

The conversation is the interface, not the source of review state. Decisions, corrections, prepared actions, execution results, and unresolved items must survive conversation boundaries.

Scheduled delivery may be added later, but it must use the same daily-review service, state, and approval policies.

## Evidence Refresh

Before producing an operational review:

1. Refresh Gmail for each enabled capability label.
2. Read `operating/README.md` and the relevant files under `operating/` from `main`.
3. Read Google Calendar when meetings, deadlines, or availability are relevant.
4. Refresh Monday.com when task state, duplicate detection, completion, or execution state is relevant.
5. Use user-provided screenshots as current evidence for systems without a live connection.
6. Do not infer current operational state from conversation memory.

If a required source cannot be read, identify the missing source and do not fabricate its current state.

## Initial Gmail Capability Groups

The first daily-review release contains three bounded groups:

1. `Career - Advisor/Expert Calls`;
2. `financial/taxes`;
3. an administrative-mail label whose exact Gmail label name must remain configurable until Brian confirms it.

Each group has independent deterministic rules, recommendations, allowed actions, confidence thresholds, completion conditions, and learning. Do not propagate rules from one group to another without explicit confirmation.

## Review Unit

The review contains one item per meaningful email thread or operational opportunity. Multiple messages in one thread remain one evidence unit. Multiple threads should be associated with the same operational object when they concern the same outcome, action, decision, dependency, or entity.

Distinct opportunities remain distinct review items even when presented inside one capability group.

Every item must propose exactly one disposition. Examples include:

- Do;
- Decide;
- Wait;
- Remind or defer;
- Delegate;
- Record only;
- Draft response;
- Send approved response;
- Record then archive;
- Archive;
- Move to Trash;
- Convert to or update an Admin OS object;
- Create or update a Monday item through the applicable gate.

## Capability Groups

The review may compress related items into a capability group when they share a stable classification and review workflow. Grouping must not erase item identity, provenance, confidence, exceptions, allowed actions, or execution state.

For `Career - Advisor/Expert Calls`:

- use the Gmail label as the intake and grouping signal;
- retain each distinct advisory opportunity as a separate item;
- display group counts and proposed disposition counts;
- show average and minimum confidence;
- separate label confidence from recommendation confidence;
- expose low-confidence and policy exceptions individually;
- allow bulk confirmation only for items with the same proposed disposition and no exception;
- record one final decision and action trail per item even when confirmed in bulk.

The label means that a message belongs in this review group. It does not mean Brian should accept the opportunity, send a reply, archive the thread, or create a task.

## Ordering

Present one ordered review in this sequence:

1. Brian acts now.
2. Brian decides.
3. Brian is blocked or waiting on others.
4. Awareness or monitor.
5. Record then archive.
6. Reading material.
7. Mass mailings and promotional material.
8. Archive or move to Trash.

Capability groups appear at the highest position required by any unresolved item inside the group. Within a group, order by urgency, impact, dependency unblocking, confidence exception, and then recency.

## Review Item Fields

During the prototype phase, include enough metadata for Brian to correct the operating model:

| Field | Meaning |
|---|---|
| Priority | Ordered position in the review |
| Review Item ID | Stable identity used in conversation and execution |
| Capability Group | Shared bounded workflow, when applicable |
| Thread | Current evidence source |
| Life Area / Program | Stable responsibility or grouped initiative |
| Outcome | Desired result |
| Action or Decision | Operational object affected |
| Status | Current review and workflow state |
| Next Owner | Brian, external party, shared, or system |
| Recommended Next Action | Single proposed next move |
| Proposed External Actions | Label, draft, send, archive, Trash, Monday, or none |
| Reason | Why the recommendation follows from the evidence |
| Entities | People, organizations, accounts, properties, or other durable subjects |
| Relationships | Grouping and contextual connections |
| Dependencies | Preconditions, blockers, or downstream triggers |
| Disposition | Final intended treatment |
| Label Confidence | Confidence that the item belongs in the capability group |
| Recommendation Confidence | Confidence in the proposed next action |
| Approval Requirement | None, item confirmation, bulk confirmation, or explicit content approval |
| Execution State | Recommended, approved, prepared, executed, verified, complete, or failed |
| Rule / Model Version | Deterministic rule and reasoning version used |
| Grounded By | Gmail, Git, Calendar, Monday, screenshot, Brian, or explicit inference |
| Brian Comments | Corrections and additional context |

## Conversation and Approval

Brian may approve, reject, correct, or defer items through natural language, including bulk instructions. The system must resolve every instruction to stable item IDs and explicit action parameters before performing any write.

Ambiguous references require clarification. A phrase such as "do those" must not execute if the intended item set is not unambiguous.

Recommendation approval is distinct from execution:

```text
recommended
  -> approved or corrected
  -> prepared
  -> executed
  -> verified
  -> complete or failed
```

A reply workflow normally requires draft, content approval, send, send verification, and then any archive action. These steps must remain visible and independently retryable.

## Confidence and Bulk Review

Confidence values are review metadata and do not independently authorize execution.

For bulk review:

1. include only items sharing the same proposed disposition;
2. show item count, confidence range, and applicable rule or model version;
3. exclude low-confidence or policy-exception items;
4. preserve item-level approval, correction, action, and audit records;
5. do not allow a bulk decision to bypass Gmail, Monday, or communication approval gates.

## Initial External-Action Policy

During the first daily-use phase:

- deterministic label writes may occur only under label-specific policy;
- archive and move-to-Trash require explicit approval;
- permanent deletion is not supported;
- replies are drafted before sending;
- sending requires explicit approval of the content;
- Monday writes continue through duplicate detection, stable identity, approval, and verification;
- every external write must record requested, attempted, verified, and failed states;
- unsuccessful writes remain visible and retry-safe.

## Monday.com Integration

Monday.com should enter the daily review as execution context, not as a separate summary. The review should surface:

- actions Brian must do;
- decisions Brian must make;
- work waiting on others;
- blocked tasks and dependencies;
- overdue and upcoming commitments;
- recently completed tasks that may complete Gmail workflows;
- Gmail evidence that should create or update a Monday item.

Admin OS should join Gmail and Monday through operational-object and mapping identity rather than matching titles alone.

## Relationships and Dependencies

Always surface proposed groupings, dependencies, and related objects for feedback. Do not silently create a relationship when confidence is not high.

A dependency may change the recommendation. An action can remain blocked until all prerequisites are complete, after which the next action should change automatically.

## Learning

Brian's corrections should be retained as structured learning events, including the original recommendation, final decision, action actually executed, observable result, relevant sender or topic, capability scope, and candidate reusable preference.

Do not silently convert a single accepted recommendation into a permanent rule. Candidate behavior progresses through `observed`, `proposed`, `confirmed`, `automatable`, and `retired` states.

## Review Completion

At the end of the review, summarize:

- completed and failed actions;
- unresolved and deferred items;
- new outcomes;
- new actions;
- new decisions;
- new entities;
- new relationships;
- new dependencies;
- learned preferences or classification rules;
- deterministic-rule false positives or false negatives;
- items that will return in the next review.

After Brian confirms the interpretation, update the relevant operating files and perform only the explicitly approved external actions.
