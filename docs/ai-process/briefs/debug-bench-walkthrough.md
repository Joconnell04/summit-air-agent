# Build brief: the debug bench for the interview walkthrough

Branch: `joco/debug-bench-walkthrough-26b150`. Issue #188 (label `agent`). The pull request says `Closes #188`.

## Goal

In the demo, Paul (the Revin FDE playing the Summit Air owner) improvises, and may ask Jackson to open the repo and show what happened on a call. Every live-call scenario and every owner question below should be answered from the bench with evidence, in two clicks or fewer from the bench's home, not with prose.

Read first: CLAUDE.md, docs/assignment.md, app/bench.py (the bench as it is), app/trace.py (what a call's record holds), app/nodes.py (the step graph and rule proof), app/cost.py and docs/cost.md (the measured rates), voice/retell_llm/call.py (the socket's stage timings and talked-over rule), app/failure_grader.py (the read-only Retell call reader).

## Audience

The bench is Jackson's own tool for driving the walkthrough: he has it open while he talks and has to find the evidence for Paul's question fast. It is an engineer's tool, not something sold to Summit Air, and it is kept apart from the owner view: its own routes (`/bench`), its own base template (`app/templates/bench_base.html`), its own stylesheet and script (`app/static/bench/`, served at `/bench/static`) with its own tokens and the system typeface. It borrows only the shared component primitives (`components.css` for panels, tables, tags and buttons, `owner.js` for row links and tooltips) and the sign-in.

## What exists

The bench (`/bench`, admin only) has: a recent-calls table with id or phone search, a call page with the step graph and rule proof, a live stream for a call in progress, a scenario draft per call, settings with documented measurements, eval runs, the rules editor, the schedule and the checks. It sits under the owner view's light header with a row of tabs.

## Gaps found

1. No filter on the call list: a call can be found only by id or phone.
2. No "why" view: priority reasons, the safety trigger line and how fast the lines came, gate refusals, and the server's `say` lines are spread across the step graph's detail panes.
3. No per-turn latency view: the socket's stage timings (`server_ms`, `llm_first_token_ms`, `tool_ms`, `hold_ms`, `reply_ms`) are stored on each agent line but shown only one node at a time.
4. "Who ended the call and why" is one word in the header.
5. No view of what Retell heard beside what the server received, and nothing shows talked-over words.
6. No booking against read-back check, no matched customer (membership) on the call page.
7. No cost per call on the bench.
8. No index of the owner's questions pointing at their evidence.
9. The side model's score is not stored, only that it raised the priority (`priority_rules` names `side_model`). Shown as the rule and reason; storing the score is a server change, left as a follow-up.

## Information architecture

A left sidebar on every bench page, no owner header or brand; the bench is dark first. Labels are three words or fewer.

| Sidebar | Page | Holds |
|---|---|---|
| Calls | `/bench` | Call list with filter chips (outcome, label, ended by, signals) and search |
| Questions | `/bench/questions` | Each scenario and owner question, the evidence from recorded calls, and a link to the view that shows it |
| Rules | `/bench/rules` | The rules editor (as is) |
| Evals | `/bench/evals` | Eval runs (as is) |
| Cost | `/bench/cost` | Rates with their basis, cost per call |
| System | `/bench/settings` | Stack, models, knobs, switches, prompt files, after-hours roster |
| Schedule, Checks | as is | The world and the checks |

The call page has a header (id, when, caller, outcome, ended by, cost) and six tabs, each a deep link (`#why`):

- **Summary**: outcome and label with reason, who ended it and why, flow, after hours, rules version, cost; bookings with booking against read-back; customer matched and how; notes and summary for the technician; flags, callbacks, transfers, handoffs, holds, pace.
- **Why**: each server decision in call order: turn, decision, rule, source (caller words, side model, phrase rule, gate), outcome. Priority reasons, the safety signal line and the seconds and turns to the lines, every gate refusal with the `say` the server wrote, the flow's server lines.
- **Transcript**: the server's lines beside what Retell heard (fetched on open from `GET /v2/get-call/{id}`, read-only, cached), with talked-over lines, holds and safety actions marked.
- **Timing**: per agent turn, the stage breakdown as a ledger row with a bar; p50 and p95.
- **Steps**: the step graph and rule proof (as is).
- **Replay**: the scenario draft.

## Scenario and question map

Each row: where it is answered, and the data behind it.

