# ADR-0015 — The Review Is of the Inbox, and Says So

**Status:** Accepted
**Date:** 2026-08-03
**Depends on:** [ADR-0003](./ADR-0003-gmail-access-and-retention.md), [ADR-0009](./ADR-0009-review-engine-implementation.md), [ADR-0011](./ADR-0011-presentation-contracts.md)
**Extends:** [ADR-0009](./ADR-0009-review-engine-implementation.md), which built the review this gives a stated scope.

## Context

Intake has asked Gmail for `INBOX` and the capability's label since [ADR-0003](./ADR-0003-gmail-access-and-retention.md), so the review has always been of the inbox in practice. What it has never done is *say* so. The scope was an implementation detail of a query, invisible in every response, and three things followed from that.

A renderer that cannot see the scope has to infer it. Asked "did you look at my archive?", the GPT can only reason from what came back — and a review with nothing archived in it looks identical whether the archive was excluded or merely empty.

Worse, a scope that is invisible looks like an opinion. A GPT that notices archived mail is never recommended is one step from proposing "only review Inbox items" as a candidate rule, and one step further from asking whether Brian would like that. Both would be wrong: it is not a preference, and there is nothing to confirm. The learning machinery in [ADR-0010](./ADR-0010-action-lifecycle-and-learning.md) exists to turn corrections into rules, and it must not be pointed at facts about the query.

Two states also escaped the query as it stood. Snoozing keeps the `INBOX` label — a snoozed thread is returned by a `labelIds: [INBOX]` listing, and Gmail's API publishes no label saying it is asleep — so mail Brian has explicitly deferred was being reviewed as though it were outstanding. And a thread already recorded that later left the inbox was never re-read: a scan of the inbox cannot return it, so its evidence kept the labels it had when last seen, and it stayed reviewable indefinitely.

## Decision

**The scope is the query.** A `ReviewScope` decides, before Gmail is called, which labels are ANDed and what search accompanies them. The default asks for `INBOX` and the capability's label with `-in:snoozed`. Nothing downstream filters the result back into shape, because a filter applied after the fact is a filter that can be forgotten.

**Labels are checked again after the fetch.** Search is Gmail's interpretation of a question; labels are the answer. Every thread is admitted only if the labels it actually carries satisfy the scope, and a thread whose labels have never been read is not admitted — never having been seen in scope is not the same as being in it.

**Sent and drafts are excluded as places, not as labels.** A thread's labels are the union of its messages', so a conversation Brian has replied to carries `SENT` and is still in his inbox and still needs answering. Excluding everything carrying `SENT` would drop exactly the threads he is most involved in. What is excluded is mail that is *only* sent or *only* an unsent draft, which is to say mail that is not in the inbox — so the inbox requirement already excludes it, and the flags say so explicitly.

**Snoozing is excluded by search, and we are honest that it is.** `in:snoozed` is a documented Gmail search operator; there is no corresponding label in the API and no field on a message that reveals a snooze. So `-in:snoozed` is the strongest available exclusion, and unlike the other exclusions it cannot be re-checked against the thread. It is recorded as `include_snoozed: false` and as part of `gmail_query`, so what is guaranteed and how is visible rather than implied.

**Every review response states its scope.** `name`, `mailbox`, an inclusion flag for each of snoozed, archived, Trash, Spam, sent and drafts, whether it was `requested`, the `gmail_query` used, and a sentence a person can be shown. It appears on the run and on each group, and the run's scope is persisted, so a run read back a week later reports the scope it was built with rather than today's default.

**Another scope only happens when it is named.** `scope: "archived" | "snoozed" | "everything"` on `POST /review/start`. An unrecognised name is `422`; there is no partial or fuzzy match. Nothing infers a scope from phrasing at the server, and the GPT is instructed to send one only when Brian asks for one.

**A review of another scope is a different run.** Run identity becomes `(review_date, channel, scope_name)`. "Show me my archive" opens a second run of the same day rather than adding archived mail to the review already under way — a request to see more mail must not widen a review that has already been made, and [ADR-0014](./ADR-0014-execution-scope-integrity.md) is the reason to be careful about scope silently growing between requests.

**Leaving the scope withdraws a row rather than deleting it.** A pending item whose thread has left the scope becomes `deferred`, with a decision recorded by the actor `scope:<name>` explaining why. Nothing was decided about the mail, and if the thread comes back to the inbox it is reviewable again. Only a thread *known* to have left is withdrawn: a row whose labels have never been read says nothing about where the thread is.

**A thread that left the inbox is asked about directly.** Recorded threads the scan cannot have covered are re-read individually, up to the scan limit, so archiving a thread in Gmail takes it out of the next review instead of leaving it there with stale labels. A thread already known to be out of scope is not re-read: its labels already say it is not being reviewed, and if it returns to the inbox the scan will find it.

**Pruning is refused outside the default scope.** Pruning treats everything it did not see as retired, and a scan of the archive has not seen the inbox.

**A capability may be configured to watch elsewhere, and that is configuration.** `gmail.mailbox` replaces `require_inbox`, defaulting to `INBOX`. It is a versioned configuration value like the rest of a capability, not something learned from behaviour.

## Alternatives Considered

**Leave the scope implicit and tell the GPT what it is in the instructions.** Rejected. It puts a fact about the data in a prompt, where a rephrasing can lose it, and leaves the GPT asserting something it cannot see. The response should carry its own provenance.

**Let the GPT propose an Inbox-only rule through the learning machinery.** Rejected, and explicitly forbidden in the instructions. A candidate rule is a claim about mail that Brian can confirm or reject; the review scope is not up for confirmation, and offering it as a choice would imply reviews had been wider than they were.

**Filter archived and snoozed mail out at presentation.** Rejected. The rows would still be built, decided against, and counted; a filter at the last step is a promise made by whoever remembers to apply it.

**Model the scope as a Gmail query string in configuration.** Rejected as too much rope. An arbitrary query cannot be checked against a thread's labels afterwards, cannot be described in a sentence, and would make "what was reviewed" a question about search syntax.

**Exclude any thread carrying `SENT` or `DRAFT`.** Rejected: see above. It reads well in a requirement and drops replied-to conversations in practice.

**Widen an existing run when another scope is asked for.** Rejected. The run is what an execution scope is drawn from, and a review that grows because of a question is the failure mode [ADR-0014](./ADR-0014-execution-scope-integrity.md) was written after.

## Consequences

The GPT can answer "what did you look at?" from the response, with the exact Gmail search if pressed. It never has to guess, and it has nothing to propose about scope.

Snoozed mail is excluded on Gmail's word rather than on evidence we hold. If `in:snoozed` were to change meaning, the exclusion would weaken silently — the mitigation is that it is stated in every response, not that it is guaranteed twice.

Re-reading threads that left the inbox costs one Gmail read each, bounded by the scan limit, and only for threads not already known to be out of scope. In the steady state that is nearly none; the day after a mass archive it is one per archived thread.

`(review_date, channel)` is no longer unique, and migration `0007_review_scope` replaces the constraint. Its downgrade refuses rather than deleting if a day holds more than one scope: losing a review to a schema change would be the wrong way to find out.

Old evidence has no labels until it is next synced. Such rows are neither admitted to a review nor withdrawn from one, which is the conservative reading of "we do not know where this is".
