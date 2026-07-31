# ADR-0027 — A Rule Is Tried Before It Is Agreed To, and Trying It Changes Nothing

**Status:** Accepted
**Date:** 2026-08-14
**Extends:** [ADR-0026](./ADR-0026-a-rule-is-a-record-with-versions.md), a rule is a record with versions; [ADR-0025](./ADR-0025-a-rule-matches-on-fields-that-exist.md), what a rule may match on; [ADR-0003](./ADR-0003-gmail-access-and-retention.md), what is retained about a message.

## Context

The rulebook can hold a rule and walk it from `proposed` to `active`, and `proposed → confirmed` runs through `tested` — which nothing could satisfy, because nothing ran a rule against anything.

A rule is a sentence, and the thing that matters is not whether the sentence reads well but what it does to a mailbox at 7am. Those come apart in both directions: a rule that reads exactly right and matches nothing, and a rule that reads narrow and catches a quarter of the inbox because the pattern turned out to be about the sender rather than the notice.

The requirement asks for four ways to try one — the current snapshot, a historical sample, named items, examples typed out — with matches, non-matches, warnings, an effects preview, and candidates for both kinds of mistake.

## Decision

**A preview reads and does not write.** It runs over retained evidence metadata — the same fields a rule may match on — and touches no Gmail API and no Monday board. `Report.executed` is a field, always false, because "this was a preview" is worth stating rather than leaving to be assumed by whoever reads the response.

**A match is a recommendation, and every previewed row says so.** `requires_confirmation` is on each matched item rather than once at the top of the report: the row is the thing that gets read, and a list of items a rule would file reads like a list of items that will be filed.

**A test is recorded against the rule, and that is what `tested` means.** `preview_rule` moves a `proposed` rule to `tested` and writes the event with the test run id, the sample it used and the counts. Testing a rule that is already active records nothing and changes nothing: it is a question, not a step. And because amending returns a rule to `proposed`, the test that lets a rule be confirmed is always a test of the version being confirmed.

**Both kinds of mistake are guessed at, and named as guesses.**

- *False positive candidates* are matches on mail reviewed under some other group. A rule written for one capability catching another's mail is the commonest sign that a pattern is about the sender rather than the notice.
- *False negative candidates* are items that failed exactly one condition, out of two or more. Failing the only condition there is, is not a near miss — it is mail the rule is not about — so a single-condition rule reports none, and every non-match would otherwise be listed as one.

**Warnings are about the shape of the result, not the shape of the rule.** Matching nothing, matching more than a quarter of the sample, having nothing to try against at all, and disagreeing with the rule's own examples: each is a legitimate outcome and each is the outcome nobody wanted often enough to say out loud. None of them blocks anything.

**Examples typed out are answered, and labelled.** `synthetic_examples` matches subjects that were written rather than received, which is the only way to try a rule on mail that has not arrived yet — and every such report carries the warning that it says nothing about the mailbox.

**The effect preview is rendered with the values the match captured.** A notification rule previews as *"Transfer 207960765 lands in 3 days"* rather than as its template, because the template is not the thing that will be read at 7am.

## Alternatives Considered

**Preview by rebuilding a review run with the rule applied.** Rejected. It is the most faithful simulation and it writes review rows, which is exactly what a preview must not do — and it forces the rule to have been stored before it can be tried.

**Report false negatives by loosening the rule and re-running it.** Rejected for now. Which loosening — drop the domain, drop the pattern, widen to a prefix — is a guess with a different answer per rule, and a list of items "a different rule would have caught" is not evidence about this one. Failing by one condition is a fact about this rule.

**Let a test satisfy `tested` without having matched anything.** Kept, deliberately, with a warning. A rule that legitimately catches nothing today — a notice that arrives quarterly — is still a rule Brian can have seen the consequences of. Refusing would make the empty inbox unable to confirm anything.

**Sample from review items rather than evidence.** Rejected. Review items keep subject and participants but not labels or the snippet, so half the field registry would be unanswerable, and a condition that cannot be evaluated is worse than one that fails.

## Consequences

A preview is only as good as what is retained: 90 days of evidence, and metadata rather than bodies. A rule about wording further down a message cannot be tested here, and cannot be written here either, which is the same boundary ADR-0025 draws.

Conflict between rules is not previewed yet — a report says what one rule would do, not which rule would win. That is the next increment, and it is where matched items start carrying the other rules that matched them.

`POST /rules/preview` tries a rule that was never stored, which means a rule can be tried, rewritten and tried again without leaving a trail. That is intentional for an unstored draft: the audit trail belongs to rules that exist, and a draft nobody kept is not a rule.
