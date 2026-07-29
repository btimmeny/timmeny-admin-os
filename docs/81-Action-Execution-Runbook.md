# 81 — Action Execution Runbook

**Status:** Active
**Version:** 0.1
**Purpose:** How to drive a daily review from `approved` to a changed mailbox, and how to walk a correction up to a rule, with the exact requests and what each one returns.
**Depends on:** [ADR-0009](./adr/ADR-0009-review-engine-implementation.md), [ADR-0010](./adr/ADR-0010-action-lifecycle-and-learning.md)

```bash
export ADMIN_OS=https://timmeny-admin-os-production.up.railway.app
export KEY="$TIMMENY_OS_API_KEY"
alias os='curl -sS -H "X-API-Key: $KEY" -H "content-type: application/json"'
```

Every route below takes `X-API-Key`; a bearer token works too. Omitting it is `401`.

---

## 0. Is the GPT holding the current contract?

The Custom GPT's Action schema is a copy, so check it against what is deployed before believing a refusal is a bug:

```bash
curl -sS "$ADMIN_OS/gpt/action-schema/version"
```
```json
{"version":"0.12.0","request_shape":"0e8d7f37…","document_sha256":"…","commit":"…"}
```

If the version in ChatGPT's imported schema differs, re-import it from `$ADMIN_OS/gpt/action-schema.yaml` — the served document is the one this deployment implements. The version moves whenever a request body changes, which is the only kind of drift that turns into a refused call. See [ADR-0016](./adr/ADR-0016-a-contract-that-cannot-go-stale.md).

---

## 1. Before the first execution

**Gmail writes stay off until each action class has been watched on real mail.** Until then every execution answers `409`, which is the correct answer rather than a fault:

```bash
os -X POST "$ADMIN_OS/review/runs/$RUN/actions/execute" \
   -d '{"scope_id":"3f0c…","item_ids":["item-1"],"action_ids":["act-1"],"confirm":true}'
```
```json
{"detail":"Gmail writes are disabled. Set GMAIL_WRITE_ENABLED=true to allow them."}
```

Turning it on is a Railway variable change on `timmeny-admin-os` (`GMAIL_WRITE_ENABLED=true`) and a redeploy. It is the global kill switch: setting it back to `false` stops every action already approved, mid-review.

Check the state of the world first:

```bash
os "$ADMIN_OS/admin/db-status"
os "$ADMIN_OS/admin/gmail/status"
```
```json
{"status":"ok","revision":"0004_action_lifecycle","detail":null}
{"configured":true,"write_enabled":false,"labels":[{"capability_key":"admin","label":"Admin","found":true}, "…"]}
```

`found: false` on a label means that capability's group will be empty. Fix the name in `config/capabilities.yaml` rather than the mailbox.

---

## 2. The morning path

```bash
os -X POST "$ADMIN_OS/review/start" -d '{}'
```

Returns the run and the first group needing attention, capabilities in configured order — Admin, then Financial/Taxes, then Career. Calling it again the same day resumes; it does not restart.

The response says what it looked at, and it is the inbox unless another scope was named:

```json
{"scope":{"name":"inbox","mailbox":"INBOX","include_snoozed":false,"include_archived":false,
          "include_trash":false,"include_spam":false,"include_sent":false,"include_drafts":false,
          "requested":false,"gmail_query":"-in:snoozed","description":"Mail in the inbox now: …"}}
```

Reviewing elsewhere is a named scope — `{"scope":"archived"}`, `"snoozed"`, or `"everything"` — and opens a *second* run of the same day rather than widening this one. An unknown name is `422`. A thread that leaves the inbox between two calls comes back `deferred` on the next start, decided by `scope:inbox`; nothing about it was settled, and it returns to the review if it returns to the inbox. See [ADR-0015](./adr/ADR-0015-review-mailbox-scope.md).

```bash
export RUN=<run_id>

# decide one item
os -X POST "$ADMIN_OS/review/runs/$RUN/items/$ITEM/decision" \
   -d '{"decision":"approve"}'

# or take a different action, with parameters
os -X POST "$ADMIN_OS/review/runs/$RUN/items/$ITEM/decision" \
   -d '{"decision":"override","action":"gmail.label",
        "action_params":{"add_labels":["Admin/- Meetings"],"remove_labels":[]}}'
```

