# Operating Model Workspace

This directory is the curated, living prototype of Timmeny Admin OS operating state.

It is intentionally separate from `docs/`, which explains the product and architecture, and from `src/`, which implements software.

## Purpose

The files here are grown through real Admin OS and Career interactions. They are used to test which concepts, fields, relationships, statuses, lifecycle rules, and review practices are actually useful before those structures are implemented durably in Monday.com, PostgreSQL, or another platform.

## Current Authority

- Gmail: email content, threads, and attachments.
- Google Calendar: events and attendance.
- User-provided screenshots and explicit user information: current state for systems without a working live connection, including Monday.com during the prototype phase.
- Monday.com: task execution, status, due dates, and closure where records exist, based on the latest available screenshot or explicit user-provided state until a working connection is available.
- `operating/`: curated prototype context, learned preferences, review rules, and reconstructed records being tested.
- `docs/`: architecture, governance, and design decisions.

## Start Here

Before making conclusions about current operating state:

1. Read this file.
2. Read `review/daily-review.md` for the review workflow and ordered table.
3. Read `model/operational-object-rules.md` for object and relationship semantics.
4. Read `learning/brian-preferences.md` for confirmed and provisional user-specific rules.
5. Read the relevant YAML state files.
6. Refresh the current external evidence required for the request.

Do not use conversation memory as a substitute for current evidence or curated repository state.

## State Files

- `life-areas.yaml`: stable top-level responsibility areas.
- `outcomes.yaml`: goals, objectives, sub-objectives, and milestones.
- `actions.yaml`: one-time actions and generated recurring occurrences.
- `obligations.yaml`: recurring responsibilities and their lifecycle conditions.
- `entities.yaml`: people, organizations, properties, accounts, and other durable subjects.
- `relationships.yaml`: time-bounded relationships among objects.
- `decisions.yaml`: open and resolved decisions.
- `evidence.yaml`: references to emails, meetings, documents, payments, and completion proof.
- `assumptions.yaml`: hypotheses being tested about the model.
- `snapshots/`: source images or indexes used during manual curation.

## Operating Guides

- `review/daily-review.md`: evidence refresh, review ordering, table fields, dispositions, and feedback loop.
- `model/operational-object-rules.md`: rules for outcomes, actions, obligations, decisions, entities, relationships, dependencies, and evidence.
- `learning/brian-preferences.md`: learned preferences and confirmed groupings used to improve recommendations.

## Prototype Rules

1. Do not treat these files as a blind mirror of Monday.com, Gmail, or any other source.
2. Reconstruct selected records with the context we believe should exist.
3. Keep uncertainty explicit.
4. Use stable IDs; do not use titles as identity.
5. Expect records and schemas to change frequently at first.
6. Promote structures into architecture only after repeated real use.
7. Do not maintain two independent execution states; Monday.com remains operationally authoritative for tasks represented there until the implementation changes.
8. Evidence is not automatically an action. Determine the affected operational object before creating state.
9. Proposed relationships and dependencies should be surfaced for feedback when not already confirmed.

## Chat Use

The Admin OS and Career chats should contribute confirmed changes to this shared model. The Architecture chat should review accumulated changes and discoveries rather than depend on complete memory of the other conversations.

When Brian confirms an operating-model change, update the relevant files under `operating/` and commit directly to `main` unless instructed otherwise.