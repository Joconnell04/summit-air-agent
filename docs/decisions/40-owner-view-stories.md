# 40. The owner view user stories for a non-technical HVAC owner

Issue: #40. Status: ready

## Question

The owner view built in brief 06 works, but it is written for a software engineer: tool names like `check_service_area`, raw JSON, call ids, millisecond timings. Summit Air's owner knows HVAC deeply and runs the shop from ServiceTitan, but doesn't read logs. Which set of stories should the redesign be built from? The answer decides the pages, and the five design directions come after it.

## The stories

Written for the owner, who could also be the service manager. Each story names the feature it needs. Three pages carry them.

### Today page

- **O-1 Morning check.** I open one page and within ten seconds I know how the agent did since I last looked: calls answered, jobs booked, emergencies handled, anything waiting on my team. *Feature: a one-sentence summary at the top ("23 calls, 8 jobs booked, 2 safety calls handled, 2 callbacks are late") over four numbers.*
- **O-2 Emergencies first.** Every gas, carbon monoxide and vulnerable-household call sits at the top, with who, where, what the agent told them, and whether a person has followed up. *Feature: a "Needs attention" list sorted by urgency, each row showing its follow-up status.*
- **O-3 My team's to-do list.** Callbacks with the time we promised (late ones stand out) and sales leads (install, membership) with the contact and what they want, so nothing slips. *Feature: a to-do list grouped by who acts: office, comfort advisor, on-call technician.*
- **O-4 What the agent put on the schedule.** Every job it booked, with arrival window, address, priority, and a check that it's in ServiceTitan, in ServiceTitan's own words (job, arrival window, membership). *Feature: a bookings list shaped like a ServiceTitan job row.*
- **O-5 Is it answering right now.** A line saying the agent is answering and when the last call came in, and an email if it stops, with what callers hear meanwhile. *Feature: a status line in the header, plus the failure alert from phase 4.*

### Call page

- **O-6 Read or hear a call.** I open any call and read it like a text thread, play the recording, and see a two-line summary (what they needed, what happened) and the job it booked. *Feature: conversation view, audio player, summary card. Recordings aren't stored today, so this one is new work.*
- **O-7 Why it did that.** Wherever the agent did something, a plain note in the thread says what it checked and what it found: "Checked ZIP 11209: we cover it", "Found Chris Moy, Summit Standard member", "Held off booking until the gas lines were said". The technical detail stays one click away for Revin. *Feature: a plain-language line for each tool call; a "Technical details" toggle.*
- **O-8 Tell Revin something's wrong.** On any line of a call I can say "this was wrong" and what should have happened. Revin fixes it, adds the call to the tests so it can't come back, and I see the status. *Feature: a note on a moment of the call that becomes an issue and an eval case. The only thing the page writes; it never changes a call or booking.*

### Results page

- **O-9 Is it paying off.** Over 7 or 30 days: calls answered (and how many after hours), the share of calls wanting a visit that got one, members recognized, leads handed to sales, and booked jobs still on the schedule when the technician arrived. *Feature: five numbers with a trend, and booking rate by call type in plain names.*
- **O-10 It stays in its lane.** What it turned away and why: commercial callers, out-of-area ZIPs (which ones, to inform expansion), bookings held until the safety lines were said. *Feature: a "Turned away or held back" list, counted from what the server records.*

### What leaves the screen

Tool names, JSON, call ids, millisecond timings and status codes move behind "Technical details". Phone numbers show the customer's name when ServiceTitan has one.

## Options

### A. All ten stories, three pages
Every owner worry in docs/users.md gets a place on screen. O-6 (recording) and O-8 (tell Revin) are the new build; the rest reuses numbers the server already computes.

### B. Lean: seven stories
Drops O-5, O-6 and O-8. Fastest, but "what happens when it breaks" and "how does it get better" stay answers I give out loud instead of things Paul can see.

### C. A plus owner settings
Adds a page where the owner changes the service area, the on-call person and business hours. Shows control, but it is the most build and the page stops being read-only (DECISIONS.md 010 kept it read-only).

## Context

| Owner worry (docs/users.md) | Story that answers it |
|---|---|
| Misses a gas smell or an emergency | O-2, O-10 |
| Books a commercial job or outside the area | O-4, O-10 |
| Promises a slot that doesn't exist | O-4 (in ServiceTitan check) |
| Sounds like a robot, brand | O-6 |
| What happens when it breaks | O-5, O-8 |
| Is it worth the money | O-9 |

Design inputs from Jackson for the next step, recorded as inputs, not decisions: "looks very AI sloppy, generic, and too dark mode GitHub coder vibe"; "on brand with Revin and somewhat similar to ServiceTitan, but keep it Revin branded"; use the real logos. Brand research is running into docs/explorations/008.

## Recommendation

A. It is still small enough to demo in a few minutes (three pages), and O-8 turns the debrief's "what I'd improve" into something on screen. If build time runs short, cut O-6 first, since recordings are the only story that needs new storage.

## Research notes

- Existing numbers reused: booking rate by type, callbacks promised vs done, booked-and-kept, out of area by ZIP, overrides and refusals (SOURCE: app/owner.py, spec S-44, S-28, S-18).
- Recordings: no recording field in app/ today (MEASURED: grep for recording and audio in app/*.py). LiveKit and Retell both can record calls; storage would fall under the retention policy in C-30.
- O-8 would write to a new table and open a GitHub issue; nothing in the call or booking tables changes.
