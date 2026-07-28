# 77 — First Slice Setup Runbook

**Status:** Active
**Version:** 0.1
**Purpose:** The manual, one-time steps required before the first vertical slice can run. Everything here is done by the account owner; none of it can be automated.
**Depends on:** [ADR-0002](./adr/ADR-0002-monday-identity-and-idempotency.md), [ADR-0003](./adr/ADR-0003-gmail-access-and-retention.md), [76 — Repository Assessment](./76-Repository-Assessment.md)

Three tasks: a Monday board column, a Railway Postgres database, and a Google OAuth credential.

---

## 1. Monday.com — add the `Admin OS ID` column

**Done.** The **To Do List** board (`8962223984`) has a text column titled `Admin OS ID` (id `text_mm5prcay`), and its `Status` column offers `Not Yet Started`, `In Progress`, and `Done` — so completion is unambiguously `Done`.

Keep the column hidden from the default view. Nothing should ever type into it by hand.

Why it exists: Monday's API has no idempotency token, so Admin OS writes its own identifier into the item and searches for it before retrying a create. Without it, a crash between the Monday write and the local commit duplicates the task. See [ADR-0002](./adr/ADR-0002-monday-identity-and-idempotency.md).

---

## 2. Railway — provision PostgreSQL

**Done.** The `timmeny-todo-os` project (production environment) now has a `Postgres` service running PostgreSQL 18.4, and the `timmeny-admin-os` service has:

```text
DATABASE_URL = ${{Postgres.DATABASE_URL}}
```

It is a *reference*, not a literal connection string, so it keeps working if Railway rotates the credential. It resolves to `postgresql://postgres:***@postgres.railway.internal:5432/railway` — note the `postgresql://` scheme, which SQLAlchemy maps to psycopg 2; `normalize_database_url` rewrites it to `postgresql+psycopg://`.

The baseline migration has already been applied, so the first deploy's pre-deploy step is a no-op.

Migrations run as a pre-deploy step, not at import time, so a failed migration stops the deploy rather than taking a live service down mid-request. `railway.json` already carries it:

```json
"deploy": {
  "preDeployCommand": ["sh scripts/migrate.sh"],
  "startCommand": "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"
}
```

`scripts/migrate.sh` exits successfully without doing anything when `DATABASE_URL` is unset, so deploys keep working in an environment that has no database.

Once the database is attached, `GET /admin/db-status` (authenticated) reports `{"status": "ok", "revision": "0001_baseline"}`.

---

## 3. Google — create the OAuth credential

This produces three values: `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`.

A service account cannot be used. Domain-wide delegation requires a Workspace domain admin, and this is a personal `gmail.com` mailbox, so access must come from a credential the account owner consents to once.

### 3a. Project and API

1. Go to https://console.cloud.google.com/ and create a project (e.g. `timmeny-admin-os`).
2. Enable the Gmail API: **APIs & Services → Library → Gmail API → Enable**.

### 3b. Consent screen — publish it to production

Under **APIs & Services → OAuth consent screen** (newer consoles present this as **Google Auth Platform → Branding / Audience**):

1. User type: **External**.
2. Fill in app name, your email as support contact, and your email as developer contact. Nothing else is required.
3. Add the scope `https://www.googleapis.com/auth/gmail.modify`. Google will flag it as sensitive/restricted; that is expected.
4. **Publish the app.** On the consent screen (or **Audience**) page, set the publishing status from **Testing** to **In production**.

Step 4 is not optional. Google issues a refresh token that **expires after 7 days** to any external consent screen left in `Testing` status ([documented here](https://developers.google.com/identity/protocols/oauth2)). A background service authenticated that way stops working every week. Publishing does not require Google verification — because the app is unverified you will see an "unverified app" warning during consent, click **Advanced → Go to … (unsafe)**, and proceed. That warning is expected and appears once.

### 3c. OAuth client

1. **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
2. Application type: **Desktop app**. Name it anything.
3. Copy the **Client ID** and **Client secret**.

### 3d. Mint the refresh token

Run this on your own machine, so your Google session never touches anything else. It starts a temporary local server, opens your browser, and prints the refresh token.

```bash
python3 -m venv /tmp/oauth && /tmp/oauth/bin/pip install -q google-auth-oauthlib
GMAIL_CLIENT_ID="<client id>" GMAIL_CLIENT_SECRET="<client secret>" /tmp/oauth/bin/python - <<'PY'
import os
from google_auth_oauthlib.flow import InstalledAppFlow

flow = InstalledAppFlow.from_client_config(
    {
        "installed": {
            "client_id": os.environ["GMAIL_CLIENT_ID"],
            "client_secret": os.environ["GMAIL_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    },
    scopes=["https://www.googleapis.com/auth/gmail.modify"],
)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
print("\nGMAIL_REFRESH_TOKEN=" + creds.refresh_token)
PY
```

`prompt="consent"` matters — without it Google reuses a prior grant and returns no refresh token.

Sign in as the mailbox owner, accept the unverified-app warning, and grant the single Gmail permission. The refresh token is printed at the end.

### 3e. Store the values

Add to Railway (**Variables** on the `timmeny-admin-os` service):

| Variable | Value |
|---|---|
| `GMAIL_CLIENT_ID` | from 3c |
| `GMAIL_CLIENT_SECRET` | from 3c |
| `GMAIL_REFRESH_TOKEN` | from 3d |
| `GMAIL_INTAKE_LABEL` | `financial/taxes` |
| `GMAIL_WRITE_ENABLED` | `false` |

`GMAIL_WRITE_ENABLED` stays `false` until the read-and-classify loop has been observed working on real threads. Nothing in the mailbox is modified while it is false.

Treat the refresh token as a mailbox credential: it grants read, label, and archive on the account until you revoke it at https://myaccount.google.com/permissions.

### 3f. Confirm it works

```bash
curl -s https://timmeny-admin-os-production.up.railway.app/admin/gmail/status \
  -H "X-API-Key: $TIMMENY_OS_API_KEY"
```

`configured` and `intake_label_found` should both be `true`. Then record the labelled threads as evidence:

```bash
curl -s -X POST "https://timmeny-admin-os-production.up.railway.app/admin/gmail/sync?limit=25" \
  -H "X-API-Key: $TIMMENY_OS_API_KEY"
```

The sync only reads. It creates no Monday task, changes no Gmail label, and archives nothing.

---

## Verification checklist

- [x] `Admin OS ID` text column exists on the To Do List board and is hidden from the default view
- [x] The board's `Status` done-labels are confirmed: completion is `Done`
- [x] Railway Postgres service exists and `DATABASE_URL` resolves on the app service
- [x] Schema applied: `alembic_version` reports `0001_baseline`
- [ ] Consent screen publishing status reads **In production**
- [ ] Only `gmail.modify` is granted — check https://myaccount.google.com/permissions
- [ ] `financial/taxes` label exists and contains the threads intended as evidence
- [ ] `GMAIL_WRITE_ENABLED` is `false`
- [ ] `GET /admin/gmail/status` reports `configured: true` and `intake_label_found: true`
- [ ] `POST /admin/gmail/sync` returns a non-zero `scanned` count
