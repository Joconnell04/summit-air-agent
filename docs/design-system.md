# Owner view design system

The owner view is built for the person who runs Summit Air: someone who knows HVAC and ServiceTitan, reads a dispatch board every morning, and has never read a log (docs/users.md, DECISIONS.md 014). Every page is drawn from the two files below, and `/owner/design` shows each token and component live, from the same CSS.

| File | What it holds |
|---|---|
| `app/static/owner/tokens.css` | Every color, size, space, radius, shadow and timing, as CSS custom properties |
| `app/static/owner/components.css` | Every component, built only from tokens |
| `app/templates/_ui.html` | Jinja macros for the components that repeat (tags, counts, figures, a call row, the status line, day navigation, note icons) |
| `app/static/owner/owner.js` | The few interactions: board filters and layout, tabs, whole-row links, tooltips, search as you type, resizable columns, the technical details switch, the Results filter, and on the debug bench picking a step on the node graph and the live view. Each page works without it, apart from search as you type, the technical details and the live view |
| `app/static/owner/fonts/` | Public Sans, served from the app, with its license |

## Principles

1. **Read like a dispatch board, not a dashboard.** The day runs left to right, the way ServiceTitan's dispatch board does, and the tables underneath use ServiceTitan's words: Job, Arrival window, Service location, Urgent, High. An owner who uses ServiceTitan should read the page without a key.
2. **Words first; color backs them up.** Every status is written out ("Late", "Urgent", "Held here"). Color repeats it for a glance and is never the only signal.
3. **Revin's brand, used sparingly.** Ember (`--ember`, #FA3500) marks the brand, the active tab, the one primary action on a page ("Send to Revin") and the "now" line. Where white text sits on it, a slightly darker Ember (`--ember-action`) keeps the text readable. It never marks a status, because ServiceTitan's red already means urgent and two reds would compete.
4. **No metric cards.** Numbers sit on one ruled line (the figures component) or in a ruled table (the Results ledger). A row of boxed numbers is the look this redesign set out to replace.
5. **Plain words on the surface; detail one click away.** Tool names, arguments and refusal codes stay on the call page behind the "Technical details" switch, which also offers the whole record as a log. The owner reads "Held the booking until the safety lines were said", and Revin's engineer turns the switch on to read `book_appointment refused gate_safety` in the same place.
6. **A row is the link.** A row that opens a call opens it anywhere on the row, and a chevron at its end says so. The row's name is the real link, for keyboards and for pages without the script.
7. **One line per row.** Longer detail, such as why a job is urgent, waits in a tooltip so rows stay the same height and a column reads straight down.
8. **Read-only except two things.** The report to Revin (story O-8), and the rules editor behind its own edit login (exploration 009), which changes only wording and guidance and goes live only after the eval gate. Nothing on the page changes a call, a job or a flag.

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

The brand values come from Revin's site and app (SOURCE: docs/explorations/012-revin-brand-and-servicetitan-ui.md). The status values are ServiceTitan's Anvil palette (SOURCE: the same exploration), with `--high` darkened from Anvil's #FFBE00 to #E5A800 so a yellow dot holds up on white (ASSUMED: judged by eye at desktop size). Text on a wash always uses the matching `-ink` value.

### Type

One family, Public Sans, from 100 to 900 weight in one variable file. Hierarchy comes from size and weight, never from color alone.

| Token | Size | Used for |
|---|---|---|
| `--text-3xl` | 32 px | The value in a figure |
| `--text-2xl` | 26 px | Page titles ("Tuesday, Nov 3") |
| `--text-xl` | 20 px | The sign-in title |
| `--text-lg` | 16 px | Values in the Results ledger |
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
Revin logo, a rule, the company name, the four tabs (Today, Calls, Results, Rules) with Ember under the current one, then search and the status line on the right. Sticky. The first three tabs carry the day being looked at, so moving between pages stays on the same day; Rules has no day. An admin also sees a fifth tab, Bench, for the debug bench; on the bench the tabs are Calls, Settings, Evals and a link back to the owner view.

