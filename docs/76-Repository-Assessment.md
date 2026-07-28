# 76 — Repository Assessment for the First Vertical Slice

**Status:** Assessment complete, implementation not started
**Version:** 0.1
**Date:** 2026-07-28
**Purpose:** Documents the current repository state, the gap to [75 — First Vertical Slice](./75-First-Vertical-Slice.md), and the proposed staged implementation.
**Depends on:** [ADR-0001](./adr/ADR-0001-admin-os-coordination-layer.md), [ADR-0002](./adr/ADR-0002-monday-identity-and-idempotency.md), [ADR-0003](./adr/ADR-0003-gmail-access-and-retention.md), [50 — Architecture](./50-Architecture.md), [60 — Domain Model](./60-Domain-Model.md), [70 — Implementation Strategy](./70-Implementation-Strategy.md), [80 — Monday.com Architecture](./80-Monday-Architecture.md), [90 — Roadmap](./90-Roadmap.md)

Assessed commit: `c65a4fe` on `main`.

---

## 1. Current-State Architecture

The runtime is a single stateless FastAPI module that translates a small, GPT-shaped HTTP contract into Monday.com GraphQL calls. There is no database, no background processing, no Gmail access, and no workflow state.

```text
Custom GPT
  -> docs/gpt-action-openapi.yaml (Action contract, bearer key)
  -> https://timmeny-admin-os-production.up.railway.app   [verified reachable: /health -> {"status":"ok"}]
  -> main.py (FastAPI app, 1,340 lines, version "0.4.1")
  -> httpx -> https://api.monday.com/v2 (GraphQL)
  -> To Do List board / GS Initiatives & Action Items board
```

**Entry point and module structure.** `main.py` at the repository root is the only runtime module. It holds route handlers, Pydantic contracts, the Monday adapter, column translation, formatting helpers, auth, and error mapping. There is no package, no `src/`, no `app/`. Railway starts it with `uvicorn main:app`.

**Routes (12).**

| Method | Path | Purpose | In GPT Action schema |
|---|---|---|---|
| GET | `/health` | liveness; no auth check | yes |
| GET | `/todos` | read items from one or both boards | no |
| GET | `/todos/read-simple` | same read, flattened to `result_text` | yes (`listTodos`) |
| GET | `/todos/metadata` | planning columns, allowed labels, observed values | no |
| GET | `/todos/metadata/read-simple` | flattened metadata | yes (`getTodoMetadata`) |
| GET | `/key-initiatives` | GS board `Key Initiatives` group | no |
| GET | `/key-initiatives/read-simple` | flattened | yes (`listKeyInitiatives`) |
| POST | `/todos` | create an item | yes (`createTodo`) |
| PATCH | `/todos/{item_id}/action-metadata` | update planning columns | yes (`updateTodoActionMetadata`) |
| POST | `/todos/bulk-action-metadata` | typed bulk update | no |
| POST | `/todos/bulk-action-metadata-json` | lenient bulk update (array / `updates` / `updates_json`) | no |
| POST | `/todos/bulk-action-metadata-simple` | bulk update, counts-only response | yes (`bulkUpdateTodoActionMetadata`) |

The `read-simple` and `-simple` variants exist to give the GPT pre-formatted, low-hallucination text. That pattern should be preserved for anything new the GPT calls.

**Authentication.** `verify_api_key()` is called explicitly at the top of every protected handler. It accepts `X-API-Key` or `Authorization: Bearer`. It **fails open**: if `TIMMENY_OS_API_KEY` is unset in the environment, every endpoint is public. There is no middleware, no dependency injection, no authorization model, and no per-client identity.

**Monday connector.** Implemented as free functions over `httpx.AsyncClient(timeout=15.0)`:

- `get_monday_items` — `items_page` with cursor pagination, then `attach_monday_column_metadata` joins column titles onto values
- `get_next_monday_items_page`
- `get_board_columns_by_title` — runtime discovery of column id/type/`settings_str`
- `create_monday_item` — `create_item`
- `update_monday_item_columns` — `change_multiple_column_values`
- `execute_monday_graphql` — transport, timeout/HTTP/JSON/GraphQL error mapping to 502/504