| Item | Bench view | Data |
|---|---|---|
| Member tune-up | Call > Summary | `bookings`, `call_customers.matched_by`, `notes`, `summaries`, read-back |
| No heat, elderly person | Call > Why | `bookings.priority_rules`, `priority_reasons` (caller's words quoted), `call_labels`, `dispatches` |
| Commercial or gas | Call > Summary, Why | `calls.outcome`, `commercial`, `safety_signal_at`, `safety_lines_at`, `flags` |
| Curveball | Calls search > Transcript | `turns`, Retell transcript |
| Wrong booking or time | Call > Summary (read-back check) | `calls.readback_json` against `bookings` window and address |
| Gas smell | Questions (all gas calls), Call > Why | seconds and turns from signal to lines, turns after lines |
| Wants a human | Call > Summary, Why | `transfers`, `handoffs`, `flags`, `request_transfer` refusals |
| Hanging up on customers | Calls filter "ended by", Questions | `calls.end_reason`, `end_requested`, Retell `disconnection_reason` |
| Older callers, robots | Call > Summary (pace), Transcript | `call_pace`, `pace_changes`, `caller_pace` |
| Bad audio, accents, noise | Call > Transcript | Retell's words beside the server's line; talked-over marks |
| Spanish | Questions | Not supported (no language setting in tenants/summit-air); the transcript shows what happened |
| Cost per call | Cost, Call header | `app/cost.py` rates times call minutes; grader cost from `grades` |
| 2am Sunday, cold snap | Calls filter "after hours", System (roster) | `calls.after_hours`, tenant `after_hours`, `dispatches` |
| 200 calls in a heat wave | Questions, System | docs/scaling.md limits (SOURCE and ASSUMED as stated there) |
| All techs booked out | Call > Why | `find_slots` results and the server's `say` |
| Prices | Questions, Call > Why | gate refusals, prompt files, reply filter (`prompts/assemble.py SpokenReply`) |
| How the tech gets details | Call > Summary | `notes`, `summaries`, booking `job_notes` |
| Membership, messy records | Call > Summary | `call_customers`, `identity_checks`, `address_moves` |
| Why not the dispatcher, skipped, not trusted | Questions | links to docs/considerations.md and docs/later.md |
| Stack, model, latency | Call > Timing, System | turn `timings_json`, config/models.yaml |
| Prompt structure, what stops invented bookings | System (prompt files), Call > Steps (rule proof) | `prompts/*.md`, `nodes.proof` |
| Interruptions, barge-in | Call > Transcript, System (knobs) | Retell word timings, voice knobs |
| How it was tested | Evals | `evals/results/*.json` |
| Two more weeks | Questions | docs/later.md |
| Debug a real issue live | Call > Steps, `/debug/logs` | JSON log, trace |

## Constraints

Read-only: the bench shows data and never changes bookings or the live world. Debug endpoints keep the debug token; bench routes keep `require_admin`. The Retell read uses only `GET /v2/get-call/{id}`, and only when a call page's transcript asks for it. No deploy, no live number. Local runs on seeded data (`scripts/seed_owner_day.py`) plus a scratch script that posts socket-shaped timed calls.

## Done

A pull request closing #188 with this brief, the bench changes, tests for every new view's data, three written review passes, and desktop screenshots (1440 wide) of each main view.

## Review passes

Each pass read the built pages fresh, at 1440 wide, against the map above.

1. **Coverage.** Every row of the map opens in two clicks from `/bench` (sidebar, then a Questions row or a filter chip, then a call tab). Changed: the call page had no failure grader verdict, so the ledger gained Grade; the Questions page's "latest eval run" picked a one-scenario rerun, so it now takes the newest run with ten or more judged scenarios; the transcript and barge-in rows fall back to the newest call when no Retell call is on record.
2. **Layout.** Measured with a script in headless Chromium: every button, field, chip and sidebar link is 30 px, every tag 20 px, every row of controls shares one height and top, and no bench page scrolls sideways. Changed: the Why table's "Server said" column was squeezed to a sliver, so the server's sentence moved under the decision; the System page's side models and switches overflowed side by side, so they stack; the sidebar's background now runs the full height of long pages.
3. **Wording.** Changed: dropped the "Debug bench" crumbs and the help sentences under panel titles on Evals, Rules, Schedule, Checks and System; shortened panel titles to three words or fewer ("Voice knobs", "Live models", "Offline models", "Server vs Retell"); a refusal's outcome reads "held" instead of repeating the gate's name; an agent's end reads "Agent" without a raw `end_call`.

## Not built

- The side model's score is not stored, only that it raised the priority; the Why tab shows the rule and reason. Storing the score is a change to the live call path (`app/priority.py`), left for the reliability lane.
- What the socket's reply filter dropped is in the socket's own log (`voice/retell_llm/call.py` `_dropped`), not the tool server's, so the bench can't show it yet.
- A call isn't linked to the eval or phone-check run that made it: eval calls carry no run id on the server.

Screenshots: `docs/images/bench/`, taken on the seeded demo day (`scripts/seed_owner_day.py`) with scratch reply timings and a stand-in Retell reader, so the numbers in them are not measurements.