A decision records intent. Nothing has been written to Gmail yet.

---

## 2a. Archiving, filing, and Trash

All three dispositions can be named as they are spoken. `archive_gmail_thread`, `move_gmail_thread_to_label`, and `move_gmail_thread_to_trash` are accepted wherever `gmail.archive`, `gmail.move`, and `gmail.trash` are, and record the same thing.

```bash
# one row
os -X POST "$ADMIN_OS/review/runs/$RUN/items/$ITEM/decision" \
   -d '{"decision":"override","action":"move_gmail_thread_to_trash"}'

# "delete all 11" — the whole group, one decision each, one audit record each
os -X POST "$ADMIN_OS/review/runs/$RUN/groups/admin/decisions" \
   -d '{"decision":"override","action":"move_gmail_thread_to_trash"}'

# "trash 2, 4 and 7"
os -X POST "$ADMIN_OS/review/runs/$RUN/groups/admin/decisions" \
   -d '{"decision":"override","action":"move_gmail_thread_to_trash",
        "item_ids":["<row 2>","<row 4>","<row 7>"]}'
```

If any named row refuses, the whole request is refused and nothing is recorded:

```json
{"detail": {
  "message": "1 of the selected items do not permit that decision, so none was recorded: itm-4.",
  "ineligible": [{"item_id":"itm-4","thread_id":"18f…","subject":"Q3 filing",
                  "reason":"'financial_taxes' is not allowed to 'gmail.trash'."}]}}
```

Financial/Taxes is not granted Trash, deliberately. Archive removes `INBOX` and leaves every other label; Trash calls `threads.trash` and is recoverable in Gmail for thirty days. Permanent deletion is not implemented anywhere — there is no request that reaches it.

### Filing a thread in a folder

Filing keeps the thread and clears the inbox: one `threads.modify` that adds the folder and removes `INBOX`. The folder is named in `action_params`, and must be one of the capability's `gmail.destinations` — the screen carries the list as the action's `choices`.

```bash
# one row, into a named folder
os -X POST "$ADMIN_OS/review/runs/$RUN/items/$ITEM/decision" \
   -d '{"decision":"override","action":"move_gmail_thread_to_label",
        "action_params":{"label":"Later"}}'

# agreeing with a recommended move: the folder the row showed is the folder used
os -X POST "$ADMIN_OS/review/runs/$RUN/items/$ITEM/decision" \
   -d '{"decision":"approve"}'

# "file 2, 4 and 7 in Later" — one folder, one decision each
os -X POST "$ADMIN_OS/review/runs/$RUN/groups/admin/decisions" \
   -d '{"decision":"override","action":"move_gmail_thread_to_label",
        "action_params":{"label":"Later"},
        "item_ids":["<row 2>","<row 4>","<row 7>"]}'
```

A move with no folder, or a folder the capability does not list, is refused with `409` at the decision — before anything reaches Gmail:

```json
{"detail": "'admin' does not file mail in 'Career/Citi'. Its folders are: Admin, Admin/- Meetings, Admin/spam & junk, Later, Notes, General & Personal."}
```

No label is ever created. A configured folder the mailbox no longer has fails the action with `The mailbox has no label named 'Later'.`, retryably. After editing `gmail.destinations`, check the names against the mailbox:

```bash
os "$ADMIN_OS/admin/gmail/labels"
```

---

## 3. Prepare, read, execute

**Prepare** resolves the selected approvals into the exact parameters that would be sent, writes nothing, and fixes the scope. It needs the rows: a request naming neither `item_ids` nor `entire_capability` is `400`, because a missing selection is not "all of them".

```bash
os -X POST "$ADMIN_OS/review/runs/$RUN/actions/prepare" -d '{"capability_key":"admin"}'
```
```json
{"detail":"Preparation needs the exact item_ids that were selected. Send entire_capability=true with a capability_key only when every approved row in that capability was asked for."}
```