### Search
One field in the header on every page, backed by a full-text index of every call on record (`app/search.py`, SQLite FTS5): the call (caller name and number, what it was about, how it ended), its job (service location, ZIP, job number, priority and why, arrival window), the flags it raised, its leads and callbacks, and every line said on it. Every word typed has to match, each as the start of a word; a phone number matches however it is written; a lone one- or two-digit number is ignored because it would match nearly everything; punctuation never reaches the index's own query syntax. The index is kept current by the jobs loop and again before each search, and only calls that changed since they were indexed are redone, so a refresh with nothing new is three reads (MEASURED: `test_a_refresh_with_nothing_new_is_three_reads` counts the statements). Calls are indexed a hundred to a transaction, so a first build never holds the write lock for long. A transcript removed by retention leaves the index on the next refresh. Each kind of result is its own query, so many matches of one kind never crowd out another, and the counts are the true totals. At about 5,800 calls (the demo day copied 130 times), a refresh with nothing new takes 19 ms, a search 36 ms and a suggestion 19 ms (MEASURED on a laptop with a scratch timing script).
- **As you type**: after two characters, a popover under the field shows the best few matches across kinds (`/owner/search/suggest`), each with its kind and time. Arrow keys move, Enter opens the highlighted match or the full results, Escape closes.
- **Results page**: matches grouped as Calls, Jobs, Needs a person, Leads and callbacks, and Said on a call, with jump links and the matched words highlighted in each snippet. A line said on a call opens the call at that line.

### Status line (story O-5)
A dot and a few words. Three states from the voice workers' heartbeats (`owner.status`): **ok** (green, "Answering calls, last one 5:15 PM"), **down** (red, "Not answering since 4:12 PM") and **unknown** (grey, "Last call 5:15 PM"), which is what a development server or the demo shows because no voice worker reports to it.

### Page head
Crumbs, the page title and day navigation (previous, next, "Back to today"). Today's numbers sit on the figures line under it. An earlier version opened with a generated sentence ("21 calls came in. 10 jobs booked."), which read as stilted, so the figures carry those counts instead.

### Figures
Up to five numbers across the page between two hairlines, each read top down: what it counts ("Calls answered"), the number in 32 px, then what the number is made of ("5 after hours"). "Need a person" counts calls; its last line gives the open items behind them, which is the count the board and Needs attention show. The value or that last line can be marked as an alarm (red), and a figure can link to the section it counts ("Need a person" jumps to Needs attention). This replaces the row of metric cards. Use it for the few numbers worth a glance; anything that needs comparing goes in a table.

### Count
A number in a small round pill beside its label, so it reads apart from the words: lane toggles, tray tabs, the call list filter, panel heads. Red when the count is of something waiting on a person. On the selected tab the pill turns ink.

### Panel
White surface, 8 px radius, a head with a title, a count, a faint note and optional controls on the right. A foot for a one-line note or a disclosure of extra rows ("Show 4 resolved"). A **compact** panel drops the ruled head, for side panels of a few lines (What happened, Job booked, For the technician, Customer on a call).

### Disclosure
A quiet outlined button with a chevron that turns when open, for rows kept out of the way ("Show 4 resolved", "What the server held back").

### Buttons
Default (outlined), **primary** (Ember, one per page at most), **quiet** (no border, for actions inside text, such as "This was wrong" on a call line) and **icon** (square). Disabled reads as `aria-disabled` and greys out. Labels are verbs or destinations, three words or fewer.

