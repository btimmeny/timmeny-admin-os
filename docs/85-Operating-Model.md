# Operating Model

## Purpose

The operating model defines the business concepts Timmeny Admin OS uses to represent Brian Timmeny's life and work. It is implementation-neutral: the concepts may be prototyped in Git and later persisted in Monday.com, PostgreSQL, or another system without changing their meaning.

## Core Concepts

### Life Area

A stable, high-level area of responsibility such as Career, Marriage, Family, Health, Finance, or Travel.

### Outcome

A desired state arranged in a recursive hierarchy. An Outcome may be typed as a goal, objective, sub-objective, or milestone. Duration does not define the type; behavior and completion criteria do.

### Action

A specific executable item. Actions may be one-time or generated from an Obligation. During the prototype, selected Actions may be recreated from Monday and enriched with curated context.

### Obligation

An ongoing responsibility that generates Action instances while its activation conditions remain true. Examples include calling a family member weekly or paying electricity for a currently occupied property.

### Entity

A person, household, organization, property, account, institution, document, or other durable subject.

### Relationship

A time-bounded connection between Entities or operating objects, such as employment, ownership, occupancy, recruiting representation, or support for an Outcome.

### Decision

An open or resolved choice that materially changes context, direction, or future Actions.

### Evidence

A reference proving activity, completion, communication, or an achieved result. Evidence may point to an email, meeting, document, payment, receipt, call, or manual confirmation.

## Outcome Hierarchy

Outcomes may contain child Outcomes at any depth:

```text
Life Area
  Outcome
    Outcome
      Outcome
        Action
```

Each Outcome should have a stable ID, parent relationship, lifecycle status, horizon, and completion criteria where applicable.

## Lifecycle Semantics

- Outcomes: proposed, active, blocked, on_hold, completed, abandoned, superseded
- Actions: open, in_progress, waiting, completed, cancelled, superseded
- Obligations: planned, active, suspended, terminating, terminated
- Relationships: planned, active, ending, ended
- Decisions: open, resolved, deferred, superseded

## Recurrence and Conditions

Recurring work is governed by an Obligation, not merely copied Actions. An Obligation is scoped to relevant Entities and relationships and includes activation and termination conditions.

When a governing relationship ends, related Obligations should enter review or termination and may generate final closure Actions rather than disappearing silently.

## Identity and Context

Every durable object requires a stable Timmeny ID. Titles are labels, not identity. Cross-system links must use stable IDs and preserve provenance, confidence, and verification where relevant.

## Prototype Rule

The `operating/` directory represents the model we believe should exist, not a faithful reproduction of current external systems. Records are intentionally mutable while the domain is being learned through real use.