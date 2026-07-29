# ADR-0018 — A Decision Is Not a Done Thing, and the Review Says So

**Status:** Accepted
**Date:** 2026-08-07
**Depends on:** [ADR-0014](./ADR-0014-execution-scope-integrity.md), which made execution take an exact scope; [ADR-0011](./ADR-0011-presentation-contracts.md), whose screens are what Brian actually reads.

## Context

Three Admin rows were told to go to Trash. The bulk decision was recorded, the review presented the next capability, and the GPT reported the messages deleted. Nothing had been prepared, confirmed or executed, and the three threads were in the inbox where they started.

Every safeguard held. Approval creates an action in `approved` and writes nothing; execution needs a prepared `scope_id`, the exact item and action ids, and `confirm: true` (ADR-0014). What failed was everything Brian and the GPT could see. A decided row read `Approved: Move it to Trash`, which is a sentence about a thread in the Trash. The group was finished with as far as the review was concerned, so the next group appeared — and a review that has moved on is a review that is done with what it moved on from. Nothing in any response said "this has not happened".

The same session began with a refusal: the group was addressed as `admin.v2`, and the answer was that no capability of that name is configured. True, and useless. `admin.v2` is the version of Admin's recommendation policy; `admin-review-v1` is its screen. A capability publishes three names and only one of them addresses it, so sending the wrong one is the ordinary mistake, not the exotic one.

## Decision

**A group holding decisions that have not reached the mailbox stays the current group.** `current_group` is the first group not `completed`, so `awaiting_actions` is somewhere the review waits rather than somewhere it passes through. Another group may still be worked by naming it directly; what no longer happens is the review offering the next one unasked, which is the whole of how "I decided" came to read as "it happened".

**A decided row says both what was decided and whether it has happened.** `Move it to Trash — decided, not yet done`, and `— done` only once the action has executed and Gmail has been read back. The two facts are said in one cell because they were read as one thing.

**A group with decided rows carries a notice, written by Admin OS rather than by the screen's configuration.** That decisions have not reached the mailbox is a fact about the review, and a presentation contract must not be able to leave it out.

**`outstanding_execution` is the machine-readable form of the same statement, on the group and on the run.** It names the capability, the exact `item_ids` waiting, and the exact request that carries them out — operation, method, path and body. It is absent where nothing is outstanding, so its presence is itself the answer to "has this happened yet?".

**An unknown capability key says which name was sent and which was meant.** Where the key given is a capability's policy version or screen id, the refusal says so and names the key; otherwise it lists the keys that exist. The request is still refused: guessing the capability from a version is how a decision reaches rows nobody chose.

## Alternatives Considered

**Execute on approval, since approving is Brian saying what should happen.** Rejected, and it is the reading that makes the bug disappear. Approving is a decision about one row among twenty; confirming is a decision about a set of writes, made with the prepared scope in front of him. Collapsing them removes the only place where "these nineteen, not those three" can be checked (ADR-0014).

**Accept `admin.v2` as an alias for `admin`.** Rejected. A policy version is a version of what a capability recommends, and today's `admin.v2` is tomorrow's `admin.v3`: an alias that works this week and silently addresses nothing next week is worse than a refusal. The refusal now names the key.

**Say it in the footer.** Rejected. The footer is progress, and it is configured per screen — a screen could omit it, and a review can't be allowed to omit this.

**Block the next group entirely until the outstanding one is executed.** Rejected. Brian may reasonably want to leave rows decided and unexecuted while he works elsewhere, and a review that refuses to show anything else is a review that has to be fought. The group stays current; naming another still works.

**Leave the wording to the GPT, and tell it in the instructions.** Rejected. The instructions already said not to claim completion, and the GPT read `Approved: Move it to Trash` off the table and believed it. The table is what gets read.

## Consequences and Tradeoffs

- The review no longer advances on its own past a decided group, so a session that decides everything in one capability and never executes will keep presenting it. That is the intent, and it is a change in feel.
- Every decided row's cell text changed, and anything asserting on `Approved:` breaks. The tests moved with it.
- `outstanding_execution` is a second place a response says what is outstanding, alongside `state: awaiting_actions`. It is the one that carries the request, and the contract points the GPT at it.
- A `failed` action counts as outstanding. It has been attempted and it has not happened, which is the question the field answers, and the notice says "attempted and failed" rather than pretending it is merely waiting.
- The contract is 0.14.0. Nothing about a request body changed, so the shape fingerprint is unchanged under the new version; what moved is what the GPT is told about the responses.

## Affected Documents

- [ADR-0014](./ADR-0014-execution-scope-integrity.md) — the exact scope this surface now guides Brian into
- [ADR-0011](./ADR-0011-presentation-contracts.md) — the screen contract the notice sits above
- [Daily Review GPT instructions](../gpt-daily-review-instructions.md)
- [GPT Action contract](../gpt-action-openapi.yaml)
