# 20 — Design Process

**Status:** Draft for review  
**Version:** 0.9  
**Stability:** Very high  
**Purpose:** Defines how Timmeny Admin OS is discovered, designed, documented, implemented, and improved.  
**Depends on:** [10 — North Star](./10-NorthStar.md)  
**Guides:** Product discovery, architecture decisions, implementation sequencing, and documentation maintenance

## Core Method

Timmeny Admin OS is not designed primarily through speculation.

It is designed through use.

The platform exists to administer the complexity of real life. Its design should therefore emerge from real administrative work rather than from an attempt to predict every capability in advance.

The preferred sequence is:

```text
Operational use
    -> observation
        -> repeated pattern
            -> product discovery
                -> architecture decision
                    -> incremental implementation
                        -> operational validation
```

Architecture should remain slightly behind observed reality. The system should generalize patterns that have demonstrated value rather than encode every interesting idea.

## The Three-Chat Operating Model

The Timmeny Admin OS Project contains three complementary workspaces.

### Timmeny Admin OS

This is the primary daily-life operating workspace.

It is used for email, calendar, finances, family, health, travel, household responsibilities, goals, reminders, decisions, and prioritization.

Its purpose is to administer life effectively, not to design software.

### Timmeny Career

This is the specialist career workspace.

It is used for executive opportunities, recruiters, resume strategy, compensation, professional positioning, networking, and long-term career decisions.

Its purpose is to manage the career domain deeply enough to expose patterns that a generic daily workspace may not reveal.

### Timmeny Admin OS Architecture

This is the product and architecture workspace.

It receives discoveries from the two operational workspaces, identifies recurring needs, evaluates tradeoffs, updates the canonical design, and guides implementation.

It should not invent capabilities in isolation from operational evidence.

## Discovery Before Architecture

Operational work naturally reveals product needs through friction, repetition, and missing context.

Examples include:

- repeatedly reconstructing the same context
- manually connecting emails to larger outcomes
- goals with no supporting actions or calendar time
- unresolved decisions blocking multiple areas
- recurring responsibilities being recreated manually
- information being stored without changing a decision or action
- activity that does not produce measurable progress

Each meaningful observation may become a product discovery. A discovery is not automatically an architectural commitment.

## Discovery Maturity

Discoveries progress through four maturity levels:

1. **Idea** — a potentially useful capability with little or no operational evidence
2. **Observation** — a real instance of friction or unmet need
3. **Validated Pattern** — a recurring need demonstrated across multiple situations
4. **Architecture** — an accepted, generalized capability with defined boundaries and responsibilities

Ideas and isolated observations should normally remain in the discovery backlog.

## Rule of Three

A capability should generally be promoted into the canonical product or architecture only after it has appeared in at least three sufficiently independent operational situations.

The rule is a heuristic, not a rigid voting mechanism. A single high-severity need may justify earlier action, while three weak examples may still be insufficient.

The purpose is to require evidence before complexity.

Example:

Goal-progress intelligence appeared independently in tennis, marriage, and career discussions. The repeated pattern was not merely a desire to track tasks; it was a need to compare intended outcomes with observed allocation of time, actions, decisions, and evidence. That justified promotion into the product definition.

## Product Discoveries

Operational discoveries are recorded in [Product Discoveries](./discovery/Product-Discoveries.md).

Each discovery should capture:

- the observation
- operational evidence
- the underlying problem
- the possible reusable capability
- affected life areas or workflows
- confidence
- maturity
- disposition

The discovery log is a staging area. It is not the roadmap and does not imply a commitment to build.

## Architecture Review

When a discovery becomes a validated pattern, the Architecture workspace evaluates:

1. Is the problem real and recurring?
2. Is it already solved by an existing concept or capability?
3. Does it belong inside Timmeny Admin OS?
4. What is the smallest useful capability?
5. Which business concept owns the behavior?
6. Which system should own the state?
7. What tradeoffs or risks are introduced?
8. Does the decision require an ADR?
9. Which documents are affected?
10. How will value be validated through operational use?

## Documentation Workflow

The documentation lifecycle is:

```text
Architecture discussion
    -> explicit decision
        -> affected documents updated
            -> ADR created when significant
                -> commit to Git
                    -> use in operations and implementation
```

Git is the architectural source of truth.

Documents should not be regenerated indiscriminately. Only the documents affected by a decision should change.

Information should be defined once and referenced elsewhere to avoid conflicting copies.

## Implementation Discipline

Implementation should occur in small, useful loops.

A capability should be built only far enough to test the value demonstrated by the discovery.

The preferred approach is:

- preserve existing working behavior
- introduce the smallest canonical abstraction needed
- expose business operations rather than implementation details
- keep important writes reviewable and auditable
- validate the capability through real use
- expand only when further evidence supports expansion

The objective is not to complete the imagined platform as quickly as possible. The objective is to produce compounding administrative value without creating unnecessary machinery.

## Feedback and Learning

After implementation, the capability returns to operational use.

The system and the design process should capture:

- whether the capability reduced friction
- whether the model matched actual behavior
- where manual corrections were required
- whether the capability improved decisions or progress
- whether new patterns emerged

Corrections may become reusable rules, product discoveries, domain-model changes, or new architecture decisions.

## Design Constraints

The process should consistently favor:

- evidence over speculation
- progress over feature volume
- small useful loops over broad platforms
- canonical concepts over integration-specific models
- explicit decisions over implicit assumptions
- durable context over conversational memory
- operational validation over architectural elegance alone

## Definition of Done for a Design Decision

A design decision is complete when:

- the problem and evidence are clear
- the decision and tradeoffs are explicit
- ownership and boundaries are defined
- affected documents are updated
- an ADR exists when required
- the next validation step is known
- the implementation can be incremental

## Governing Principle

Timmeny Admin OS should be designed the same way it is intended to operate:

**Observe. Learn. Recognize patterns. Decide deliberately. Improve continuously.**