# ADR-0001 — Admin OS Is the Coordination Layer

**Status:** Accepted  
**Date:** 2026-07-27

## Context

The prototype initially relied on ChatGPT to read and coordinate Gmail, Monday.com, Calendar, repository state, and emerging operating-model records directly. This made correct operation dependent on chat-specific tool availability, conversation memory, and repeated prompting. It also blurred reasoning, state ownership, orchestration, and execution.

## Decision

ChatGPT is the reasoning and conversation layer. It does not coordinate external systems directly.

Timmeny Admin OS is the coordination and orchestration layer. It exposes one coherent operational model to AI clients and owns domain behavior, synchronization, routing, workflows, identity resolution, context assembly, verification, and audit.

System responsibilities are:

- **ChatGPT:** reasoning, prioritization, recommendations, explanation, and conversation.
- **Timmeny Admin OS:** domain logic, coordination, synchronization, routing, workflow execution, context assembly, verification, and audit.
- **Monday.com:** executable tasks, task status, owners, due dates, and task closure.
- **PostgreSQL:** durable persistence for programs, outcomes, decisions, entities, relationships, dependencies, evidence, preferences, mappings, and workflow state.
- **Gmail:** communication content and thread state.
- **Google Calendar:** events, attendance, and scheduling state.

PostgreSQL stores state; it does not own business meaning. Timmeny Admin OS owns the domain model and business rules persisted there.

External evidence is never converted directly into a task. It is first classified against an operational object. A workflow is then selected, including create a task, archive, label, wait, create a decision, update an existing object, or record evidence only.

## First Vertical Slice

Implement one end-to-end workflow before expanding scope:

```text
Gmail evidence
  -> classify operational object
  -> create or update Monday task when required
  -> observe completion
  -> archive or otherwise disposition the Gmail thread
  -> record verification and audit history
```

## Alternatives Considered

1. **ChatGPT coordinates Gmail and Monday directly.** Rejected because tool access and chat state are inconsistent and orchestration would be coupled to a reasoning client.
2. **Monday.com is the complete operating model.** Rejected because rich relationships, evidence, decisions, identity, dependencies, and history do not fit cleanly as execution-board metadata.
3. **PostgreSQL owns the business model.** Rejected because persistence should not own domain meaning or workflow policy.
4. **Build all integrations before validating a workflow.** Rejected because it delays operational learning and increases implementation risk.

## Consequences

- AI clients become replaceable and stateless.
- External connectors are implemented once behind Admin OS.
- Operational state and workflow decisions become auditable.
- Context assembly becomes a core service responsibility.
- Direct Gmail or Monday access by ChatGPT is transitional evidence access, not the target architecture.
- The MVP is constrained to PostgreSQL, Gmail, Monday, an Executive Review API, and a ChatGPT interface.

## Affected Documents

- `docs/50-Architecture.md`
- `docs/60-Domain-Model.md`
- `docs/70-Implementation-Strategy.md`
- `docs/80-Monday-Architecture.md`
- `docs/90-Roadmap.md`
- `operating/README.md`

## Validation

The decision is validated when Brian can request `Refresh Admin OS` and receive a verified executive review assembled from synchronized operational state without ChatGPT directly coordinating Gmail or Monday.com.