Column selection is by **exact column title** (`Action Group`, `Action Date`, `Action`, `Annual Objective`, `Initiative`, `Status`, `Owner`, `Due Date`). A missing column is a hard 502 rather than a silent drop. Completion is inferred from the `Status` text against `{"done", "complete", "completed"}`.

**Railway.** `railway.json`: NIXPACKS builder, `uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}`, healthcheck `/health` with a 100s timeout. `runtime.txt` pins `python-3.12`. No database service is referenced anywhere in the repository.

**Environment variables.** `MONDAY_API_TOKEN`, `TIMMENY_OS_API_KEY`, `TODO_BOARD_ID` (`8962223984`), `TODO_GROUP_ID`, `GS_TODO_BOARD_ID`, `GS_TODO_GROUP_ID`, `GS_KEY_INITIATIVES_GROUP_ID`. All read ad hoc via `os.getenv` at call time; there is no settings object.

**Persistence.** None. Nothing is written outside Monday.com. `operating/*.yaml` is hand-curated prototype state read by humans and chats, not by the service.

**Tests.** `tests/test_main.py`, 36 tests, 2,363 lines. `conftest.py` only injects the repo root onto `sys.path`. Tests use `fastapi.testclient.TestClient`, `monkeypatch.setenv` for configuration, and a `FakeAsyncClient` installed with `monkeypatch.setattr(main.httpx, "AsyncClient", ...)` — i.e. patched on the shared `httpx` module object, so the fake survives moving adapter code into another module. Verified: **36 passed** under Python 3.12; collection **fails under Python 3.10** (`StrEnum`).

**Logging and error handling.** There is no `logging` import and no structured logging anywhere. Every failure is an `HTTPException`. Monday GraphQL errors are returned to the caller verbatim under `detail.errors`, which can echo upstream payload text. There are no request IDs, no retries, no backoff, and no circuit breaking.

**Local development.** Python 3.12 venv, `pip install -r requirements-dev.txt`, `pytest`. No linter, formatter, type checker, pre-commit config, or CI workflow exists (`.github/` is absent).

---

## 2. Existing Capabilities That Must Be Preserved

1. The seven GPT Action operations in `docs/gpt-action-openapi.yaml` and their exact response shapes, especially the flat `result_text` reads.
2. `uvicorn main:app` as the Railway start command and `/health` as the healthcheck path.
3. Monday board routing by `todo` / `gs` and the existing environment variable names.
4. Title-based column resolution and the `Decision` → `Status: Not Yet Started` side effect.
5. Cursor pagination and default exclusion of done items.
6. Dual `X-API-Key` / bearer auth, including the current fail-open behavior for existing routes unless a change is explicitly approved.
7. All 36 existing tests, unchanged.

---

## 3. Gap Analysis Against the First Vertical Slice

| Slice requirement | Today | Gap |
|---|---|---|
| PostgreSQL persistence | none | everything: driver, engine, session, migrations |
| `operational_objects`, `evidence`, `external_mappings`, `workflow_runs`, `workflow_steps` | none | full schema |
| Gmail adapter | none | OAuth, thread sync, labels, archive |
| Classification service | none | full boundary, no classification concept exists |
| Workflow service and explicit state | none | full state machine, retry, resume |
| Idempotent external writes | **absent** — `create_item` is called unconditionally; a retried request creates a duplicate | idempotency keys plus a recovery path for crash-between-create-and-commit |
| Read-after-write verification | absent — the mutation's echoed id is trusted | verification read plus recorded digests |
| Stable external mappings | absent — Monday item ids are returned to the caller and forgotten | `external_mappings` table |
| Completion detection | `is_done_monday_item()` exists but is only used to filter reads | promote to a sync step |
| Read a single mapped Monday item | not supported (only whole-board reads) | new `items(ids:)` query |
| Audit trail | none | `workflow_steps` |
| Human review state | none | review queue and approval endpoints |
| Executive Review API | none | new assembled read model |
| Retries / backoff | none | bounded retry with `next_retry_at` |
| Structured logging with redaction | none | logger plus redaction helper |
| CI | none | GitHub Actions running pytest |

The two hardest gaps are **idempotent Monday creation** (the Monday API has no client-supplied idempotency token) and **safe Gmail archive ordering**.

---

## 4. Proposed Target Module Structure

