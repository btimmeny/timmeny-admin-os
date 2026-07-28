# ADR-0005 — Duplicate Review Before Monday Writes

**Status:** Accepted
**Date:** 2026-07-28
**Depends on:** [ADR-0001](./ADR-0001-admin-os-coordination-layer.md), [ADR-0002](./ADR-0002-monday-identity-and-idempotency.md), [ADR-0004](./ADR-0004-classification-boundary-and-review.md), [75 — First Vertical Slice](../75-First-Vertical-Slice.md)

## Context

ADR-0002 makes a Monday write idempotent *with respect to Admin OS*: a reserved mapping and an `Admin OS ID` stamped on the item mean a crash mid-write recovers the item instead of duplicating it. That protects against the system creating the same task twice.

It does nothing about the other duplicate, which is the one that actually happens. The To Do List board holds 1,047 items — 94 open, 953 done — created by hand over years, and none of them carries an `Admin OS ID`. A task proposed from a tax thread will frequently correspond to work that is already on the board under a different wording, or that was completed the last time the same annual obligation came round. The board already contains its own near-duplicates: "GS | KO Shares (obtain cost basis)" alongside "GS | KO Shares (decide strategy and get cost basis)".

The mapping table cannot detect this. It only knows about items Admin OS itself created, which is currently none of them.

## Decision

**A board read comes before the first board write.** `GET /admin/monday/board` reads the To Do List board with a status filter (`open` / `done` / `all`) and an optional name filter. Both filters are pushed to Monday through `query_params`, not applied after paging: filtering a page locally would return "the matches in the first hundred items" while looking like "the matches".

**Duplicate review is a separate read-only operation.** `POST /admin/monday/duplicate-check` ranks existing items against proposed titles and returns the candidates with scores. It performs no mutation, and the write path — when it exists — will be a distinct, explicitly approved call.

**Completed items are compared, not filtered out.** Nine in ten items on this board are done, and a large share of the work is recurring: annual filings, seasonal maintenance. "You completed this in March" is a useful answer to "is this a duplicate?", so the default scope is the whole board.

**Similarity is scored, never decided.** The endpoint reports a score and flags a strong match; it does not refuse anything and does not choose. Deciding that two differently worded tasks are the same piece of work is a judgment about the domain, and the account owner makes it. This is the same boundary ADR-0004 draws for classification.

**Scoring is deterministic and explainable.** Two measures, taking the higher: weighted token overlap, which survives the board's "Context | Action" naming convention being reversed, and character sequence ratio, which catches near-identical phrasing. Tokens are weighted by inverse document frequency over the board itself, because "GS" prefixes a large fraction of these items and must not count as evidence of duplication the way "KPMG" does. A word absent from the board takes the maximum weight — it is as distinctive as a word can be — which stops a short title being judged "contained in" a longer one on the strength of its single common word. Titles sharing no word at all score zero regardless of character overlap, which is otherwise how "Call plumber about kitchen leak" comes to resemble "Zac | Pimple Patches".

**No model call.** As with classification, the mailbox metadata and the board contents stay inside Admin OS, and the result is reproducible.

## Alternatives Considered

1. **Rely on `Admin OS ID` alone.** Rejected: it is empty on all 1,047 existing items, so it can only prevent Admin OS duplicating its own work, not duplicating the owner's.
2. **Exact-title matching.** Cheap and useless here: the duplicate arrives as a mail subject or a rephrasing, never as a byte-identical string.
3. **Block creation automatically on a strong match.** Rejected: it makes a guess authoritative, and the legitimate case — this year's instance of an annual filing — looks exactly like a duplicate.
4. **Embeddings or an LLM similarity judgment.** Better recall, but non-deterministic, unexplainable in the audit record, and it sends the board and mailbox metadata to a third party for a decision a human is making anyway.
5. **Compare only open items.** Faster, and wrong for a board that is 91% completed recurring work.
6. **Filter locally after reading the board.** Rejected: with paging, local filtering quietly answers a different question than the one asked.

## Consequences

- The duplicate check reads the whole board — roughly eleven paged requests. Acceptable for an operation that precedes a human decision; if it is later called per-queue-item in a loop, the board read will need caching.
- Thresholds (0.45 to surface a candidate, 0.75 to call it strong) are tuned against this board's vocabulary and will drift as it changes. They are constants in one module, and the scores are returned so a bad threshold is visible rather than silent.
- Scoring compares titles only. Two tasks with the same name and different intent will look identical, which is exactly why the result is advisory.
- The Monday client in `adminos/` is read-only and deliberately has no mutation method, so this increment cannot write to the board even by mistake. Adding one is a visible change.

## Affected Documents

- [75 — First Vertical Slice](../75-First-Vertical-Slice.md)
- [90 — Roadmap](../90-Roadmap.md)
- [README](../../README.md)

## Validation

- Deterministic tests: the To Do List board id is the only board read; status and name filters are sent to Monday rather than applied locally; paging follows the cursor and stops at the limit; an unset text column reads as absent rather than as an identifier; a rejected token, a GraphQL error, a missing board, and a transport failure each surface instead of returning an empty board; the client sends no mutation; identical and reordered titles score 1.0; titles with no shared word score 0.0; a shared rare word outranks a shared common one; completed matches are reported and open matches outrank done ones at equal score; the duplicate check makes exactly one read and no write.
- Against the live board: a filtered read returns the expected items, and known duplicates on the board score above the strong-match threshold while unrelated work reports no match.
