# ADR-0025 — A Rule Matches on Fields Admin OS Can Answer For

**Status:** Accepted
**Date:** 2026-08-13
**Extends:** [ADR-0003](./ADR-0003-gmail-access-and-retention.md), what is and is not retained; [ADR-0010](./ADR-0010-action-lifecycle-and-learning.md), a rule is proposed rather than applied; [ADR-0023](./ADR-0023-a-session-runs-a-playbook.md), the playbook as configuration.

## Context

Administrative behaviour is to become configuration: rules Brian can read, write, test and retire, over email and over Monday work, instead of judgement spread across route handlers and a GPT's instructions. Everything above that — versioned rule records, priority, conflict, preview, automation levels — rests on one thing being right first: **what a rule is allowed to match on, and what it means to match.**

Three things make that harder than a field list.

**The obvious field list includes fields that do not exist here.** A rules engine over email is expected to offer "body contains". Admin OS has never stored a message body, by decision, and will not start to in order to satisfy a condition editor. What it does hold is Gmail's snippet: roughly two hundred characters of preview. A `body` field backed by a snippet is not a small inaccuracy — it is a rule that appears to search the message and searches the first paragraph, failing silently on every email where the interesting sentence is further down.

**A condition language makes it easy to write a rule about nothing.** "All Inbox email", "any email containing 'money'", "every Monday item", "any task older than a day" are all well-formed, and each of them is a rule that fires on most of a morning. The requirement names them as things to reject. Being in the inbox is the one thing every item in an inbox review has in common; an age bounds a rule to "eventually, everything".

**A rule that cannot explain itself has to be trusted.** The point of writing behaviour down is to stop having to take it on faith, and a recommendation whose reason is "a rule matched" restores exactly the faith it was meant to remove.

## Decision

**The fields are a closed registry, and every field names its source.** `subject`, `participant`, `participant_domain`, `snippet`, `gmail_label`, `capability`, `thread_age_days` — nothing else, and each carries the sentence describing what stands behind it. `snippet` says in its own description that bodies are not stored and that this is the whole of what a rule can read of what an email says. There is no `body`, no attachment field and no "known vendor": a field with no data behind it is worse than a missing feature, because it is a rule that matches nothing while looking like it matches something.

**Facts are built for the engine, not read through it.** `email_facts(...)` reduces a thread to the registry's fields, and evaluation sees only that. A rule cannot reach past the field list into a model, which is the property that keeps the list honest as the models grow.

**A rule must narrow, and the check is structural.** Fields are marked as narrowing or not: subjects, addresses and domains cut a set down; labels, groups and ages describe the whole review. A condition narrows when it is on a narrowing field, is not a negation, and carries at least four characters of fixed text — for a pattern, four characters of literal text and no match against the empty string. In an `all` branch one condition must narrow; in an `any` branch **every** alternative must, because one branch matching everything makes the rule match everything. `check_breadth` refuses the rest, naming what would fix it.

**Matching returns its reasons.** Every condition yields what it tested, whether it matched, and what it saw, in the field's own words: *"the subject matches the pattern Inter Institution Transfer Request [number] Will Occur in [days] Days — matched (…)"*. Named captures come back as extracted values, so a rule can say the transfer number and the day count without a second parse.

**Prose is generated from structure.** `describes()` renders a tree as sentences, and patterns render with their captures shown as `[number]` rather than as regex. A summary written alongside a rule drifts from it; a summary derived from it cannot.

**An example produces suggestions, never a rule.** `suggest_subject_conditions` reads one subject and offers the readings of it, narrowest first — the exact subject, the same wording with any numbers, the opening words, the longest fixed phrase — each with what it would catch and what it would miss. Which parts of a subject vary is a guess, and the guess is Brian's to make against a preview.

## Alternatives Considered

**Store bodies so that "body contains" can exist.** Rejected. The retention decision is older than this feature and better than it: the value of matching on a sentence somewhere in an email does not justify holding every email's contents in a service that only needs to know what kind of thing it is.

**Offer `body` as an alias for the snippet.** Rejected, and this is the one worth being firm about. It would work in the demo, where the interesting text is in the preview, and fail in production, where it is not — silently, on the emails that matter most.

**Let a rule be as broad as Brian writes it, and warn.** Rejected. A warning shown at proposal time is not present at 7am three weeks later when the rule recommends filing on ninety rows. Breadth is refused where it is written, with the sentence that says what narrows.

**Free-text matching with a single `query` string, Gmail-search style.** Rejected. It cannot be rendered back as sentences, cannot report which part matched, and cannot be checked for breadth — three of the four properties this exists to have.

**Generate the regex and activate it once Brian says yes to the summary.** Rejected. The summary of a generated pattern is agreeable in a way the pattern is not: what a pattern does to the other four hundred threads in the mailbox is a fact about the mailbox, not about the sentence. Suggestions carry breadth and consequences, and preview against real items comes before confirmation.

## Consequences

The condition language is narrower than the requirement's field list, and says so where each field is defined. Monday fields join the same registry when the board review is built — the engine is source-agnostic, and only the fact-builders are per-source.

Rules that were expressible in the earlier learning model — a handful of metadata fields, AND-ed — remain expressible; this generalises them rather than replacing them, and the existing `CandidateRule` match format is unchanged until the versioned rule record lands.

Breadth refusal will occasionally reject something Brian meant. That is the intended direction of the error: the alternative is a rule nobody notices until it has recommended the same thing eighty times in one morning.
