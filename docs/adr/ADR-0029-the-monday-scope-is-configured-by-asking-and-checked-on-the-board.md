# ADR-0029 — The Monday Scope Is Configured by Asking, and Checked on the Board

**Status:** Accepted
**Date:** 2026-07-31
**Extends:** [ADR-0024](./ADR-0024-a-monday-scope-is-exact-or-it-does-not-look.md), a scope that is exact or does not look; [ADR-0023](./ADR-0023-a-session-runs-a-playbook.md), the playbook as versioned configuration.

## Context

ADR-0024 made the Monday scope exact and left it unconfigured, because no board in the account carried the labels the process names. Brian has since added them: `Working on it today` on the To Do List Status column, and a Cadence column with `Daily`. Read off the live board, that is board `8962223984`, column `status` with `Working on it today`, and column `color_mm5sa3g0` with `Daily` — an item qualifies on either.

Two ways of putting that into the service were available and both were wrong on their own.

Writing it into `config/assistant-playbook.yaml` alone reaches a fresh database and nothing else. The deployment's playbook was seeded from that file weeks ago and has been revised since; a file the running database has already read is not configuration Brian can change.

Letting it be set by asking, on the other hand, hands the GPT a filter it can compose from a column *title*. Monday names columns by ids that survive renaming — `color_mm5sa3g0` is titled Cadence and says nothing about it — and a filter naming a column that is not there is not an error to Monday. It matches nothing, and matching nothing on a 1,048-item board returns a page of the whole board under the heading "what you're working on today". That is the failure ADR-0024 exists to prevent, and it would arrive through the front door.

## Decision

**The scope is a playbook change like any other.** `set_monday_scope` and `clear_monday_scope` join the change operations, so "review my Daily items too" goes through propose → read back → confirm, the revision is kept, and the revision a session opened with still says what it said. Sources were the one part of the playbook that could only be edited in a file; they are not any more.

**Proposing a scope reads the board.** `POST /playbook/propose` resolves every configured column and label against Monday before it writes the proposal down, and refuses with what the board has instead where one is missing. Proposing is the moment a mistyped id can still be corrected by the person who typed it; discovering it at review time means discovering it in the middle of a morning. A proposal recorded for a scope that cannot exist is a revision waiting to be confirmed into a filter that matches nothing.

**A scope that cannot be checked is not proposed.** Without a Monday token the endpoint refuses rather than recording an unverified scope. The check is the safeguard; a proposal that skipped it would carry the same authority as one that passed.

**Only Monday changes call Monday.** Reordering a morning does not read a board.

**Nothing else changes.** Clearing needs no board — reviewing no Monday work is answerable without asking Monday anything. The active playbook now reports `monday_scope`, so what is configured can be read back, and `getMondayScope` reads the live board and reports what it resolves to, both read-only.

**The shipped file carries the ids read off the board**, and a test holds it to them, so a fresh database starts where the running one is rather than unconfigured.

## Consequences

The published contract moves to **0.20.0**: two new change operations, `monday_scope` on the playbook, and `getMondayScope`. Nothing already sent changes shape.

The request-shape fingerprint now resolves the components a body references. It did not, which meant adding an operation to `PlaybookChange` — a real change to what a GPT may send — left the fingerprint untouched and an imported copy indistinguishable from a current one. Fingerprints recorded for 0.19.2 and earlier are a record of what was published, not something recomputable from the document today.

Configuring the scope does not make Monday part of the review. Reconciliation and the to-do review remain `unavailable`, and a review that reported otherwise would be lying. What is true today is that the scope resolves, that `getMondayScope` says exactly which items it takes, and that the count it reports is currently zero — which is an answer about the board, not about the configuration.
