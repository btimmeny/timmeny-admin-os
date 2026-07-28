# Devin Implementation Brief — Gmail Archive and Delete-to-Trash

## Objective

Allow Brian to tell the Timmeny Admin OS assistant to archive Gmail threads or delete them. In this product, **delete means move to Gmail Trash**. Permanent deletion is not supported.

The current failure is that Daily Review rows do not return archive or move-to-Trash as permitted actions, so the GPT correctly refuses the request. Fix the Admin OS capability and execution layer; do not work around this in the GPT.

## Required behavior

For every Gmail-backed review item, Admin OS must independently determine whether these actions are permitted:

- `archive_gmail_thread`
- `move_gmail_thread_to_trash`

These actions may be recommended or selected as an override even when the original recommendation was draft reply, create Monday task, leave alone, or defer.

The screen row `actions` array must expose the applicable action identifiers. The GPT must never need to invent them.

## Natural-language mapping

The GPT will map:

- archive → `archive_gmail_thread`
- delete / remove / trash → `move_gmail_thread_to_trash`

Do not implement permanent Gmail deletion.

## Decision handling

`decideReviewItem` and `decideReviewGroup` must accept these action identifiers when they are permitted for the selected item or items.

A user-selected action different from the recommendation is an `override` decision.

Bulk decisions are allowed only when every selected item permits the same action. Return a clear item-level validation error for any excluded item; do not partially apply a bulk decision silently.

## Preparation

`prepareReviewActions` must translate approved decisions into durable action plans with stable IDs and enough Gmail identity to execute safely, including at minimum:

- review run ID
- review item ID
- Gmail account/capability identity
- Gmail thread ID
- requested action
- prior execution state
- idempotency key

Preparation performs no Gmail write.

## Gmail execution

Implement the Gmail adapter operations:

### Archive

Remove the `INBOX` label from the target thread. Preserve all other labels and message content.

### Move to Trash

Use Gmail's thread trash operation, or the equivalent supported API operation that applies the `TRASH` state to the thread. Do not permanently delete messages or threads.

Both operations must be idempotent. Re-executing an already archived or trashed thread should return a successful no-op or verified-complete result rather than creating an error.

## Permissions and safety

- Require explicit item or bulk approval.
- Require the existing prepare → explicit execute confirmation → verify lifecycle.
- Respect `gmail_write_enabled` and capability-specific write policy.
- Never translate dismiss, leave alone, or defer into a Gmail mutation.
- Never expose a permanent-delete action.

## Verification

After execution, read Gmail state and verify:

- archive: thread no longer has `INBOX`
- Trash: thread is in `TRASH`

Only then mark the action `verified` or `completed`.

Persist requested, prepared, executed, verified, failed, and no-op states. Store returned Gmail errors without losing the prepared action, so retries are safe and visible.

## Review-state behavior

After verified archive or move-to-Trash:

- mark the review item complete for the current run;
- retain its audit trail;
- remove it from unresolved rows on the next rendered screen;
- include the completed action in the review summary.

## Presentation contract

For applicable rows, return action identifiers in `screen.rows[].actions`.

The user-facing option labels should be:

- `Archive`
- `Move to Trash`

Do not label the second option as permanent deletion. The GPT may conversationally accept the word “delete,” but the action and confirmation language should make clear that it moves the thread to Trash.

## API and OpenAPI

The existing GPT Action endpoints are sufficient if the generic `action` field accepts these identifiers. No new GPT endpoint is required.

Confirm that the runtime API and validation schemas allow:

- `archive_gmail_thread`
- `move_gmail_thread_to_trash`

If internal enums are documented in OpenAPI, add both identifiers and bump the schema version. If `action` remains an unrestricted string, no OpenAPI structural change is required.

## Tests

Add unit and integration coverage for:

1. archive appears as a permitted action for an eligible Gmail row;
2. move-to-Trash appears as a permitted action for an eligible Gmail row;
3. unsupported rows reject the action;
4. item override records correctly;
5. bulk archive and bulk Trash validate every item;
6. preparation creates no Gmail write;
7. execution without `confirm: true` is rejected;
8. archive removes `INBOX` and preserves other labels;
9. Trash places the thread in `TRASH` without permanent deletion;
10. repeated execution is idempotent;
11. verification failure does not report completion;
12. completed items disappear from unresolved review rows but remain in the audit summary.

## Acceptance scenario

Given 11 displayed Gmail review rows that all permit move-to-Trash, when Brian says:

> Delete all 11.

Admin OS should:

1. record an override to `move_gmail_thread_to_trash` for all 11;
2. prepare 11 actions;
3. ask for explicit execution confirmation through the GPT;
4. execute only after confirmation;
5. verify all 11 threads are in Trash;
6. report completed and failed counts accurately.

The assistant must not respond that only draft, Monday task, leave alone, or not today are available once this implementation is deployed.