### Filters
Two controls, the same height as every other control in the row:
- **Toggle**: an on/off button with a color swatch and a count ("Needs a person 5"). Off shows a hollow swatch on a sunken background. Used for the board's lanes and "Show resolved".
- **Segmented control**: one choice of a few ("7 AM to 9 PM" or "Whole day"; "Day", "7 days", "30 days"; the call list's All, Needs a person, Booked, Turned away, Placed by us).
- **Select**: for a choice of many (call type on Results).

Filters are remembered per browser in `localStorage` (a convenience only; the page works and reads correctly without it).

### Tags
A word on a wash, 22 px high: **urgent**, **high**, **done**, **info**, **ember** and **outline** (states that are neither good nor bad, such as "Moved earlier", "Held here" or "Placed by us"). The word always names the state. Use as few as the row needs: priority is a tag only at Urgent and High (Normal is plain text), arrival windows and job numbers are plain text, and a call header carries one tag for where the call stands.

### Tooltip
Longer detail on hover, focus or a tap, in a small dark box placed by `owner.js` so a table's edge never clips it, from a small "i" after the row's name. Escape or a scroll closes it, and the same words are the icon's label for screen readers.

### Table
Dense rows with a 13 px heading in faint ink. Columns that hold times or short values shrink to fit (`.time`, `.fit`); the main column takes the rest and shows a bold heading with its reason under it (`.what`, `.why`). A **fixed** table (Jobs booked) sets its column widths and keeps every row to one 44 px line, with the reason in a tooltip. Its columns can be **resized**: dragging the edge between two headings moves width from one to the other, the widths are kept per browser, and a double-click on an edge puts them back. Job gets the most room by default. A row that opens a call carries `data-href` and ends in a chevron (`.go`). An open urgent row is washed red. A resolved row goes faint. Group rows (`.group`) split a table by window or day.

### Dispatch board
The day from 7 AM to 9 PM, widening to the hour for any call outside it (`owner.board`).
- **Axis**: hour labels every two hours, hairlines every hour, and the "now" label in Ember. The now line runs over the marks and windows, so the present is never hidden behind one.
- **Lanes**: Needs a person (labelled marks: "Gas smell", "Late callback"), Jobs booked (blue dots at the time the call booked), Other calls (grey dots). A safety call that booked a check shows in both of the first two, because it is both.
- **Marks**: a labelled pill or a 14 px dot, positioned by the minute of the day. Marks that would overlap move down a row (first fit, in time order, in `owner.js`). Each links to its call, and its tooltip carries the full reason.
- **Arrival windows**: a bar per window with how many jobs land in it and how many are urgent. A window that has passed goes grey; one with no jobs is dashed.
- **Filters**: each lane can be hidden; resolved flags are hidden until "Show resolved" is on; the hours can switch to the whole day.

Positions are computed in Python (`owner.position`) so the board is right before any script runs, and the script only re-places marks when the range changes.

### Tray
A panel with tabs (Callbacks, Leads, Turned away), each with its count, and a list under each. Late items are washed red and sort first. A callback says who calls back and by when ("The office calls back by 1:40 PM", "The agent called back").

### List
Rows of a bold heading, a meta line and an end column for a tag and the chevron. The whole row opens its call. Used in the tray, the Results turned-away list and the reports.

### Key-value list
Labels in muted ink on the left, values on the right. Used for the job and customer on a call.

### Call header
Crumbs back to the day and the call list, the caller's name (or address, or number) with one tag for where the call stands (Needs a person, Booked or Placed by us), a facts line (time and length, number, what it was about with a warning sign for a safety call, the job number, "Call 22 of 23 that day") and "Earlier" and "Later" buttons with the times of the calls either side. An earlier version put safety, priority, job number and status in four tags, which read as clutter. This replaced a strip of dots across the day, which showed where the call sat but not what was around it.

### Conversation (stories O-6, O-7)
Drawn like a commit graph: the turns run down a rail with the time from the start of the call on the left, a hollow node for the caller and a filled Ember node for the agent. What the agent did between turns branches off the rail on a short edge, as a note from `plain.py`: a check mark for done, a pause sign for held back, a warning sign for safety and overrides. "Said the signed-off safety lines" appears only on the turn the server accepted as the lines; a safety call where they never came ends with a warning saying so. **Technical details** is a switch in the conversation head, off by default and remembered per browser: on, each step shows the tool's name and, under its note, what the agent sent and got back (name, result, gate, timing, arguments), and "Download log" appears to save the call's whole record as JSON (`/owner/calls/{id}/log`). Off, the owner sees only the plain notes. An earlier version put a "Details" disclosure on every step, which left a control on every row of the plain view. When the call was recorded, the player sits under the panel head, above the first turn; when it wasn't, the panel foot says so.

### Player (story O-6)
A call's recording on one row, one control high, on the sunken surface under the conversation head: an icon button (play, then pause), a scrub bar, and the time as `0:42 / 2:13` in tabular figures. The played part of the bar and the playhead are Ember, the same "now" mark as the board. The bar is a native range input, so arrow keys move it and it takes the focus ring. Without the script the browser's own audio controls show in its place. Macro `player(src)` in `_ui.html`; the audio is `/owner/calls/{id}/recording`, which asks for the owner sign-in like every page.

### Report box (story O-8)
An Ember-washed form under the conversation, so it is read after the call: which line (or the whole call), what should have happened, and "Send to Revin", on one row at one height. The text box starts one line high, level with the select and the button, and grows as you type. Hovering an agent line shows "This was wrong", which picks that line and focuses the box. Sent reports list under it with their state: **Held here** (no GitHub token on the server), **With Revin, #41** (filed as an issue) or **Retrying**.

### Bar chart
First on Results. Calls by hour as stacked bars: booked (blue), safety (red), other (light blue), with the count on top and a zero greyed. The call type select redraws it from the calls embedded in the page.

### Results ledger
Titled "Performance", under the chart. A table of measures down the side and periods across: this period, the period before, and the change (green up, red down, a dash when either side has nothing to measure). One line per measure, with what it counts beside its name in faint ink. Rates change in percentage points. Built by `owner.ledger`.

### Range picker
The Results period: Day, 7 days or 30 days, and "Custom", a disclosure whose popover takes a from and a to date and Apply. Up to 92 days; the dates can come in either order, an end after today is read as today, and a longer range shows its last 92 days and says so. The period before is the same number of days.

### Field
One line of text an owner types: 32 px high, the same edge and radius as a select, a blue focus ring. An invalid field gets a red edge (`aria-invalid`) and the reason under it in red (`.error.field-error`), written by the server, never a browser alert. Used on the rules editor.

### Form table
A table (`.table.form`) whose rows are a label and its field, centred on the field's line so a label reads level with its box. The rules editor's Wording and Guidance panels.

### Actions
A page's controls on one row at one height (`.actions`): a status tag, a field at a fixed 280 px, then the buttons, the primary last. The rules editor's head: "What changed", Discard, Save draft, Publish.

### Rules page
`/owner/rules` lists every rule by call stage (Before hello, Opening, Whole call, Booking, Closing, After the call), one panel each, built from the tenant files and prompts (`app/rulebook.py`). Each row is the rule, how it is kept (Server check, Exact wording, Company setting, Agent guidance, Your guidance) and a tag: **Editable** (info) or **Locked** (outline), whose tooltip says why. A segmented control shows All, Editable or Locked. Versions sit under the stages with their check: **Passed 18 of 18** (done), **Blocked, 2 of 18 failed** (urgent, the failed calls in the tooltip), **Checking** (high), **Rolled back**, **Draft**, **Not published, live changed** (a rollback landed while it was checked) and **1 on a rerun** (outline). While a check runs, the editor offers **Stop check**, which returns the draft; a check that never reports back returns it after an hour. The editor (`/owner/rules/edit`) needs an edit code; the Rules page opens with the read token.

### Node graph (debug bench)
A call as the steps it took, one 32 px row each, read top down: the time from the start of the call, the step in the lane of whoever took it (Caller, Voice worker, Model, Server), and its timing on the right. Each lane has a hairline rail and each step a 12 px node on its rail: hollow for the caller, Ember for the model, grey for the voice worker, ink for the server; a step that held something back is yellow, a safety step red, a rule that let a request through green. A short edge joins a step to the lane of the step before it, so a turn reads as a path across the lanes, the way a sequence diagram does. The row shows the step's name in bold and a one-line summary that ends in an ellipsis; a timing the record doesn't hold reads "missing" in faint italic, never a made-up number, and one over 1.5 s is red. Picking a step (a click, or the arrow keys) shows its **detail** in a sticky panel beside the graph: a key-value list of facts, the messages a model request was given under their roles, and code blocks for arguments, results and prompts, each in a disclosure. Without the script each step links to its detail by its anchor. Macros `node_graph(graph)` and `node_detail(graph)` in `_ui.html`; the graph comes from `app/nodes.py`.

### Rate chart (debug bench)
Eval runs as thin bars, oldest on the left, each as high as its pass rate, with the ratchet's floor as a dashed line across and its value at the right end. A bar under the floor is red, a run with no verdict grey. Each bar links to its run and carries the run and the count in its tooltip.

### Basis and outcome tags (debug bench)
Where a documented number comes from, as a tag: **Measured** (done), **Source** (info), **Assumed** (outline), following the labels every doc uses. How a rule or an eval came out: **Pass** and **Passed** (done), **Fail** (urgent), **Held** (high, a rule refused a request), **No verdict** and **Not reached** (outline). Macros `basis(label)` and `outcome(value)`.

### Prose
A document in a panel (an eval round on the bench): headings at body size in semibold, lists, ruled tables in a light frame, at most 860 px wide.

### Empty state
One centred line in faint ink that says what isn't there ("Nothing needs a person right now."). Never an illustration.

## Debug bench

`/bench`, for engineers and admins only (`require_admin` in `app/bench.py`), on the same server and design system as the owner view, denser. Calls (search by call id or phone digits, calls in progress first), a call's node graph with the rule proof and a draft eval scenario made from it, Settings (the tuning knobs and model profiles with the measurement each has in the docs) and Evals (the rate chart, each run's failures with their code checks and grader notes, a scenario's transcript, the rounds). A call in progress updates as it goes, from server-sent events. Every value is redacted before it is drawn (`app/redact.py`): phone numbers keep their last four digits, a service location keeps its street name, and anything key-like is replaced. The owner pages don't change, apart from the admin's Bench tab.

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
| Resolved, Show resolved | Handled, closed |
| Performance | Is it paying off |
| The office calls back by 1:40 PM | Office, by 1:40 PM |
| Locked, Editable | Gated, overridable |
| Publish, Roll back | Deploy, revert |
| Note to the agent, guidance | Prompt, instruction |

Sentence case everywhere. No ids, tool names or codes outside "Technical details" and the log. Name ServiceTitan once in a place: the logo does it, so the words beside it say "all 10 sent", not "10 in ServiceTitan". Numbers carry their unit in the label ("booking rate, 8 of 9"), not in a tooltip. "Resolved" for a flag a person has dealt with, not "handled".

## Accessibility

- Text meets 4.5:1 on its background (MEASURED with the WCAG 2.1 contrast formula on the token values): `--ink` 17.5:1 on white, `--muted` 6.7:1, `--faint` 5.2:1 on white and 4.8:1 on the page, every `-ink` on its `-wash` between 5.8:1 and 7.9:1, links 5.4:1, and white on `--ember-action` 4.7:1. Two tokens were darkened to get there: `--faint` was #86837E (3.8:1) and white on the brand Ember is 3.8:1, which is why buttons use `--ember-action`.
- Status never depends on color alone; the word is always there.
- Every board mark is a link with the full reason in its title and, for dots, screen-reader text.
- A row that opens a call has a real link on its name, so it works by keyboard and without the script; the rest of the row is a mouse convenience. Tooltip text is also in the page as screen-reader text, and anything with a tooltip takes focus.
- Toggles use `aria-pressed`, tabs use `role="tab"` and `aria-selected`, and the status line is a `role="status"`.
- Focus shows a blue ring on every control.

## Adding to it

A new component gets its tokens from `tokens.css` (add a token only when no existing one fits, and say what it is for), its CSS block in `components.css` under a heading comment, a specimen on `/owner/design`, and a section here. A page never sets a color or size inline except a position on the board or a bar height.
