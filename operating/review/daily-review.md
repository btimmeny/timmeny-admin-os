# Daily Operations Review

## Purpose

The daily review converts current operational evidence into one ordered executive worklist. It is not an email summary. It answers: what requires Brian's attention, what should happen next, and how each item connects to the broader operating model.

## Evidence Refresh

Before producing an operational review:

1. Refresh Gmail when email state is relevant.
2. Read `operating/README.md` and the relevant files under `operating/` from `main`.
3. Read Google Calendar when meetings, deadlines, or availability are relevant.
4. Use user-provided screenshots as current evidence for systems without a live connection, including Monday.com during the prototype phase.
5. Do not infer current operational state from conversation memory.

If a required source cannot be read, identify the missing source and do not fabricate its current state.

## Review Unit

The review may initially contain one row per email thread so Brian can validate the model. Multiple threads should be associated with the same operational object when they concern the same outcome, action, decision, dependency, or entity.

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

## Ordering

Present one ordered table in this sequence:

1. Brian acts now.
2. Brian decides.
3. Brian is blocked or waiting on others.
4. Awareness or monitor.
5. Record then archive.
6. Reading material.
7. Mass mailings and promotional material.
8. Archive or move to Trash.

Within a group, order by urgency, impact, dependency unblocking, and then recency.

## Bootstrap Table

During the prototype phase, include enough metadata for Brian to correct the operating model:

| Field | Meaning |
|---|---|
| Priority | Ordered position in the review |
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
| Confidence | High, medium, or low |
| Grounded By | Gmail, Git, Calendar, screenshot, Brian, or explicit inference |
| Brian Comments | Corrections and additional context |

## Relationships and Dependencies

Always surface proposed groupings, dependencies, and related objects for feedback. Do not silently create a relationship when confidence is not high.

A dependency may change the recommendation. An action can remain blocked until all prerequisites are complete, after which the next action should change automatically.

## Review Completion

At the end of the review, summarize proposed:

- new outcomes
- new actions
- new decisions
- new entities
- new relationships
- new dependencies
- learned preferences or classification rules

After Brian confirms the interpretation, update the relevant operating files and perform explicitly approved Gmail actions.