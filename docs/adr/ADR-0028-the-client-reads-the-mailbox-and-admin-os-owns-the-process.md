# ADR-0028 — The Client Reads the Mailbox, Admin OS Owns the Process

**Status:** Accepted
**Date:** 2026-07-29
**Amends:** [ADR-0001](./ADR-0001-admin-os-coordination-layer.md), which said Admin OS coordinates every external system and that direct Gmail access by ChatGPT is transitional.
**Extends:** [ADR-0023](./ADR-0023-a-session-runs-a-playbook.md), a session runs a playbook; [ADR-0022](./ADR-0022-every-entry-reads-the-mailbox.md), every entry reads the mailbox; [ADR-0003](./ADR-0003-gmail-access-and-retention.md), what is retained about a message.

## Context

Admin OS reads Gmail itself. It syncs threads, holds evidence, classifies by label into three capabilities, and builds the review out of what it holds. That path works and has execution safeguards, exact scopes and verification behind it.

It also has a ceiling. The three capabilities are Gmail labels, so a new group needs a label on the mailbox before it can exist; classification is by label rather than by reading the mail; and the summary of a thread is a snippet, because bodies are not stored and will not be.

Brian's ask is a review of ten groups — act now, decisions, legal, financial, career, awaiting confirmation, waiting on others, informational, archive or trash, admin cleanup — where each thread comes with what it says, why it matters, what to do about it and how sure the reading is. That is reading comprehension over the whole mailbox, and the client already has both halves it needs: the Gmail app, and the ability to read.

What the client does not have is a process. Groups in a prompt drift; a GPT that decides for itself when a review is finished will decide it is finished; and nothing in a conversation survives the conversation.

## Decision

**The client reads the mailbox. Admin OS owns the process and holds the result.**

ChatGPT reads the current Inbox through the Gmail app and classifies it. Admin OS publishes the playbook it must classify against, validates what comes back against that playbook, records it, and moves the review on. Concretely:

- **Admin OS owns** the phases and their order, the groups and their order, the fields every reviewed thread must state, how the result is presented, when a phase is finished, the durable state of the review, the version it is pinned to, and the audit trail.
- **The client owns** reading Gmail, classifying, and summarising.
- **Neither owns both.** Admin OS never claims to have read the mailbox; the client never decides what a complete review is.

**MCP is a second transport over the same services, not a second service.** The five tools — `start_admin_review`, `read_admin_review`, `read_review_playbook`, `record_email_review`, `complete_review_phase` — are adapters over `adminos.domain.guided_review`. The OpenAPI Action contract is untouched and keeps working. MCP exists because ChatGPT can hold a remote MCP connector and the Gmail app in one conversation, which is the arrangement this needs.

**The transport is Streamable HTTP at `POST /mcp`, on the service that is already deployed.** One request, one answer, no session id and no server-initiated stream; `GET` and `DELETE` say so rather than hang. Authentication is the API key the rest of the service uses, as `Authorization: Bearer` or `X-API-Key`, and no OAuth metadata is advertised, so a client is never sent looking for an authorization server that does not exist. A client that accepts `text/event-stream` is answered with one SSE event because that is what the remote MCP clients in use are proven against; one that asks only for JSON gets JSON. Running it inside the existing app rather than beside it is what makes the tools and the services the same deployment, the same key and the same commit.

**The groups are versioned configuration, and a review pins the version.** `config/review-playbook.yaml` seeds a revision in the same `playbook_revisions` table the session playbook uses. A review holds its revision id for its whole life; a submission naming a different version is refused rather than quietly measured against the current one. A group added at ten o'clock does not change what a review started at nine is being held to.

**A submission is validated whole or refused whole.** Wrong version, wrong scope, a group the pinned playbook does not have, a missing required field, the same thread twice, a count that disagrees with what was sent, an item left out of the recommended order, an order naming mail that is not in the review — each is refused, all of them are named at once, and nothing is written. Half a recorded review reads like a review of the mailbox and is a review of part of it.