```bash
os -X POST "$ADMIN_OS/review/runs/$RUN/actions/prepare" \
   -d '{"capability_key":"admin","item_ids":["item-1","item-2","item-3"]}'
```
```json
{"scope_id": "3f0c…", "capability_key": "admin", "entire_capability": false,
 "requested_item_ids": ["item-1", "item-2", "item-3"],
 "prepared_item_ids": ["item-1", "item-2", "item-3"],
 "action_ids": ["act-1", "act-2", "act-3"],
 "excluded_items": [],
 "scope_matches_request": true,
 "gmail_write_enabled": false,
 "counts": {"total": 3, "prepared": 3},
 "actions": [{"action_id": "act-1", "action": "gmail.archive", "state": "prepared",
              "prepared_params": {"remove_labels": ["INBOX"], "thread_id": "197b351c69d3613f"},
              "idempotency_key": "9f2c…", "attempts": 0}]}
```

Preparing twice returns the same actions; the idempotency key is derived from the item, the action, and its parameters. It does **not** return the same scope: the newer preparation supersedes the older one, and the older `scope_id` stops being executable.

**Check the scope, not just the plan.** `prepared_item_ids` is what a confirmation would run. If it is not the set that was asked for — or `excluded_items` is non-empty, or `scope_matches_request` is `false` — stop and read the exclusions rather than executing the part that matches.

```bash
os "$ADMIN_OS/review/runs/$RUN/actions?state=prepared"
os "$ADMIN_OS/review/runs/$RUN/scopes/$SCOPE"     # what this scope covers, and whether it still stands
```

**Execute.** All four fields are required — the `scope_id`, the `item_ids` and `action_ids` the same preparation returned, and `confirm`. Omitting any is a refusal rather than a default:

```bash
os -X POST "$ADMIN_OS/review/runs/$RUN/actions/execute" \
   -d '{"scope_id":"3f0c…","item_ids":["item-1"],"action_ids":["act-1"]}'
```
```json
{"detail":"Executing changes the mailbox. Send confirm=true to proceed."}
```

A request missing `item_ids` or `action_ids` is `422`, and writes nothing: restating the scope is how a caller shows it read the preparation rather than remembering it.

```bash
os -X POST "$ADMIN_OS/review/runs/$RUN/actions/execute" \
   -d '{"scope_id":"3f0c…","confirm":true,
        "item_ids":["item-1","item-2","item-3"],
        "action_ids":["act-1","act-2","act-3"]}'
```

The scope runs its own action ids and nothing else. There is no capability-wide execution: `capability_key` is not a parameter here.

Each action is read back from Gmail immediately. `completed` means Gmail was asked *and* Gmail agrees. `failed` with `last_error` means it does not, and the mailbox may or may not have changed — which is why the next section exists.

Start narrowly: prepare one `item_id` and execute that scope.

### 3a. ScopeMismatch

Every scope check happens before the first write, so a `409` means **nothing was written**:

```json
{"detail":{"error":"ScopeMismatch",
           "message":"The selection being confirmed is not the one that was prepared, so nothing was executed.",
           "scope_id":"3f0c…",
           "prepared_item_ids":["item-1","item-2"],
           "requested_item_ids":["item-1","item-2","item-9"],
           "not_prepared":["item-9"],
           "prepared_but_not_requested":[]}}
```

It is returned when the scope was superseded by a later preparation, has already been executed, names rows that were decided again since it was prepared, or when the `item_ids` or `action_ids` being confirmed are not the ones prepared. The answer is always to prepare again against the selection that is actually meant — never to retry the same request.

---

## 4. When something goes wrong

```bash
os "$ADMIN_OS/review/runs/$RUN/actions?state=failed"
os "$ADMIN_OS/review/runs/$RUN/actions/$ACTION"          # + the full event trail
os -X POST "$ADMIN_OS/review/runs/$RUN/actions/$ACTION/retry"
os -X POST "$ADMIN_OS/review/runs/$RUN/actions/$ACTION/verify"   # re-reads Gmail, writes nothing
```

