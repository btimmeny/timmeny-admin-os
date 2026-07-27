# Operational Object Rules

## Evidence Is Not the Work

Emails, calendar events, screenshots, documents, and payment confirmations are evidence. They may trigger or update work, but they are not automatically actions or outcomes.

For each evidence item, determine whether it creates or changes:

- a life area
- an outcome
- an action
- an obligation
- a decision
- an entity
- a relationship
- a dependency
- evidence only

## Stable Identity

Use stable IDs for all curated objects. Titles and email subjects are labels, not identity.

## Outcomes

An outcome describes a desired result. Multiple evidence items and actions may support one outcome. Do not create a new outcome merely because a new email thread exists.

## Actions

An action is a concrete next step or executable item. Every active operational object should expose one recommended next action, but that recommendation is not always an action owned by Brian. It may be to wait, remind, archive, or move a message to Trash.

Monday.com remains authoritative for execution state where a corresponding record exists. During the prototype phase, Monday state is supplied through screenshots or explicit user information unless a working live connection is available.

## Obligations

Use obligations for recurring responsibilities whose lifecycle continues across repeated occurrences, such as regular payments or daily routines. Do not model a recurring responsibility as a permanently open one-time action.

## Decisions

Use a decision when Brian must choose among alternatives. Record options, selected option, rationale, and evidence when known. A decision may generate subsequent actions.

## Entities

Create entities for durable people, organizations, properties, accounts, and other subjects that will recur across evidence or operational objects. Avoid creating durable entities for incidental automated senders unless they matter operationally.

## Relationships

Relationships describe durable or time-bounded connections among objects. Relevant relationship patterns include:

- contains / member of
- depends on / prerequisite for
- blocks / unblocks
- related to
- owns / managed by
- employed by / provider for
- recruiter for / advisor for

## Dependencies

Dependencies are operational relationships that affect readiness or sequencing. A downstream action should remain blocked until required prerequisites are complete.

When all prerequisites complete, propose the newly unblocked action.

## Evidence

Prefer one evidence record per meaningful thread or source artifact rather than one record per individual message, unless separate messages independently prove distinct events.

Evidence records should preserve source-system references and link to the objects they support.

## Uncertainty

Keep uncertainty explicit. Use confidence and model status rather than converting uncertain inference into confirmed state.

## Promotion Rule

New structures and fields remain prototype concepts until repeated real use demonstrates that they should be promoted into durable architecture or implementation.