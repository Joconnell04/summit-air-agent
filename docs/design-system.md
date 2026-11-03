# Owner view design system

The owner view is built for the person who runs Summit Air: someone who knows HVAC and ServiceTitan, reads a dispatch board every morning, and has never read a log (docs/users.md, DECISIONS.md 014). Every page is drawn from the two files below, and `/owner/design` shows each token and component live, from the same CSS.

| File | What it holds |
|---|---|
| `app/static/owner/tokens.css` | Every color, size, space, radius, shadow and timing, as CSS custom properties |
| `app/static/owner/components.css` | Every component, built only from tokens |
| `app/templates/_ui.html` | Jinja macros for the components that repeat (tags, figures, the status line, day navigation, note icons) |
| `app/static/owner/owner.js` | The few interactions: board filters and layout, tabs, the plain and technical call views, the Results filter. Each page works without it |
| `app/static/owner/fonts/` | Public Sans, served from the app, with its license |

## Principles

1. **Read like a dispatch board, not a dashboard.** The day runs left to right, the way ServiceTitan's dispatch board does, and the tables underneath use ServiceTitan's words: Job, Arrival window, Service location, Urgent, High. An owner who uses ServiceTitan should read the page without a key.
2. **Words first; color backs them up.** Every status is written out ("Late", "Urgent", "Held here"). Color repeats it for a glance and is never the only signal.
3. **Revin's brand, used sparingly.** Ember (`--ember`, #FA3500) marks the brand, the active tab, the one primary action on a page ("Send to Revin") and the "now" line. Where white text sits on it, a slightly darker Ember (`--ember-action`) keeps the text readable. It never marks a status, because ServiceTitan's red already means urgent and two reds would compete.
4. **No metric cards.** Numbers sit on one ruled line (the figures component) or in a ruled table (the Results ledger). A row of boxed numbers is the look this redesign set out to replace.
5. **Plain words on the surface; detail one click away.** Tool names, arguments and refusal codes stay on the call page behind "Technical details". The owner reads "Held the booking until the safety lines were said", and Revin's engineer reads `book_appointment refused gate_safety` in the same place.
6. **Read-only except one thing.** The only write is the report to Revin (story O-8). Nothing on the page changes a call, a job or a flag.

## Tokens

### Color

| Token | Value | Used for |
|---|---|---|
| `--page` | #F7F6F5 | The page behind panels (Revin's Offwhite) |
| `--surface` | #FFFFFF | Panels, controls |
| `--sunken` | #FAFAF9 | Board axis, group rows, notes in a call |
| `--hover`, `--selected` | #F5F4F2, #FFF6F2 | Row hover; a call line picked for a report |
| `--line`, `--line-soft`, `--line-strong` | #E6E4E1, #EFEEEB, #D3D1CD | Panel edges; rows inside a panel; control edges |
| `--ink`, `--ink-2`, `--muted`, `--faint` | #1A1919, #3B3A38, #5E5C59, #706D68 | Body, secondary copy, labels, times and captions |
| `--ember`, `--ember-ink`, `--ember-wash`, `--ember-line` | #FA3500, #BA2500, #FFF3EE, #FFC6B3 | Brand, the agent's name in a call, the report box |
| `--ember-action` | #DE2F00 | The primary button and the "now" label, where white text sits on Ember |
| `--urgent`, `--urgent-ink`, `--urgent-wash` | #D62100, #A11A00, #FDECEA | Urgent priority, late callbacks, safety |
| `--high`, `--high-ink`, `--high-wash` | #E5A800, #6B4E00, #FFF4CC | High priority, things held or waiting |
| `--done`, `--done-ink`, `--done-wash` | #077E50, #055C3B, #E6F4EE | Done, sent, answering |
| `--info`, `--info-ink`, `--info-wash` | #0265DC, #0B4A9E, #E3EEFD | Scheduled jobs, arrival windows, links |
| `--neutral`, `--neutral-ink`, `--neutral-wash` | #A3A09B, #4A4845, #F1EFEC | Normal priority, other calls, segmented control track |

The brand values come from Revin's site and app (SOURCE: docs/explorations/009-revin-brand-and-servicetitan-ui.md). The status values are ServiceTitan's Anvil palette (SOURCE: the same exploration), with `--high` darkened from Anvil's #FFBE00 to #E5A800 so a yellow dot holds up on white (ASSUMED: judged by eye at desktop size). Text on a wash always uses the matching `-ink` value.

### Type

One family, Public Sans, from 100 to 900 weight in one variable file. Hierarchy comes from size and weight, never from color alone.

