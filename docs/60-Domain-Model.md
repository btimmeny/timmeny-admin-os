# 60 — Domain Model

**Status:** Active draft  
**Version:** 0.2  
**Stability:** Medium  
**Purpose:** Defines the canonical business concepts and relationships used throughout Timmeny Admin OS.  
**Depends on:** [40 — Product](./40-Product.md), [50 — Architecture](./50-Architecture.md)

## Core Rule

Evidence is not automatically a task. Every new external signal is first classified against an operational object. The resulting object state determines which workflow, if any, should execute.

## Canonical Concepts

### Direction and outcomes

- Purpose
- Life Area
- Goal
- Program
- Outcome
- Objective
- Milestone

Outcome objects may be nested. Their type, lifecycle, completion criteria, and parent relationship determine behavior rather than a fixed depth.

### Execution and responsibility

- Action
- Obligation
- Recurring Template
- Occurrence
- Owner
- Dependency

An Action is finite executable work. An Obligation is an ongoing responsibility whose lifecycle conditions generate Action occurrences.

### Context and identity

- Entity
- Relationship
- External Identity Mapping
- Preference
- Policy

Entities include people, organizations, properties, accounts, documents, and other durable subjects. Relationships are explicit and may be time-bounded.

### Reasoning and governance

- Decision
- Evidence
- Signal
- Assumption
- Progress Assessment
- Life Health Assessment
- Workflow Rule
- Event
- Audit Record

Evidence records what is known and its provenance. A Decision records a choice or unresolved choice. A Workflow Rule determines safe execution after classification.

## Operational Object

`Operational Object` is the shared abstraction for durable objects that can be created, updated, completed, blocked, supported, or related by evidence. Initial operational object types include:

- Program
- Outcome
- Objective
- Action
- Obligation
- Decision
- Entity
- Relationship

Evidence attaches to operational objects but is not itself executable work.

## Classification Result

Each incoming email, calendar event, Monday change, document, or user statement should produce an explicit classification result:

- affected operational object
- relationship to that object: creates, updates, completes, blocks, supports, or contradicts
- confidence and evidence provenance
- selected disposition
- selected workflow, if any

## Initial Hierarchy

```text
Purpose
  -> Life Area
      -> Goal or Program
          -> Outcome / Objective / Milestone
              -> Action

Obligation
  -> recurring Action occurrences

Entity and Relationship
  -> provide shared context across all objects

Evidence and Decisions
  -> explain state, change, and workflow selection
```

## Ownership

The domain model is owned by Timmeny Admin OS and remains independent of Monday.com columns, Gmail structures, database tables, AI-provider prompts, or any other implementation detail. PostgreSQL persists canonical state; external systems retain ownership of their native records.