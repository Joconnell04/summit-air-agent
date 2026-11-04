# Owner view design rules for agents

The short form of docs/design-system.md, for an agent building or changing an owner view page. It is also the brand book of the saved copy in Claude (see docs/design-system.md, The saved copy in Claude).

Build every owner view page from these tokens and the classes in `components/bundle.css`. The reader runs a residential HVAC company, knows ServiceTitan, reads a dispatch board every morning and has never read a log. Each page should read like that board, not like a dashboard.

## Content

- Write in sentence case, in plain words. Use the owner's and ServiceTitan's words: Job, Jobs booked, Arrival window ("Today 4 to 8"), Service location, Needs a person, Late callback, Turned away, Resolved, Performance, Send to Revin. Never write booking, slot, address, flag, escalation, overdue, declined, handled or "Is it paying off" on a page.
- Say who does what and by when: "The office calls back by 1:40 PM", "The agent called back", "Held the booking until the safety lines were said".
- Put a number's unit in its label ("89% booking rate: 8 of 9 who wanted a visit"), never only in a tooltip.
- Never open a page with a generated sentence ("21 calls came in. 10 jobs booked."). Put the counts in figures.
- Keep tool names, ids and codes behind the Technical details switch and the log. The owner reads the plain note; the engineer turns the switch on.
- Name ServiceTitan once in a place: its logo does it, so the words beside it say "all 10 sent".
- Buttons are verbs or destinations of three words or fewer. No emoji anywhere.

## Color

- Ground the page in `page` (Revin's Offwhite); put panels and controls on `surface`; use `sunken` for the board axis, group rows and notes in a call.
- Set body copy in `ink`, secondary copy in `ink-2`, labels in `muted`, and only times, captions and table headings in `faint`.
- Use `ember` for the brand alone: the logo, the active tab's underline, the now line and the agent's node in a call. Put white text only on `ember-action`. Ember never marks a status, because ServiceTitan's red already means urgent.
- Status comes from ServiceTitan's Anvil palette: `urgent` (urgent priority, late, safety), `high` (high priority, held or waiting), `done` (done, sent, answering), `info` (scheduled jobs, arrival windows, links), `neutral` (normal, other calls). Text on a wash is always the matching `-ink` token: `urgent-ink` on `urgent-wash`, and so on.
- Status never depends on color alone: write the word ("Late", "Urgent", "Held here").
- Hairlines separate, never decorate: `line` for panel edges, `line-soft` for rows inside a panel, `line-strong` for control edges.

## Type

- One family, Public Sans (`fonts/PublicSans-latin.woff2`, weights 100 to 900). Hierarchy comes from size and weight, never from color alone.
- `page-title` once per page; `figure-value` for the number in a figure; `heading` for panel heads and the name in a row; `body` for copy and rows; `control` for buttons, tabs and notes; `caption` for tags, headings in tables and the line under a figure; `tech` (mono) only for technical details.
- Turn on tabular figures (`.num`) for times, phone numbers and counts so columns line up; leave them off in running text.

## Layout, space and shape

- The page is at most `page-w` wide with `gutter` sides; panels stack `s-6` apart and pad `s-4`. Space is on a 4px grid, `s-1` to `s-12`.
- Every control in a row shares `control-h`; tags in tables are `tag-h`; a fixed table's row is `row-h`.
- Radii: `r-sm` for tags, `r-md` for controls and notes, `r-lg` for panels and popovers, `r-full` for count pills, board marks, dots and nodes.
- Panels are flat with a 1px `line`. Only the active segmented button gets `shadow-1`, and only things that float over the page (suggestions, popovers, tooltips) get `shadow-pop`. Focus is the `focus` ring on every control.
- Motion is short and only for changes the eye should follow: 120ms for hover, 200ms when board marks move, none under reduced motion.
- No metric cards. Put a few numbers on the figures line and anything to compare in a ruled table. No gradients, no colored left borders, no illustrations.

## Patterns

- A row that opens a call is the link: the whole row takes the click, its name is the real link for keyboards, and a chevron at its end says so. Never add an "Open call" link.
- One line per row: longer detail (why a job is urgent) goes in a tooltip from a small "i" after the name, with the same words as its label for screen readers.
- Use as few tags as a row needs: priority is a tag only at Urgent and High, arrival windows and job numbers are plain text, and a call header carries one tag for where the call stands.
- Counts sit in a pill beside their label, red when they count something waiting on a person.
- The report to Revin goes under the conversation, so it is read after the call. It is the one write staff can make; settings, rules and users are for admins, checked on the server.

## Iconography

Inline stroke SVG on a 24px grid, 2px stroke, round caps and joins, `currentColor`, 14 to 16px in place: chevrons, a check for done, a pause for held back, a warning triangle for safety and overrides, an "i" for a tooltip, a magnifier in search, a calendar on the range picker, a download arrow on the log. The logos are in `assets/Logos` (see its README); never redraw or recolor them.

## Accessibility

Text meets 4.5:1 on its ground: `ink` 17.5:1 on white, `muted` 6.7:1, `faint` 5.2:1 on white and 4.8:1 on `page`, each `-ink` on its `-wash` 5.8:1 to 7.9:1, links 5.4:1, white on `ember-action` 4.7:1. Toggles use `aria-pressed`, tabs `role="tab"` and `aria-selected`, the status line `role="status"`, and every control shows the focus ring.

## Not synced

The components here are static renditions: the owner view's own markup, captured from the running app and styled by its unchanged stylesheet. The page script (`owner.js`: board packing, row clicks, tooltips, search as you type, resizable columns, the technical details switch) is not bundled, so previews do not respond. Whole pages built from these parts are not cards: the Rules page, the Settings page and the prose panel on the debug bench.
