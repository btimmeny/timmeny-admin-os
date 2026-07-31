# ADR-0024 — A Monday Scope Is Exact, or the Review Does Not Look

**Status:** Accepted
**Date:** 2026-08-12
**Extends:** [ADR-0023](./ADR-0023-a-session-runs-a-playbook.md), the playbook as configuration; [ADR-0015](./ADR-0015-review-mailbox-scope.md), a review that says what it is of.
**Extended by:** [ADR-0029](./ADR-0029-the-monday-scope-is-configured-by-asking-and-checked-on-the-board.md) — the labels described below as absent have since been created, and the scope is now configured by asking rather than only in a file.

## Context

Brian asked for the day's Monday work in the daily review, scoped exactly: an item counts if its today-status is `Working on it today` **or** its cadence is `Daily`, and if the board, the columns or the labels cannot be resolved, the review is to stop with a configuration error rather than broaden the query.

That instruction is stricter than it first reads, and the strictness is earned. Gmail's `INBOX` label either exists on a thread or does not. A Monday scope is three separate things that can each be right in configuration and absent from the board: a board id, a column id, and a label text. Boards get renamed, columns get rebuilt with new ids, labels get retyped. Monday reports none of it. A rule naming a column that is not there, or comparing against a label index that does not exist, matches nothing — and a `query_params` filter that matches nothing on the To Do List board returns a page of its 1,048 items, indistinguishable in shape from a correct answer.

So the failure mode is not an error. It is a morning spent reviewing a thousand rows of somebody's backlog under the heading "what you're working on today", or — the quieter version — the first hundred of them, with the rest silently absent.

There is also a fact about the live account: **no board in it carries the labels the process names.** The To Do List board has `In Progress` / `Not Yet Started` / `Done`; the GS board has an Urgency column reading `Priority` / `Urgent` / `Today` / `Follow-Up`. Nothing anywhere reads `Working on it today` or `Daily`, as a status label, a dropdown value, a column title or a group.

## Decision

**The scope is configuration in the playbook, and nothing about it has a default.** `sources.monday` names the board id, the column ids and the exact label texts. There is no fallback board, no assumed status column and no assumed label. Absent configuration means Monday is not reviewed, and is reported as unconfigured rather than as an empty day.

**The board is read before it is queried.** `resolve_board_scope` fetches the board's name and every column on it, finds each configured column by id, and resolves each configured label to the index Monday actually filters by. Anything missing raises `BoardScopeUnresolved` naming what was looked for and what the board has instead — the columns it does have, or the labels that column does offer.

**Labels are matched on exact text.** `daily` is not `Daily`. Case-insensitive matching would be convenient exactly until the board carried both, and a scope that quietly resolves to something near enough is a scope Brian did not agree to.

**Rules compare indexes, and Monday applies them.** Filtering after paging would either burn the complexity budget on every review or filter one page and call it the answer. The two filters are OR-ed by Monday itself (`operator: or`), which is how "either column qualifies" is expressed there.

**What comes back is checked against what was asked for.** Every returned item is held against the configured labels, and if any item matches neither filter, the read is refused as a filter that did not apply. Refused, not trimmed: an unfiltered board arriving under the name of today's work is the whole failure this exists to prevent, and trimming it would produce a review that looks right and is missing everything past the first page.

**Nothing widens, ever.** There is no path in which a scope that fails to resolve becomes a bigger scope. The only outcomes are the exact items, or a stop with the reason.

**`GET /admin/monday/scope` reads the scope against the live board and writes nothing.** Configuration only exercised in the middle of a morning is configuration whose mistakes are found in the middle of a morning.

## Alternatives Considered

**Map the request onto the labels the boards already have** — GS `Urgency = Today`, or To Do List `Status = In Progress`, or `Action Date = today`. Rejected, and this is the decision that matters. Each is defensible and none is what was asked for, and the difference between them is which work Brian sees tomorrow morning. Picking one would encode a guess as configuration, where it would then look exactly like something he had agreed to. The scope stays unconfigured until he says which pair means "what I'm working on today", or creates the labels the process names.

**Fall back to the whole board when the filter cannot be resolved.** Rejected: it is the specific behaviour the requirement forbids, and it is also the behaviour a broken filter produces by accident, which is why the returned items are checked rather than trusted.

**Match labels case-insensitively, or by trimmed text.** Rejected. It hides exactly the mistakes the resolution step exists to find, and it turns a board with two similar labels into a scope whose meaning depends on which one Monday returns first.

**Hard-code the board and columns in the adapter, as the To Do List integration does.** Rejected. That integration writes to one known board with three known columns; this reads a scope Brian sets and changes. Configuration in the playbook also gets versioning, validation and the propose/confirm path for free, which is where a change to what "today's work" means belongs.

## Consequences

The Monday phase of the daily review cannot be built on real data until the scope resolves, and it will not pretend otherwise: unconfigured is reported as unconfigured. Once Brian names the columns and labels — or creates them — the scope is two entries in the playbook and the review reads exactly those items, with the resolution check standing between a renamed column and a morning of the wrong work.
