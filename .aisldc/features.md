# Timmeny-ToDo-OS Feature Overview

## Product Summary

Timmeny-ToDo-OS is a personal task-management integration that lets a conversational client, such as a custom GPT, read and organize action items stored in Monday.com. It provides a small, explicit API between the user-facing assistant and two Monday.com boards:

- a general `todo` board for personal or general action items;
- a `gs` board for GS initiatives and action items.

Monday.com is the source of truth. The service does not maintain its own task database.

## Current Capabilities

### 1. Service Health

The service exposes a health endpoint for deployment monitoring and availability checks.

- `GET /health`
- Returns `{"status": "ok"}` when the application is running.
- Used by Railway as the deployment health check.

### 2. Todo Capture

The service can create a task on either configured Monday.com board.

- `POST /todos`
- Routes the item using `list=todo` or `list=gs`.
- Defaults to the general `todo` board when no list is supplied.
- Supports an optional configured Monday.com group for each board.
- Trims task titles and rejects empty titles.
- Limits task and metadata text fields to 255 characters.

A task can be created with any of the following planning metadata:

- Annual Objective
- Initiative
- Action Group
- Action Date
- Action

When `Action` is `Decision`, the service also sets `Status` to `Not Yet Started`.

### 3. Todo Review

The service can read tasks from one or both boards.

- `GET /todos`
- Supports `list=all`, `list=todo`, and `list=gs`.
- Returns the item ID, title, board/list, Monday.com group, and available planning metadata.
- Returns `Status`, `Owner`, and `Due Date` when those columns exist.
- Excludes items with a status of `Done`, `Complete`, or `Completed` by default.
- Can include completed items when `include_done=true`.
- Supports cursor-based Monday.com pagination.
- Accepts a limit from 1 through 500 for each selected board.

### 4. GS Key-Initiative Context

The service provides a dedicated view of open work in the GS board's `Key Initiatives` group.

- `GET /key-initiatives`
- Finds the group by its configured ID when `GS_KEY_INITIATIVES_GROUP_ID` is set.
- Otherwise finds the group by the case-insensitive title `Key Initiatives`.
- Excludes completed items by default.
- Returns planning, ownership, status, and due-date context.

This endpoint lets an assistant use the user's current key initiatives when reviewing, grouping, or prioritizing GS action items.

### 5. Board-Metadata Discovery

The service can inspect a board before proposing or writing planning values.

- `GET /todos/metadata`
- Supports either the `todo` or `gs` board.
- Discovers configured Monday.com planning columns by title.
- Returns each column's ID, title, and Monday.com type.
- Extracts configured labels from status and dropdown column settings.
- Reports values currently observed on live items.

This makes it possible for an assistant to use valid Monday.com labels instead of inventing values that a constrained column will reject.

### 6. Single-Item Organization

The service can update planning metadata on an existing task.

- `PATCH /todos/{item_id}/action-metadata`
- Supports Annual Objective, Initiative, Action Group, Action Date, and Action.
- Requires at least one metadata field.
- Adapts values to the destination column type:
  - text columns receive strings;
  - date columns receive Monday.com's date representation;
  - dropdown columns receive label arrays;
  - status columns receive a status label.
- Applies the decision-status behavior when `Action=Decision`.
- Fails explicitly when the required Monday.com column does not exist.

### 7. Bulk Organization

The service can update as many as 100 tasks in one request.

- `POST /todos/bulk-action-metadata`
- Supports items from both boards in the same batch.
- Reads each board's columns once per request and reuses that metadata.
- Processes items independently.
- Continues after an individual item fails.
- Returns overall counts and a success or error result for every item.
- Sets overall `success=false` when any item fails.

This supports conversational grouping and classification passes while preserving per-item receipts.

### 8. GPT Action Integration

The repository contains an OpenAPI 3.1 definition for exposing the service as GPT Actions.

The action surface includes:

