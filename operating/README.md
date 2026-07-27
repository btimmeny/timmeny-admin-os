# Operating Model Workspace

This directory is the curated, living prototype of Timmeny Admin OS operating state.

It is intentionally separate from `docs/`, which explains the product and architecture, and from `src/`, which implements software.

## Purpose

The files here are grown through real Admin OS and Career interactions. They are used to test which concepts, fields, relationships, statuses, and lifecycle rules are actually useful before those structures are implemented durably in Monday.com, PostgreSQL, or another platform.

## Current Authority

- Monday.com: current task execution, status, due dates, and closure where records already exist.
- Gmail: email content and threads.
- Google Calendar: events and attendance.
- `operating/`: curated prototype context and reconstructed records being tested.
- `docs/`: architecture, governance, and design decisions.

## Files

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

## Prototype Rules

1. Do not treat these files as a blind mirror of Monday.
2. Reconstruct selected records with the context we believe should exist.
3. Keep uncertainty explicit.
4. Use stable IDs; do not use titles as identity.
5. Expect records and schemas to change frequently at first.
6. Promote structures into architecture only after repeated real use.
7. Do not maintain two independent execution states; Monday remains operationally authoritative until the implementation changes.

## Chat Use

The Admin OS and Career chats should contribute changes to this shared model. The Architecture chat should review the accumulated changes and discoveries rather than depend on complete memory of the other conversations.