A retry verifies the effect before attempting it again, so a write that landed just before the connection dropped is adopted rather than repeated. Verify is safe to call at any time and is the way to answer "is this still true?" weeks later. Retry names one action explicitly and is the only execution path without a scope.

### Undoing a Trash

A thread in Trash can be taken back out of it. The group response lists what is restorable, with the request that does it:

```bash
os "$ADMIN_OS/review/runs/$RUN/groups/admin"
#    "restorable": [{"item_id":"item-4","thread_id":"19fab1e380288ea9",
#                    "action":"restore_gmail_thread_from_trash",
#                    "method":"POST","path":"/review/runs/…/items/item-4/decision",
#                    "body":{"decision":"override","action":"restore_gmail_thread_from_trash"}}]

os -X POST "$ADMIN_OS/review/runs/$RUN/items/$ITEM/decision" \
   -d '{"decision":"override","action":"restore_gmail_thread_from_trash"}'
os -X POST "$ADMIN_OS/review/runs/$RUN/actions/prepare" \
   -d '{"capability_key":"admin","item_ids":["'$ITEM'"]}'
os -X POST "$ADMIN_OS/review/runs/$RUN/actions/execute" \
   -d '{"scope_id":"<from the preparation>","confirm":true,
        "item_ids":["'$ITEM'"],"action_ids":["<from the preparation>"]}'
```

The restore is `gmail.untrash`: it removes `TRASH` from the whole thread and verifies that Gmail agrees. A thread already out of Trash completes without a write. It is granted to the capabilities that may Trash — Admin and Career — and to no others.

---

## 5. Sending a reply

Creating a draft never sends it. Sending takes two further deliberate steps.

```bash
# 1. the draft action's verification tells you what to approve
os "$ADMIN_OS/review/runs/$RUN/actions/$DRAFT_ACTION"
#    "verification": {"draft_id":"r-123","message_id":"msg-456","sent":false}

# 2. approve that exact draft — this still does not send
os -X POST "$ADMIN_OS/review/runs/$RUN/items/$ITEM/send-draft" \
   -d '{"draft_id":"r-123","draft_message_id":"msg-456","confirm":true}'
#    -> {"action":"gmail.send_draft","state":"approved"}

# 3. prepare that item, then execute the scope it produced
os -X POST "$ADMIN_OS/review/runs/$RUN/actions/prepare" \
   -d '{"capability_key":"admin","item_ids":["'$ITEM'"]}'
os -X POST "$ADMIN_OS/review/runs/$RUN/actions/execute" \
   -d '{"scope_id":"<from the preparation>","confirm":true,
        "action_ids":["<the send action id>"]}'
```

Editing the draft between steps 2 and 3 invalidates the approval:

```json
{"detail":"The draft has changed since it was approved. Review the new text and approve that draft instead."}
```

Read the draft in Gmail before approving it. This is the one action that cannot be undone.

---

## 6. Turning a correction into a rule

Corrections accumulate on their own and change nothing:

```bash
os "$ADMIN_OS/learning/events?capability_key=admin&kind=override"
os "$ADMIN_OS/learning/rules?state=observed"
```

Making one real is four explicit steps, and stopping after any of them is a valid place to stop.

```bash
# propose — written down in full, still inactive
os -X POST "$ADMIN_OS/learning/rules" -d '{
  "capability_key":"admin",
  "match":{"participant_domains":["email.informeddelivery.usps.com"],
           "subject_contains":["Daily Digest"]},
  "action":"gmail.archive",
  "rationale":"A USPS digest is a notice with nothing to act on."}'
```
```json
{"rule_id":"349a7fac-…","state":"proposed","active":false,
 "may_execute_without_approval":false,"next_states":["confirmed","retired"]}
```

