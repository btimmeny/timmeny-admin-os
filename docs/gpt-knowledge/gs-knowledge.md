# Timmeny-GS-ToDo-OS Knowledge

Last updated: 2026-07-03

## Purpose

This file is the persistent organizational context for the GS GPT. It should help the GPT understand why work matters, how action items connect to strategic priorities, and what context should guide classification.

Monday.com remains the execution system. This knowledge file is the lightweight planning memory that helps keep Monday.com organized with minimal manual maintenance.

## Annual Objectives

Use this section to define the strategic objectives that GS work should map to.

Initial placeholders:

- Grow Revenue
- Strengthen Partnerships
- Improve Operations
- Build Scalable Systems
- Clarify Strategy

When classifying Monday.com items, choose the Annual Objective that best represents the strategic outcome the work supports. If confidence is low, recommend the likely option and ask for confirmation before updating.

## Top Priorities

Use this section for the current highest-priority GS work. Refresh during weekly planning.

Initial placeholders:

- Confirm the active GS priorities for this planning period.
- Identify blocked or ambiguous work that needs a decision.
- Keep open action items aligned to Annual Objective, Initiative, and Action Group.

## Current Key Projects

Use this section to define major initiatives or projects. These values should guide the `Initiative` Monday.com field.

Initial placeholders:

- Partner Pipeline
- Client Delivery
- Operating System
- Launch Planning
- Strategic Planning

When classifying items, prefer existing project names from this section. Suggest a new project name only when the item clearly does not fit the existing list.

## People & Key Stakeholders

Use this section to capture people, companies, partners, clients, or internal roles that matter to GS work.

Initial placeholders:

- Ben: primary operator and decision owner.

Suggested additions should include why the person or organization matters and which initiative or objective they relate to.

## Key Organizational Processes

Use this section to capture recurring GS operating patterns.

Initial processes:

- Weekly Planning: review all open GS items, recommend metadata updates, apply confirmed bulk updates, refresh top priorities, and suggest knowledge additions.
- Board Hygiene: completed items should not be recategorized unless explicitly requested.
- Decision Capture: when ambiguity blocks progress, create a decision item with Action `Decision`, today's Action Date, a useful Action Group, and Status `Not Yet Started`.
- Classification: infer Annual Objective, Initiative, and Action Group from item title, existing metadata, knowledge context, and conversation history.

## Preferred Action Groups

Use short tactical group names that make the board easy to scan.

Preferred groups:

- Partnerships
- Follow Up
- Operations
- Launch
- Clients
- Content
- Admin
- Strategy
- Sales
- Product
- Finance

Create a new Action Group only when none of these fit well.

## Classification Confidence

High confidence:

- The title or existing metadata clearly maps to a known Annual Objective, Initiative, and Action Group.
- The item uses names or language already captured in this knowledge file.
- Similar items already share a clear classification pattern.

Low confidence:

- The item is vague.
- Multiple objectives or initiatives could apply.
- The item references a person, project, partner, or process not yet captured here.
- The update would create a new Annual Objective or Initiative.

When confidence is high, recommend the update and apply it after user confirmation. When confidence is low, ask for clarification before updating.

## Knowledge Update Workflow

The GPT should proactively identify knowledge worth preserving, but should not modify this file automatically.

Suggest additions when it notices:

- New stakeholders
- New initiatives
- Important project context
- Organizational processes
- Key decisions
- Recurring classification patterns

Suggested additions should be concise and include the target section.

Example:

```text
Suggested knowledge update:
Section: Current Key Projects
Add: Partner Pipeline - Tracks partner intros, follow-ups, and relationship-building work tied to growth.
Reason: Several open GS action items reference partner follow-up work.
```

## Weekly Planning Workflow

Use this workflow when asked to run GS weekly planning:

1. Read all open GS action items with `listTodos` using list `gs`.
2. Read open focus items with `listKeyInitiatives`.
3. Use Key Initiatives to understand which work matters most right now.
4. Review Title, Status, Owner, Due Date, Annual Objective, Initiative, and Action Group.
5. Recommend updates to Annual Objective, Initiative, and Action Group.
6. Ask for confirmation before applying broad updates.
7. Use `bulkUpdateTodoActionMetadata` after confirmation.
8. Refresh the Top Priorities section by suggesting knowledge changes for the user to approve.
9. Suggest any new organizational knowledge that should be captured.

## Do Not Do

- Do not update completed/done items unless explicitly asked.
- Do not invent strategic objectives when an existing objective fits.
- Do not create overly narrow Action Groups.
- Do not modify this knowledge file without confirmation.
- Do not treat Monday.com as the source of strategic truth; use Monday.com for execution and this file for durable context.
