# Daily Operations Review

## Purpose

The daily review converts current operational evidence into one ordered executive worklist. It is not an email summary. It answers: what requires Brian's attention, what should happen next, and how each item connects to the broader operating model.

## Evidence Refresh

Before producing an operational review:

1. Refresh Gmail when email state is relevant.
2. Read `operating/README.md` and the relevant files under `operating/` from `main`.
3. Read Google Calendar when meetings, deadlines, or availability are relevant.
4. Refresh Monday.com when task state, duplicate detection, or execution state is relevant.
5. Use user-provided screenshots as current evidence for systems without a live connection.
6. Do not infer current operational state from conversation memory.

If a required source cannot be read, identify the missing source and do not fabricate its current state.

## Review Unit

The review may initially contain one row per meaningful email thread so Brian can validate the model. Multiple messages in one thread remain one evidence unit. Multiple threads should be associated with the same operational object when they concern the same outcome, action, decision, dependency, or entity.

Distinct opportunities remain distinct review items even when they are presented inside one capability group.

Every row must propose exactly one next action, including non-operational messages. Examples include:

- Do
- Decide
- Wait
- Remind
- Delegate
- Record then archive
- Archive
- Move to Trash
- Convert to an Admin OS object

## Capability Groups

The review may compress related items into a capability group when they share a stable classification and review workflow. Grouping must not erase item identity, provenance, confidence, or the ability to inspect exceptions.

The first deterministic group is:

```text
Career - Advisor/Expert Calls
```

For this group:

- use the Gmail label as the intake and grouping signal;
- retain each distinct advisory opportunity as a separate item;
- display group counts and proposed disposition counts;
- show average and minimum confidence;
- separate label confidence from recommendation confidence;
- expose low-confidence and policy exceptions individually;
- allow bulk confirmation only for items with the same proposed disposition and no exception;
- record one final decision per item even when confirmed in bulk.

The label means that a message belongs in this review group. It does not mean Brian should accept the opportunity or that a task should be created.

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

## Bootstrap Table

During the prototype phase, include enough metadata for Brian to correct the operating model:

| Field | Meaning |
|---|---|
| Priority | Ordered position in the review |
| Capability Group | Shared bounded workflow, when applicable |
| Thread | Current evidence source |
| Life Area / Program | Stable responsibility or grouped initiative |
| Outcome | Desired result |
| Action or Decision | Operational object affected |
| Status | Current state |
| Next Owner | Brian, external party, shared, or system |
| Recommended Next Action | Single proposed next move |
| Reason | Why the recommendation follows from the evidence |
| Entities | People, organizations, accounts, properties, or other durable subjects |
| Relationships | Grouping and contextual connections |
| Dependencies | Preconditions, blockers, or downstream triggers |
| Disposition | Keep, archive after action, archive now, move to Trash, or convert |
| Label Confidence | Confidence that the item belongs in the capability group |
| Recommendation Confidence | Confidence in the proposed next action |
| Rule / Model Version | Deterministic rule and reasoning version used |
| Grounded By | Gmail, Git, Calendar, Monday, screenshot, Brian, or explicit inference |
| Brian Comments | Corrections and additional context |

## Confidence and Bulk Review

Confidence values are review metadata and do not independently authorize execution.

For bulk review:

1. include only items sharing the same proposed disposition;
2. show item count, confidence range, and applicable rule or model version;
3. exclude low-confidence or policy-exception items;
4. preserve item-level approval, correction, and audit records;
5. do not allow a bulk decision to bypass Gmail, Monday, or communication approval gates.

## Relationships and Dependencies

Always surface proposed groupings, dependencies, and related objects for feedback. Do not silently create a relationship when confidence is not high.

A dependency may change the recommendation. An action can remain blocked until all prerequisites are complete, after which the next action should change automatically.

## Learning

Brian's corrections should be retained as structured learning events, including the original recommendation, final decision, relevant sender or topic, and any candidate reusable preference.

Do not silently convert a single accepted recommendation into a permanent rule. Candidate behavior progresses through `observed`, `proposed`, `confirmed`, `automatable`, and `retired` states.

## Review Completion

At the end of the review, summarize proposed:

- new outcomes
- new actions
- new decisions
- new entities
- new relationships
- new dependencies
- learned preferences or classification rules
- deterministic-rule false positives or false negatives

After Brian confirms the interpretation, update the relevant operating files and perform only the explicitly approved external actions.