| Token | Size | Used for |
|---|---|---|
| `--text-2xl` | 26 px | Page titles ("Tuesday, Nov 3") |
| `--text-xl` | 20 px | Figures |
| `--text-lg` | 16 px | The summary sentence, values in the Results ledger |
| `--text-md` | 14 px | Body copy and table rows |
| `--text-sm` | 13 px | Controls, captions, notes in a call |
| `--text-xs` | 12 px | Tags, table headings, hour labels |

Why Public Sans: Revin uses GT America, a paid font that can't ship here (ASSUMED license). GT America is built from Franklin Gothic and News Gothic; Public Sans comes from Libre Franklin, a Franklin Gothic revival, so it keeps the same American gothic shape. It was made by the US Web Design System for dense, plain government pages, and Google's file carries tabular figures (MEASURED: its GSUB table lists `tnum`). Inter, the old choice, is the default of half the dashboards on the web, which is part of why the old page read as generic. Schibsted Grotesk, Instrument Sans and Archivo were compared on the same sample (DECISIONS.md 016).

Times, phone numbers and counts use `.num` (tabular figures) so columns line up. Tabular figures are not turned on for running text, where they space commas and colons oddly.

### Space, shape and size

- Space is on a 4 px grid: `--s-1` (4) to `--s-12` (48). Panels pad 16; the page gutter is 24; panels stack 24 apart.
- Radius: `--r-sm` 4 for tags, `--r-md` 6 for controls, `--r-lg` 8 for panels.
- Sizes: every control in a row is `--control-h` (32 px); tags inside tables are `--tag-h` (22 px); the header is `--header-h` (56 px); the page is at most `--page-w` (1280 px).
- Depth: panels are flat with a 1 px line. Only the active segmented button gets `--shadow-1`. Focus is a 3 px blue ring (`--focus`).
- Motion: `--fast` (120 ms) for hover, `--base` (200 ms) when board marks move. Both drop to zero under `prefers-reduced-motion`.

## Components

Each component lists its parts, its states and when to use it. All of them are on `/owner/design`.

### App header
Revin logo, a rule, the company name, the three tabs (Today, Calls, Results) with Ember under the current one, and the status line on the right. Sticky. The tabs carry the day being looked at, so moving between pages stays on the same day.

### Status line (story O-5)
A dot and a few words. Three states from the voice workers' heartbeats (`owner.status`): **ok** (green, "Answering calls, last one 5:15 PM"), **down** (red, "Not answering since 4:12 PM") and **unknown** (grey, "Last call 5:15 PM"), which is what a development server or the demo shows because no voice worker reports to it.

### Page head
Crumbs, the page title, day navigation (previous, next, "Back to today") and, on Today, the summary sentence. The sentence is built by `owner.summary` as parts: plain, bold for jobs booked, and red for anything late or needing a person, so the alarm words stand out without a banner.

### Figures
Up to four numbers on one line, each a value over a short label, separated by hairlines. One can be marked as an alarm (red) and one can link to the section it counts ("4 need a person" jumps to Needs attention). This replaces the row of metric cards. Use it for the few numbers worth a glance; anything that needs comparing goes in a table.

### Panel
White surface, 8 px radius, a head with a title, a count in faint ink and optional controls on the right. A foot for a one-line note or a `<details>` of extra rows ("4 handled today").

### Buttons
Default (outlined), **primary** (Ember, one per page at most), **quiet** (no border, for actions inside text, such as "This was wrong" on a call line) and **icon** (square). Disabled reads as `aria-disabled` and greys out. Labels are verbs or destinations, three words or fewer.

