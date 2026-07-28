# timmeny-admin-os

An admin operating system for Brian Timmeny's personal and work workflows.

`timmeny-admin-os` is a Railway-hosted FastAPI service used by private custom GPTs. It owns the integrations, approval controls, and durable workflow rules that sit between conversational interfaces and systems of record.

The first working capability is Monday.com todo and action-item management. Monday.com is the source of truth for commitments. Gmail will become the source of communication when email workflows are added.

## Architecture

```text
Private custom GPTs
        |
        v
timmeny-admin-os API on Railway
        |
        +-- Monday.com commitments and planning metadata
        +-- Gmail communication workflows, planned
        +-- Approval controls, schedules, and background processing
```

The GPT supplies reasoning and conversation. `timmeny-admin-os` supplies controlled execution.

## API

### `GET /health`

Returns service health.

```json
{
  "status": "ok"
}
```

### `GET /admin/db-status`

Reports whether the operational database is reachable and migrated. Requires `TIMMENY_OS_API_KEY`; unlike the todo routes this endpoint refuses to serve when no key is configured.

```json
{
  "status": "ok",
  "revision": "0001_baseline",
  "detail": null
}
```

`status` is `not_configured` when `DATABASE_URL` is unset, `ok` when the schema is present, and `error` when the database is unreachable or unmigrated.

### `POST /todos`

Creates a todo item on a Monday.com board.

Request:

```json
{
  "title": "TEST - Railway",
  "list": "todo",
  "action_group": "Launch",
  "action_date": "2026-06-21",
  "action": "Decision",
  "annual_objective": "Grow Revenue",
  "initiative_project": "Partner Pipeline"
}
```

`list` is optional and defaults to `todo`. Use `gs` to create the item in the GS todo target.
`annual_objective`, `initiative_project`, `action_group`, `action_date`, and `action` are optional Monday.com column values.

Response:

```json
{
  "success": true,
  "item_id": "...",
  "title": "TEST - Railway",
  "list": "todo",
  "action_group": "Launch",
  "action_date": "2026-06-21",
  "action": "Decision",
  "annual_objective": "Grow Revenue",
  "initiative_project": "Partner Pipeline"
}
```

### `GET /todos`

Reads todo items from Monday.com.

Query parameters:

- `list`: `all`, `todo`, or `gs`. Defaults to `all`.
- `limit`: maximum items to read from each selected board. Defaults to `500`, max `500`.
- `include_done`: include items whose `Status` is `Done`, `Complete`, or `Completed`. Defaults to `false`.

By default, completed items are excluded so grouping workflows only consider open work.
Monday.com cursor pagination is used when the board has more items than one page.

Example:

```bash
curl https://timmeny-admin-os-production.up.railway.app/todos?list=all \
  -H "Authorization: Bearer $TIMMENY_OS_API_KEY"
```

Include completed items only when needed:

```bash
curl "https://timmeny-admin-os-production.up.railway.app/todos?list=all&include_done=true" \
  -H "Authorization: Bearer $TIMMENY_OS_API_KEY"
```

Response:

```json
{
  "success": true,
  "count": 2,
  "items": [
    {
      "item_id": "...",
      "title": "follow up",
      "list": "todo",
      "group_id": "...",
      "group_title": "To Do",
      "action_group": "Launch",
      "action_date": "2026-06-21",
      "action": "Decision",
      "annual_objective": "Grow Revenue",
      "initiative_project": "Partner Pipeline",
      "status": "Not Yet Started",
      "owner": "Ben",
      "due_date": "2026-07-10"
    }
  ]
}
```

### `GET /key-initiatives`

Reads open items from the `Key Initiatives` group inside the GS board. Use this as planning context before grouping or prioritizing GS todo items.

Query parameters:

- `limit`: maximum items to read. Defaults to `500`, max `500`.
- `include_done`: include items whose `Status` is `Done`, `Complete`, or `Completed`. Defaults to `false`.

Example:

```bash
curl https://timmeny-admin-os-production.up.railway.app/key-initiatives \
  -H "Authorization: Bearer $TIMMENY_OS_API_KEY"
```

### `GET /todos/metadata`

Reads the configured planning columns, allowed labels, and observed live values for a board. Use this before preparing updates to constrained Monday.com columns such as dropdown or status fields.

