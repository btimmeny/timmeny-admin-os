# ADR-0011 — Presentation Is a Versioned Contract Owned by Admin OS

**Status:** Accepted
**Date:** 2026-07-30
**Depends on:** [ADR-0001](./ADR-0001-admin-os-coordination-layer.md), [ADR-0009](./ADR-0009-review-engine-implementation.md), [ADR-0010](./ADR-0010-action-lifecycle-and-learning.md)

ADR-0009 decided what a review *is*. This ADR decides who decides what it *looks like*.

## Context

Until now the review API returned facts — items, states, recommendations, confidences — and left the presentation to whatever asked for them. In practice that means the GPT: it chooses the columns, the order, the wording, whether a confidence of `0.85` reads as "85%" or "high", and whether a thread with no recommendation is shown at all.

That arrangement has three costs, and they compound.

The first is instability. The layout lives in a prompt, so it varies between renderings of the same data, and there is no version to point at when it changes. "It showed me something different yesterday" has no answer.

The second is invention. A renderer given raw fields and no contract fills the gaps: it summarises rationales it finds long, drops columns it judges uninteresting, offers a "delete" that does not exist, and infers urgency the service never asserted. Each is individually reasonable and collectively a system whose behaviour nobody specified.

The third is the boundary. ADR-0001 puts domain meaning in Admin OS and reasoning in the GPT. Presentation had quietly landed on the wrong side of that line: deciding that "Key Facts" means sender and age, or that an item with nothing recommended still deserves a row, is domain meaning, not conversation.

There is a real argument for the other side, and it should be stated. A language model is good at presenting things, adapting to the question actually asked, and a rigid table is a worse answer to "what's urgent?" than a sentence. That argument loses here for one reason: this review authorises changes to a mailbox. What the reader saw when they said "yes to row 3" has to be reconstructable, and it cannot be if the rendering was improvised.

## Decision

**A screen is configuration, in the same file as the capability it renders.** `config/capabilities.yaml` gains a `screens:` section, and each capability names the one it uses. Nothing about layout lives in code or in a prompt; changing what a review looks like is an edit to a reviewed file, and every run already records the version and digest of that file.

**Screens are versioned by id, and the id travels with the data.** `admin-review-v1`, `tax-review-v1`, `advisor-review-v1`. Every review response carries a `screen_id`, so any rendering can be traced to the exact contract that produced it. Changing a screen compatibly is an edit; changing it incompatibly is a `-v2` and a capability pointed at it. The three capabilities share one column set today through a YAML anchor — writing the columns out in one of them is how it stops sharing, with no code change.

**The service sends finished cells, not fields to be formatted.** A row is `cells`: strings, one per column, in the columns' order. Truncation, percentages, relative dates, the wording of an action, and what an absent value looks like are all decided here. The contract can be honoured by a renderer that understands nothing about email.

**A column may only name a value the service computes.** Columns declare a `source` from a closed set, each with a value type, and a format valid for that type. A screen asking for a column that does not exist, or for a percentage of a subject line, fails to load rather than rendering as blank.

**The contract carries the request each decision makes.** Every offered action names its method, its path — already filled in with the run and capability — and its body. A renderer needs no route knowledge, and a path that does not exist cannot be advertised, because the service constructs it.

**A screen may not offer what the capability cannot do.** Two checks, one static and one per row. At load, an action offered to a capability that is not allowed it, or a whole-group decision offered to a capability that does not take bulk decisions, is a configuration error. At render, each row lists the offered actions *that row* would accept, asked of the same `check_decision` the decision endpoint runs — so the contract and the enforcement cannot drift apart. A row with nothing recommended does not offer "approve"; a settled row offers nothing.

**The GPT renders and converses; it does not lay out.** It prints the columns and the cells as given, prints the footer, and sends decisions using the requests in the contract. Prioritising, explaining, and answering questions about what is on the screen remain its work.

## Alternatives Considered

1. **Leave presentation to the GPT and describe the intent in instructions.** Rejected: instructions are not versioned with the data, cannot be enforced, and are silently reinterpreted. The failure mode is a rendering nobody chose and nobody can reproduce.
2. **Send raw fields and a column list, letting the renderer format.** Rejected as a half-measure: the interesting decisions are the formatting ones — what 0.0 confidence looks like, how long a rationale may be, how an age reads. Splitting them across the boundary means neither side owns the result.
3. **Serialise a rendered table as text or Markdown.** Rejected: it forecloses non-textual renderers and hides the structure that makes a decision referable to a row. Cells plus columns are as prescriptive without being a dead end.
4. **A separate `/screens/{id}` endpoint the renderer fetches once and caches.** Rejected for now: a cached contract can be stale against the data it renders, which is the failure this ADR exists to prevent. Returning them together costs a few hundred bytes.
5. **One screen shared by all capabilities.** Rejected: the capabilities already differ in what they may do — Financial/Taxes may not send, Admin may archive in bulk — and a single screen would either offer too much or too little. Separate ids also let one capability's presentation change without touching the others.

## Consequences

- Review responses are larger, and carry the columns on every call.
- A layout change now requires a configuration edit and a deployment. That is the intended cost: it makes the change reviewable and dateable.
- The GPT's instructions get shorter and more prohibitive: render this, do not compose your own.
- Two contracts must be kept honest against each other — what a screen offers, and what a capability permits — which is why both checks are enforced rather than documented.
- `tax-review-v1` and `advisor-review-v1` exist and are wired up, but only Admin has recommendation rules behind it, so they currently render mostly `needs_review`.

## Not Yet Implemented

- Screens for anything but a table. `kind` exists and accepts one value.
- Row limits and paging. A group with a hundred threads renders a hundred rows.
- Per-row emphasis — urgency, overdue, blocked — which the service does not yet assert.
- A contract for the action lifecycle and rule-learning views; those endpoints still return fields alone.

## Affected Documents

- [README](../../README.md) — Presentation
- [ADR-0009](./ADR-0009-review-engine-implementation.md) — the review this renders
- [docs/gpt-action-openapi.yaml](../gpt-action-openapi.yaml) — the screen schemas
- [82 — Daily Review GPT Instructions](../gpt-instructions/daily-review-gpt-instructions.md)

## Validation

- A test asserts that `admin-review-v1` has exactly the agreed columns, with the agreed labels, in the agreed order.
- A test asserts that every enabled capability resolves its own screen.
- Tests assert that cells are finished text in column order: a percentage where a percentage is asked for, a placeholder where the value is absent, a truncation where the contract sets a limit.
- Tests assert that rows are ordered by the contract, and that a thread with no date sorts last in either direction rather than leading the table.
- Tests assert that a screen cannot ask for a value the service does not compute, cannot format a value in a way its type does not allow, cannot order by a value with no order, and cannot use an unknown footer substitution.
- Tests assert that a capability may not reference a screen that does not exist, that a screen may not offer an action the capability is not allowed, and that it may not offer a bulk decision where bulk decisions are refused.
- Tests assert that a row offers only the decisions it would accept, checked against the same function the decision endpoint uses.
- A test drives a decision entirely from the contract — method, path, body — and asserts it is recorded, so the contract is sufficient on its own.