Additive. `main.py` remains the Railway entry point and keeps its existing routes; new code lives in a package and is mounted onto the existing `app`.

```text
main.py                       # unchanged routes; + include_router(...) and a lifespan hook
adminos/
  config.py                   # typed settings; single place reading os.environ
  logging.py                  # structured logger + redaction helpers
  db/
    engine.py                 # SQLAlchemy engine/session, DATABASE_URL-gated
    models.py                 # ORM tables
    migrations/               # Alembic
  domain/
    operational_objects.py
    evidence.py
    mappings.py               # external identity resolution
    classification.py         # ClassificationResult + deterministic rules
  adapters/
    monday_client.py          # execute_monday_graphql + column helpers (extracted, re-imported by main.py)
    monday.py                 # slice operations: create/update/read-one/verify
    gmail.py                  # thread sync, labels, archive
  workflow/
    states.py                 # WorkflowState enum + legal transitions
    engine.py                 # run/step execution, idempotency, retry, audit
    gmail_to_monday.py        # the one workflow
  api/
    workflows.py              # POST /workflows/gmail-intake, /runs/{id}/retry, reviews
    review.py                 # GET /executive-review (+ /read-simple)
```

Only one existing-code move is proposed, and it is mechanical: `execute_monday_graphql` and the column helpers move to `adminos/adapters/monday_client.py` and are re-imported by `main.py`, so the new adapter does not duplicate transport and error mapping. The existing tests continue to pass because they patch the shared `httpx` module. If even that is unwanted, the fallback is to leave `main.py` untouched and accept ~40 duplicated lines.

---

## 5. Minimal PostgreSQL Schema

Six tables. Nothing from the wider domain model is implemented yet.

```sql
operational_objects (
  id uuid pk, type text, title text, status text,
  life_area text null, parent_id uuid null,
  confidence numeric null, source text,
  created_at timestamptz, updated_at timestamptz
)

evidence (
  id uuid pk, source_system text,            -- 'gmail'
  source_thread_id text, source_message_id text null,
  subject text, participants jsonb, received_at timestamptz,
  snippet text null,                          -- redacted, bounded length; never full bodies
  content_hash text, raw_ref text null,
  created_at timestamptz,
  unique (source_system, source_thread_id)
)

classifications (
  id uuid pk, evidence_id uuid fk, classifier_version text,
  matched_object_id uuid null, proposed_object_type text null,
  relationship text,                          -- creates|updates|completes|blocks|contradicts|supports
  disposition text, confidence numeric,
  requires_review boolean, rationale text,
  created_at timestamptz
)                                             -- inference, never treated as confirmed fact

external_mappings (
  id uuid pk, internal_type text, internal_id uuid,
  external_system text, external_kind text,   -- 'monday'/'item', 'gmail'/'thread'
  external_id text null, board_id text null,
  state text,                                 -- pending|active|orphaned
  admin_os_id text unique,                    -- written into Monday for crash recovery
  created_at timestamptz, updated_at timestamptz,
  unique (external_system, external_kind, external_id),
  unique (internal_type, internal_id, external_system, external_kind)
)

workflow_runs (
  id uuid pk, workflow_name text,
  idempotency_key text unique,
  state text, evidence_id uuid, operational_object_id uuid null,
  requires_review boolean, attempt_count int, next_retry_at timestamptz null,
  last_error text null, created_at timestamptz, updated_at timestamptz
)

workflow_steps (
  id uuid pk, run_id uuid fk, sequence int, step_name text,
  status text, request_digest text, response_digest text,
  external_ref text null, error text null,
  started_at timestamptz, finished_at timestamptz,
  unique (run_id, step_name, sequence)
)

decisions (                                   -- only for ambiguous classification
  id uuid pk, workflow_run_id uuid null, operational_object_id uuid null,
  question text, options jsonb, selected_option text null,
  status text, created_at timestamptz, resolved_at timestamptz null
)
```

`classifications` is deliberately separate from any confirmed link so an uncertain inference is never stored as fact. `evidence` never stores full message bodies.

---

## 6. Required Database Migrations

Alembic, `adminos/db/migrations/`, `script_location` configured in `alembic.ini`. Migrations are run as a Railway pre-deploy command rather than at import time, so a bad migration cannot take the running service down mid-request.