**`source_snapshot.thread_count` is checked against what was submitted.** It is the client's own count of what it read. Two numbers that should agree are the only way this design can notice a thread that was read and quietly dropped, because Admin OS cannot go and look.

**Dispositions are recommendations, and the tools cannot execute.** `archive`, `move_to_trash`, `file_to_existing_label` are recorded as what the review thinks should happen. Executing one remains the existing path: decided, prepared into an exact scope, confirmed, executed, verified. Nothing in this milestone touches Gmail.

**A completed phase is not a completed review.** Monday reconciliation, to-do review and daily planning are named in every review and return `unavailable`. Completing the email phase leaves the review `partially_complete`. Reporting a process as done when three quarters of it was never built is the failure this whole arrangement is arranged to avoid.

**No mail is stored.** Thread ids, and the interpretation the client submitted. No bodies, which is ADR-0003 unchanged, and no subject or sender is returned by `read_admin_review`.

## What This Changes in ADR-0001

ADR-0001 said direct Gmail access by ChatGPT is "transitional evidence access, not the target architecture". That is now true of *coordination* and false of *reading*. The revised division:

> Admin OS publishes the workflow, operating model, durable state, configuration and constraints. ChatGPT uses approved source apps to gather evidence, reasons over that evidence according to the Admin OS playbook, and records structured decisions and outcomes back into Admin OS.

What has not changed: Admin OS still owns every write to Gmail and Monday, still owns identity resolution, verification and audit, and is still the only thing that persists anything.

## Alternatives Considered

**Have Admin OS fetch the mail and pass it to the client to classify.** Rejected for this milestone. It doubles the Gmail traffic, needs bodies to be passed through the service that has committed to not storing them, and buys one thing — Admin OS knowing the true thread count. The reader's own count, checked against its own submission, catches the failure that matters (dropping threads) without any of that.

**Keep classification in Admin OS and add the seven new groups as Gmail labels.** Rejected. It makes Brian label his mailbox before he can have a review, and it still classifies by label rather than by reading — which is the actual request.

**Put the groups in the GPT's instructions.** Rejected, and it is the alternative worth naming. It works immediately and it means Admin OS cannot check anything: a submitted group key would be whatever the model remembered, a changed group would need the instructions repasted, and the review would have no version to be held to.

**Trust the submission and record what arrives.** Rejected. The one thing a validating layer can do that a reasoning layer cannot is refuse, and everything downstream — counts, completion, later reconciliation against Monday — is arithmetic over a set that has to be exactly the Inbox, once each.

**Reuse `review_runs` for this.** Rejected. That table's rows are evidence Admin OS synced, with decisions, prepared scopes and verified executions hanging off them. These rows are an interpretation submitted by a client, with no evidence behind them and nothing executable. Folding them together would weaken the guarantees on both.

**A stateful MCP server with sessions.** Rejected. There is nothing to keep between calls that the database does not already hold, and a session id is state a redeploy loses.

## Consequences

There are now two review paths in one service: the Gmail-synced one behind `/review/*`, and this one behind `/mcp`. They share the playbook table and nothing else. That is a real cost, and the alternative — one path that does both jobs — is the one that made the ten groups impossible in the first place.

Admin OS cannot verify that a submission is the whole Inbox. It can only check the submission against itself and against the playbook. If the client reads half the mailbox and reports having read half, the review is honest about being of half, and nothing here notices that the other half exists.

A correction to a completed phase is a new review, not an edit. Re-submitting before completion is allowed and writes a new snapshot with the earlier one kept.

Monday reconciliation is the next phase and it is still blocked on the same two labels ADR-0024 named. The daily plan phase needs objectives, which do not exist here yet.

## Validation

Brian says "good morning" in a conversation holding both the Gmail app and Admin OS, gets a review of every thread now in his Inbox in the ten groups plus the catch-all, in Admin OS's order, with each thread saying what it is and what it is for — and Admin OS refuses the submission if a single thread is missing, duplicated, or put in a group that does not exist.
