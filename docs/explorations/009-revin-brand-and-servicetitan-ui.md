# 009. Revin brand and ServiceTitan UI patterns for the owner view

Issue: none opened yet. Researched 2026-11-02 and 2026-11-03 with WebSearch, WebFetch, curl and a local headless Chrome (no paid calls). Labels: SOURCE (URL given), MEASURED (computed style read from the live page in headless Chrome on those dates), ASSUMED (my judgment). Assets are in `docs/images/brand/`. Longer than two pages because it carries two reference tables the redesign will copy from.

## 1. Question

What are Revin's real brand values (logo, colors, type, shape, tone), what do ServiceTitan's screens and words look like to an HVAC owner who uses it daily, and what does that mean for a Revin-branded owner dashboard that a ServiceTitan user feels at home in?

## 2. Why it matters to Summit Air

`docs/brief.md` has the owner judging the agent from the owner view, and `docs/spec.md` S-44 lists what it must show (today's bookings, flags, people needed, booking rate by call type, callbacks promised vs done, booked-and-kept). DECISIONS 012 rebuilt that page for a non-technical owner, but `app/templates/base.html` still uses a dark dev palette (`--bg: #0e0e10`, `--accent: #7d9bff`, mono numerals). The tester playing the owner works in ServiceTitan all day, and the case is judged by Revin, so the page should look like Revin's product and read like ServiceTitan. The mock FSM already follows ServiceTitan's field names (exploration 006), so the words are cheap to match.

## 3. Options

### 3a. Revin brand facts

| Item | Value | Label |
|---|---|---|
| Light or dark | Marketing site and sign-in are light; the product stylesheet defines both a light `:root` and a `.dark` theme | MEASURED revin.ai, app.revin.ai/sign-in; SOURCE [app css] |
| Background, text | White `#FFFFFF`; text Offblack `#1A1919` on the site, `#18181B` (zinc-900) in the app | MEASURED; SOURCE [app css] |
| Accent | "Ember" `#FA3500` (the logo flag, every primary button, eyebrow labels); tints `#FFEEE9` (50), `#FF6C4E` (300), `#BA2500` (700) | MEASURED button fill `rgb(250, 53, 0)`; SOURCE [logo svg], [app css] |
| Secondary | Offwhite `#F7F6F5`, zinc greys (`#FAFAFA` card, `#E5E7EB` border, `#71717A` muted); named but rarely used: Hardhat `#E4F223`, Gold `#FECB16`, Camo `#393E22`, Indigo `#232EFE` | SOURCE [app css] |
| Status and chart colors in the app | Tinted tag pairs (for example blue-100 background with blue-900 text, green, orange, red, purple); channel colors SMS blue `#237EFE`, voice purple `#8B5CF6`, email gold `#E08928`; revenue "automated" green `#009966`, "assisted" `#83C3A7`, "human" grey `#E4E4E7` | SOURCE [app css] |
| Fonts | GT America (Regular, Medium, Light, Bold; commercial, self-hosted from framerusercontent.com on the site and from the app's own `/_next/static` files in the app); GT America Mono in the app; Fragment Mono (Google Fonts) for the small uppercase orange eyebrow labels; Inter as the site's fallback | SOURCE [home html], [app html]; MEASURED h1 `GT America Medium` 56px, body 16px |
| Shape | Site: pill buttons (radius 50px), cards 12px, hero 20 to 26px. App: base radius `.5rem` (8px); inputs and the Log in button measure 6px, 40 to 42px tall | MEASURED; SOURCE [app css] |
| Buttons | Primary: Ember fill, white text, medium weight. Secondary: white fill, 1px `#E5E7EB` border, dark text ("Book a demo today") | MEASURED app sign-in |
| Photos | Full-bleed, warm, documentary-style images of tradespeople at work (hard hat and vest, ladder, service van, a man in a cap checking a phone); white-on-photo type; Ember used as flat color blocks. No illustrations or 3D | MEASURED screenshots in `docs/images/brand/` |
| Copy tone | Plain, outcome-first, sentence case: "Let's get to work." "Action Furnace didn't need another dashboard. They needed another set of hands." "No scheduling links. No manual entry." Numbers lead cards ("10%", "15 days"). Ops words: booked, dispatch board, CSR, handoff | SOURCE [action furnace], [home] |
| Dashboard screens | Revin's own customer dashboard is behind login ("Revin Dashboard" is the page title; a job post calls it a Next.js dashboard for SMS, voice and email conversations). No public screenshot exists on the site, blog, or case studies; Action Furnace's case study says they "didn't need another dashboard". The sign-in screen is saved; the in-app layout is unknown. | SOURCE [app html], [action furnace]; gap |

Logo files saved (SOURCE [logo svg] and favicon links in revin.ai's `<head>`): `revin-logo.svg` (real vector, two fills: `#1A1919` letters, `#FA3500` flag), `revin-logo.png` (rendered from that SVG, not redrawn), `revin-mark-64.png`, `revin-mark-dark-64.png` (white R for dark backgrounds), `revin-mark-180.png` (apple-touch-icon), `revin-og-image.jpg`. No standalone vector of the R mark and no white wordmark exist on the site; I did not redraw either. ServiceTitan: `servicetitan-logo.svg` and `.png` (horizontal, black, taken from the inline SVG in servicetitan.com's header) and `servicetitan-logo-stacked.png` (its og:image). Screenshots: `revin-app-sign-in.png`, `revin-site-home-hero.png`. Both logos are trademarks; ServiceTitan's is for reference only.

### 3b. ServiceTitan patterns an owner already knows

| Area | What the owner sees | Label |
|---|---|---|
| Design system | Anvil2 (React, `@servicetitan/anvil2`), built on the public `@servicetitan/hammer-token` package; light and dark pairs for every token; a "Carto" theme (Inter body, Sofia Pro headings) exists for the AI Kit only | SOURCE [anvil about], [anvil theming], [hammer npm] |
| Colors (light) | Primary and info blue `#0265DC` (hover `#1D4CA3`); success `#077E50`; warning `#FFBE00`; danger `#D62100`; page `#FFFFFF`; subdued text `#707070`; default border `#8B8B8B`; hover surface `#F9F9F9` | SOURCE [hammer npm], read from `semantic.scss` v3.7.0 |
| Type | Sofia Pro for headings (bold), Nunito Sans for body and eyebrows (uppercase, subdued, semibold); body 16px, small 14px, headline 20 to 32px | SOURCE [anvil type], [hammer npm] |
| Shape | Buttons and inputs radius 0.375rem (6px); radius scale 3px to 24px; focus ring in info blue | SOURCE [hammer npm] |
| Navigation | Calls icon and Settings gear sit in a top toolbar; Settings has a side panel. The Daily Dispatch Board is under Dispatch | SOURCE [calls help], [dispatch help] |
| Dispatch board | One timeline row per Technician with a profile photo; job bubbles on the timeline; a Jobs Tray at the bottom with tabs Unassigned, Hold, Canceled and Priority Low, High or Urgent. Appointment colors: Scheduled light blue, Confirmed dark blue, Dispatched purple, Arrived (shown as Working) green, Done light grey. A red outline with an alert icon means act now (Late, Running Over, Not Completed, Appointment Request). Arrival Window is a separate field from the start time | SOURCE [dispatch glossary], [dispatch help] |
| Call booking | The Calls screen shows a green call bubble per live call; clicking it ties the call to the customer, location and Campaign. Not booking: close it with X and pick a Call Reason. Booking: Overview section, then the Book Job button; the call then clears. A call over 60 seconds counts as a lead unless a Call Reason says otherwise | SOURCE [book a job], [calls help] |
| "Booking" is not a job | In ServiceTitan a Booking is a request from the Web Scheduler or a lead site, waiting on the Bookings tab until someone accepts it into a Job | SOURCE [manage bookings] |
| Job record | Job Actions menu; Service Location, Bill To, Job Summary blocks each with Edit; Estimate; Appointments (Scheduled, Dispatched, Working, Done, Hold, Canceled); Attachments; History filtered by All, Events, Calls, Notes, Files, Email | SOURCE [job record] |
| Dashboard | Modular Dashboard of Report, KPI (up to six KPIs per module) and Visualization modules, grouped into sections; reports filter by date range, Business Unit, Job Type, Technician | SOURCE [dashboards help] |
| Vocabulary | Job, Appointment, Arrival Window, Technician, Business Unit, Campaign, Call, Call Reason, Lead, Booking, Membership, Priority (API values Low, Normal, High, Urgent, per exploration 006), Job Summary, Dispatch | SOURCE [help links above], [006] |

### 3c. Three ways to apply it

**A. Revin skin, current layout.** Replace the tokens in `base.html` with Revin's light product tokens (white page, `#FAFAFA` panels, `#E5E7EB` borders, 8px radius, Ember for the primary action only, tinted status tags, GT America stand-in). Keep every panel as is. Effort: low, one stylesheet (ASSUMED half a day). Cost: none. Risk: looks Revin but reads like a general dashboard; the words still say "Bookings" for something ServiceTitan calls a Job.

**B. A plus ServiceTitan vocabulary and cues.** Do A, then rename and recolor to ServiceTitan's conventions: "Jobs booked today" grouped by Arrival Window, Priority shown as ServiceTitan's Low, High, Urgent chips, flagged rows get a red outline as on the dispatch board, panel tabs like the Jobs Tray (Needs a person, Callbacks, Leads), the six tiles kept as one KPI row, a Call Reason column on Calls. Effort: medium (templates `owner.html`, `call.html`, labels in `app/owner_view.py`; tests that assert labels change; ASSUMED one day). Cost: none. Risk: label changes touch S-44 wording, so it goes through `/round`.

**C. Marketing-site look.** Big rounded photo cards, pill buttons, hero images. Effort: low. Risk: wrong tool for a dense table page; photos add weight and licensing questions. Listed only to rule it out.

## 4. Recommendation, and what would change it

Do B. Five concrete takeaways:

1. **Light page, one Ember accent.** White background, `#1A1919` text, Ember `#FA3500` only on the primary button, the Revin logo flag and the active tab. Both Revin's site and ServiceTitan's Anvil default to light, so an owner sees a familiar surface. ASSUMED: dark mode stays out of V1.
2. **Keep status colors ServiceTitan's, not Ember.** Ember is a red-orange and would blur with danger. Use ServiceTitan's `#D62100` danger, `#FFBE00` warning, `#077E50` success, `#0265DC` info for Urgent, High, Done and links, always with a text label (not color alone), and put alerts in a red outline as the dispatch board does. Use Revin's tinted tag style (light fill, dark text, 1px border) for the chips. ASSUMED that tinted pairs read better than the current filled outlines.
3. **Use ServiceTitan's words, and fix "Bookings".** Rename the Bookings panel "Jobs booked today", say Appointment and Arrival Window, Technician, Business Unit, Campaign, Call Reason, Membership. In ServiceTitan a Booking is an unaccepted request, so the current label would mislead the tester; the S-44 tile "Booked" can stay as "Jobs booked".
4. **Layout cues from the dispatch board and dashboard.** Top bar (Revin wordmark left, "Summit Air" and the date right, no dark sidebar); one row of six KPI tiles (ServiceTitan's KPI module also tops out at six); tabbed panels like the Jobs Tray; tables over cards; Revin's 8px radius, 6px on controls, 40px control height.
5. **Type and tone.** GT America is a paid font and cannot be shipped (ASSUMED license); use Inter, which is Revin's own site fallback and Anvil Carto's body font, from Google Fonts, plus Fragment Mono only for times and phone numbers. Copy stays plain and outcome-first: "3 callbacks overdue", "Job booked for Tue 8 to 12", sentence case, no jargon like `out_of_area` on the main view.

What would change it: if Revin supplies its dashboard screenshots or Figma, copy those instead of inferring from tokens; if the tester says the owner uses a dark screen or a phone, add the `.dark` token set (Revin already has one) and test a narrow layout; if Revin objects to a ServiceTitan-flavored look, fall back to A.

## 5. Open questions for Jackson

- Is it fine to show the Revin logo in the header of a page built for a Revin interview, or should the header carry only the "Summit Air" name with a small "powered by Revin" mark?
- Dark theme as an option, or light only?
- Rename "Bookings" to "Jobs booked" even though the assignment and spec say "bookings"? (Spec S-44 changes through `/round`.)
- Should the ServiceTitan logo appear anywhere in the UI (for example on a "Synced to ServiceTitan" tag), or stay in this repo as reference only?

## 6. Sources

Read on 2026-11-02 or 2026-11-03; none carries a publication date unless stated.
- [home html] https://revin.ai/ (rendered and CSS-inspected; hero, nav, logo strip lists "ServiceTitan Certified App")
- [logo svg] https://framerusercontent.com/images/9sHKoqf3QeWPdyWgTVrX6iu6whI.svg; favicons https://framerusercontent.com/images/LTdvECxooB3hBGomzDiqKn1Ou0M.png, https://framerusercontent.com/images/UQIGggAcLb5nxvXqQCyS47BTdss.png; apple-touch-icon https://framerusercontent.com/images/6ctrwxlcOuKhCI4hqyYAKl84i8.png
- [app html] https://app.revin.ai/sign-in (title "Revin Dashboard"; loads GT America and GT America Mono)
- [app css] https://app.revin.ai/_next/static/immutable/chunks/2d5ln7ytsmwev.css (palette, `.dark` theme, channel and revenue tokens)
- [action furnace] https://revin.ai/case-studies/action-furnace and https://revin.ai/hvac-plumbing-electrical
- [anvil about] https://anvil.servicetitan.com/docs/about-anvil2; [anvil type] https://anvil.servicetitan.com/docs/foundations/typography.md; [anvil theming] https://anvil.servicetitan.com/docs/foundations/theming.md; colors page https://anvil.servicetitan.com/docs/foundations/colors.md (names ramps, no hex)
- [hammer npm] https://registry.npmjs.org/@servicetitan/hammer-token/-/hammer-token-3.7.0.tgz (published 2026-11-02 per the npm registry; hex values from `build/web/core/semantic.scss`)
- [dispatch help] https://help.servicetitan.com/docs/dispatch-track-your-technicians; [dispatch glossary] https://help.servicetitan.com/docs/dispatching-icon-and-alert-glossary
- [book a job] https://help.servicetitan.com/docs/book-a-job.md; [manage bookings] https://help.servicetitan.com/docs/manage-bookings.md; [calls help] https://help.servicetitan.com/docs/reclassify-and-edit-calls
- [job record] https://help.servicetitan.com/docs/job-record-overview-1; [dashboards help] https://help.servicetitan.com/docs/custom-dashboards
- ServiceTitan logo: inline SVG in the header of https://www.servicetitan.com/ and og:image https://images.ctfassets.net/3q1a0w8cwuwf/7ISRCzbAIAcs4MGNvrYSWN/67264761ca3557c758165afc41dc04a9/ServiceTitan_Logo_Black_2.png
- [006] `docs/explorations/006-servicetitan-field-names.md`
- Gaps: no public Revin dashboard screenshots; Anvil color hex values are only in the npm package, not on the docs page; no image of ServiceTitan's dispatch board or job page was captured (help text only); the dispatch-board and job-page left/right arrangement was not stated in text.
