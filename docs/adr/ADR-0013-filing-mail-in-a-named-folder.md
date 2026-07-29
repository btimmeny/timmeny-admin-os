# ADR-0013 — A Recommendation to File Mail Names the Folder

**Status:** Accepted
**Date:** 2026-08-01
**Depends on:** [ADR-0010](./ADR-0010-action-lifecycle-and-learning.md), [ADR-0011](./ADR-0011-presentation-contracts.md), [ADR-0012](./ADR-0012-gmail-dispositions.md)
**Extends:** [ADR-0012](./ADR-0012-gmail-dispositions.md), which recorded archive and Trash as the two dispositions.

## Context

Archive and Trash cover the two ends of a review: keep it out of the way, or throw it away. Most mail is neither. It is kept deliberately — an expert-call confirmation, a tax notice, a receipt — and it should not be in the inbox, but it should be somewhere findable.

Archiving does that badly. An archived thread is "not in the inbox", which is exactly the state in which a tax deadline is lost. What Brian asks for is a folder: keep it, and put it *there*.

That turns a recommendation into something with a variable in it. "Archive it" is a complete recommendation; "File it" is not, because the only interesting part — where — is missing. Whatever names the folder is making a real decision about where mail lives, and if that is the renderer, then a language model is inventing mailbox structure per utterance. Gmail will happily create `Carrer/Citi` from a typo, and the reader will not find it again.

## Decision

**Filing is one action, `gmail.move`, which adds a folder and removes `INBOX` in a single `threads.modify`.** Not a label followed by an archive: the two together are what "move it" means, and doing them as two actions can leave a thread labelled and still in the inbox when the second half fails. The spoken name is `move_gmail_thread_to_label`, mapping onto the stored kind as archive and Trash do (ADR-0012).

**The folder is data on the recommendation, not prose in it.** A rule recommending a move must carry `move_to`, the item stores `recommendation_params`, and the decision, the approval, the prepared action, and the audit row all carry the same `{"label": …}`. The Recommended Action cell reads "File it in Later" because the destination is a value the row already holds.

**Approving inherits the folder that was shown.** "Yes" means the thread is filed where the row said it would be — not a second question about where. An override may name a different folder, and is checked like any other.

**Each capability lists the folders it may file in, and the list is closed.** A destination outside `gmail.destinations` is refused when the decision is recorded, before anything reaches Gmail. Gmail's own labels — `INBOX`, `TRASH`, `SPAM`, `SENT`, `DRAFT` and the rest — are refused as destinations at configuration load: they are states, not folders.

**No label is ever created.** Filing resolves the configured name to a label id the mailbox already has; a name Gmail does not know fails the action. A folder taxonomy is Brian's to decide, and a system that invents one while filing is a system that has quietly decided it.

**The folders are in the presentation contract.** A move action carries `params: [{name: "label", required: true, choices: [...]}]`, so a renderer offers a choice rather than inviting a folder name to be typed (ADR-0011). The contract is where a permission or a vocabulary lives; the GPT does not hold either.

**The destination is re-read at every step, as permission is.** A folder withdrawn from a capability after an approval stops the action at preparation rather than filing mail somewhere no longer sanctioned. The same check retires a learned rule in effect, without anyone editing it.

**A learned rule's folder is part of its identity.** Filing a sender's mail in `Financial/Taxes` and filing it in `Later` are two candidate rules, so confirming one does not quietly confirm the other. A move rule with no folder is refused: it is not something anyone could review.

**Verification is both halves.** Gmail is read back and must show the folder present *and* `INBOX` absent. A thread already in that state is completed without a write, which is what makes a retry after a dropped connection safe.

## Alternatives Considered

**Let the GPT choose the folder from the mailbox's label list.** Rejected, and this is the ADR's point. The label list is long, similar names abound, and the failure mode is silent: mail filed in a plausible wrong folder looks exactly like mail filed correctly until it is needed.

**Compose filing from the existing `gmail.label` and `gmail.archive` actions.** Rejected. Two actions have two failure points and an intermediate state — labelled, still in the inbox — that the review has no way to describe or resume from.

**Create the label when it does not exist.** Rejected. It converts a typo into a permanent change to Brian's mailbox structure, and it is the one Gmail write here that cannot be verified as intended rather than accidental.

**Let a destination be any label the mailbox has.** Rejected in favour of a per-capability allowlist. "Which folders may the tax review file into" is a decision worth writing down once, and a closed list makes an unexpected destination a refusal rather than a surprise.

**One shared destination list across capabilities.** Rejected. Admin filing into `Financial/Park City Home` is not a thing to make easy, and the lists differ precisely where it matters.

## Consequences and Tradeoffs

- Adding a folder to the mailbox does not add it to the review; that is a configuration edit, and deliberately so.
- A folder renamed in Gmail breaks filing into it, loudly, at execution. That is preferred to filing into a newly created label of the old name.
- The destination lists are validated against the live mailbox by hand, not automatically. `GET /admin/gmail/labels` lists the real names; a name that no longer resolves fails the action rather than the deployment.
- Filing is granted to all three capabilities, including Financial/Taxes, which may still not archive or Trash. Keeping tax mail somewhere named is the disposition that suits it.
- A bulk filing applies one folder to every selected row, and refuses as a whole if any row cannot take it (ADR-0012).

## Affected Documents

- [README](../../README.md) — Actions, Presentation, Learning, Capabilities
- [ADR-0012](./ADR-0012-gmail-dispositions.md) — the dispositions this extends
- [81 — Action Execution Runbook](../81-Action-Execution-Runbook.md)
- [82 — Daily Review GPT Instructions](../gpt-instructions/daily-review-gpt-instructions.md)
- [docs/gpt-action-openapi.yaml](../gpt-action-openapi.yaml)

## Validation

- Tests assert that an eligible row offers `move_gmail_thread_to_label`, and that the screen action carries the capability's folders as `choices`.
- Tests assert that a recommended move names its folder in the Recommended Action cell, and that a decided one names it too.
- Tests assert that approving a recommended move with no parameters files the thread in the folder the row showed.
- Tests assert that a move without a folder, and a move to a folder the capability does not list, are refused before anything is written.
- Tests assert that a Gmail system label cannot be configured as a destination.
- Tests assert that filing adds the folder, removes `INBOX`, and leaves every other label untouched.
- Tests assert that verification requires both the folder present and `INBOX` absent, and that a thread already filed completes without a write.
- Tests assert that a folder withdrawn from the capability after approval stops the action at preparation.
- Tests assert that a folder the mailbox does not have fails the action, retryably, and that no code path creates a label.
- Tests assert that a bulk filing applies one folder to every selected row and records nothing when one row is ineligible.
- Tests assert that a learned move rule carries its folder, that two folders are two rules, and that a move rule without a folder is refused.