Query parameters:

- `list`: `todo` or `gs`. Defaults to `gs`.

Example:

```bash
curl "https://timmeny-admin-os-production.up.railway.app/todos/metadata?list=gs" \
  -H "Authorization: Bearer $TIMMENY_OS_API_KEY"
```

### `PATCH /todos/{item_id}/action-metadata`

Updates the Monday.com action metadata columns for an existing todo item.

Request:

```json
{
  "list": "gs",
  "annual_objective": "Grow Revenue",
  "initiative_project": "Partner Pipeline",
  "action_group": "Partnerships",
  "action_date": "2026-06-21",
  "action": "Decision"
}
```

At least one of `annual_objective`, `initiative_project`, `action_group`, `action_date`, or `action` is required. The board must have columns named exactly `Annual Objective`, `Initiative`, `Action Group`, `Action Date`, and `Action` when those values are used.
When `action` is `Decision`, the service also writes `Not Yet Started` to the `Status` column.

### `POST /todos/bulk-action-metadata`

Updates Monday.com action metadata columns for multiple existing todo items in one request. Use this for larger grouping passes.

Request:

```json
{
  "updates": [
    {
      "item_id": "...",
      "list": "todo",
      "annual_objective": "Grow Revenue",
      "initiative_project": "Partner Pipeline",
      "action_group": "Launch"
    },
    {
      "item_id": "...",
      "list": "gs",
      "action_group": "Partnerships",
      "action_date": "2026-06-21",
      "action": "Decision"
    }
  ]
}
```

Response:

```json
{
  "success": true,
  "updated_count": 2,
  "failed_count": 0,
  "results": [
    {
      "success": true,
      "item_id": "...",
      "list": "todo",
      "action_group": "Launch",
      "action_date": null,
      "action": null,
      "annual_objective": "Grow Revenue",
      "initiative_project": "Partner Pipeline",
      "error": null
    }
  ]
}
```

If one update fails, the service keeps processing the rest and reports the per-item error in `results`.

## Configuration

Set these environment variables:

- `MONDAY_API_TOKEN`: Monday.com API token.
- `TIMMENY_OS_API_KEY`: optional shared API key for protected clients such as a GPT Action.
- `TODO_BOARD_ID`: Monday.com board id for `To Do List`.
- `TODO_GROUP_ID`: optional Monday.com group id for regular todos.
- `GS_TODO_BOARD_ID`: Monday.com board id for `GS | Initiatives & Action Items`.
- `GS_TODO_GROUP_ID`: optional Monday.com group id for GS todos.
- `GS_KEY_INITIATIVES_GROUP_ID`: optional Monday.com group id for `Key Initiatives`. If omitted, the service matches a group named `Key Initiatives`.
- `DATABASE_URL`: optional PostgreSQL connection string for Admin OS operational state. Leave it unset to run the service exactly as before, without persistence.
- `LOG_LEVEL`: optional log level for `adminos` loggers. Defaults to `INFO`.

The organize workflow also expects these Monday.com columns on each board:

- `Annual Objective`: text, dropdown, or status column for strategic objective alignment.
- `Initiative`: text, dropdown, or status column for major initiative or project alignment.
- `Action Group`: text column for GPT-selected themes.
- `Action Date`: date column for decision/action timing.
- `Action`: dropdown or status column for labels such as `Decision`.
- `Status`: status column. Decision items are set to `Not Yet Started`.
- `Owner`: people or text column returned during board reviews when present.
- `Due Date`: date column returned during board reviews when present.

Use `.env.example` as the local template.

## Local Development

This project uses Python 3.12.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Then visit `http://localhost:8000/health`.

Run tests with:

```bash
pip install -r requirements-dev.txt
pytest
```

### Database

Persistence is optional and entirely gated on `DATABASE_URL`. With it unset, every existing route behaves as it always has and `GET /admin/db-status` reports `not_configured`.

Apply the schema with:

```bash
alembic upgrade head
```

See [docs/77 — First Slice Setup Runbook](./docs/77-First-Slice-Setup.md) for provisioning.

## Railway

Railway uses `railway.json` to start the app with Uvicorn:

```bash
uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
```