- `listTodos`
- `createTodo`
- `getTodoMetadata`
- `listKeyInitiatives`
- `updateTodoActionMetadata`
- `bulkUpdateTodoActionMetadata`

The repository also includes GS-specific GPT instructions that define how an assistant should:

- read live Monday.com data before making recommendations;
- use Key Initiatives and durable GS knowledge as planning context;
- retain item IDs in recommendations;
- verify board metadata before constrained writes;
- show bulk update payloads and obtain confirmation before broad writes;
- avoid changing completed work unless explicitly asked.

### 9. Durable GS Planning Context

The GS knowledge file captures longer-lived context that helps the assistant interpret live tasks, including:

- annual objectives;
- top priorities;
- current initiatives and projects;
- stakeholders;
- organizational processes;
- classification guidance.

This file is context rather than live state. Current tasks and Key Initiatives are always read from Monday.com.

### 10. API Access Control

The service supports an optional shared API key.

- The server reads `TIMMENY_OS_API_KEY`.
- Clients can send the key using `X-API-Key` or `Authorization: Bearer`.
- When the server key is configured, missing or incorrect credentials return HTTP 401.
- When it is not configured, API-key enforcement is disabled.
- The Monday.com token remains server-side and is never part of the client request contract.

### 11. Validation and Failure Reporting

The API uses Pydantic request and response models to validate:

- board/list names;
- required fields;
- string lengths;
- blank values;
- date formats;
- bulk request size.

Integration failures are translated into clear HTTP responses:

- `401` for missing or invalid client credentials;
- `422` for invalid inputs or empty update requests;
- `500` for missing server configuration;
- `502` for Monday.com HTTP, GraphQL, malformed-response, or board-schema failures;
- `504` for Monday.com timeouts.

### 12. Deployment and Verification

The service is designed for Python 3.12 and Railway.

- FastAPI provides the HTTP application and generated API documentation.
- Uvicorn runs the production process.
- Railway builds with Nixpacks.
- Railway checks `GET /health`.
- The service has no database or persistent local storage.
- Pytest coverage exercises health, authentication, board routing, item creation, reads, pagination, completion filtering, metadata discovery, single updates, bulk updates, GS context, decision behavior, and error handling.

## Primary User Workflows

### Capture a Task

1. The user describes a task conversationally.
2. The GPT selects the general or GS list.
3. The GPT calls `createTodo`.
4. Timmeny-ToDo-OS creates the item in Monday.com.
5. The created item ID is returned as the receipt.

### Review Open Work

1. The GPT calls `listTodos`.
2. Timmeny-ToDo-OS reads the selected Monday.com board or boards.
3. Completed work is filtered unless explicitly requested.
4. The GPT summarizes live work using the returned planning context.

### Organize GS Work

1. The GPT reads open GS tasks.
2. The GPT reads open Key Initiatives.
3. The GPT reads GS board metadata.
4. The GPT compares live items with Key Initiatives and the GS knowledge file.
5. The GPT proposes classifications and retains each live item ID.
6. The user confirms the exact update payload.
7. The GPT applies the changes through the bulk endpoint.
8. The service returns an item-by-item result.

### Record a Decision

1. The user or assistant identifies a decision that should be tracked.
2. After confirmation, the GPT creates a GS task with `Action=Decision`.
3. The service writes the decision metadata and sets `Status=Not Yet Started`.

## Explicit Product Boundaries

The current system does not:

- store tasks independently of Monday.com;
- provide a web or mobile user interface;
- schedule reminders or recurring jobs;
- synchronize email, calendar, or other task platforms;
- delete, archive, or mark tasks complete;
- modify titles, owners, due dates, or Monday.com groups after creation;
- maintain user accounts, roles, sessions, or OAuth connections;
- cache Monday.com data;
- run background workers;
- automatically update the GS knowledge file;
- implement the broader knowledge-management and automation ambitions described in the charter.

These boundaries are important: the shipped product is currently a stateless, conversationally accessible Monday.com task gateway with GS planning support.

