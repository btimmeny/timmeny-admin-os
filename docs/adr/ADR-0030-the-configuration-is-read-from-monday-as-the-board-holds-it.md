# ADR-0030: The configuration is read from Monday, as the board holds it

- Status: Accepted
- Date: 2026-07-28
- Extends: [ADR-0028](./ADR-0028-the-client-reads-the-mailbox-and-admin-os-owns-the-process.md), [ADR-0029](./ADR-0029-the-monday-scope-is-configured-by-asking-and-checked-on-the-board.md)

## Context

Brian keeps a Monday board — `Admin OS Config` — of processes and email rules: what an item applies to, what to do about it, what context it needs, what to produce, in what order, and what not to conclude. He edits it without a deploy and without asking anyone, which is what makes it configuration in the sense that matters.

The review that runs in ChatGPT reads the Inbox itself (ADR-0028) and takes its process from Admin OS. Until now the only configuration it took was the review playbook. The board is the other half: the playbook says what the shape of a review is, and the board says how Brian wants particular mail read.

Two things about the board are not what the request assumed, and both change the implementation:

- **The split is a column, not two groups.** The request named a `Processes` group and an `Email Configurations` group. The board has one group, `Configurations`, and a `Configuration Type` dropdown carrying `Process`, `Email Rule`, `To-Do Rule` and `Reference Data`. Filtering by groups that do not exist would have matched nothing — and, per ADR-0024, matching nothing is the failure that looks most like an answer.
- **The columns are the board's, not this repository's.** The board is named by an environment variable, and Monday generated ids like `long_text_mm5s61kk` for its columns. A repository that hard-coded them would work on exactly one board and silently return blank instructions on any other.

## Decision

`get_admin_os_configuration` reads the board named by `MONDAY_ADMIN_OS_CONFIG_BOARD_ID` on every call, and answers with the active processes and email rules. It writes nothing, keeps nothing and caches nothing.

- **Items are split by `Configuration Type`**, not by group: `Process` into `processes`, `Email Rule` into `email_configurations`. The group title is reported on each entry so a reader can see where it lives.
- **Columns are found by title.** Renaming a column therefore breaks the read — loudly, naming the title that is missing and listing the ones the board has. That is the trade for a board id that is configuration.
- **Only `Active` items are configuration.** `Draft` and `Inactive` are Brian thinking, not Brian deciding. Monday applies the status filter and every item that comes back is checked against it again; a filter that did not apply is refused rather than trimmed, because an unfiltered board would put drafts into a review as though he had agreed to them.
- **Anything that could shorten the answer silently is a refusal**: a missing column, a `Status` column with no `Active` label, a `Configuration Type` column that offers no `Email Rule`. The last one is the subtle case — an email review would read an empty list as "nothing is configured" and proceed with none of his rules.
- **Order is `Order`, ascending, with an unordered item last.** A rule Brian never placed should not become the first thing applied.
- **The Monday item id is the stable reference.** Each entry also carries a `key` slugged from its name, which is for reading and for him to quote; a rename changes the key and never the id.
- **`configuration_type` accepts `email` only.** `To-Do Rule` and `Reference Data` are on the board and are not read here yet, and answering with the email ones under another name would misreport what was applied.
- **Nothing is cached.** The board is where he changes his mind, and a cached answer is a review run on what he used to think. The MVP reads it every call and pays the round trip.

The tool sits in the MCP transport alongside the five review tools, behind the same API key. It touches no review state, no persistence, no versioning, no Gmail and no execution, and it holds a `MondayClient`, which cannot write — writing is `MondayWriter`, a different class.

## Consequences

- Brian can change how his mail is read by editing a Monday item, and the next review uses it.
- A misnamed column or a switched-off label is found the moment the tool is called, with the board's own titles in the message, rather than showing up as configuration that quietly did nothing.
- Renaming a configuration column breaks the read until the title is corrected here or on the board. That is deliberate: the alternative is a rule with no instructions, which reads exactly like a rule that says nothing.
- Every review costs two Monday reads, one for the board shape and one for the items. At two items and eight columns that is not a cost worth caching against, and caching would reintroduce the staleness the board exists to prevent.
- Making the tool call Monday made the whole MCP dispatch path asynchronous. It was synchronous only because nothing behind it did I/O over the network.
- The configuration is read and reported; nothing validates a review against it. A rule Brian writes badly is a rule the GPT follows badly, and Admin OS will not notice. Holding a submission to the rules that were in force is a later increment, and it needs the rules to be recorded against the review first.
