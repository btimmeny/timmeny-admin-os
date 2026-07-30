# ADR-0023 — A Session Runs a Playbook, and the Playbook Is Configuration

**Status:** Accepted
**Date:** 2026-08-10
**Extends:** [ADR-0019](./ADR-0019-a-review-states-its-plan.md), the plan a review states; [ADR-0020](./ADR-0020-a-session-opens-with-the-playbook.md), the words a session opens with.

## Context

The daily review was the whole of admin because it was the only part built. Brian's admin is larger than his inbox: objectives, to-dos, the calendar, follow-ups, and a closeout that says what the morning actually did. What he asked for is a wake-up that reads the order out — email first, then objectives, then to-dos — and an order he can change by saying so.

Two things could hold that order. It could live in the GPT's instructions, where it is prose nobody can version and no test can check, and where "always do objectives first" is a sentence in a text box that any re-paste undoes. Or it could be configuration Admin OS owns, with revisions, validation and a confirmation step. ADR-0020 already decided this for the opening words, for the same reason, and the argument is stronger for the order of the work than for the sentence that introduces it.

The harder problem is honesty. Objectives, the calendar and follow-ups have no data source here. A plan that reads them out as though they will happen is a plan that lies once a morning, and one that silently drops them is a plan that answers a different question from the one Brian asked.

## Decision

**The playbook is versioned configuration, and the database holds it.** `config/assistant-playbook.yaml` is a seed: the first revision written when there is none. After that Brian changes the playbook by asking, and every change writes a new revision and leaves the old one where it was. A revision is `proposed`, `active`, `superseded` or `invalid`.

**A change is proposed, read back, and confirmed, in that order.** `POST /playbook/propose` writes down what the playbook would become and changes nothing; it returns the effect in sentences, the order before, the order after, and the exact request that would make it so. `POST /playbook/revisions/{id}/confirm` takes `confirm: true` and nothing else. A correction said once in a morning does not become how every morning works.

**A session is one run of one revision.** `POST /session/start` loads the playbook in force, pins that revision on the session, reads the mailbox afresh, and states the plan without presenting any of the work. Confirming a playbook change halfway through a morning changes the next session: rearranging a morning around a change made in the middle of it would be answering a question with a different question.

**Session-only wishes are recorded on the session.** "Skip objectives today", "calendar first this time", "only email tonight" are `order`, `only` and `skip` on the session, durably recorded, and reported back in `plan.overrides`. They never touch the playbook, and the response says which they were, so a session that ran differently can be seen to have run differently.

**An activity that is named and unbuilt is `unavailable`, and is persisted as such.** The registry knows six activities and where each one's data comes from; two are built. The rest are rendered in the plan in their configured place, marked unavailable with the source they will come from, and are neither hidden nor counted as work done. Validation makes this the only possible outcome: an unknown key is an error naming the exact path, and a known-but-unbuilt activity is a warning the response carries.

**Activity state and execution state are counted separately.** `advanceSession` asks the work whether it is finished rather than asserting it: an email review holding decisions the mailbox has not seen is not finished, and the session stays where it is. `closeout` reports activities completed, skipped and unavailable alongside `awaiting_execution` — the rows decided whose action Gmail never saw. A session can be through every activity and still owe the mailbox every write.

**Validation is recomputed on every read.** A playbook is valid against a set of capabilities, and capabilities change underneath it. A revision naming a capability since removed is marked `invalid`, the last revision that still works is put back in force, and the session says which playbook it is actually running rather than refusing to open at all.

**A proposal the playbook has moved on from is refused.** Confirmation checks that the revision was written against the revision still in force. Otherwise confirming an older proposal would quietly undo whatever was agreed to in between, and what Brian confirmed would not be what he was read.

## Alternatives Considered

**Put the order in the GPT's instructions.** Rejected, on ADR-0020's grounds. An order that lives in a text box cannot be versioned, cannot be validated against the capabilities that exist, and cannot record that Brian agreed to it. "Always do objectives first" would be true until the next paste.

**Let a conversational preference change the playbook directly.** Rejected. The distinction Brian drew — a wish about today versus a change to how we work — is the whole point, and a system that cannot tell them apart makes every offhand sentence permanent. Two calls is the price of that distinction, and only the persistent one pays it.

**Omit unbuilt activities from the plan until they exist.** Rejected. The plan is what Admin OS and Brian have agreed to work through; an activity he asked for and that is not built yet is a fact about this service, not a fact about his admin. Saying "objectives: not built here yet" costs a line and keeps the plan true.

**Fold the review's own plan into the session's.** Rejected for now. The review's plan is over capability groups within one snapshot and has its own lifecycle, counts and standing; collapsing the two would make the session's plan change shape depending on which activity was in hand. The session names the steps; the review still states its own plan when its activity begins.

**Make `confirmed` and `active` separate states.** Rejected. Nothing happens between them: there is no scheduling, no staged rollout, and no second party. A confirmation that did not activate would be a state whose only content is that a further call is owed.

**A single `paused` session state.** Deferred. `proposed`, `in_progress`, `completed` and `abandoned` cover what happens today; a session left alone and returned to is resumed by `continueSession` without needing a name for the gap.

## Consequences and Tradeoffs

- Entering admin is two calls before any work: start states the plan, begin works it. That is the plan-first behaviour Brian asked for, and it is one turn each morning.
- The review routes are unchanged and still usable on their own. A session's email activity holds a review and works it through exactly the same lifecycle, which is why nothing about scope, decisions, preparation or execution moves here.
- Sessions accumulate one row per entry, with their activities. Like reviews, this is a record of when admin was worked and in what order.
- The contract moves to 0.19.0: the session and playbook operations are additive, and every review operation keeps its name and shape.
- Objectives, to-dos, the calendar and follow-ups now have a place to be built into, and until they are, they are visible as unavailable rather than absent.

## Affected Documents

- `docs/gpt-action-openapi.yaml` — the session and playbook operations, `Session`, `SessionPlan`, `SessionCloseout`, `Playbook`, `PlaybookValidation`, `PlaybookChange`.
- `docs/gpt-daily-review-instructions.md` — entering admin is `startSession`; today's wishes versus persistent changes.
- `README.md` — the session and playbook routes, and what a revision means.
