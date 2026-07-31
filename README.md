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

Intake is the intersection of `INBOX` and the label, not the label alone, and the search also excludes snoozed mail. Archiving a thread is how the mailbox owner says they are finished with it, so archived mail stays out of scope even when it still carries the label. See [the review scope](#what-a-review-looks-at).

| Query parameter | Default | Effect |
|---|---|---|
| `limit` | 50 | Threads to scan, 1–200 |
| `prune` | `false` | Delete evidence for Gmail threads no longer in scope |

```json
{
  "scope": "inbox",
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

## The session and the playbook

A session is the whole of an admin interaction; the playbook is the order it runs in. "Good morning" opens a session, which loads the playbook in force, holds that exact revision for its whole life, reads the mailbox afresh, and states what will be worked through before working any of it. The email review is one activity of several. See [ADR-0023](docs/adr/ADR-0023-a-session-runs-a-playbook.md).

```text
playbook (configuration, one revision at a time in force)
 └── activity (email, objectives, to-dos, calendar, follow-ups, closeout)
      └── step (a capability within that activity, in configured order)

session (one run of one revision, against data read now)
 └── session activity (pending, in_progress, completed, skipped, unavailable)
      └── the email activity's review, worked by the review routes
```

### The session routes

| Request | What it does |
| --- | --- |
| `POST /session/start` | Loads the active playbook, syncs Gmail, opens a session in `proposed` and states its plan. A session already open is set aside, not resumed. `order`, `only` and `skip` name activity keys and apply to this session alone. |
| `POST /session/continue` | Resumes the session under way, and only that. `404` where there is none. |
| `GET /session/{session_id}` | Where a session stands, changing nothing. |
| `POST /session/{session_id}/begin` | Starts working the plan, from its first workable activity. |
| `POST /session/{session_id}/activities/{activity_key}/begin` | Starts one activity by name. |
| `POST /session/{session_id}/advance` | Moves on, once the activity in hand is finished. An email review holding decisions the mailbox has not seen is not finished, and the session stays where it is. |

`opening` carries Admin OS's own words and is returned by starting and continuing alone, so the orientation cannot be repeated between activities: it is not there to repeat. `plan.activities` gives the order, each activity's steps, and its state; an activity the playbook names and this service has not built reads `unavailable` with the source it will come from, rather than being hidden or counted as done.

`closeout` counts what the session did and what it still owes separately: activities completed, skipped and unavailable, items reviewed, actions verified by kind, deferred, dismissed, and `awaiting_execution` — rows decided whose action Gmail never saw. A session can have worked every activity and still owe the mailbox every write, and that is said rather than implied.

### The playbook routes

| Request | What it does |
| --- | --- |
| `GET /playbook` | The playbook a session would run right now, with its validation against the capabilities configured now. |
| `GET /playbook/revisions` | Every version it has had, newest first. |
| `GET /playbook/revisions/{revision_id}` | One version, exactly as it stood. |
| `POST /playbook/propose` | Writes down what the playbook would become, and changes nothing. Returns the effect in sentences, the order before and after, and the exact request that would confirm it. |
| `POST /playbook/revisions/{revision_id}/confirm` | Makes a proposed revision the playbook, on `confirm: true` and nothing else. |
| `POST /playbook/revisions/{revision_id}/activate` | The same operation under the name for undoing: puts an earlier revision back in force. |

The seed is `config/assistant-playbook.yaml`; after the first read the database is the playbook, because Brian changes it by asking rather than by editing YAML. A revision is `proposed`, `active`, `superseded` or `invalid`, and a proposal the playbook has moved on from is refused rather than confirmed: it was written against a revision no longer in force, so confirming it would quietly undo whatever was agreed to in between.

Validation is recomputed on every read rather than trusted from when a revision was written, because a playbook is valid against a set of capabilities and capabilities change underneath it. A revision naming a capability that has since been removed is marked `invalid` and the last revision that still works is put back, with the session saying which playbook it is actually running. Unknown activity, capability or step keys are errors naming the exact path; activities that are known and unbuilt are warnings, which is how a session can be honest about them.

A session keeps the revision it opened with. Confirming a change halfway through a morning changes the next session, not this one: rearranging a morning around a change made in the middle of it would be answering a question with a different question.

### The Monday scope

Which Monday items count as the day's work is `sources.monday` in the playbook: a board id, column ids, and the exact label texts that qualify an item. An item qualifies on any one of the filters. Nothing here has a default, and the section is absent until the board carries the labels the process means — an unconfigured scope reviews no Monday work and says so, rather than reviewing a guess. See [ADR-0024](docs/adr/ADR-0024-a-monday-scope-is-exact-or-it-does-not-look.md).

```yaml
sources:
  monday:
    board_id: "8962223984"
    match: any            # an item qualifies on either column
    filters:
      - column_id: status
        labels: ["Working on it today"]
      - column_id: color_mkq6wnv7
        labels: ["Daily"]
```

The board is read before it is queried: every column id and every label is found on the live board first, and each label is resolved to the index Monday filters by. A missing column or label stops with what was looked for and what the board has instead. This is not defensiveness for its own sake — a Monday rule naming a column that is not there matches nothing, and a filter that matches nothing returns the whole board looking exactly like an answer. For the same reason, what comes back is checked against the labels that were asked for, and a read containing an item matching neither filter is refused rather than trimmed. Nothing widens: the outcomes are the exact items or a stop with the reason.

`GET /admin/monday/scope` resolves the configured scope against the live board and counts what it takes, writing nothing — the way to find a renamed column before a morning does.

## Daily review

The daily review is what a session's email activity runs, and what "good morning" called before there were sessions. One request refreshes the mailbox, opens a review of what Gmail says now, and hands back a single capability group to work through — not an undifferentiated inbox. See [ADR-0007](docs/adr/ADR-0007-daily-review-engine.md).

```text
review (one per date, mailbox scope and revision)
 └── group (one per enabled capability, in configured order)
      └── item (one Gmail thread, with the recommendation shown for it)
           └── decision (append-only record of what a human chose)
```

### `POST /review/start`

Reviews the mailbox as it is now. Requires `TIMMENY_OS_API_KEY` and `DATABASE_URL`.

```json
{"review_date": null, "sync": true, "limit": 50, "scope": null}
```

```json
{
  "review_id": "…",
  "run_id": "…",
  "review_date": "2026-07-28",
  "revision": 1,
  "status": "in_progress",
  "state": "in_progress",
  "started_at": "2026-07-28T08:02:11Z",
  "completed_at": null,
  "abandoned_at": null,
  "evidence_refresh_at": "2026-07-28T08:02:11Z",
  "snapshot_at": "2026-07-28T08:02:11Z",
  "supersedes_review_id": null,
  "superseded": null,
  "config_version": "2026-07-28.1",
  "config_digest": "…",
  "screen_id": "tax-review-v1",
  "scope": {
    "name": "inbox",
    "mailbox": "INBOX",
    "include_snoozed": false,
    "include_archived": false,
    "include_trash": false,
    "include_spam": false,
    "include_sent": false,
    "include_drafts": false,
    "requested": false,
    "gmail_query": "-in:snoozed",
    "description": "Mail in the inbox now: archived, snoozed, trashed, spam, sent-only and draft-only threads were excluded."
  },
  "groups": [
    {"capability_key": "financial_taxes", "state": "pending", "counts": {"total": 3, "pending": 3, "remaining": 3}}
  ],
  "current_group": { "…": "the first group still needing a decision, with its screen and items" },
  "warnings": []
}
```

Identity is the review date, the mailbox scope and the revision, and calling it twice in a day is two snapshots: the second reads Gmail again and opens the next revision, and the first is set aside with its decisions, its actions and its audit intact, named in `supersedes_review_id`. `snapshot_at` says which mailbox a review is of, and unlike `evidence_refresh_at` it does not move. Gmail being unreachable is a warning, not a failure — the review still opens over the evidence already recorded. See [ADR-0022](docs/adr/ADR-0022-every-entry-reads-the-mailbox.md).

A thread settled in an earlier review does not come back, unless its content has changed since. A reply reopens a conversation; sitting in the inbox does not.

### The life of a review

A review is an object rather than a scratchpad: `review_id`, `review_date`, its scope, a `status` of `not_started`, `in_progress`, `awaiting_actions`, `completed` or `abandoned`, and the times it started, completed, was abandoned, and last read Gmail. `run_id` and `state` are the older names for the id and the status, kept because the routes are addressed by run. See [ADR-0017](docs/adr/ADR-0017-a-review-is-an-object-with-a-life.md).

| Request | What it does |
| --- | --- |
| `POST /review/start` | Reads Gmail and opens a review of what it says now. Whatever review existed — finished or half worked — is set aside rather than handed back, and named in `supersedes_review_id`. |
| `POST /review/continue` | Resumes, and only resumes. `404` where there is no review of today, `409` where today's is finished. It never opens one. |
| `POST /review/restart` | The same operation under the name for asking: "refresh mail", "check again". Abandons the review that exists — keeping its decisions, its actions, its audit and the hour it was completed at — refreshes Gmail, and opens the next revision of the same date. |

A finished review returns `restart_available: true` and a `restart_action`: the exact request that reviews the date again on refreshed mail, `{"sync": true, "scope": "inbox"}` to `POST /review/restart`. See [ADR-0021](docs/adr/ADR-0021-refreshing-mail-is-a-restart.md). A review under way carries neither: the way on from a morning half worked is to continue it.

Where the review set aside was left holding decisions the mailbox never saw, the fresh review returns `superseded` — the review's id, its standing, and a sentence saying what is owed. Those rows are real and they are in a review nobody will open again, so the new review is the last place they can be said.

Abandoning a review supersedes every action scope prepared in it, so a preparation from a review Brian has set aside can never execute, and every mutating route refuses an abandoned review outright. A restart is the only thing that produces revision 2 of a date; the calendar turning over opens revision 1 of the new one.

A review is a snapshot rather than the mailbox, so no review — finished, empty or half worked — stands between Brian and the inbox as it is now. Resuming is `POST /review/continue`, and it is his to ask for.

### How a session opens

Whatever the words that start a morning, they arrive as a start, a continue or a restart, and each of those opens with `plan.opening`: what will be worked through, that it follows the playbook Brian and the agent hold together, and that the playbook evolves by agreement rather than by itself. The text is Admin OS's, in `config/capabilities.yaml` under `opening`, so it is versioned with the workflow it describes and rendered as written rather than composed afresh each morning. See [ADR-0020](docs/adr/ADR-0020-a-session-opens-with-the-playbook.md).

`mode` is `new` where a morning is being laid out and `resumed` where one is being carried on with; a review that exists and has been shown nothing is still new, because what makes a review resumable is work in it rather than a row in a table — and agreeing the plan is work in it, since rows have been presented by then. Only `start`, `continue` and `restart` carry an opening — beginning the plan, deciding a row and reading the review back carry `null` — so it is said once on entering and cannot be repeated between groups. A completed review carries none: its prompt already says the day was worked, and promising to guide him through a review that is not happening would be a lie about the state of the world.

### The plan a review states before it works

A review says what it is going to do before it does any of it. `POST /review/start` returns a `plan` and **no** `current_group`: the ordered groups with their item counts, which of them are empty, the mailboxes left out, whether this is a new review or one being resumed, the ten steps working a group consists of, and what the review already owes the mailbox. Nothing is presented and nothing is decided until `POST /review/runs/{run_id}/plan` says to begin. See [ADR-0019](docs/adr/ADR-0019-a-review-states-its-plan.md).

```json
{"order": ["financial_taxes"], "only": null, "skip": ["admin"]}
```

`order` brings named groups to the front, `only` works those and sets the rest aside, `skip` sets named ones aside — all three about that review and nothing else, so "skip Admin today" is recorded as a decision about today rather than as a group that never existed. A group set aside is not counted among the groups remaining, and the review can complete without it. Naming a group that is not in the review is `422` rather than a silent no-op, and so is setting every group aside. Changing the order or the set-aside groups raises the plan's `version`.

Deciding a row begins the plan without being asked: working the review is a stronger answer than agreeing to work it, so a resumed review does not ask permission it has already been given. `plan.status` is `proposed` until then, and `active` afterwards.

Every response carries what the review owes, counted from the rows and their actions rather than from anything said about them:

| Count | Means |
| --- | --- |
| `decided_not_executed` | Decided and not prepared. Nothing has changed in Gmail. |
| `prepared_awaiting_confirmation` | Prepared, waiting on a confirmation that has not come. |
| `failed_or_unverified` | Attempted with no verified result. Not done. |

The same three appear per group, in `plan.groups[].standing` and in a sentence on the group itself, and the end-of-review `summary` counts completions from verified execution alone: `summary.done` is keyed by what actually happened — `archived`, `filed`, `moved_to_trash`, `replies_drafted`, `replies_sent`, `tasks_created` — and a decision never raises one of them.

### What a review looks at

The default review is the inbox, and that is a property of the query rather than a preference anyone was asked about. Intake asks Gmail for threads carrying `INBOX` *and* the capability's label, with `-in:snoozed`, and every thread is checked again against the labels it actually carries before it enters a review. Nothing archived, snoozed, trashed, spam, sent-only or draft-only reaches the table, and a thread that loses `INBOX` — because it was archived, in the review or in Gmail — is off the table by the next review.

Snoozing is the one state with no label of its own: Gmail's API publishes none, so it can only be asked about in the search language. It is excluded by `-in:snoozed`, which is a query and not a label check, and what a search proved is kept on the evidence as `snoozed` — `in:snoozed` proves a snooze, `-in:snoozed` disproves it, anything else says nothing. A review of snoozed mail admits threads on that record alone, so mail no snoozed search has ever returned is not shown as asleep merely because it is not in the inbox. See [ADR-0015](docs/adr/ADR-0015-review-mailbox-scope.md).

Every review response carries the scope it used, on the run and on each group, so "did you look at my archive?" is answered from the response rather than inferred from what came back. A row withdrawn because its thread left the scope is `deferred`, with a decision recorded by `scope:inbox` — nothing was decided about the mail, and if it returns to the inbox it is reviewable again.

Another scope happens only when it is named, and is its own run of the same day rather than an addition to the one already under way:

| `scope` | Mail | Gmail search |
|---|---|---|
| omitted or `inbox` | in the inbox now | `-in:snoozed` |
| `archived` | carries the label, left the inbox | `-in:inbox -in:snoozed` |
| `snoozed` | held back until the snooze expires | `in:snoozed` |
| `everything` | all of it, Trash and Spam included | `in:anywhere` |

A scope that does not exist is `422`. `prune` is refused outside the default scope: pruning retires everything the scan did not see, and a scan of the archive has not seen the inbox.

### `GET /review/runs/{run_id}` and `GET /review/runs/{run_id}/groups/{capability_key}`

Read the run, or one capability group with its items. Neither changes anything.

Groups are worked one at a time in configured order, but a group waiting only on execution does not hold the review up: `current_group` moves to the next group that needs a decision and returns to the outstanding actions once every group has been decided.

### `POST /review/runs/{run_id}/items/{item_id}/decision`

Records one decision: `approve` (take the recommended action), `override` (take a different one), `dismiss` (settled, and it does not come back), or `defer` (not today — it returns in tomorrow's review).

```json
{"decision": "override", "action": "move_gmail_thread_to_trash", "note": "Circular"}
```

An action may be named as it is spoken (`archive_gmail_thread`, `move_gmail_thread_to_label`, `move_gmail_thread_to_trash`) or as it is stored (`gmail.archive`, `gmail.move`, `gmail.trash`); both record the same thing, so the audit history keeps one vocabulary while the API answers in the other.

Approval is the only route to an action, and configuration decides which actions exist for a capability at all: an action it is not granted is refused with `409` however the request is phrased. An approved item is recorded as approved and **not executed**: approval creates an action in `approved`, and reaching the mailbox takes the separate steps below. A run holding unexecuted actions reports `awaiting_actions` rather than claiming completion.

A decision is not a done thing, and the review says so rather than leaving it to be inferred. The row reads `Move it to Trash — decided, not yet done` until execution has been verified; the group carries a `notice` saying nothing has changed in Gmail; the group and the run carry `outstanding_execution`, naming the exact `item_ids` waiting and the exact request that carries them out; and the review stays on that group instead of presenting the next one, because a review that has moved on reads as a review that is finished with what it moved on from. See [ADR-0018](docs/adr/ADR-0018-a-decision-is-not-a-done-thing.md).

A group is addressed by its `capability_key` — `admin`, not `admin.v2`, which is the version of what Admin recommends, and not `admin-review-v1`, which is how it is drawn. A request naming one of those is refused with the key it meant.

`override` may carry `action_params` — `add_labels` and `remove_labels` for `gmail.label`, `to`/`cc`/`subject`/`body` for `gmail.draft_reply`, and `label` for `gmail.move`, which is the folder to file the thread in and must be one the capability lists.

Approving a recommended move with no `action_params` files the thread in the folder the row named. Agreeing with a recommendation takes the action that was shown, not a version of it that has to be spelled out again.

### `POST /review/runs/{run_id}/groups/{capability_key}/decisions`

Applies one decision across a group — "archive all of these" — or across named rows, with `item_ids`, which is what "trash 2, 4 and 7" becomes. Refused with `409` where the capability sets `allow_bulk_decisions: false`.

All or nothing, and the refusal names every offending row rather than the first, because "trash 2, 4 and 7" is answered usefully only by saying which of them cannot be trashed:

```json
{
  "message": "1 of the selected items do not permit that decision, so none was recorded: itm-2.",
  "ineligible": [
    {"item_id": "itm-2", "thread_id": "18f…", "subject": "Q3 filing", "reason": "'financial_taxes' is not allowed to 'gmail.trash'."}
  ]
}
```

Nothing is recorded when a bulk request is refused, and every row that is applied keeps its own decision and audit record — a bulk request is a shorthand for many decisions, not a decision of its own.

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

## Presentation

Every review response carries the screen that renders it. Admin OS decides the columns, their order, the wording, the formatting, and which decisions are on offer; a client prints what it is given. See [ADR-0011](docs/adr/ADR-0011-presentation-contracts.md) for why, and [Daily Review GPT Instructions](docs/gpt-daily-review-instructions.md) for what that asks of the GPT. That file is the whole of what is pasted into the Daily GPT, and it is kept under the 8,000 characters that field accepts — there is one of it, because two files saying the same thing in different words is two files that disagree by next month.

```json
{
  "screen_id": "admin-review-v1",
  "screen": {
    "screen_id": "admin-review-v1",
    "kind": "table",
    "title": "Admin — today's review",
    "columns": [
      {"key": "index", "label": "#", "align": "right", "format": "number"},
      {"key": "group", "label": "Group", "align": "left", "format": "text"},
      {"key": "what_it_is", "label": "What it is", "align": "left", "format": "text"},
      {"key": "key_facts", "label": "Key Facts", "align": "left", "format": "text"},
      {"key": "recommended_action", "label": "Recommended Action", "align": "left", "format": "text"},
      {"key": "why", "label": "Why", "align": "left", "format": "text"},
      {"key": "confidence", "label": "Confidence", "align": "right", "format": "percent"},
      {"key": "decision", "label": "Decision", "align": "left", "format": "text"}
    ],
    "rows": [
      {
        "item_id": "…",
        "thread_id": "…",
        "cells": [
          "1", "Admin", "Daily Digest for July 27",
          "usps@email.informeddelivery.usps.com · 3 days ago",
          "Archive it", "A digest has nothing to act on once it has been seen.",
          "95%", "Pending"
        ],
        "actions": ["approve", "archive_gmail_thread", "move_gmail_thread_to_label", "move_gmail_thread_to_trash", "dismiss"]
      }
    ],
    "actions": [
      {
        "id": "approve",
        "label": "Do what is recommended",
        "decision": "approve",
        "action": null,
        "scope": "item",
        "method": "POST",
        "path": "/review/runs/…/items/{item_id}/decision",
        "body": {"decision": "approve"}
      }
    ],
    "footer": "1 item still need you. Answer with the row number and what to do with it.",
    "empty_text": "Nothing in Admin needs you today."
  }
}
```

`cells` are finished strings in `columns` order — a renderer prints them and adds nothing. Percentages, relative dates, truncation, the wording of an action, and what an absent value looks like (`—`) are all decided here.

Each offered action carries the exact request that records it, already filled in with the run and the capability, so a renderer needs no knowledge of the routes. Item-scoped paths contain `{item_id}`; a row's own `actions` list names the ids **that row** would accept, asked of the same check the decision endpoint runs. An item with nothing recommended does not offer `approve`; a settled item offers nothing.

Screens are versioned by id and configured next to the capabilities they render, so a layout change is a reviewable edit rather than a change of prompt:

```yaml
screens:
  - id: admin-review-v1
    title: Admin — today's review
    columns:
      - {label: "#", source: index, align: right}
      - {label: Confidence, source: confidence, format: percent, align: right}
    sort:
      - {source: received, direction: desc}
    actions:
      - {id: approve, label: Do what is recommended, decision: approve}
      - {id: archive_gmail_thread, label: Archive, decision: override, action: gmail.archive}
      - {id: move_gmail_thread_to_label, label: File it in a folder, decision: override, action: gmail.move}
      - {id: move_gmail_thread_to_trash, label: Move to Trash, decision: override, action: gmail.trash}
    footer: "{remaining_items} still need you."
```

An action that needs something said carries what, and the only answers it takes. Filing carries the capability's folders, so a renderer offers a choice rather than inviting a folder name to be typed:

```json
{
  "id": "move_gmail_thread_to_label",
  "label": "File it in a folder",
  "decision": "override",
  "action": "gmail.move",
  "body": {"decision": "override", "action": "gmail.move"},
  "params": [{"name": "label", "label": "Folder", "required": true, "choices": ["Later", "Notes"]}]
}
```

A recommended move names its folder in the cell that recommends it — "File it in Later", not "File it" — because a destination the reader cannot see is not one they can agree to.

A column names a `source` from a closed set, and a format its value type allows: a percentage of a subject line is a configuration error, not a blank cell. So is ordering by a value with no order, an unknown footer substitution, an override that names no action, a capability pointing at a screen that does not exist, a screen offering an action the capability is not allowed, or a whole-group decision offered where `allow_bulk_decisions` is false.

A screen also decides which rows exist: `rows: unresolved`, which all three shipped screens set, leaves out the threads that have been settled or already acted on, so a thread that has just been trashed does not come back to be trashed again. The rows, the footer, and the group's `counts.remaining` are read from that one list, so a table of four rows cannot be captioned "4 of 28": what dropped out is in the group's counts by state and in its action history, not in what is described as still waiting.

Three screens ship — `admin-review-v1`, `tax-review-v1`, `advisor-review-v1` — one per capability. They share a column set today; giving one its own is an edit to that screen. A compatible change edits a screen in place; an incompatible one is a new `-v2` with the capability pointed at it.

## Actions

An approval is intent. Between intent and a changed mailbox sit four states, each durable, so at every point there is an answer to what was meant to happen, what was attempted, and what Gmail actually shows. See [ADR-0010](docs/adr/ADR-0010-action-lifecycle-and-learning.md) for why, and [81 — Action Execution Runbook](docs/81-Action-Execution-Runbook.md) for the requests.

```text
approved  -> a decision, and nothing more
prepared  -> exact parameters and a stable idempotency key; still no write
executed  -> Gmail has been called
verified  -> Gmail was read back and agrees
completed -> done; re-running is a no-op
failed    -> durable, with the error, and retryable
```

Seven Gmail actions ship: `gmail.label`, `gmail.archive`, `gmail.move`, `gmail.trash`, `gmail.untrash`, and `gmail.draft_reply`, plus `gmail.send_draft`, which sends only a specific draft that has been approved by id.

The dispositions are what a review mostly does, and the API names them as they are spoken:

| Said | Sent as | Gmail call | Reversible |
|---|---|---|---|
| "archive" | `archive_gmail_thread` | `threads.modify`, removing `INBOX` | yes, the thread and its other labels are untouched |
| "file it in Later", "move it out of my inbox" | `move_gmail_thread_to_label` with `{"label": "Later"}` | `threads.modify`, adding the folder and removing `INBOX` | yes, the thread and its other labels are untouched |
| "delete", "remove", "trash" | `move_gmail_thread_to_trash` | `threads.trash` | yes, Gmail restores from Trash for 30 days |
| "undo that", "put it back" | `restore_gmail_thread_from_trash` | `threads.untrash` | it *is* the undo |

All of them act on the whole thread the row stands for, and all are verified by reading Gmail back: archive requires `INBOX` to be absent, a move requires the folder to be present *and* `INBOX` absent, Trash requires `TRASH` to be present, a restore requires it to be gone. A thread that is already in the requested state is completed without a write.

A Trash can be taken back. A group response carries `restorable` — the threads that capability trashed, each with the exact request that restores it — and the restore runs through decision, preparation, confirmation, execution, and verification like anything else. Restoring is a permission, granted to the capabilities that may Trash and to no others; nothing restores a thread unasked.

Filing is one action rather than a label followed by an archive, because the two together are what "move it" means: doing them separately can leave a thread labelled and still in the inbox when the second half fails.

A folder is chosen, never invented. Each capability lists the folders it may file in under `gmail.destinations`, the screen carries that list as the action's `choices`, and a destination outside it is refused when the decision is recorded — before anything reaches Gmail. A folder the mailbox does not actually have fails the action rather than creating a label: nothing here calls `labels.create`. See [ADR-0013](docs/adr/ADR-0013-filing-mail-in-a-named-folder.md).

**There is no permanent deletion.** `messages.delete` and `threads.delete` are not implemented, not gated: no capability, rule, or request can reach them, and a test asserts the client has no method that could.

### Scope: an execution runs the rows that were selected

A selection has to survive three requests — deciding, preparing, executing — and it does so as a persisted scope rather than as something each step re-derives. Nineteen rows chosen are nineteen rows prepared and nineteen rows run. See [ADR-0014](docs/adr/ADR-0014-execution-scope-integrity.md).

### `POST /review/runs/{run_id}/actions/prepare`

Resolves the selected approvals into exact parameters, writes nothing, and fixes the scope. Preparing twice returns the same actions, because the idempotency key is derived from the item, the action, and its parameters rather than from the attempt — but it returns a *new* scope, and supersedes the previous one.

```json
{"capability_key": "admin", "item_ids": ["item-1", "item-2", "item-3"]}
```

`item_ids` is the selection, and it is required: a request naming neither `item_ids` nor `entire_capability: true` is `400`, because a missing selection is not "all of them". Whole-capability preparation exists and must be asked for by name.

The response states the scope rather than implying it — `scope_id`, `requested_item_ids`, `prepared_item_ids`, `action_ids`, `prepared_items`, `excluded_items` with a reason each, and `scope_matches_request`. A caller that has to infer which rows a confirmation covers is a caller that will eventually infer wrongly.

### `POST /review/runs/{run_id}/actions/execute`

The only route that changes the mailbox, behind five gates: the capability must be *allowed* the action, separately *permitted to execute* it, `GMAIL_WRITE_ENABLED` must be true, the request must carry `confirm: true`, and it must name a `scope_id` that still stands.

```json
{"scope_id": "3f0c…", "confirm": true,
 "item_ids": ["item-1", "item-2", "item-3"],
 "action_ids": ["act-1", "act-2", "act-3"]}
```

It runs that scope's action ids and nothing else; there is no capability-wide execution. All four fields are required: `item_ids` and `action_ids` restate what is being confirmed, and a caller that cannot restate the scope has not read it. A request missing either is `422` and writes nothing; a restatement that disagrees stops the request instead of being reconciled with it.

A scope that was superseded by a later preparation, has already run, or whose rows were decided again since answers `409` with `ScopeMismatch`, naming the difference in both directions. Every one of those checks happens before the first Gmail call, so a `409` means nothing was written — and nothing partial was written either: a mismatch refuses the whole request rather than running the part that still agrees.

Every execution is read back from Gmail. An archive, or a move, that Gmail still lists in the inbox is `failed`, not `completed` — a write that cannot be confirmed is not a write that happened. Permission and the kill switch are rechecked at execution, not trusted from preparation, so revoking a permission stops work already approved.

### `GET /review/runs/{run_id}/scopes/{scope_id}`

What a preparation covers and whether it still stands: its state, the rows it prepared, the rows it left out and why.

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

## The GPT Action contract

The Custom GPT holds a copy of `docs/gpt-action-openapi.yaml`, and a copy says nothing about its age. So the deployment serves the contract it was built with, and states its version:

- `GET /gpt/action-schema.yaml` — the document to import into the GPT. Importing from the running service is what makes the schema and the API the same commit.
- `GET /gpt/action-schema/version` — `version`, `request_shape`, `document_sha256`, and the deployed `commit` where the platform records one.

Both are unauthenticated, because ChatGPT's import sends no headers; neither shows anything a caller could not learn from a `401`, and every operation still needs the API key.

`info.version` changes whenever a request body in the contract changes, which is the only thing that turns a stale import into a refused call. `request_shape` is a digest of every request body in the document, ignoring prose, and `tests/test_adminos_gpt_schema.py` records the fingerprint each published version carried: changing a shape without a new version fails there rather than in front of Brian.

## The MCP server: the client reads the mailbox, Admin OS owns the process

The review behind `/review/*` is built from mail Admin OS synced and classified by Gmail label. The review behind `/mcp` is the other way round: ChatGPT reads the Inbox through the Gmail app, classifies it against the playbook Admin OS publishes, and submits what it made of it. Admin OS validates, records, audits and moves the phase on, and never reads a mailbox. See [ADR-0028](./docs/adr/ADR-0028-the-client-reads-the-mailbox-and-admin-os-owns-the-process.md).

`POST /mcp` speaks JSON-RPC over HTTP — `initialize`, `tools/list`, `tools/call`, `ping` — behind the same API key as everything else, with no session to keep. `GET /mcp/tools` reads the same tool list without JSON-RPC, for checking a deployment against the instructions. Five tools, each a thin adapter over `adminos/domain/guided_review.py`:

- `start_admin_review` — always a fresh review of the mailbox as it is now, pinned to the playbook version in force. Any review still open is set aside and kept, named in `supersedes_review_id`.
- `read_review_playbook` — the groups and their order, the fields every reviewed thread must state, how to present them, when the phase is finished, and the allowed dispositions and urgencies. Configuration, from `config/review-playbook.yaml`, versioned in the same table as the session playbook.
- `read_admin_review` — where a review stands and what may be called next. No email content.
- `record_email_review` — every Inbox thread, classified into exactly one group, with the count the client read and one recommended order over all of it.
- `complete_review_phase` — finishes a phase whose result was accepted, and says what follows.

A submission is refused whole or recorded whole. Wrong playbook version, wrong scope, an unknown group, a missing required field, the same thread twice, a count that disagrees with what was sent, an item missing from the recommended order or one that is not in the review: each is named, with a code and a path, and nothing is written. `source_snapshot.thread_count` is the client's own count of what it read, checked against what it sent — the only way this arrangement can notice a thread that was read and dropped.

Gmail dispositions here are recommendations. Nothing in these tools archives, labels, trashes, replies or sends; executing anything remains the prepared-scope path above. Nothing keeps a message body, and `read_admin_review` returns no subject or sender.

Monday reconciliation, the to-do review and the daily plan are named in every review and return `unavailable`. Completing the email phase leaves the review `partially_complete`, because three quarters of the process is not built.

The instructions for the GPT that holds this connector and the Gmail app are `docs/gpt-admin-review-instructions.md`.

### Connecting ChatGPT to it

| | |
| --- | --- |
| URL | `https://timmeny-admin-os-production.up.railway.app/mcp` |
| Transport | Streamable HTTP (`POST` for every message; no session id, no server-initiated stream) |
| Authentication | The same API key as the rest of the service, as `Authorization: Bearer <TIMMENY_OS_API_KEY>` or `X-API-Key: <TIMMENY_OS_API_KEY>`. No OAuth: nothing here advertises an authorization server, and a `401` carries no `WWW-Authenticate`, so a client is not sent looking for one. |
| Content types | Send `Accept: application/json, text/event-stream`. A client that accepts a stream is answered with one SSE `message` event; a client that asks only for JSON gets JSON. Both carry the same JSON-RPC body. |
| Health | `GET /health`, which is not the MCP endpoint and is not authenticated. |

This is an MCP connector rather than a GPT Action, so there is no OpenAPI document and no `servers:` block to fill in — the URL above goes in the connector's URL field. The Action contract at `/gpt/action-schema.yaml` is the other integration and is unrelated to this one.

What the official MCP Python client sees when it connects:

```text
initialize:
  protocolVersion: 2025-06-18
  serverInfo: timmeny-admin-os 1.0.0
  capabilities.tools: listChanged=False
tools/list:
  start_admin_review     required=[]
  read_admin_review      required=['review_id']
  read_review_playbook   required=['review_id']
  record_email_review    required=['review_id', 'playbook_version_id', 'source_snapshot']
  complete_review_phase  required=['review_id']
```

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

A rule that files mail carries the folder it files into, as `action_params: {"label": "Later"}`, and is refused without one: "file this sender's mail" is not something anyone can confirm. Two folders are two rules — confirming "file KPMG mail in Financial/Taxes" leaves "file KPMG mail in Later" a separate proposal — and a rule whose folder its capability no longer lists stops recommending, without anyone editing it.

Even a promoted rule only *approves*: execution permission and the kill switch still stand between it and the mailbox, and the action it approves records `approval_kind: automatable_rule` with the rule that did it.

### What a rule may match on

Conditions are a tree — `all`, `any`, `none` — over a closed list of fields, each of which names the thing that stands behind it:

| Field | Reads | Narrows |
| --- | --- | --- |
| `subject` | the thread's subject | yes |
| `participant` | every address on the thread | yes |
| `participant_domain` | the domains of those addresses | yes |
| `snippet` | Gmail's ~200-character preview | yes |
| `gmail_label` | the labels Gmail had when the thread was last seen | no |
| `capability` | the group the item was placed in | no |
| `thread_age_days` | whole days since the newest message | no |

Operators are `equals`, `not_equals`, `contains`, `not_contains`, `starts_with`, `ends_with` and `regex` for text, and `equals`, `at_least`, `at_most` for numbers.

**There is no `body` field.** Message bodies are not stored ([ADR-0003](docs/adr/ADR-0003-gmail-access-and-retention.md)), and `snippet` says so where it is defined: a `body` that searched a preview would fail silently on every email whose point is further down.

**A rule has to narrow.** "All Inbox email", "any email containing 'money'" and "anything older than a day" are all well-formed and all refused: at least one condition must be on a narrowing field, positive, and carry four or more characters of fixed text — and in an `any` branch every alternative must, because one branch matching everything makes the rule match everything.

**Matching explains itself.** Every condition reports what it tested and what it saw — *"the subject matches the pattern Inter Institution Transfer Request [number] Will Occur in [days] Days — matched (…)"* — and named captures come back as extracted values (`{"number": "207960765", "days": "3"}`). `describes()` renders the tree as sentences from the structure, so the summary cannot drift from the rule.

An example email produces suggestions rather than a rule: `suggest_subject_conditions` offers the readings of a subject narrowest first — the exact subject, the same wording with any numbers, the opening words, the longest fixed phrase — each saying what it would catch and what it would miss. Which parts of a subject vary is a guess, and it is Brian's to make against a preview. See [ADR-0025](docs/adr/ADR-0025-a-rule-matches-on-fields-that-exist.md).

### The rulebook: a rule is a record with versions

A rule is `rules` — what it is about and where it stands — plus `rule_versions`, written once each and never edited, plus `rule_events`, everything that happened to it. The row points at the version in force; the review records the version it ran under.

```text
observed -> proposed -> tested -> confirmed -> active -> automatable
                 ^                               |
                 └──── amended ───────────┘   paused, and back
                                              retired, and not back
```

- **Confirming and activating are two acts.** Agreeing that a rule is right is not the same as putting it to work, so `confirmed` changes nothing; only `active` and `automatable` shape a review.
- **Confirming needs a test first.** `proposed` can only reach `confirmed` through `tested`: a rule nobody previewed is a rule nobody has seen the consequences of.
- **An edit is a new version, and it stands the rule down.** Amending an active rule writes version *n+1* and returns it to `proposed`, because what Brian agreed to was a version. The old version stays readable, and the event says what it was stood down from.
- **Retiring is final.** No path back, so a rule's history never has to be read as "active, unless it was retired in between".

Each rule declares a **type**, the type owns the kinds of **effect** it may carry, and every effect declares what class of thing it is — classification, display, recommendation. A rule carrying two effects of one kind is refused: that is two rules. Fourteen types are declared and four are available; the rest say why not (`todo_reminder_rule`: "Nothing in this service wakes up on a schedule, so a reminder at a time could only be a promise"), and proposing one is refused rather than stored as a promise nothing keeps.

**Constraints are the strictest reading and cannot be relaxed.** `auto_execute` is refused outright; `automation_level` is capped at 1 — classify and recommend — because levels 2 to 4 need grants that do not exist yet; and deciding, preparing, confirming and verifying are the review's safeguards, not a rule's to waive.

Before a rule is stored it is checked against the configuration it will run under: the match must narrow, a group it files into must be a group the review has, an action must be one its capability is allowed to take, and a folder must be one that capability lists. `summary` is generated from the rule at the moment it is written, so the sentences kept in history are the ones the rule actually said. See [ADR-0026](docs/adr/ADR-0026-a-rule-is-a-record-with-versions.md).

### Trying a rule, which changes nothing

`POST /rules/{id}/test` runs a rule against real mail and writes no mail: it reads retained evidence metadata, and the only thing it writes is that the test happened. Four samples — `current_snapshot` (what is in the inbox now), `historical_sample` (90 days, wherever the mail went), `selected_items` (threads you name), `synthetic_examples` (subjects you type, and every such report says it says nothing about your mailbox). `POST /rules/preview` does the same for a rule that has not been written down.

A report gives counts, every match with the conditions that matched, the values captured, and the effect **rendered with those values** — *"Transfer 207960765 lands in 3 days"*, not the template. Each matched row carries `requires_confirmation: true`, and the report carries `executed: false`, because a list of mail a rule would file reads like a list of mail that is about to be filed.

It guesses at both kinds of mistake and says they are guesses. **False positive candidates** are matches on mail reviewed under another group — the commonest sign a pattern is about the sender rather than the notice. **False negative candidates** are items that failed exactly one condition out of two or more; failing the only condition there is, is not a near miss.

Warnings are about the result rather than the rule: matched nothing, matched more than a quarter of the sample, had nothing to try against, or disagreed with the rule's own `positive_examples` / `negative_examples`. None of them blocks anything.

Testing a proposed rule is what makes it `tested`, and `tested` is the only way to `confirmed`. Testing an active rule records nothing — it is a question, not a step. See [ADR-0027](docs/adr/ADR-0027-a-rule-is-tried-before-it-is-agreed-to.md).

## Capabilities

Capabilities are data, in `config/capabilities.yaml` — not branches in code. Adding one, reordering the review, or changing what an action may do is a configuration change.

```yaml
- key: financial_taxes
  name: Financial/Taxes
  position: 20
  gmail:
    labels: [Financial/Taxes]
    mailbox: INBOX
    destinations: [Financial, Financial/Taxes, Later]
  presentation:
    screen: tax-review-v1
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
  allowed_actions: [gmail.label, gmail.archive, gmail.move, gmail.draft_reply, monday.create_task]
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
- A capability allowed `gmail.move` must list the folders it files in, and a rule may not file mail anywhere else.
- A Gmail system label — `INBOX`, `TRASH`, `SPAM` and the rest — may not be a destination: they are states, not folders.
- `allow_automatable_rules` without `allow_rule_learning` is refused: a rule cannot be promoted where none may be learned.
- No capability auto-approves a disposition: archiving, filing, and Trash are decided item by item, or by a rule that has been separately confirmed *and* promoted.
- `learning.record_message_content: true` is refused: message content is never retained ([ADR-0003](docs/adr/ADR-0003-gmail-access-and-retention.md)).
- Two capabilities may not share a position, since position is what orders the review.
- A capability may not name a screen that is not defined, and a screen may not offer more than the capability permits ([Presentation](#presentation)).

Rules read only retained metadata — subject, participants, dates — never message content, because there is none to read. The first matching rule wins; unmatched mail falls to `needs_review`.

The shipped configuration carries five rules, each written against mail the mailbox actually receives, and each recommending only what is reversible:

| Capability | Rule | Recommends |
|---|---|---|
| Admin | a USPS Informed Delivery digest | `gmail.archive` |
| Admin | a GitHub notification more than a week old | `gmail.archive` |
| Financial/Taxes | a tax form or an authority's acknowledgement — 1099, FBAR, IRS Direct Pay, Buzón Tributario | file in `Financial/Taxes` |
| Financial/Taxes | a KPMG statement or document notice | file in `Financial/Taxes` |
| Career | an expert network asking for a survey rather than a call | file in `Career - Advisory/Expert Survey` |

The filing rules deliberately match records rather than requests: mail that asks for something — a fee proposal, a missing-information request, an availability request for a call — matches none of them and stays in the inbox, because filing answers nothing and out of the inbox is how an obligation is missed. Everything else arrives as `needs_review`. No capability auto-approves anything, so a matched rule still waits for a decision.

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
- `ASSISTANT_PLAYBOOK_PATH`: optional path to the playbook a fresh database is seeded from. Defaults to `config/assistant-playbook.yaml`. Once seeded, the database is the playbook and this is not read again.
- `REVIEW_PLAYBOOK_PATH`: optional path to the review playbook the MCP tools seed from. Defaults to `config/review-playbook.yaml`, and likewise is not read again once seeded.
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
- `config/assistant-playbook.yaml` seeds the playbook a session runs, after which the database holds it.
- `config/review-playbook.yaml` seeds the review playbook the MCP tools publish: the phases, the groups and their order, and what every reviewed thread must state.
- `adminos/mcp/` is the MCP transport: JSON-RPC, tool schemas, and adapters that call the same services the API does.
- `adminos/db/migrations/` contains the Alembic migration history.
- `scripts/migrate.sh` applies migrations, and is Railway's pre-deploy command.
- `requirements.txt` defines the Python dependencies.
- `requirements-dev.txt` defines local test dependencies.
- `railway.json` configures Railway deployment.
- `docs/gpt-action-openapi.yaml` defines the GPT Action schema, served at `/gpt/action-schema.yaml`.
- `docs/gpt-admin-review-instructions.md` instructs the GPT that holds the Gmail app and the Admin OS MCP connector.
- `tests/` covers the current API surface.
- `docs/charter.md` defines the initial scope, values, and near-term direction.
- `docs/architecture.md` describes the target operating model.

## Next Steps

- Keep the current Monday todo capability stable.
- Add Gmail as the next integration behind explicit review and approval controls.
- Split large code paths into modules as the API grows beyond the first capability.
