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

## 1. Before the first execution

**Gmail writes stay off until each action class has been watched on real mail.** Until then every execution answers `409`, which is the correct answer rather than a fault:

```bash
os -X POST "$ADMIN_OS/review/runs/$RUN/actions/execute" -d '{"confirm":true}'
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

## 3. Prepare, read, execute

**Prepare** resolves every approval into the exact parameters that would be sent, and writes nothing:

```bash
os -X POST "$ADMIN_OS/review/runs/$RUN/actions/prepare" -d '{"capability_key":"admin"}'
```
```json
{"gmail_write_enabled": false,
 "counts": {"total": 1, "prepared": 1},
 "actions": [{"action_id": "…", "action": "gmail.archive", "state": "prepared",
              "prepared_params": {"remove_labels": ["INBOX"], "thread_id": "197b351c69d3613f"},
              "idempotency_key": "9f2c…", "attempts": 0}]}
```

Preparing twice returns the same action; the idempotency key is derived from the item, the action, and its parameters.

**Read the plan** before running it. This is the point at which a wrong label or a wrong thread is cheap:

```bash
os "$ADMIN_OS/review/runs/$RUN/actions?state=prepared"
```

**Execute.** `confirm` is required, and omitting it is `400` rather than a default:

```bash
os -X POST "$ADMIN_OS/review/runs/$RUN/actions/execute" -d '{}'
```
```json
{"detail":"Executing changes the mailbox. Send confirm=true to proceed."}
```

```bash
os -X POST "$ADMIN_OS/review/runs/$RUN/actions/execute" \
   -d '{"confirm":true,"capability_key":"admin"}'
```

Each action is read back from Gmail immediately. `completed` means Gmail was asked *and* Gmail agrees. `failed` with `last_error` means it does not, and the mailbox may or may not have changed — which is why the next section exists.

Start narrowly. `{"confirm":true,"action_ids":["…"]}` runs exactly one.

---

## 4. When something goes wrong

```bash
os "$ADMIN_OS/review/runs/$RUN/actions?state=failed"
os "$ADMIN_OS/review/runs/$RUN/actions/$ACTION"          # + the full event trail
os -X POST "$ADMIN_OS/review/runs/$RUN/actions/$ACTION/retry"
os -X POST "$ADMIN_OS/review/runs/$RUN/actions/$ACTION/verify"   # re-reads Gmail, writes nothing
```

A retry verifies the effect before attempting it again, so a write that landed just before the connection dropped is adopted rather than repeated. Verify is safe to call at any time and is the way to answer "is this still true?" weeks later.

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

# 3. execute it like any other action
os -X POST "$ADMIN_OS/review/runs/$RUN/actions/execute" \
   -d '{"confirm":true,"action_ids":["<the send action id>"]}'
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
2. Approve exactly one archive, prepare it, and read `prepared_params`. Confirm it names the thread you expect and removes only `INBOX`.
3. Set `GMAIL_WRITE_ENABLED=true` in Railway and redeploy.
4. Execute that one action by id — `{"confirm":true,"action_ids":["…"]}` — not the whole run.
5. Check the thread in Gmail, and check the action reports `completed` with its verification detail.
6. Leave the switch on only while watching. Setting it back to `false` stops everything already approved.
