# 45. Which owner view design directions to develop further

Issue: #45. Status: decided

## Question

Five design directions for the owner view, each drawn as the Today, Call and Results pages from the stories in DECISIONS.md 012, are on the design canvas: https://claude.ai/artifact/MFSZ4p6oo871gh7U2Ss8hL (private to Jackson). Which should be refined? This blocks the build brief for the new owner view.

All five share the same base, from exploration 008: Revin's real logo, a light page, Revin's Ember orange (#FA3500) only for the brand and the main action, ServiceTitan's status colors (Urgent red, High amber, Done green, Scheduled blue) and ServiceTitan's words (Job, Arrival window, Service location).

## Options

### 1. Dispatch desk
A ServiceTitan-style ops console: a one-sentence summary, six number tiles, a "Needs attention" table with red outlines on open urgent rows, a jobs table, and a tabbed tray for callbacks and leads. The call page looks like a ServiceTitan job record. Most familiar to a ServiceTitan user; the densest.

### 2. Shift report
The page reads like a note from the agent: a headline sentence, then numbered sections (needs a person now, callbacks we owe, what went on the schedule, everything else). The call page sets "what the agent did" in a margin beside the conversation. Easiest to read for someone who doesn't like dashboards; least scannable.

### 3. Work queue
An inbox: queues on the left, items in the middle, the selected item on the right with one next step and buttons to call or open the job. The call page is a chat thread with the agent's checks as small chips. Closest to how the owner acts on things; hides the big-picture numbers until Results.

### 4. Big board
Large numbers and large buttons, an Ember block for "Need you now", jobs grouped by arrival window. The call page is a five-step story with the full conversation behind a button. Readable on a tablet in a truck; uses Ember as a color block, which is closer to Revin's marketing site than its product.

### 5. Day timeline
The day as lanes on a timeline (safety, jobs booked, callbacks, other calls), like the dispatch board. The call page has a scrubber with marked moments over the transcript. Shows the shape of the day at a glance; the hardest to build and to read on a phone.

## Context

| | Familiar to a ServiceTitan user | Plain for a non-technical owner | Build effort (ASSUMED) | Works on a phone |
|---|---|---|---|---|
| 1. Dispatch desk | High | Medium | Low, closest to the current page | Medium |
| 2. Shift report | Low | High | Low | High |
| 3. Work queue | Medium | High | Medium | Medium |
| 4. Big board | Low | High | Low | High |
| 5. Day timeline | High | Medium | High | Low |

## Recommendation

Develop 1 and 3. Dispatch desk is what Paul, playing a ServiceTitan owner, will read fastest, and Work queue is the most different way to get the same job done; seeing them side by side shows which one an owner prefers. Shift report's headline sentence is worth borrowing into either.

## Research notes

- Brand values and ServiceTitan conventions: docs/explorations/008-revin-brand-and-servicetitan-ui.md.
- Data on every page is the seeded demo day from the current owner view (Mon Nov 2, 23 calls, 9 jobs). The agent's lines on the call pages are sample copy, not a real transcript.
- The canvas is private until shared from its Share menu.

## Decision

Jackson chose direction 5 mixed with direction 1, and asked for two versions of that mix. Recorded in DECISIONS.md 013.
