# ADR-0002 — Monday Identity and Write Idempotency

**Status:** Accepted
**Date:** 2026-07-28
**Depends on:** [ADR-0001](./ADR-0001-admin-os-coordination-layer.md), [75 — First Vertical Slice](../75-First-Vertical-Slice.md), [76 — Repository Assessment](../76-Repository-Assessment.md)

## Context

The first vertical slice requires that every external write be idempotent and that every external record have a stable mapping to an Admin OS object.

The Monday.com GraphQL API provides no client-supplied idempotency token for `create_item`. Calling it twice with identical arguments creates two items. The current connector in `main.py` calls it unconditionally and returns the resulting id to the caller without persisting it, so a retried request duplicates work and no mapping survives the request.

Persisting the mapping in PostgreSQL after the write closes most of the gap but leaves one window open: if the service crashes, times out, or is redeployed between the successful Monday mutation and the local commit, the next attempt has no record that the item exists and creates a second one. Because `create_item` accepts arbitrary column values, that window can be closed by writing a recoverable Admin OS reference into the item itself.

Titles cannot be used for recovery. Titles are labels chosen by classification, may be edited in Monday, and are not unique.

## Decision

**Board.** The first vertical slice targets the existing **To Do List** board (`TODO_BOARD_ID`, `8962223984`). The GS board is untouched by the slice.

**Mapping column.** A text column titled **`Admin OS ID`** is added to that board. It holds an opaque Admin OS identifier and is never interpreted by a human workflow.

**Write protocol.** Every Monday create in a workflow follows reserve → recover → write → verify → activate:

```text
1. reserve   INSERT external_mappings (state='pending', admin_os_id=<generated>)  and COMMIT
2. recover   query the board for an item whose "Admin OS ID" equals admin_os_id
                found     -> adopt that item id, skip step 3
                not found -> continue
3. write     create_item(..., column_values={..., "Admin OS ID": admin_os_id})
4. verify    read the item back; assert the expected column values
5. activate  UPDATE external_mappings SET external_id=<item id>, state='active'
```

The mapping row is reserved and committed *before* the external write, so a crash at any point leaves a `pending` row that step 2 can reconcile on the next attempt. Updates need no such protocol: `change_multiple_column_values` converges on repeat.

**Identity rules.** `external_mappings` is the only place a Monday item id is stored. Monday item ids and board ids are preserved verbatim. Titles are never used as identity.

## Alternatives Considered

1. **Postgres-only mapping, no Monday column.** Simpler and requires no board change, but leaves the crash window open and can silently duplicate a task — a direct violation of the slice's idempotency invariant.
2. **Recover by searching title plus creation timestamp.** Fragile: titles are editable and non-unique, and the search is racy under retry.
3. **Write the reference into the item's update/notes feed instead of a column.** Not queryable by board scan without reading every item's updates; far more API calls against Monday's complexity budget.
4. **Accept duplicates and deduplicate later.** Rejected. Duplicate tasks are exactly the failure mode the slice exists to prevent, and cleanup would need the same identity mechanism anyway.
5. **A dedicated Admin OS board.** Rejected for the slice; the brief explicitly excludes Monday workspace redesign.

## Consequences

- The To Do List board gains one column that is written by Admin OS and ignored by humans. It should not be added to default board views.
- Every workflow create costs one extra Monday read (the recovery scan) and one extra read (verification). Both are bounded and acceptable at this volume.
- `external_mappings` rows can accumulate in `pending` when a run is abandoned. A `pending` row older than a threshold is surfaced by the Executive Review as an unresolved workflow rather than being cleaned up silently.
- The board's column set becomes part of the deployment contract. A missing `Admin OS ID` column must fail loudly, consistent with the existing `require_board_column` behavior.
- `ADMIN_OS_ID_COLUMN_TITLE` becomes a configuration value so the column can be renamed without a code change.

## Affected Documents

- `docs/76-Repository-Assessment.md`
- `docs/80-Monday-Architecture.md`
- `README.md` (configuration and required columns)

## Validation

The decision is validated when the same evidence, replayed after a simulated crash between the Monday mutation and the local commit, results in exactly one item on the To Do List board and one `active` mapping row.