```bash
# read it back and check the conditions are exactly what you meant
os "$ADMIN_OS/learning/rules/$RULE"

# confirm — it now recommends, and still cannot act
os -X POST "$ADMIN_OS/learning/rules/$RULE/confirm" -d '{}'
#    -> {"state":"confirmed","active":true,"may_execute_without_approval":false}

# promote — the narrowest grant in the system, and it needs its own confirmation
os -X POST "$ADMIN_OS/learning/rules/$RULE/promote" -d '{}'
#    -> 400 "Promotion lets this rule act without approval. Send confirm=true to grant that."
os -X POST "$ADMIN_OS/learning/rules/$RULE/promote" -d '{"confirm":true}'
#    -> {"state":"automatable","may_execute_without_approval":true}
```

Skipping a step is refused, not shortcut:

```json
{"detail":"A 'proposed' rule cannot become 'automatable'."}
```

Retirement is how a rule stops, and it is permanent:

```bash
os -X POST "$ADMIN_OS/learning/rules/$RULE/retire" -d '{"reason":"The account is closed."}'
```

Even a promoted rule only *approves*. Its approvals appear as ordinary actions with `approval_kind: automatable_rule`, and they still have to be prepared, executed, and verified — so a rule promoted in error is visible in the action list before it does anything, and the kill switch still stops it.

---

## What has been run, and where

Verified against production after deployment. Nothing was written to Gmail or Monday: the board still holds zero `Admin OS ID` values, and the one rule created was retired immediately and matches an address that does not exist.

```text
GET  /admin/db-status                    200  revision 0004_action_lifecycle
GET  /admin/capabilities                 200  2026-07-29.1, admin → financial_taxes → career_advisor_calls
GET  /admin/gmail/status                 200  all three labels found, write_enabled:false
POST /review/start                       200  resumed today's run
GET  /review/runs/{run}/actions          200  no actions, gmail_write_enabled:false
POST /review/runs/{run}/actions/prepare  200  nothing approved, nothing written
POST /review/runs/{run}/actions/execute  400  confirm=true required
POST …/execute {"confirm":true}          409  Gmail writes are disabled
POST …/items/{item}/send-draft           400  confirm=true and the exact draft ids required
POST /learning/rules                     201  proposed, active:false
POST /learning/rules/{id}/promote        409  a 'proposed' rule cannot become 'automatable'
POST /learning/rules/{id}/retire         200  retired, no further states
GET  /learning/events                    200  empty; no decisions taken yet
any route without a key                  401
```

Also verified against this branch running locally, with a real database and Gmail deliberately unconfigured, for the paths that cannot be exercised safely in production:

```text
POST /review/start                     200  new run, three groups in Admin-first order
GET  /learning/rules/{id}              200  exact match conditions returned
POST /learning/rules/{id}/confirm      200  confirmed, active:true, automatable:false
POST /learning/rules/{id}/promote      400  needs confirm=true
POST /learning/rules/{id}/promote      200  automatable, may_execute_without_approval:true
POST /learning/rules (empty match)     422  refused
POST …/execute {"confirm":true}        503  Gmail not configured
```

The paths that write to Gmail are covered by tests against a fake mailbox, not by this runbook. They are deliberately unexercised in production until `GMAIL_WRITE_ENABLED` is turned on under supervision.

## Turning writes on for the first time

The deployment itself is verified above; what follows is the first real write.

1. Wait for a run created *after* the deployment. A run resumed across a configuration change keeps the recommendations its items were given, so an item opened under `admin.v1` stays `needs_review` even where an `admin.v2` rule would now match it. The group reports the `policy_version` it was populated under, which is how to tell.
2. Approve exactly one archive, prepare **that one `item_id`**, and read `prepared_params`. Confirm it names the thread you expect and removes only `INBOX`. For a move, confirm `add_labels` names the folder you meant and `remove_labels` is `["INBOX"]` and nothing else. Confirm `prepared_item_ids` is that one item and `excluded_items` is empty.
3. Set `GMAIL_WRITE_ENABLED=true` in Railway and redeploy.
4. Execute that scope — `{"scope_id":"…","confirm":true,"item_ids":["…"],"action_ids":["…"]}` — which is the only thing it can run.
5. Check the thread in Gmail, and check the action reports `completed` with its verification detail.
6. Leave the switch on only while watching. Setting it back to `false` stops everything already approved.
