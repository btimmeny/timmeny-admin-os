# 60 — Domain Model

**Status:** Draft scaffold  
**Version:** 0.1  
**Stability:** Medium  
**Purpose:** Defines the canonical business concepts and relationships used throughout Timmeny Admin OS.  
**Depends on:** [40 — Product](./40-Product.md), [50 — Architecture](./50-Architecture.md)

## Initial Canonical Concepts

- Purpose
- Life Area
- Goal
- Portfolio
- Objective
- Action
- Decision
- Dependency
- Evidence
- Signal
- Entity
- Relationship
- Recurring Template
- Occurrence
- Progress Assessment
- Life Health Assessment
- Policy
- Workflow Rule
- Event

## Initial Hierarchy

```text
Purpose
  -> Life Area
      -> Goal
          -> Portfolio
              -> Objective
                  -> Action
                      -> Evidence and Signals
                          -> Progress and Health
```

This document will define each concept, its identity, lifecycle, ownership, allowed relationships, and invariants. It must remain independent of Monday.com columns, Gmail structures, database tables, or any other implementation-specific model.