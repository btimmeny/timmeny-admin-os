# ADR-0020 — A Session Opens With the Playbook, in Admin OS's Words

**Status:** Accepted
**Date:** 2026-08-08
**Depends on:** [ADR-0011](./ADR-0011-presentation-contracts.md), which made presentation a contract Admin OS owns; [ADR-0019](./ADR-0019-a-review-states-its-plan.md), which made the plan precede the first group.

## Context

"Good morning" was answered with a table, or worse, with "How can I help?" — a question Brian has already answered by saying good morning to an admin assistant. Neither says what the next twenty minutes consist of, that they follow a shared way of working, or that the way of working is his to change.

The obvious remedy is to tell the GPT to say it. That is the remedy ADR-0018 records failing: instructions the GPT holds are followed until they are inconvenient, cannot be versioned with the workflow they describe, and cannot be tested. An orientation composed fresh each morning is also an orientation that drifts — one day a promise to guide him through, the next a summary of his inbox.

The second half of the problem is repetition. An orientation said before every group is noise, and noise is skipped, which is how a safeguard becomes decoration.

## Decision

**The opening is Admin OS's text, returned in the response.** `plan.opening` carries `mode` and `text`: what will be worked through, that it follows the playbook Brian and the agent hold together, and that the playbook evolves by agreement. The GPT prints it as written, first, ahead of the screen and anything else. It renders; it does not compose.

**The words are configuration.** `opening.new` and `opening.resumed` live in `config/capabilities.yaml`, so they are versioned with the workflow they describe: every review records the configuration version that produced it, and changing the opening is an edit with a version, not a new sentence somebody tried. Defaults live beside the model, so an omitted section is not an empty greeting.

**Two modes, and Admin OS chooses.** `new` lays a morning out; `resumed` carries one on. A review that exists but has settled nothing is still new — what makes a review resumable is work in it, not a row in a table — so opening the app twice before breakfast does not produce a spurious "continuing from where we left off".

**Said once on entering, and structurally unable to be said twice.** Only `startDailyReview`, `continueDailyReview` and `restartDailyReview` carry an opening. `beginReviewPlan`, decisions, group reads and every other response carry `null`. A caller cannot repeat between groups what it is not given twice, which is a stronger guarantee than asking it not to.

**A finished review gets no opening.** Its prompt already says the day was worked and offers to review it again; promising to guide him through a review that is not happening would be the orientation lying about the state of the world. Restarting from that prompt opens with the new-review opening, because that is what it then is.

## Alternatives Considered

**Put the copy in the GPT instructions.** Rejected. It is the approach ADR-0018 watched fail, it cannot be versioned with the workflow, and no test can hold a paste-in field to it. The instructions now carry one rule — print `plan.opening.text` first, never "How can I help?" — which is a rule about rendering, the thing the GPT is for.

**Hard-code the copy in Python.** Rejected: the opening is workflow, and workflow is configuration here. It is defaulted in code so the wording cannot go missing, and overridden in YAML so it can change without a deploy.

**Return the opening on every response and ask the GPT to show it once.** Rejected. "Show this once" is a rule that fails silently and reads as nagging when it does.

**Detect the greeting server-side and orient from the phrase.** Rejected. Admin OS is not in the conversation; it has no message to classify. Mapping "good morning", "check my inbox" and "pick up where we left off" onto start and continue is the GPT's job, and the openings differ by what the *review* is, not by what was typed.

**Include the opening in `plan.message`.** Rejected. The message says what today's review consists of and changes with the review; the opening says what the practice is and does not. Merging them means neither can be edited without disturbing the other.

## Consequences and Tradeoffs

- Every entry into a review costs a paragraph before the screen. That is the point, and it is bounded: one paragraph, once.
- Changing the opening changes the configuration version, so reviews made before and after are told apart by the version they recorded.
- The GPT can still ignore the field. What has changed is that ignoring it is visible in the transcript against a text the service published, rather than a difference of opinion about instructions.
- `plan.opening` is nullable and additive: the contract moves to 0.16.0, no request shape changes, and a stale import keeps working with no orientation rather than failing.
- Configuring an opening per capability, per scope, or per weekday is deliberately not possible yet. One practice, one set of words, until there is a reason.

## Affected Documents

- `config/capabilities.yaml` — the `opening` section.
- `docs/gpt-action-openapi.yaml` — `ReviewOpening`, referenced by `ReviewPlan`.
- `docs/gpt-daily-review-instructions.md` — "Every session opens with the playbook".
- `README.md` — how a session begins.
