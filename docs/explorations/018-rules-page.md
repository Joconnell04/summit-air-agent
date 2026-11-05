# 018. One simple Rules page on the bench

Written by the issue #140 lane from the code and screenshots of the running demo; not a researcher run. Three directions for Jackson to pick from, then the pick is built.

## Question

The owner view's Rules page and the bench's rule editor have grown into many fields that read as a heap of unconnected rules. What is the simplest single page, on the debug bench only, that shows (a) what triggers a fixed response, (b) the owner's own rules, with a way to add one, and (c) the assembled system prompt, section by section in the order `prompts/assemble.py` joins it?

## The pages today

`before-owner-rules.png` and `before-bench-rules.png` in `018-rules-page/`.

- **`/owner/rules`** (read-only, `app/owner_view.py` and `app/rulebook.py`): 251 rows in six stage panels, 17,032 px tall at desktop width (MEASURED, headless Chromium at 1440 px on `make owner-demo`). The rows are 40 server checks, 95 wording lines, 49 company settings and 66 prompt items, each with a "Kept by" column and a Locked or Editable tag; only 7 are editable. A segmented control filters All, Editable, Locked, and a Versions table sits under the stages.
- **`/bench/rules`** (the editor, DECISIONS.md 055): 6 wording fields, one guidance field per stage with a blank one added under each (4 stages, up to 5 notes each), a "What changed" field, Save draft, Discard, Stop check, Override (with a reason) and Publish, and the Versions table with Roll back.

## Every field and control, kept, merged or dropped

| Where | Field or control | Verdict | Why |
|---|---|---|---|
| Owner | The whole Rules page and its tab | Dropped | Jackson: rules live on the bench only. The route redirects to the bench (see the build). |
| Owner | All, Editable, Locked filter | Dropped | The new page's structure already separates what's fixed from what's yours. |
| Owner | Edit on the bench button | Dropped | The page itself moves there. |
| Owner | 40 server checks | Merged | Into "Fixed responses" as hard rules, each with the line it says when there is one. |
| Owner | 95 wording lines | Merged | Into "Fixed responses": one row per trigger, so the 7 name checks, 7 move lines, 5 callback times and 3 pause fillers each become one row with their variants in a tooltip. 60 rows. |
| Owner | 49 company settings | Dropped | Hours, ZIPs, windows and phrase lists are tenant configuration, not responses; the ones that start a fixed response (the safety words, the business words) are named in that response's trigger. |
| Owner | 66 prompt items | Merged | Into "System prompt", shown whole and in order instead of cut into rows. |
| Owner | "Kept by" column, Locked and Editable tags | Dropped | One distinction is left: a fixed response, or your rule. |
| Owner | Versions table | Merged | With the bench's, which already has it. |
| Bench | Crumb link to the owner's page | Dropped | That page is gone. |
| Bench | "What changed" field | Merged | Into the Publish and Save step as the version's note, so the head is three buttons. |
| Bench | 6 wording fields | Merged | Shown in place inside "Fixed responses" (Greeting and Closing), as a field instead of a quote, because they are fixed responses whose words the owner may change. |
| Bench | Guidance, one field per stage with blanks | Merged | Into "Your rules": one short list, each row a stage and the rule, then one "Add a rule" row. |
| Bench | Save draft, Discard, Stop check | Kept | The draft and check flow is unchanged. |
| Bench | Override with a reason, Publish | Kept | Decision 031 and 055: Publish runs the must-pass gate; Override is logged. |
| Bench | Versions with Roll back | Kept | Nothing is deleted; a rollback is a new version. |
| Bench | Panel foot about publishing | Dropped | The Publish button and the version's check tag already say it. |

The guidance check (an owner rule that contradicts a locked rule is refused with the reason) and every server rule are unchanged in all three directions. This is a UI change.

## Three directions

