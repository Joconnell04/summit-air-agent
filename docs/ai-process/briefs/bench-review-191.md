# Brief: debug bench, round 2 (issue #191)

Follow-up to #188 from Jackson's review of the deployed bench. The bench is Jackson's own engineer's tool for the interview walkthrough, not the owner view: separate code, routes and styling, dark, Retell-dashboard feel, minimal wording, one set of control sizes, no browser alert, confirm or prompt. Read-only toward live calls; the one exception is the schedule editor, which acts on the mock ServiceTitan only. Every bench page needs an admin login, and the debug token never reaches a page.

## What changes

1. **Schedule.** View, add, edit and delete bookings and blocks across every technician, to show that a block changes what the agent can offer. Edits go through `app/schedule.py` (the FSM adapter the tools use, never the mock's database) and clear the slot cache, so the next `find_slots` sees them. New: cancel a job from the bench, and a "What find_slots offers" panel for the day, read the way `find_slots` reads it, with the count before and after the last edit.
2. **System.** Show what the live call runs and what would be demoed: the live stack, the knobs that are realistically changed on the live Retell socket agent (with a plain description and why each value), the models in use, the switches that matter. Cut the side models that are off (Jev), the LiveKit-only knobs, the Basis column, the capacity table and the prompt-section list.
3. **Calls, Summary and Why.** Summary leads with outcomes: booked or dispatched, callback queued or scheduled, details captured, customer matched by phone (with name and membership) or added to ServiceTitan by the booking. Flags and their callbacks are one list without the repeated reason. Why says what happened and why for each decision in plain words; rows with nothing to say go.
4. **Log button.** `/bench/calls/{key}/log` reads the call's JSON log behind the bench login, so the button works without the debug token.
5. **Questions.** The owner's likely questions, each with a one-line answer and the live evidence behind it, and a link to the view that shows it. Cut items that only point at a doc.
6. **Rules.** Prompt versions v1 to v15 of the live socket prompt (`retell/socket/prompt.md`), from `retell/PROMPT-LOG.md` and the file's git history, beside the owner-rule versions. The log's table is backfilled for v8 to v15 (words and "never" counts, MEASURED from git).
7. **Evals.** The suite chips go; the filter is by call journey (docs/call-journeys.md), which counts only that journey's scenarios in each run. Rounds show what each settled (evals/rounds/README.md). The symptom-questions A/B results files are added so its runs show, and the reliability loop gets a round entry.
8. **Cost.** A small interactive bar chart of the stacks we tested: pick stacks and a measure (typical wait, slowest 10%, cost per call). Every number comes from evals/rounds or docs/latency.md, with its source in the bar's tooltip.

## Done when

- Each point above works on the seeded demo data at 1440 wide, with tests for each behavior.
- Three review passes: findability (can each walkthrough question be answered in two clicks), measured layout at 1440 (control heights, no sideways scroll), wording (labels of three words or fewer, no ASSUMED or MEASURED in visible copy).
- `make check` passes; the PR has desktop screenshots and says Closes #191.

## Not in this round

- Reading the live Retell agent at page load: the knobs show the snapshot in `retell/summit-air-jackson/agent.json`, which the socket agent copies, and the values the socket push and `config/models.yaml` set.
- Owner-view changes: the owner's schedule keeps its current actions.
