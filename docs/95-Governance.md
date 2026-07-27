# Governance

## Purpose

This document defines how the Admin OS, Career, and Architecture chats contribute to one shared operating model and how discoveries are promoted into durable architecture and implementation.

## Chat Responsibilities

### Timmeny Admin OS

Primary operational interface for personal administration. It reviews tasks, email, calendar, priorities, obligations, decisions, and cross-domain progress. It produces operating-model changes.

### Timmeny Career

Specialized operational interface for career positioning, opportunities, recruiters, compensation, interviews, decisions, and career actions. It uses and contributes to the same operating model as Admin OS.

### Timmeny Admin OS Architecture

Design and governance workspace. It does not manage daily execution. It reviews accumulated operating changes and discoveries to evolve the domain model, Monday design, persistence model, APIs, and roadmap.

## Governing Principle

Chats do not depend on complete cross-chat conversation memory. They share durable state through the operating model.

## Session Outputs

A substantive Admin OS or Career session should produce, when applicable:

### Operating Model Changes

- Added
- Updated
- Completed
- Archived
- Linked or unlinked

### Model Discoveries

- Missing fields
- Duplicate concepts
- Unclear classifications
- Candidate object or relationship types
- Context that does not fit current structures

### Implementation Implications

- Candidate Monday changes
- Candidate PostgreSQL responsibilities
- Integration or automation requirements

## Promotion Path

```text
Real operation
  -> curated operating-model change
  -> repeated evidence
  -> architecture decision
  -> implementation backlog
  -> Monday/PostgreSQL/API implementation
```

No concept is promoted merely because it seems theoretically useful. Repeated real-world evidence is preferred.

## Review Cadence

### Operating Review

Review active Outcomes, Actions, Obligations, Decisions, stale records, duplicates, missing next actions, and unsupported goals.

### Architecture Review

Review changes since the prior architecture session, identify recurring patterns, refine schemas, and decide whether any pattern is mature enough to enter architecture or implementation.

## Authority During Prototype

- Monday.com remains authoritative for current execution status where an item already exists there.
- Gmail remains authoritative for email content and threads.
- Google Calendar remains authoritative for event timing and attendance.
- `operating/` is authoritative for the curated prototype context being tested.
- `docs/` is authoritative for architecture and governance.

## Change Discipline

Every operating record should preserve its source, prototype lifecycle, and last verification date when available. Uncertainty should be represented explicitly rather than hidden.