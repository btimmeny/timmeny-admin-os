# Monday.com Architecture

## Purpose

This document captures the evolving implementation design for Monday.com within Timmeny Admin OS. It is downstream of the operating model: Monday structure should be derived from validated operating needs rather than used to define the domain prematurely.

## Current Position

Monday.com remains the operational execution system for current to-dos, status, due dates, recurrence, and closure. The existing workspace remains in use while the future model is discovered through the Admin OS and Career workflows.

The prototype in `operating/` may temporarily duplicate selected Monday items. That duplication is intentional and curated. It is used to determine the correct context, fields, relationships, and object boundaries before any automated import or redesign.

## Design Principles

1. Do not import existing Monday data blindly.
2. Clean and normalize items, contacts, and relationships before migration.
3. Preserve one authoritative owner for each field once integration begins.
4. Use stable identifiers rather than titles to connect records.
5. Separate execution metadata from richer contextual and historical metadata.
6. Treat recurring obligations separately from generated action instances.
7. Design boards, groups, columns, and automations only after repeated operational evidence.

## Questions to Resolve

- Which concepts require separate boards?
- Which concepts should be groups, connected-board relationships, subitems, or views?
- Should Goals, Outcomes, Opportunities, Entities, Properties, Accounts, and Obligations live in Monday or PostgreSQL?
- Which metadata must be visible in Monday for daily execution?
- How should Gmail threads, calendar events, documents, and completion evidence be referenced?
- How should duplicate contacts and overlapping Objectives be resolved?
- How should recurring work activate, suspend, terminate, and create final closure actions?
- How should personal and career work coexist without becoming tangled?

## Design Backlog

- Inventory the current boards, groups, columns, automations, and naming conventions.
- Review representative daily to-do screenshots.
- Define the minimum Action fields required in Monday.
- Evaluate an Objectives or Outcomes board.
- Evaluate an Entities or Contacts board.
- Evaluate an Opportunities board for career processes.
- Evaluate an Obligations board for recurring responsibilities.
- Define evidence and source-reference fields.
- Define stable Timmeny IDs and external-system mappings.
- Define cleanup and migration rules.

## Promotion Rule

A proposed Monday structure should be implemented only after it has been exercised in the curated operating model and shown to be useful across multiple real cases.