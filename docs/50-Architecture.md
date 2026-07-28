# 50 — Architecture

**Status:** Active draft  
**Version:** 0.2  
**Stability:** Medium  
**Purpose:** Defines the major system components, responsibilities, boundaries, sources of truth, and interaction model.  
**Depends on:** [30 — Architecture Principles](./30-Architecture-Principles.md), [40 — Product](./40-Product.md), [ADR-0001](./adr/ADR-0001-admin-os-coordination-layer.md)

## Architectural Model

```text
ChatGPT and other AI clients
        |
        | reasoning requests and recommendations
        v
Timmeny Admin OS
        |
        |-- domain model and business rules
        |-- identity resolution and context assembly
        |-- synchronization and routing
        |-- workflow execution and verification
        |-- audit history
        |
        +-- PostgreSQL: durable operational state
        +-- Monday.com: task execution
        +-- Gmail: communication
        +-- Google Calendar: scheduling
```

ChatGPT is a reasoning client, not the operating system. It reasons over one operational model exposed by Timmeny Admin OS and does not coordinate external systems directly in the target architecture.

## Responsibility Boundaries

### ChatGPT

Owns:

- reasoning
- prioritization
- recommendations
- synthesis
- explanation
- conversation

It does not own durable operational state, synchronization, workflow execution, or cross-system verification.

### Timmeny Admin OS

Owns:

- the canonical domain model and business rules
- coordination and orchestration
- connector synchronization
- canonical identity and external-system mappings
- evidence classification
- context assembly
- workflow selection and execution
- verification, idempotency, and audit
- the Executive Review API

### PostgreSQL

Persists:

- programs and outcomes
- decisions
- entities and relationships
- dependencies
- evidence and source mappings
- preferences and learned rules
- workflow and synchronization state
- audit records

PostgreSQL persists the model; Timmeny Admin OS owns its meaning and behavior.

### Monday.com

Owns executable work:

- tasks
- status
- owners
- due dates
- task completion and closure

Monday.com is not the complete canonical domain model.

### Gmail

Owns email messages, threads, labels, archive state, and attachments. Emails are evidence and communication, not tasks by default.

### Google Calendar

Owns events, attendance, invitations, and scheduling state.

## Operational Object Classification

No external event becomes a task directly. Admin OS first determines which operational object the evidence creates, updates, completes, blocks, or supports. It then selects a workflow such as:

- create or update a Monday task
- create or resolve a decision
- attach evidence to an existing object
- wait for further evidence
- apply a Gmail label
- archive or move a message to Trash
- record evidence only

## Context Assembly

Admin OS assembles one coherent operational context from PostgreSQL and synchronized external systems. AI clients receive this assembled model rather than independently reconciling Gmail, Monday, Calendar, and historical state.

## Current Prototype

During discovery, Git under `operating/` remains the curated operating model used by the chats. User-provided Monday screenshots and direct Gmail or Calendar reads are current evidence sources. This is transitional evidence gathering; the target runtime is Admin OS backed by PostgreSQL and connectors.

## Success Condition

The architecture is successful when Brian can request `Refresh Admin OS` and receive:

- synchronized Gmail state
- synchronized Monday state
- synchronized Calendar state
- verified safe workflow execution
- one current Executive Review

without ChatGPT coordinating those systems directly.