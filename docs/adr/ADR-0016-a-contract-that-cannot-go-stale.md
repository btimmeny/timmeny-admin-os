# ADR-0016 — The Contract the GPT Holds Is the One This Service Serves

**Status:** Accepted
**Date:** 2026-08-05
**Depends on:** [ADR-0014](./ADR-0014-execution-scope-integrity.md), which defined the scope this contract has to describe.
**Extends:** [ADR-0014](./ADR-0014-execution-scope-integrity.md).

## Context

ADR-0014 made the server refuse an execution that is not the selection that was prepared. The Custom GPT reaches that server through an OpenAPI document pasted into ChatGPT, and that document is a copy: taken by hand, on some day, from a file in this repository. Nothing in ChatGPT states which version it holds, and nothing in this repository knows what was imported.

Two failures follow, and they are quiet ones. A schema that lags the API sends request bodies the API has stopped accepting, which surfaces as a refusal in the middle of Brian's review rather than in CI. A schema that runs ahead of a deployment — imported from a commit that was never released — promises fields the running service does not have. Either way the first person to find out is the person using it, and the diagnosis is "which copy is this?", which nobody can answer.

The execution contract made the second problem concrete. `item_ids` and `action_ids` were documented and accepted but optional: a caller could confirm a scope it had never read, and the restatement — the corroboration ADR-0014 relies on — was only performed by callers that chose to. Making it required is right, and it is exactly the kind of change a stale copy turns into a mystery.

## Decision

**The deployment serves the contract it was built with.** `GET /gpt/action-schema.yaml` returns `docs/gpt-action-openapi.yaml` from the running service. Importing the GPT Action from that URL makes the schema and the API the same commit by construction, which is the only way the two cannot drift.

**The contract states its version, and the version is checkable while deployed.** `GET /gpt/action-schema/version` returns the version, a fingerprint of every request body in the document, the document's hash, and the commit the platform recorded. An import that is already in place can be compared against what is running without re-importing it.

**`info.version` changes whenever a request shape changes.** Prose, examples, and response fields may be improved in place; a request body may not. `tests/test_adminos_gpt_schema.py` records the fingerprint each published version carried, so changing a shape under an existing version fails a test rather than reaching a GPT that cannot tell.

**Execution requires the scope restated in full.** `scope_id`, `item_ids`, `action_ids`, and `confirm: true`. A request missing any of them is invalid and writes nothing. A caller that cannot restate what it is running has not read the preparation, and this is the one request that changes the mailbox.

**Both schema routes are unauthenticated.** ChatGPT's import sends no headers, so a key-protected document could not be imported from the URL at all. The document describes operations, not data, and every one of those operations still requires the API key.

## Alternatives Considered

**Leave the schema a file and be careful.** Rejected. It is the current arrangement, and its failure mode is invisible: nothing anywhere states which copy is loaded. Care is not a mechanism.

**Serve FastAPI's generated `/openapi.json` to the GPT instead.** Rejected. The generated document describes every operational route, including ones a GPT should never call, and carries none of the prose that tells it *when* to call the rest. The hand-written contract is deliberately smaller than the API; the test that every documented operation exists is what keeps it honest.

**Generate the GPT contract from the app at build time.** Rejected for now, and worth revisiting. It would remove the possibility of the two disagreeing about shapes, but the value of this document is largely its descriptions — which rows to send, what a `409` means, what not to infer — and those are not derivable from the code.

**Keep `item_ids` and `action_ids` optional and rely on the GPT to send them.** Rejected on the same grounds as ADR-0014: a safeguard that only operates when the caller chooses it is not a safeguard. The scope is still server-held; the restatement is corroboration, and corroboration that can be skipped corroborates nothing.

**Version the schema by date, or by commit.** Rejected. A commit hash says everything changed and nothing about whether it matters; a date says nothing at all. A version that moves when request shapes move is the fact a caller needs.

## Consequences and Tradeoffs

- A request omitting `item_ids` or `action_ids` now fails validation. That is a breaking change to the route the GPT uses, and like ADR-0014's it is meant to break.
- The GPT Action must be re-imported from the served URL after any version change. That is a manual step in ChatGPT and nothing here can perform it; the version endpoint is how it can be checked rather than remembered.
- Two unauthenticated routes exist on a service that otherwise has none. They read one file and one environment variable.
- The fingerprint ignores descriptions, so a shape can be re-described freely and a version bump is not forced by prose. The converse is that a purely descriptive fix does not tell an existing import to refresh.
- The record of published fingerprints is a test constant. It is honest about history but not tamper-proof: editing both the shape and its recorded fingerprint under one version would pass. It makes the change visible in review, which is the intent.

## Affected Documents

- [README](../../README.md) — The GPT Action contract
- [ADR-0014](./ADR-0014-execution-scope-integrity.md) — the scope this contract describes
- [81 — Action Execution Runbook](../81-Action-Execution-Runbook.md)
- [Daily Review GPT instructions](../gpt-daily-review-instructions.md)
