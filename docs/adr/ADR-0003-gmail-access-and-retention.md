# ADR-0003 — Gmail Access, Intake Scope, and Retention

**Status:** Accepted
**Date:** 2026-07-28
**Depends on:** [ADR-0001](./ADR-0001-admin-os-coordination-layer.md), [75 — First Vertical Slice](../75-First-Vertical-Slice.md), [76 — Repository Assessment](../76-Repository-Assessment.md)

## Context

The first vertical slice needs to read Gmail threads, apply labels, and archive a thread after a Monday task is verified complete. The mailbox is a personal `gmail.com` account, not a Google Workspace account.

Three constraints follow from that:

1. **Service accounts do not work.** Domain-wide delegation requires a Workspace domain admin. A personal mailbox can only be accessed with a user-consented OAuth credential.
2. **Archive requires write scope.** Archiving is `users.threads.modify` with `removeLabelIds: ["INBOX"]`. `gmail.readonly` cannot perform it.
3. **Refresh tokens expire under Testing status.** Google issues a refresh token that expires in **7 days** to any external OAuth consent screen whose publishing status is `Testing`, unless the requested scopes are only name/email/profile ([source](https://developers.google.com/identity/protocols/oauth2)). A long-running Railway service authenticated that way would break weekly.

The mailbox also contains material the service has no reason to hold. `PRIVACY.md` and the slice's own logging invariant both call for minimizing what is persisted.

## Decision

**Credential.** A single user-consented OAuth 2.0 credential (authorization-code flow with `access_type=offline`) for the personal mailbox. The refresh token is stored as a Railway environment variable; access tokens are held in memory only.

**Publishing status.** The Google Cloud OAuth consent screen is set to **In production**, not Testing, specifically so the refresh token does not expire every 7 days. The app remains unverified, so consent shows an "unverified app" warning that the account owner accepts once. Verification is unnecessary for a single-user app and is not pursued.

**Scope.** Exactly one: `https://www.googleapis.com/auth/gmail.modify` — read, label, and archive, with no ability to permanently delete. `gmail.readonly` is insufficient (cannot archive) and broader scopes are unnecessary. Permanent deletion is deliberately out of reach: the "move to Trash" disposition named in the domain model is implemented as `trash`, not `delete`, and is not part of this slice.

**Intake scope.** The service reads only threads carrying the label **`financial/taxes`**. It does not read the inbox at large. The label is configuration (`GMAIL_INTAKE_LABEL`), so the intake set can be widened one label at a time as the loop proves reliable. Anything outside the configured label is invisible to Admin OS.

**Transport.** The Gmail REST API is called directly with `httpx`, matching the existing Monday adapter, rather than pulling in `google-api-python-client`. Only `google-auth` is added, for token refresh.

**Sync model.** Polling `users.threads.list` with a bounded query. No Pub/Sub push, no webhook endpoint, no inbox watch. The trigger is an explicit sync call; scheduling is a separate decision.

**Write gating.** All Gmail writes are gated by `GMAIL_WRITE_ENABLED`, default false. The first deployments read and classify without touching the mailbox. Label writes are enabled before archive writes.

**Retention.** For each thread Admin OS persists: Gmail thread id, message ids, subject, participant addresses, timestamps, a bounded snippet, and a content hash. It does **not** persist message bodies, attachments, or raw MIME. Audit records store digests, not payloads. Logs carry the thread id and never subject lines, addresses, or body text.

## Alternatives Considered

1. **Service account with domain-wide delegation.** Not available for a personal `gmail.com` mailbox.
2. **IMAP with an app password.** Can archive by removing `\\Inbox`, but Gmail label semantics map poorly onto IMAP folders, message-id stability is weaker, and the credential is mailbox-wide with no scope limitation. Rejected.
3. **Keep the consent screen in Testing and re-consent weekly.** Rejected — a 7-day manual step guarantees the service breaks.
4. **Complete Google's verification process.** Disproportionate for one user, and it does not change any technical capability.
5. **Read the whole inbox and let classification decide.** Rejected for the first slice. A narrow label bounds the blast radius of a bad classification and makes the evidence set reviewable by hand.
6. **Gmail push notifications via Pub/Sub.** Lower latency, but adds a public webhook, a Pub/Sub topic, and renewal of the `watch` registration. Not justified before the loop works.
7. **Persist full message bodies for better classification.** Rejected. It conflicts with the logging and privacy invariants, and headers plus a snippet are sufficient for the deterministic rules of classification v1.

## Consequences

- One manual consent step, performed once by the account owner, produces a refresh token that must be treated as a mailbox credential.
- The refresh token is a single point of failure: revoking account access, changing the password, or removing the app from account permissions invalidates it. Token refresh failures must surface as a blocked workflow, not a silent no-op.
- Only `financial/taxes` threads exist as far as Admin OS is concerned, so the Executive Review's coverage claims must be scoped to that label rather than presented as a whole-mailbox view.
- Classification cannot rely on body text, which reinforces a deterministic, explainable v1.
- Because bodies are not stored, re-classification of historical evidence under improved rules requires re-fetching from Gmail.
- The `gmail.modify` scope cannot permanently delete, so an accidental destructive action is bounded at "moved to Trash", which is recoverable for 30 days.

## Affected Documents

- `docs/76-Repository-Assessment.md`
- `docs/70-Implementation-Strategy.md`
- `README.md` (configuration)
- `PRIVACY.md` (retention)

## Validation

The decision is validated when the service, running on Railway with no interactive session, refreshes its own access token after the original has expired, reads a `financial/taxes` thread, and stores an evidence row containing no message body — and when a token older than 7 days still works, confirming the publishing-status decision.