All three are rendered through the app's own `base.html`, `_ui.html` macros, `tokens.css` and `components.css` from real data: the live rulebook, the demo day's version 3, and the prompt files. Open the `.html` files from the repo, or see the screenshots. The prompt sections and fixed rows are the same in each; only the layout differs.

### A. One column, one topic at a time (`option-a.png`)

Four panels in reading order: Your rules (the short list you edit), Fixed responses (100 rows behind a segmented control of eight topics: Safety, Greeting, Drops and outages, Who's calling, Address and area, Booking, Passing to a person, Closing), System prompt (eight collapsed sections, numbered in join order, each with its size), Versions.

- For: the page is 1,900 px tall instead of 17,000; never more than one topic's rows (5 to 22) at once; your rules sit first, so the page opens on the one thing an admin changes; works at phone width as one column.
- Against: you see one topic at a time, so a full scan of every fixed response takes eight clicks; the topic control is eight segments, which wraps on a phone.

### B. Tabs (`option-b.png`)

One panel with four tabs: Your rules, Fixed responses, System prompt, Versions. The Fixed responses tab is one ruled table with the topics as group rows.

- For: the least on screen at a time; each tab is one job; the tabs reuse the tray tabs from the Today page.
- Against: your rules and the fixed responses they are checked against are never visible together, which is the disconnection Jackson named; the Fixed responses tab is still 100 rows in one scroll; tabs hide the prompt, so its order is two clicks from the rules it carries.

### C. Side by side (`option-c.png`)

The fixed responses as one full table on the left, your rules and the versions in a side panel on the right, the system prompt full width under both.

- For: your rules beside the fixed responses, so the comparison the guidance check makes is visible; everything fixed in one scroll for an engineer checking a line.
- Against: the most at once (100 rows, 6,975 px); the side panel squeezes the rule fields and the versions table; at phone width the split stacks and the rules land under 100 rows.

## Recommendation

**A.** It answers "less all at once" directly: a short list of your rules, one topic of fixed responses, and a prompt you open section by section. It keeps the three things on one page in one order, so they read as one system: what you add, what it can't override, and where both end up in the prompt. B separates your rules from what they're checked against; C shows everything at once.

What would change it: if the bench's main use turns out to be an engineer scanning every fixed line (not an admin adding a rule), C's single scroll wins.

## Build notes for the pick

- The prompt panel reads its sections from `prompts/assemble.py` itself (a `sections()` function that `instructions()` is built from), on a sample call context labelled as a sample, so it can't drift from what a call gets.
- The owner's rules show where each lands: opening and whole-call rules in the prompt's house guidance section, booking and closing rules in a `[server]` note when the call reaches that stage (`assemble.py`, `STAGE_NOTES`).
- `/owner/rules` redirects to `/bench/rules`, and the owner view loses its Rules tab.

## Built (option A) and the second check

Jackson picked A. The first build is `build-1-desktop.png` and `build-1-phone.png`; the reviewer agent was then asked one question, whether anything on the page is not needed, repeated or disconnected. What changed from its answer (`build-2-desktop.png`, `build-2-phone.png`):

- Server checks that say no line no longer fill the second column with "Held by the server" seven times; they are listed once under an **Always** group row at the end of their topic.
- Counts only on the topic tabs; the panel totals are gone.
- Panel subtitles only on Fixed responses, the one place that needs to say why it can't be edited.
- The "Goes" column is gone; one line under Your rules says where rules reach the agent.
- The prompt sections lost their character counts; each keeps what it is for.
- The crumb no longer repeats the live version, which the Versions table tags.

Kept against the review, with the reason: the six lines the owner may reword stay as fields in place among the fixed responses, as this option specified (moving them into Your rules would make it long again); the By column stays, because an override records who skipped the check; tables scroll sideways on a phone, the design system's pattern, instead of turning into cards.

Measured on the built page (headless Chromium, 1440 px): every control in the head and in a rule's row is 32 px high with a 6 px radius and 13 px type; the page is 1,915 px tall against 17,032 px for the old owner page.
