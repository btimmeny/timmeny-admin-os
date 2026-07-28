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

### `GET /admin/capabilities`

Reports the capability configuration the service is running on, including the digest recorded against every review run. Requires `TIMMENY_OS_API_KEY`. See [Capabilities](#capabilities).

### `GET /admin/gmail/labels`

Lists the mailbox's label names, so `config/capabilities.yaml` can name them exactly. Requires `TIMMENY_OS_API_KEY` and the Gmail credentials.

### `GET /admin/gmail/status`

Reports whether Gmail is configured and whether each enabled capability's labels resolve. Requires `TIMMENY_OS_API_KEY`. `configured` is false unless all three of `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, and `GMAIL_REFRESH_TOKEN` are set.

```json
{
  "configured": true,
  "write_enabled": false,
  "labels": [
    {"capability_key": "financial_taxes", "label": "Financial/Taxes", "found": true}
  ],
  "detail": null
}
```

### `POST /admin/gmail/sync`

Records threads that are **in the inbox and carry an enabled capability's label** as evidence, attributed to that capability. Requires `TIMMENY_OS_API_KEY`, `DATABASE_URL`, and the Gmail credentials.

Read-only with respect to both Gmail and Monday.com: no labels change, no mail is archived, and no task is created. Classification and task creation are separate steps.

Intake is the intersection of `INBOX` and the label, not the label alone. Archiving a thread is how the mailbox owner says they are finished with it, so archived mail stays out of scope even when it still carries the label.

| Query parameter | Default | Effect |
|---|---|---|
| `limit` | 50 | Threads to scan, 1–200 |
| `prune` | `false` | Delete evidence for Gmail threads no longer in scope |

```json
{
  "labels": ["Financial/Taxes", "Admin"],
  "scanned": 12,
  "created": 3,
  "updated": 1,
  "unchanged": 8,
  "removed": 0,
  "warnings": []
}
```

Evidence is keyed by thread, so re-running the sync updates existing rows rather than duplicating them. A thread carrying two capabilities' labels is recorded once and appears in both reviews.

A label that does not exist in the mailbox is reported in `warnings` rather than failing the sync, so one mistyped label cannot stop the other capabilities from being reviewed.

`prune` returns `409` when the scan filled `limit` or a label failed to resolve: an incomplete listing cannot distinguish a thread that was archived from one that is simply further down the page, and pruning on that basis would delete everything past the first page.

### `POST /admin/classify`

Classifies evidence that the current classifier version has not seen. Requires `TIMMENY_OS_API_KEY` and `DATABASE_URL`.

Classifier `v1-review-all` makes no inference: every thread is recorded as `needs_review` with zero confidence and an `undetermined` relationship, per [ADR-0004](docs/adr/ADR-0004-classification-boundary-and-review.md). No Monday task is created and no mailbox is touched.

```json
{
  "classifier_version": "v1-review-all",
  "scanned": 1,
  "created": 1,
  "unchanged": 0
}
```

One classification per (evidence, classifier version), enforced by a unique constraint, so re-running changes nothing.

### `GET /admin/review-queue`

Lists the evidence awaiting a human decision, newest first. Requires `TIMMENY_OS_API_KEY` and `DATABASE_URL`. `limit` (query, 1–200, default 50) bounds the page.

```json
{
  "count": 1,
  "items": [
    {
      "classification_id": "…",
      "evidence_id": "…",
      "source_thread_id": "197b351c69d3613f",
      "subject": "KPMG Activities",
      "received_at": "2025-06-27T14:02:11+00:00",
      "disposition": "needs_review",
      "rationale": "Classifier v1 asserts nothing about intake threads and routes every one to human review."
    }
  ]
}
```

An item leaves the queue when `POST /admin/monday/tasks` creates a verified Monday task from it. The classification is not rewritten: it records what the classifier inferred, and the workflow run records what was done about it.

### `GET /admin/monday/board`

Reads the To Do List board (`TODO_BOARD_ID`). Requires `TIMMENY_OS_API_KEY` and `MONDAY_API_TOKEN`. Both filters are applied by Monday, not after paging, so a filtered read is the whole answer rather than the first page of one.

| Parameter | Meaning |
|---|---|
| `filter` | `open` (default), `done`, or `all`. An item with no status counts as open. |
| `contains` | Case-insensitive substring of the item name. |
| `limit` | 1–1500, default 200. |

```json
{
  "board_id": "8962223984",
  "filter": "open",
  "contains": "KPMG",
  "count": 1,
  "items": [
    {
      "item_id": "11002016029",
      "name": "Annual Taxes | KPMG",
      "group": "Tasks | Action Items",
      "status": "Not Yet Started",
      "admin_os_id": null,
      "action_date": null
    }
  ]
}
```

### `POST /admin/monday/duplicate-check`

Ranks existing board items against proposed task titles, so a candidate can be reviewed against what the board already holds before anything is created. Reads only; see [ADR-0005](docs/adr/ADR-0005-duplicate-review-before-monday-writes.md).

Request. `titles` takes up to 20 candidates; `filter` defaults to `all` because completed work answers the recurring-obligation case ("you filed this last year").

```json
{
  "titles": ["Annual Taxes | KPMG", "Renew the car registration"],
  "filter": "all",
  "match_limit": 5,
  "threshold": 0.45
}
```

Response. `score` runs 0–1; at or above `strong_match_score` the candidate almost certainly already exists. A score of 1.0 means the two titles use the same words, in any order. A title wholly contained in a longer one caps at 0.95 — a strong candidate, but not the same task.

```json
{
  "board_id": "8962223984",
  "filter": "all",
  "compared": 1047,
  "strong_match_score": 0.75,
  "reports": [
    {
      "title": "Annual Taxes | KPMG",
      "normalized_title": "annual taxes kpmg",
      "has_strong_match": true,
      "matches": [
        {
          "item_id": "11002016029",
          "name": "Annual Taxes | KPMG",
          "status": "Not Yet Started",
          "group": "Tasks | Action Items",
          "admin_os_id": null,
          "score": 1.0,
          "is_done": false,
          "is_strong": true
        }
      ]
    }
  ]
}
```

### `POST /admin/monday/tasks`

Creates one To Do List item from one piece of evidence and verifies it landed. The only route in `adminos/` that writes to Monday. Requires `TIMMENY_OS_API_KEY`, `MONDAY_API_TOKEN`, and `DATABASE_URL`; `TODO_GROUP_ID` is optional and picks the group.

```json
{
  "evidence_id": "…",
  "title": "Taxes | Confirm KPMG scope for 2026",
  "action_date": "2026-08-15",
  "confirmed": false
}
```

The approval gate, per [ADR-0006](docs/adr/ADR-0006-approval-gate-and-verified-writes.md): a task is created unprompted only when the classification is fully confident **and** the duplicate check finds nothing resembling the title. Anything else returns `409` with the duplicate report and creates nothing. `confirmed: true` is a human overriding that refusal. Classifier v1 has zero confidence on everything, so today every task needs confirmation.

```json
{
  "run_id": "…",
  "operational_object_id": "…",
  "admin_os_id": "…",
  "item_id": "12345",
  "board_id": "8962223984",
  "title": "Taxes | Confirm KPMG scope for 2026",
  "adopted": false,
  "confirmed": true,
  "verified": true,
  "duplicates": { "…": "the report the decision was made on" }
}
```

`admin_os_id` is written into the board's Admin OS ID column and reserved in Postgres *before* Monday is called, so a retry finds and adopts the existing item (`adopted: true`) rather than creating a second one. `verified` means the item was read back and its board, name, and Admin OS ID all match what was asked for.

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

## Daily review

The daily review is what "good morning" calls. One request refreshes the mailbox, opens or resumes today's review, and hands back a single capability group to work through — not an undifferentiated inbox. See [ADR-0007](docs/adr/ADR-0007-daily-review-engine.md).

```text
run (one per day)
 └── group (one per enabled capability, in configured order)
      └── item (one Gmail thread, with the recommendation shown for it)
           └── decision (append-only record of what a human chose)
```

### `POST /review/start`

Starts or resumes today's review. Requires `TIMMENY_OS_API_KEY` and `DATABASE_URL`.

```json
{"review_date": null, "sync": true, "limit": 50}
```

```json
{
  "run_id": "…",
  "review_date": "2026-07-28",
  "state": "in_progress",
  "config_version": "2026-07-28.1",
  "config_digest": "…",
  "groups": [
    {"capability_key": "financial_taxes", "state": "pending", "counts": {"total": 3, "pending": 3}}
  ],
  "current_group": { "…": "the first group still needing a decision, with its items" },
  "warnings": []
}
```

Identity is the review date, so calling it twice in a day resumes rather than restarts: decisions already made are kept and only mail that has arrived since is added. Gmail being unreachable is a warning, not a failure — the review still opens over the evidence already recorded.

A thread settled in an earlier review does not come back, unless its content has changed since. A reply reopens a conversation; sitting in the inbox does not.

### `GET /review/runs/{run_id}` and `GET /review/runs/{run_id}/groups/{capability_key}`

Read the run, or one capability group with its items. Neither changes anything.

Groups are worked one at a time in configured order, but a group waiting only on execution does not hold the review up: `current_group` moves to the next group that needs a decision and returns to the outstanding actions once every group has been decided.

### `POST /review/runs/{run_id}/items/{item_id}/decision`

Records one decision: `approve` (take the recommended action), `override` (take a different one), `dismiss` (settled, and it does not come back), or `defer` (not today — it returns in tomorrow's review).

```json
{"decision": "override", "action": "gmail.archive", "note": "Superseded"}
```

Approval is the only route to an action, and configuration decides which actions exist for a capability at all: an action it is not granted is refused with `409` however the request is phrased. An approved item is recorded as approved and **not executed**: approval creates an action in `approved`, and reaching the mailbox takes the separate steps below. A run holding unexecuted actions reports `awaiting_actions` rather than claiming completion.

`override` may carry `action_params` — `add_labels` and `remove_labels` for `gmail.label`, `to`/`cc`/`subject`/`body` for `gmail.draft_reply`.

### `POST /review/runs/{run_id}/groups/{capability_key}/decisions`

Applies one decision across a group — "archive all of these". Refused with `409` where the capability sets `allow_bulk_decisions: false`. All or nothing: if the decision is not permitted for one item, none of them is decided.

### `POST /review/runs/{run_id}/items/{item_id}/assessment`

Records the model's reading of a thread: a category the capability recognises, a confidence, and at most a *suggested* action.

```json
{
  "category": "obligation",
  "confidence": 0.9,
  "rationale": "The adviser asked for a filing date.",
  "model_version": "gpt-…",
  "recommendation": "monday.create_task"
}
```

A suggestion is adopted as the item's recommendation only when it clears the capability's `min_ai_confidence`; below that it is recorded as unadopted, with the reason. A suggestion the capability is not allowed to act on is refused. An assessment never decides, approves, or executes anything.

## Actions

An approval is intent. Between intent and a changed mailbox sit four states, each durable, so at every point there is an answer to what was meant to happen, what was attempted, and what Gmail actually shows. See [ADR-0010](docs/adr/ADR-0010-action-lifecycle-and-learning.md).

```text
approved  -> a decision, and nothing more
prepared  -> exact parameters and a stable idempotency key; still no write
executed  -> Gmail has been called
verified  -> Gmail was read back and agrees
completed -> done; re-running is a no-op
failed    -> durable, with the error, and retryable
```

Three Gmail actions ship: `gmail.label`, `gmail.archive`, and `gmail.draft_reply`. `gmail.send_draft` exists but sends only a specific draft that has been approved by id. **There is no permanent deletion**: `gmail.trash` has no executor and no capability is granted it.

### `POST /review/runs/{run_id}/actions/prepare`

Resolves approvals into exact parameters and writes nothing. Preparing twice returns the same action, because the idempotency key is derived from the item, the action, and its parameters rather than from the attempt.

### `POST /review/runs/{run_id}/actions/execute`

The only route that changes the mailbox, behind four gates: the capability must be *allowed* the action, separately *permitted to execute* it, `GMAIL_WRITE_ENABLED` must be true, and the request must carry `confirm: true`.

```json
{"confirm": true, "capability_key": "admin"}
```

Every execution is read back from Gmail. An archive that Gmail still lists in the inbox is `failed`, not `completed` — a write that cannot be confirmed is not a write that happened. Permission and the kill switch are rechecked at execution, not trusted from preparation, so revoking a permission stops work already approved.

### `GET /review/runs/{run_id}/actions` and `GET …/actions/{action_id}`

Every action with its state, verification, attempts, and last error; the single-action route adds the full event trail — `approved`, `prepared`, `execution_started`, `executed`, `verified`, and any failure — in order.

### `POST /review/runs/{run_id}/actions/{action_id}/retry` and `…/verify`

Retry re-attempts a failed action, checking first whether the effect already landed: a draft created just before a connection dropped is adopted rather than written twice. Verify only re-reads Gmail.

### `POST /review/runs/{run_id}/items/{item_id}/send-draft`

Creating a draft never sends it. Sending takes an explicit approval of one exact draft, named by both its draft id and the message id it carried when it was read back:

```json
{"draft_id": "r-123", "draft_message_id": "msg-456", "confirm": true}
```

And approving still does not send: it creates a `gmail.send_draft` action that has to be executed like any other, and that refuses if the draft has changed since it was approved.

## Learning

A correction is evidence, not an instruction. Every decision that answers a recommendation is recorded as a learning event — with the metadata the decision turned on, the actor, the policy version, and the provenance, and never message content — and no event changes what the review recommends.

A rule reaches autonomy only by being walked through five states, each an explicit act:

```text
observed    -> a correction was seen; the review is unchanged
proposed    -> written down in full, and still inactive
confirmed   -> recommends
automatable -> may approve without being asked
retired     -> neither, permanently
```

- `GET /learning/events` — what the review has been taught, filterable by capability and kind.
- `GET|POST /learning/rules` — read every candidate rule, or propose one. Conditions are exact and metadata-only, and a rule with no condition is refused because it would match everything.
- `GET /learning/rules/{rule_id}` — the exact conditions and the single action, before agreeing to either.
- `POST /learning/rules/{rule_id}/confirm` — activate it for recommendations. Agreeing with a rule does not license it to run unattended.
- `POST /learning/rules/{rule_id}/promote` — the narrowest grant in the system, and it needs `confirm: true`. Only a promoted rule may approve without being asked, and the capability must set `learning.allow_automatable_rules`.
- `POST /learning/rules/{rule_id}/retire` — stops it recommending and acting, for good.

Even a promoted rule only *approves*: execution permission and the kill switch still stand between it and the mailbox, and the action it approves records `approval_kind: automatable_rule` with the rule that did it.

## Capabilities

Capabilities are data, in `config/capabilities.yaml` — not branches in code. Adding one, reordering the review, or changing what an action may do is a configuration change.

```yaml
- key: financial_taxes
  name: Financial/Taxes
  position: 20
  gmail:
    labels: [Financial/Taxes]
    require_inbox: true
  playbook:
    id: evidence_to_obligation
    steps: [collect_evidence, recommend, await_decision, prepare_actions, execute_approved, verify]
  recommendation_policy:
    version: taxes.v1
    categories: [filing_obligation, payment_due, advisor_request, reference]
    rules:
      - id: adviser_asks_for_something
        when: {participant_domains: [kpmg.com]}
        recommend: monday.create_task
        confidence: 0.9
        rationale: The adviser is asking for something.
  allowed_actions: [gmail.label, gmail.archive, gmail.draft_reply, monday.create_task]
  approval:
    auto_approve: []
    allow_bulk_decisions: true
  execution:
    permitted_actions: [gmail.label, gmail.draft_reply]
    require_verification: true
  learning:
    allow_rule_learning: true
    allow_automatable_rules: false
```

Two grants govern an action, deliberately separately. `allowed_actions` is what may be *approved*; `execution.permitted_actions` is what may actually *reach the mailbox*. The capability above may approve an archive and never carry one out — useful while a new action is being trusted, and the reason a mistake in one grant does not become a write.

The file is validated on load and rejected outright — with `503` rather than a silently empty review — if it is wrong. Some refusals are deliberate:

- A rule may not recommend an action the capability is not granted.
- The default recommendation may not be an action, or unmatched mail would be acted on by omission.
- A capability may not auto-approve an action it is not allowed to take.
- A capability may not execute an action it may not approve.
- `allow_automatable_rules` without `allow_rule_learning` is refused: a rule cannot be promoted where none may be learned.
- No capability is granted `gmail.trash`, and there is no code to perform it.
- `learning.record_message_content: true` is refused: message content is never retained ([ADR-0003](docs/adr/ADR-0003-gmail-access-and-retention.md)).
- Two capabilities may not share a position, since position is what orders the review.

Rules read only retained metadata — subject, participants, dates — never message content, because there is none to read. The first matching rule wins; unmatched mail falls to `needs_review`.

The shipped configuration carries two rules, both on Admin, both narrow and both recommending only `gmail.archive`: a USPS Informed Delivery digest, and a GitHub notification more than a week old. Everything else — all of Financial/Taxes, all of Career — arrives as `needs_review`. No capability auto-approves anything, so a matched rule still waits for a decision.

Rules are the place to encode a pattern once it has been seen often enough to be worth stating, which is what the [learning](#learning) routes are for.

Every run records both the configuration version and a digest of the file, so a decision made months ago can be explained against the exact configuration that produced it.

Three capabilities ship, reviewed in this order and named exactly as the mailbox names them: `Admin`, `Financial/Taxes`, then `Career - Advisory/Expert Calls`. All three were resolved against the live mailbox with `GET /admin/gmail/labels`, which is the way to check after any label is renamed. Matching is case-insensitive, and a label that does not resolve is reported as a warning rather than a failure, so a wrong name shows up as an empty group.

Gmail nesting is not hierarchy: `Admin/- Meetings` and `Admin/spam & junk` are separate labels, and a thread in one of them does not carry `Admin`. Sub-labels are included only by naming them.

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
- `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`: optional Gmail OAuth credentials. All three must be present; a partially configured environment counts as unconfigured. See `docs/77-First-Slice-Setup.md`.
- `CAPABILITIES_PATH`: optional path to the capability configuration. Defaults to `config/capabilities.yaml`, which is what defines the Gmail labels in scope. See [Capabilities](#capabilities).
- `GMAIL_WRITE_ENABLED`: whether Gmail writes are permitted. Defaults to `false`.
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
- `adminos/adapters/` contains clients for external systems.
- `adminos/domain/` contains domain logic: evidence recording, the review state machine, the action lifecycle, and rule learning.
- `adminos/capabilities/` loads and validates the capability configuration.
- `config/capabilities.yaml` defines the capabilities the daily review presents.
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
