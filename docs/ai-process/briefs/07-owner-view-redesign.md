# Build brief: owner view redesign

Branch: `joco/summit-air-owner-view-7aab0a`. Depends on brief 06 (the owner view and background jobs). Issue #53.

## Goal

Rebuild the owner view for the person who runs Summit Air: someone who knows HVAC and ServiceTitan well, and has never read a log. Brief 06 built a page for an engineer. This one keeps every number brief 06 computes and shows them the way a dispatcher reads a day.

Read first: CLAUDE.md, DECISIONS.md 014 to 016, docs/decisions/40-owner-view-stories.md (the ten stories, O-1 to O-10), docs/decisions/45-owner-view-design-direction.md, docs/explorations/009-revin-brand-and-servicetitan-ui.md, docs/users.md (owner, dispatcher). The design canvas (version A, "Dispatch board") is the reference: https://claude.ai/artifact/MFSZ4p6oo871gh7U2Ss8hL.

## Scope

- **A design system, used by every page.** Tokens (color, type, space, radius, lines, elevation, motion), components (header, tabs, page head, stat line, board, filter bar, segmented control, toggle, table, tag, button, tray, conversation, key-value list, report form, bar chart, empty state) and the words the pages use. Written up in `docs/design-system.md` and shown live at `/owner/design`. Revin's brand, ServiceTitan's status colors and words, Public Sans served from the app.
- **Today** (O-1 to O-5). A one-sentence summary, a few figures on a ruled line (no cards), the dispatch board (the day from 7 AM to 9 PM with lanes for what needs a person, jobs booked and other calls, and a row for the arrival windows) with filters, the "Needs attention" table, the jobs table, and a tray for callbacks, leads and calls turned away. A status line says whether the agent is answering and when the last call came in.
- **Calls.** The day's calls as a list with filters, and each call as a page (O-6 to O-8): a header that places the call in the day, what happened in two lines, the job it booked, the conversation with a plain note wherever the agent did something, technical details behind a toggle, and a box to tell Revin something was wrong.
- **Results** (O-9, O-10). Today, 7 days or 30 days: the measures in a ruled table with the period before beside them, calls by hour with a call type filter, booking rate by call type, and what was turned away or held back.
- **Tell Revin (O-8).** The only write. A report is stored against the call with the moment it is about. When `REPORTS_GITHUB_TOKEN` and `REPORTS_GITHUB_REPO` are set, the jobs loop files it as a GitHub issue with the replay command for the call, so the fix gets an eval case; without them the report is held, the same way alert email is held without a mail server.

## Markup round

After the first build, Jackson marked up the built pages on the design canvas. Those notes, applied as DECISIONS.md 017: figures in place of the summary sentence, counts in pills, whole-row links, a one-line Jobs table with tooltips, one tag in the call header, the conversation as a graph with per-step details and a downloadable log in place of the technical toggle, the report under the conversation, and a search field in the header.

A second round of notes on the revised pages, as DECISIONS.md 018: search over a full-text index with suggestions as you type, technical details as a switch on the call page, Results with the chart first, a "Performance" ledger and a custom range, resizable Jobs columns, and the report box at one height.

## Not in scope

Call recordings (O-6's audio): none are stored today, and DECISIONS.md 014 says this is the first cut if time runs short. The page says the recording isn't kept. Recorded in docs/later.md.

## Done when

`make check` passes with tests for the board positions, the filters' data, the plain notes, the report write and filing, the status line and every Results number; the pages render from `make owner-demo` and are screenshot-checked at desktop size; the reviewer agent has checked the branch; README, app/README.md and docs/architecture.md point at the new pages. Spec S-44 is unchanged: every number it lists is still on the page, and docs/spec.md changes only through `/round`.
