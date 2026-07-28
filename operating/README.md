# Operating Model Workspace

This directory is the curated, living prototype of Timmeny Admin OS operating state.

It is intentionally separate from `docs/`, which defines product and architecture, and from `src/`, which implements the runtime platform.

## Purpose

The files here are grown through real Admin OS and Career interactions. They test which objects, fields, relationships, statuses, lifecycle rules, classifications, dispositions, and review practices are useful before those structures are implemented durably through Timmeny Admin OS and PostgreSQL.

This directory is transitional prototype state. The target runtime architecture is defined by [ADR-0001](../docs/adr/ADR-0001-admin-os-coordination-layer.md): ChatGPT reasons over one operational model exposed by Admin OS; Admin OS coordinates Gmail, Monday.com, Calendar, and PostgreSQL.

## Current Authority During the Prototype

- Gmail: email content, threads, labels, archive state, and attachments.
- Google Calendar: events, invitations, attendance, and scheduling state.
- User-provided screenshots and explicit user information: current evidence for systems without a working live connection, including Monday.com.
- Monday.com: native task execution, status, owners, due dates, and closure where records exist, based on the latest available evidence until a working connector is available.
- `operating/`: curated prototype context, learned preferences, reconstructed operational objects, and model assumptions.
- `docs/`: architecture, governance, ADRs, and implementation direction.

Do not confuse the prototype representation under `operating/` with the final source of runtime state. PostgreSQL will persist canonical operational state when the implementation is ready; Admin OS will own the domain behavior.

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
- `outcomes.yaml`: goals, programs, outcomes, objectives, sub-objectives, and milestones.
- `actions.yaml`: one-time actions and generated recurring occurrences.
- `obligations.yaml`: recurring responsibilities and lifecycle conditions.
- `entities.yaml`: people, organizations, properties, accounts, and other durable subjects.
- `relationships.yaml`: explicit, potentially time-bounded relationships among objects.
- `decisions.yaml`: open and resolved decisions.
- `evidence.yaml`: references to emails, meetings, documents, payments, and completion proof.
- `assumptions.yaml`: hypotheses being tested about the model.
- `snapshots/`: source images or indexes used during manual curation.

## Operating Guides

- `review/daily-review.md`: evidence refresh, review ordering, table fields, dispositions, and feedback loop.
- `model/operational-object-rules.md`: rules for outcomes, actions, obligations, decisions, entities, relationships, dependencies, and evidence.
- `learning/brian-preferences.md`: learned preferences and confirmed groupings used to improve recommendations.

## Classification Rule

Emails, calendar events, screenshots, documents, and other evidence are not automatically tasks.

For each new piece of evidence:

1. identify the operational object it creates, updates, completes, blocks, contradicts, or supports;
2. preserve uncertainty, provenance, and confidence;
3. select exactly one recommended disposition or workflow;
4. create or update a task only when execution is actually required.

Possible dispositions include create or update a Monday task, create a decision, wait, remind, apply a Gmail label, archive, move to Trash, or record evidence only.

## Prototype Rules

1. Do not blindly mirror Monday.com, Gmail, or any other source.
2. Reconstruct selected records with the context we believe should exist.
3. Keep uncertainty explicit.
4. Use stable IDs; do not use titles as identity.
5. Expect records and schemas to change frequently at first.
6. Promote structures into architecture only after repeated real use.
7. Do not maintain two independent execution states; Monday.com remains authoritative for its native task fields until the implementation changes.
8. Evidence is not automatically an action.
9. Proposed relationships and dependencies should be surfaced for feedback when not confirmed.
10. Every reviewed item receives one recommended next action or disposition.

## Chat Use

The Admin OS and Career chats contribute confirmed changes to this shared prototype. The Architecture chat reviews accumulated changes and discoveries and records significant decisions as ADRs rather than relying on complete memory of other conversations.

When Brian confirms an operating-model change, update the relevant files under `operating/` and commit directly to `main` unless instructed otherwise.