Migrations run before the new deployment goes live, via the pre-deploy command `sh scripts/migrate.sh`. The script exits successfully without doing anything when `DATABASE_URL` is unset, so a project without a database still deploys.

Add `MONDAY_API_TOKEN` and `TODO_BOARD_ID` as Railway environment variables before calling `POST /todos`.

### Railway Rename Checklist

Use this when renaming the deployed service:

1. Rename the Railway project to `timmeny-admin-os`.
2. Rename the Railway service to `timmeny-admin-os`.
3. In service networking, generate or attach the public domain:

```text
https://timmeny-admin-os-production.up.railway.app
```

4. Confirm existing variables are still present:
   - `MONDAY_API_TOKEN`
   - `TIMMENY_OS_API_KEY`
   - `TODO_BOARD_ID`
   - `GS_TODO_BOARD_ID`
   - optional group ids
5. Redeploy the latest GitHub commit.
6. Rename the custom GPT to `Timmeny Admin OS`.
7. Update the GPT Action schema server URL to the new Railway domain.
8. Test `/health`, then test a read-only action before any write.

Production health check:

```bash
curl https://timmeny-admin-os-production.up.railway.app/health
```

Production todo test:

```bash
curl -X POST https://timmeny-admin-os-production.up.railway.app/todos -H "Content-Type: application/json" -H "X-API-Key: $TIMMENY_OS_API_KEY" -d '{"title":"TEST - Railway Deploy"}'
```

Production GS todo test:

```bash
curl -X POST https://timmeny-admin-os-production.up.railway.app/todos -H "Content-Type: application/json" -H "X-API-Key: $TIMMENY_OS_API_KEY" -d '{"title":"TEST - GS Railway Deploy","list":"gs"}'
```

Production decision todo test:

```bash
curl -X POST https://timmeny-admin-os-production.up.railway.app/todos -H "Content-Type: application/json" -H "Authorization: Bearer $TIMMENY_OS_API_KEY" -d '{"title":"Decide launch owner","list":"todo","action_group":"Launch","action_date":"2026-06-21","action":"Decision"}'
```

Production bulk grouping test:

```bash
curl -X POST https://timmeny-admin-os-production.up.railway.app/todos/bulk-action-metadata -H "Content-Type: application/json" -H "Authorization: Bearer $TIMMENY_OS_API_KEY" -d '{"updates":[{"item_id":"ITEM_ID_1","list":"todo","action_group":"Launch"},{"item_id":"ITEM_ID_2","list":"gs","action_group":"Partnerships"}]}'
```

## Naming

Product/app name: `timmeny-admin-os`

Current capability name: `Timmeny ToDo`

Primary GPT name: `Timmeny Admin OS`

Current production URL:

```text
https://timmeny-admin-os-production.up.railway.app
```

The URL can be renamed later in Railway after the service/domain rename is complete. Endpoint paths should stay stable until the GPT Action schema and deployed service are updated together.

## Intent

`timmeny-admin-os` should make administrative workflows easy to trust and easy to improve. The system should favor durable structure over clever one-offs, clear records over mystery state, and small useful loops over sprawling machinery.

## Principles

- **Legible by default:** important behavior should be understandable from the repo.
- **Local-first where practical:** personal data and working context should remain portable.
- **Automation with receipts:** automated actions should leave traceable outputs and decisions.
- **Human override:** workflows should help the operator think, not hide the controls.
- **Composable pieces:** prompts, scripts, notes, and agents should be easy to replace independently.

## Repository Map

- `main.py` contains the FastAPI app.
- `adminos/` contains the coordination layer: configuration, persistence, and coordination endpoints.
- `adminos/db/migrations/` contains the Alembic migration history.
- `scripts/migrate.sh` applies migrations, and is Railway's pre-deploy command.
- `requirements.txt` defines the Python dependencies.
- `requirements-dev.txt` defines local test dependencies.
- `railway.json` configures Railway deployment.
- `docs/gpt-action-openapi.yaml` defines the GPT Action schema.
- `tests/` covers the current API surface.
- `docs/charter.md` defines the initial scope, values, and near-term direction.
- `docs/architecture.md` describes the target operating model.

## Next Steps

- Keep the current Monday todo capability stable.
- Add Gmail as the next integration behind explicit review and approval controls.
- Split large code paths into modules as the API grows beyond the first capability.
