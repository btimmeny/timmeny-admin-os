# timmeny-admin-os Architecture

`timmeny-admin-os` is the execution layer behind Brian Timmeny's private custom GPTs.

The GPTs provide conversation, reasoning, synthesis, and planning. The API provides controlled access to external systems, durable workflow rules, and auditable writes.

## Operating Model

```text
Private custom GPTs
        |
        v
timmeny-admin-os API, hosted on Railway
        |
        +-- Monday.com: commitments, todos, action metadata
        +-- Gmail: communication, extraction, follow-up context, planned
        +-- Background jobs: scheduled reviews and monitors, planned
        +-- Approval controls: human confirmation before important writes
```

## Sources Of Truth

- Monday.com is the source of truth for commitments, action items, planning metadata, and status.
- Gmail is the source of communication and email context.
- GPT instructions define conversational behavior and workflow discipline.
- Knowledge files provide durable context, but they are not live operational data.
- The API is the controlled execution surface.

## Current Capability

The first implemented capability is Monday.com todo management:

- Read open personal and GS todos.
- Read Key Initiatives as GS planning context.
- Create personal or GS todos.
- Create decision items with action metadata.
- Read board metadata and observed values.
- Update planning metadata one item at a time or in bulk.

Existing endpoint paths remain todo-oriented because they are already wired to the GPT Action and Railway deployment.

## Planned Capabilities

### Gmail Review

Gmail workflows should extract communication context and propose follow-ups, decisions, or todos. They should not automatically create Monday commitments without review.

### Background Processing

Scheduled workflows may eventually run recurring reviews, inbox scans, or planning checks. Background jobs should produce reviewable outputs before making important changes.

### Multiple GPTs

Different GPTs can specialize by workflow while sharing the same API:

- GS planning GPT
- Personal admin GPT
- Email review GPT
- Future letter or writing GPT

Each GPT should use the same principle: reason conversationally, then call `timmeny-admin-os` for controlled execution.

## Approval Rules

Reads are allowed without confirmation.

Small writes can be performed when the user explicitly asks.

Broad writes, bulk updates, email sends, destructive changes, and scheduled automation changes require explicit confirmation.

Every write workflow should be able to answer:

1. What did it read?
2. What did it decide?
3. What did it change?