1. `0001_baseline` — the six tables plus indexes on `evidence(source_thread_id)`, `workflow_runs(state, next_retry_at)`, `external_mappings(internal_type, internal_id)`.
2. Later increments add columns only; no destructive migrations during the slice.

`DATABASE_URL` gates all of it. When it is unset the app behaves exactly as it does today.

---

## 7. Required Gmail Integration Approach

Decided in [ADR-0003](./adr/ADR-0003-gmail-access-and-retention.md). Gmail REST API over user OAuth (authorization-code flow, `access_type=offline`, refresh token in Railway). A service account will not work — domain-wide delegation is unavailable for a personal `gmail.com` mailbox.

- Scope: `https://www.googleapis.com/auth/gmail.modify` only. `gmail.readonly` cannot archive.
- **The consent screen must be published "In production", not "Testing".** Google issues a refresh token that expires in 7 days to any external consent screen in Testing status ([source](https://developers.google.com/identity/protocols/oauth2)), which would break the service weekly.
- Dependencies: `google-auth` only; the REST calls go through `httpx`, matching the Monday adapter, avoiding `google-api-python-client`.
- Sync: poll `users.threads.list` scoped to the `financial/taxes` label, then `users.threads.get` with `format=metadata` plus a bounded snippet.
- Archive: `users.threads.modify` with `removeLabelIds: ["INBOX"]`. Guarded by `GMAIL_WRITE_ENABLED`, default false.
- Idempotency: Gmail label operations are naturally idempotent; the thread id is the natural evidence key.
- Retention: headers, participants, timestamps, a bounded snippet, and a content hash. Never bodies, attachments, or raw MIME.

---

## 8. Required Changes to the Monday Adapter

Additive only:

1. `get_monday_item(item_id)` — `items(ids:)` read for a single mapped item.
2. `verify_item_columns(item_id, expected)` — read-after-write assertion, digest recorded on the workflow step.
3. `find_item_by_admin_os_id(board_id, admin_os_id)` — recovery lookup used before any retried create.
4. `create_item_idempotent(...)` — reserves an `external_mappings` row in `pending` with a generated `admin_os_id`, writes it into a Monday column during creation, verifies, then marks the mapping `active`. A retry that finds a `pending` mapping searches by id before creating.
5. `read_item_completion(item_id)` — reuses the existing `is_done_monday_item` label logic.
6. Bounded retry with jittered backoff on 429/5xx inside `execute_monday_graphql`, exposed as an opt-in parameter so existing call sites are unaffected.

Item 4 requires **one new text column on the target board**, titled `Admin OS ID`. Recorded in [ADR-0002](./adr/ADR-0002-monday-identity-and-idempotency.md).

---

## 9. Workflow State Machine

```text
discovered
  -> classified
       -> completed            (disposition = record_evidence_only)
       -> awaiting_review      (ambiguous or low confidence)
            -> approved | cancelled
       -> approved             (confident, disposition selects a task write)
  -> task_creation_pending
       -> task_created
       -> failed
  -> awaiting_completion
       -> completion_verified
  -> disposition_pending       (label / archive)
       -> completed
       -> failed
blocked      (external prerequisite unmet; retryable)
failed       (terminal until manually retried; resumes at the failed step)
cancelled    (terminal)
```

Every transition writes a `workflow_steps` row. `failed` and `blocked` retain `last_error` and `next_retry_at`. Retry resumes at the first non-`succeeded` step; already-succeeded steps are never re-executed.

---

## 10. Idempotency Strategy

- **Run level.** `idempotency_key = sha256(workflow_name | gmail_thread_id | latest_message_id)`. A unique constraint makes re-ingesting the same thread a no-op that returns the existing run.
- **Step level.** `unique (run_id, step_name, sequence)`; a step already `succeeded` short-circuits.
- **Monday create.** Mapping row reserved *before* the write, `admin_os_id` embedded in the created item, recovery search by that id before any retried create. This closes the crash-between-create-and-commit window that the Monday API cannot close on its own. See [ADR-0002](./adr/ADR-0002-monday-identity-and-idempotency.md).
- **Monday update.** Naturally idempotent — `change_multiple_column_values` with the same payload converges.
- **Gmail archive.** Naturally idempotent — removing `INBOX` twice is harmless — and only reachable from `completion_verified`.

---

## 11. Verification Strategy

Every external write is followed by a read of the same object, compared against the expected values, with both digests recorded on the step. A write is not `succeeded` until verified. `completion_verified` requires an explicit read showing a done `Status` label; the Monday mutation response alone is never sufficient. Archive is unreachable from any state other than `completion_verified`.

---

## 12. Audit Model

`workflow_runs` plus ordered `workflow_steps` answer the four required questions for any run: what was read (evidence + step request digests), what was inferred (the `classifications` row with confidence and rationale), what was changed (step `external_ref` and mapping rows), and what was verified (verification step response digests). `GET /workflows/{run_id}` returns this narrative. Digests and redacted snippets are stored rather than payloads, so audit records never carry secrets or full message bodies.

---

## 13. Test Strategy

Extend the existing suite; do not modify it.

- `tests/test_main.py` stays exactly as-is and acts as the regression guard for existing GPT contracts.
- New tests use SQLAlchemy against in-memory SQLite with `StaticPool`, keeping ORM types portable (`JSON`, string UUIDs). One Alembic upgrade/downgrade smoke test runs against Postgres in CI.
- Gmail and Monday are faked at the `httpx` transport boundary, matching the existing `FakeAsyncClient` pattern.
- Add GitHub Actions running `pytest` on Python 3.12 — the repo has no CI today, and the safety invariants below deserve one.

Mapping of the 12 required scenarios to tests:

| # | Scenario | Test |
|---|---|---|
| 1 | actionable email creates exactly one task | `test_actionable_evidence_creates_single_task` |
| 2 | retry creates no duplicate | `test_reingest_same_thread_is_idempotent` |
| 3 | non-actionable email records evidence only | `test_non_actionable_evidence_records_only` |
| 4 | ambiguity pauses for review | `test_low_confidence_pauses_for_review` |
| 5 | completion syncs before archive | `test_archive_requires_completion_verified` |
| 6 | failed Monday write leaves Gmail untouched | `test_failed_monday_write_leaves_thread_unarchived` |
| 7 | failed archive retries safely | `test_archive_retry_does_not_recreate_task` |
| 8 | related task updated, not duplicated | `test_existing_mapping_updates_task` |
| 9 | completed workflow stays idempotent | `test_completed_run_reprocess_is_noop` |
| 10 | review reports stale/blocked/failed | `test_executive_review_reports_state` |
| 11 | API retries do not corrupt state | `test_transient_5xx_resumes_at_failed_step` |
| 12 | existing endpoints unaffected | existing 36 tests |

---

## 14. Required Credentials and Configuration

| Variable | Purpose | Status |
|---|---|---|
| `DATABASE_URL` | Railway Postgres | **must be provisioned** |
| `GMAIL_CLIENT_ID` | Google OAuth client | **needed** |
| `GMAIL_CLIENT_SECRET` | Google OAuth client | **needed** |
| `GMAIL_REFRESH_TOKEN` | offline access for the personal mailbox, scope `gmail.modify` | **needed** |
| `GMAIL_INTAKE_LABEL` | bounds the evidence set | `financial/taxes` |
| `GMAIL_WRITE_ENABLED` | gates label/archive writes | new, defaults false |
| `MONDAY_API_TOKEN` | existing, write scope | needed locally for integration checks |
| `TIMMENY_OS_API_KEY` | existing | needed to exercise production |
| `TODO_BOARD_ID` | slice target: To Do List, `8962223984` | existing |
| `ADMIN_OS_ID_COLUMN_TITLE` | mapping column title, `Admin OS ID` | new, requires a board column |

Also required: Railway access to add the Postgres service and a pre-deploy migration command, or the user performing that step.

---

## 15. Specific Files to Create or Modify

**Create:** `alembic.ini`; `adminos/__init__.py`, `config.py`, `logging.py`; `adminos/db/{engine,models}.py` and `migrations/`; `adminos/domain/{operational_objects,evidence,mappings,classification}.py`; `adminos/adapters/{monday_client,monday,gmail}.py`; `adminos/workflow/{states,engine,gmail_to_monday}.py`; `adminos/api/{workflows,review}.py`; `tests/adminos/*`; `.github/workflows/tests.yml`; `docs/adr/ADR-0002-monday-idempotency-and-mapping.md`; `docs/adr/ADR-0003-gmail-access-and-retention.md`.

**Modify:** `main.py` (router includes, lifespan, extracted client import — no route changes); `requirements.txt` (`sqlalchemy`, `alembic`, `psycopg[binary]`, `google-auth`, `google-auth-oauthlib`); `requirements-dev.txt` (`pytest-asyncio`); `railway.json` (pre-deploy migration); `.env.example`; `README.md`; `docs/gpt-action-openapi.yaml` (only in Increment 7, when the review action is real); `docs/90-Roadmap.md`.

---

## 16. Staged Implementation Plan

One PR per increment; no increment starts before the previous one is green.

| # | Scope | Exit condition |
|---|---|---|
| 1 | This assessment, [ADR-0002](./adr/ADR-0002-monday-identity-and-idempotency.md), [ADR-0003](./adr/ADR-0003-gmail-access-and-retention.md) | approved |
| 2 | `adminos` package, config, logging, engine, models, baseline migration, `GET /admin/db-status`, CI | migrations apply on Railway; existing 36 tests pass |
| 3 | Gmail adapter (read-only), evidence ingestion, idempotent thread sync | a real thread becomes exactly one `evidence` row; re-sync is a no-op |
| 4 | Classification boundary, deterministic rules, review state | scenarios 3 and 4 pass |
| 5 | Monday create/update with mapping, verification, retry | scenarios 1, 2, 8, 11 pass |
| 6 | Completion sync, Gmail label/archive disposition | scenarios 5, 6, 7, 9 pass |
| 7 | Executive Review API + `read-simple`, GPT Action schema update, Railway config, docs | scenario 10 passes; end-to-end on a real thread |

---

## 17. Risks, Unknowns, and Assumptions

**Risks**

1. *Monday create is not idempotent.* A crash between the mutation and the commit can duplicate a task. Mitigated by the reserved-mapping + `Admin OS ID` recovery design, which needs a board column.
2. *Gmail archive is user-visible and effectively irreversible in workflow terms.* Mitigated by `GMAIL_WRITE_ENABLED`, a label-only first phase, and the `completion_verified` gate.
3. *Fail-open auth.* If `TIMMENY_OS_API_KEY` is ever missing from the Railway environment, the new endpoints — which archive mail — would be public. New routes should fail closed regardless of the legacy behavior.
4. *Verbatim Monday errors in responses* can echo upstream content; new code should redact.
5. *`main.py` growth.* Mitigated by keeping all new code in `adminos/`.
6. *Rate limits.* Monday enforces a complexity budget and Gmail a quota; polling frequency and page sizes must stay bounded.
7. *No CI today.* Invariants this important should not rely on local runs.

**Resolved since drafting:** the slice targets the **To Do List** board (`8962223984`) and may add an `Admin OS ID` text column; Gmail intake is scoped to the **`financial/taxes`** label; Railway Postgres does **not** exist yet and must be provisioned.

**Unknowns:** the real label set of the target board's `Status` column; what triggers a sync run; where approval happens.

**Assumptions:** single user, single tenant; Railway remains the runtime; ChatGPT remains the only client; classification v1 is deterministic; Calendar stays out of scope.

**Observed drift worth a cleanup commit (not part of the slice):** `main.py` reports version `0.4.1` while `docs/gpt-action-openapi.yaml` reports `0.6.0`; the schema documents a default `limit` of 100 while the service defaults to 500; `README.md` still points at `docs/charter.md` and `docs/architecture.md`, which now live under `docs/archive/`.

---

## 18. Smallest Safe First Code Change

Add the `adminos` package containing `config.py`, `logging.py`, `db/engine.py`, `db/models.py`, and the `0001_baseline` Alembic migration; mount a single new authenticated route `GET /admin/db-status` that reports `not_configured | ok | error`; add the CI workflow.

Everything is gated on `DATABASE_URL`. When it is unset the engine is never created and the service behaves byte-for-byte as it does today. No existing route, response model, or environment variable changes. This is the smallest change that proves Postgres connectivity and migration execution inside the real Railway environment — the single largest unknown in the plan.