### Filters
Two controls, the same height as every other control in the row:
- **Toggle**: an on/off button with a color swatch and a count ("Needs a person 5"). Off shows a hollow swatch on a sunken background. Used for the board's lanes and "Handled too".
- **Segmented control**: one choice of a few ("7 AM to 9 PM" or "Whole day"; "Day", "7 days", "30 days"; "Plain" or "Technical details"; the call list's All, Needs a person, Booked, Turned away, Placed by us).
- **Select**: for a choice of many (call type on Results).

Filters are remembered per browser in `localStorage` (a convenience only; the page works and reads correctly without it).

### Tags
A word on a wash, 22 px high: **urgent**, **high**, **done**, **info** (windows, job numbers), **ember** and **outline** (states that are neither good nor bad, such as "Held here" or "Placed by us"). The word always names the state.

### Table
Dense rows with a 13 px heading in faint ink. Columns that hold times or short values shrink to fit (`.time`, `.fit`); the main column takes the rest and shows a bold heading with its reason under it (`.what`, `.why`). An open urgent row is washed red. A closed row goes faint. Group rows (`.group`) split a table by window or day.

### Dispatch board
The day from 7 AM to 9 PM, widening to the hour for any call outside it (`owner.board`).
- **Axis**: hour labels every two hours, hairlines every hour, and the "now" label in Ember.
- **Lanes**: Needs a person (labelled marks: "Gas smell", "Late callback"), Jobs booked (blue dots at the time the call booked), Other calls (grey dots). A safety call that booked a check shows in both of the first two, because it is both.
- **Marks**: a labelled pill or a 14 px dot, positioned by the minute of the day. Marks that would overlap move down a row (first fit, in time order, in `owner.js`). Each links to its call, and its tooltip carries the full reason.
- **Arrival windows**: a bar per window with how many jobs land in it and how many are urgent. A window that has passed goes grey; one with no jobs is dashed.
- **Filters**: each lane can be hidden; handled flags are hidden until "Handled too" is on; the hours can switch to the whole day.

Positions are computed in Python (`owner.position`) so the board is right before any script runs, and the script only re-places marks when the range changes.

### Tray
A panel with tabs (Callbacks, Leads, Turned away) and a list under each. Late items are washed red and sort first.

### List
Rows of a bold heading, a meta line and an end column for a tag and a link. Used in the tray, the Results turned-away list and the reports.

### Key-value list
Labels in muted ink on the left, values on the right. Used for the job and customer on a call.

### Call header
Crumbs back to the day and the call list, the caller's name (or address, or number), tags for safety, priority, job number and "Needs a person", a facts line (time and length, number, what it was about, "Call 22 of 23 that day") and "Earlier" and "Later" buttons with the times of the calls either side. This replaced a strip of dots across the day, which showed where the call sat but not what was around it.

### Conversation (stories O-6, O-7)
Turns with the time from the start of the call, the speaker (the agent in Ember ink) and the words. After each turn, a note for whatever the agent did, from `plain.py`: a check mark for done, a pause sign for held back, a warning sign for safety and overrides. "Technical details" shows each tool call's name, result, gate, timing and arguments under its note. The recording isn't kept, and the panel foot says so.

### Report box (story O-8)
An Ember-washed form: which line (or the whole call), what should have happened, and "Send to Revin". Hovering an agent line shows "This was wrong", which picks that line and focuses the box. Sent reports list under it with their state: **Held here** (no GitHub token on the server), **With Revin, #41** (filed as an issue) or **Retrying**.

### Bar chart
Calls by hour as stacked bars: booked (blue), safety (red), other (light blue), with the count on top and a zero greyed. The call type select redraws it from the calls embedded in the page.

### Results ledger
A table of measures down the side and periods across: this period, the period before, and the change (green up, red down, a dash when either side has nothing to measure). Rates change in percentage points. Built by `owner.ledger`.

### Empty state
One centred line in faint ink that says what isn't there ("Nothing needs a person right now."). Never an illustration.

## Words

| Use | Not |
|---|---|
| Job, Jobs booked | Booking, appointment (in the page; the code still says booking) |
| Arrival window, "Today 4 to 8" | Slot, time window |
| Service location | Address |
| Needs a person | Flag, escalation |
| Late callback | Overdue callback |
| Turned away | Declined, refused |
| Held the booking until... | Refused, gate fired |
| Tell Revin, Send to Revin | Report a bug, feedback |
| Safety override | Override flag |

Sentence case everywhere. No ids, tool names or codes outside "Technical details". Numbers carry their unit in the label ("booking rate, 8 of 9"), not in a tooltip.

## Accessibility

- Text meets 4.5:1 on its background (MEASURED with the WCAG 2.1 contrast formula on the token values): `--ink` 17.5:1 on white, `--muted` 6.7:1, `--faint` 5.2:1 on white and 4.8:1 on the page, every `-ink` on its `-wash` between 5.8:1 and 7.9:1, links 5.4:1, and white on `--ember-action` 4.7:1. Two tokens were darkened to get there: `--faint` was #86837E (3.8:1) and white on the brand Ember is 3.8:1, which is why buttons use `--ember-action`.
- Status never depends on color alone; the word is always there.
- Every board mark is a link with the full reason in its title and, for dots, screen-reader text.
- Toggles use `aria-pressed`, tabs use `role="tab"` and `aria-selected`, and the status line is a `role="status"`.
- Focus shows a blue ring on every control.

## Adding to it

A new component gets its tokens from `tokens.css` (add a token only when no existing one fits, and say what it is for), its CSS block in `components.css` under a heading comment, a specimen on `/owner/design`, and a section here. A page never sets a color or size inline except a position on the board or a bar height.
