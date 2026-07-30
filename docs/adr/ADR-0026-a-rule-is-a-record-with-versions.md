# ADR-0026 — A Rule Is a Record With Versions, and a Standing It Has to Earn

**Status:** Accepted
**Date:** 2026-08-13
**Extends:** [ADR-0025](./ADR-0025-a-rule-matches-on-fields-that-exist.md), what a rule may match on; [ADR-0010](./ADR-0010-action-lifecycle-and-learning.md), a correction is evidence rather than an instruction; [ADR-0023](./ADR-0023-a-session-runs-a-playbook.md), configuration that changes by being proposed and confirmed.

## Context

Administrative behaviour is becoming configuration Brian writes, tests and retires. The learning path already holds one narrow kind of rule — a filing habit noticed from corrections, walked from `observed` to `automatable` — and it is not enough for the rest: a rule now needs a type, a priority, effects other than filing, immutable versions, and a history of everything that happened to it.

Three problems have to be solved at once, because solving them separately produces a store that cannot be corrected safely.

**An edit to an active rule is a way to change behaviour nobody agreed to.** If a rule row is updated in place, then "Brian confirmed this rule" means "Brian confirmed whatever this rule says now". The confirmation and the content come apart, quietly, in exactly the case that matters: someone widening a pattern on a rule that is already recommending.

**A rule with several effects is several decisions bundled into one yes.** A record that classifies an email, files it, drafts a reply and creates a task is four things Brian can only agree to together, and only reject together.

**The requirement names fourteen rule types and this system can carry out four.** Storing the other ten cleanly would produce rules that exist, look confirmed, and never run: to-do reminders with no scheduler, reconciliation rules with no Monday review behind them.

## Decision

**Three tables, and only one of them changes.** `rules` holds identity and standing; `rule_versions` are written once and never updated; `rule_events` records every move with who made it. The rule points at the version in force, and a review can record the exact version it ran under.

**The lifecycle is `observed → proposed → tested → confirmed → active`, with `automatable`, `paused` and `retired` beside it.** Two properties are deliberate. *Confirming needs a test first* — `proposed` reaches `confirmed` only through `tested`, so nobody agrees to a rule whose consequences have not been shown. *Confirming is not activating* — only `active` and `automatable` shape a review, so agreeing that a rule is right and putting it to work stay two acts, as the requirement asks.

**Amending writes a version and stands the rule down to `proposed`.** What Brian agreed to was a version of a rule; change what it matches or does, and the agreement does not carry over. The event records what it was stood down from, so "this stopped recommending on Tuesday" has an answer.

**Retiring is final.** A rule that can return from retirement makes every history ambiguous about whether it was in force at a given moment.

**A rule has one type, and the type owns which effects it may carry.** Effects declare their class — classification, display, recommendation — and two effects of the same kind are refused as two rules. There is no `execution` effect class: nothing a rule says reaches Gmail or Monday without the decision, preparation, confirmation and verification the review already requires.

**Constraints are stored with the rule, at their strictest, and cannot be relaxed.** `auto_execute` is refused outright, `automation_level` is capped at 1, and the four safeguards cannot be waived by a rule. A stored claim to act unattended, before anything exists that could grant it, would be a permission waiting for an implementation to honour it by accident.

**A type nothing here can carry out is declared, listed, and refused at proposal**, with the reason in the refusal — the same treatment the session gives an activity with no data source. `todo_reminder_rule` says there is no scheduler; `reconciliation_rule` says the Monday half of the review does not exist yet.

**A rule is checked against the configuration it will run under, at write time.** The match must narrow; a group it classifies into must exist; an action must be one its capability is allowed to take; a folder must be one that capability lists; and a notification's wording may only name values the match actually captures. The generated summary is stored on the version, so the sentences in the history are the ones the rule said when it was written.

**`candidate_rules` is left alone.** The learning path keeps working, unchanged, against its own table. Folding it into the rulebook is a data migration of rules that are currently recommending, and doing it in the same change as introducing the model would put live behaviour and a new schema in one step.

## Alternatives Considered

**Extend `candidate_rules` in place with types, priority, effects and versions.** Rejected for now. It is the right end state and the wrong first move: the columns that make a rule general are exactly the ones that change how the existing recommendation path reads a row, and the existing path is running against Brian's mailbox.

**Update rules in place and keep a changelog.** Rejected. A changelog is a description of a rule; a version is the rule. Only the second lets a review say what it ran under, and only the second makes an amendment unable to reinterpret a confirmation.

**Let a rule carry any effect, and validate on activation.** Rejected. Validation at activation is validation at the moment somebody is trying to get work done; the refusals here belong where the rule is written, when the alternative is still in mind.

**Store all fourteen types and mark unimplemented ones inactive.** Rejected. Inactive and unavailable read the same on a screen and mean different things, and the failure is silent: a to-do reminder rule that stores, confirms and activates cleanly, and never once reminds anyone.

**Allow `automation_level` above 1 now, and enforce it later.** Rejected. The enforcement point would be code that does not exist yet, which makes the stored level a claim on a future implementation rather than a permission granted by anyone.

## Consequences

There are two rule stores for a while, holding different things: `candidate_rules` for learned filing habits that are already recommending, and the rulebook for rules Brian writes. That is a cost, and the alternative was rewriting live rules underneath a new schema.

Nothing in the rulebook reaches a review yet. Preview testing comes next, then conflict resolution and per-row explanation, which is the point at which the review starts consulting `read_effective_rules` and the version ids get recorded on the run.

Four types are available; the other ten become available as the things behind them get built — the Monday review, reconciliation, group configuration — and each one is a line of code in `RULE_TYPES` when it does.
