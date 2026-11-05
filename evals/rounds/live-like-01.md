# Live-like round 01: the baseline on main

**The bar.** On today's main, 0 of the 12 live-like calls pass. Every checked call is slow to its first sound and spends more model requests than it needs; half have long silences and a hang-up right on the goodbye. These are the numbers the behavior lane (#149, PR #155) and the latency lane (#150) have to beat, on the same scenarios.

Run: `make rig STACK=livekit SUITE=live`, 12 calls, $1.70 (MEASURED), results `evals/results/20261105-052940-livekit-rig-fast-live.json`, recordings `data/sim/20261105-043119-livekit` (and in the bucket under `sim/`). Code: this branch, which is main plus eval code only, so the agent is main's. Table: `uv run python -m evals.audio_rig.live_report <results>`.

## Why this suite exists

The text suite scored 91% while Jackson's live calls 1 and 2 went badly. A transcript can't show a 20 s silence after "Got it.", a line said twice, or "Friday at 2 PM" turned into "tomorrow between 1 and 3". The nine checks (evals/README.md, "Live-like suite") read the rig's timeline of each call and fail on exactly those. They also fail on both live calls as recorded: call 1 on the half reply, the reply delay and the callback time; call 2 on the repeat, three half replies, the callback time and three model requests on plain turns (tests/evals/test_live_checks.py).

## Baseline, check by check

Each cell counts that check's failures in the call. Outcome `error` means the call ran past its scenario's turn limit: the agent was still going, and nothing broke in the harness. Before the fix in this lane, audio checks were skipped on those calls (see below).

| Scenario | Outcome | silence | half_reply | repeat | first_sound | talk_over | llm_requests | readback_rate | hangup_gap | time_as_asked | Record checks |
|---|---|---|---|---|---|---|---|---|---|---|---|
| live-call-03-card-then-step-away | fail | 1 | 2 |  | 3 | 3 | 6 |  | 1 |  |  |
| live-call-04-gas-in-one-breath | error (the call did not finish within 8 caller turns) |  |  |  |  |  |  |  |  |  |  |
| live-call-05-person-now | error (the call did not finish within 8 caller turns) |  |  |  |  |  |  |  |  |  | handoff: no handoff was decided |
| live-call-06-no-zip-booking | fail | 3 | 4 |  | 2 |  | 1 |  |  |  | booking: 0 booking(s) made, expected 1 |
| live-call-07-older-no-heat | error (the call did not finish within 18 caller turns) |  |  |  |  |  |  |  |  |  |  |
| live-call-08-sparks-and-leak | error (the call did not finish within 18 caller turns) |  |  |  |  |  |  |  |  |  |  |
| live-call-09-pushes-priority | fail | 1 |  |  | 4 | 1 | 11 | 2 | 1 | 1 | priority_raise: raised_after_wait is False, expected True |
| live-call-10-unit-correction | fail | 1 |  | 1 | 4 |  | 5 | 4 | 1 |  |  |
| live-multi-book-then-callback | fail | 2 | 1 |  | 4 |  | 8 | 2 | 1 | 1 |  |
| live-multi-move-visit-then-quote | fail |  |  | 1 | 2 | 1 | 2 | 2 | 1 | 1 |  |
| live-replay-1-cancel-wrong-name | fail |  | 3 |  | 3 |  | 10 |  | 1 | 2 | identity: identity check never ran, expected mismatch; no_changes: records changed: mark_do_not_call |
| live-replay-2-move-and-tuneup | fail | 1 |  |  | 2 |  | 8 |  |  |  | tool_ok: record_move never succeeded |
| Scenarios failing | | 6 | 4 | 2 | 8 | 3 | 8 | 4 | 6 | 4 | |

Time to first sound, every timed turn in the run:

| Turns | Count | Median ms | Target | Slowest 10% ms | Target |
|---|---|---|---|---|---|
| lookup | 61 | 2799 | 2500 | 3899 | 3500 |
| plain | 79 | 2474 | 1200 | 3666 | 2000 |

Model requests per turn:

| Turns | Count | Median | Most | Limit |
|---|---|---|---|---|
| lookup | 60 | 2 | 7 | 2 |
| plain | 71 | 2 | 5 | 1 |

## What the failures are

- **Slow first sound, every call.** Plain turns: median 2,474 ms against 1,200 (S-36), slowest 10% 3,666 ms. Lookups: median 2,799 ms against 2,500, slowest 10% 3,899 ms (MEASURED, 79 and 61 turns). This matches the lookup lane's main-branch rig runs: 2.31 s plain, 2.60 s lookup (DECISIONS.md 092).
- **Too many model requests.** A median of 2 on plain turns, which need 1, and up to 7 on one lookup turn. Every extra request is a wait the caller hears.
- **Half replies.** "Hello. I'm still here.", "Yes.", "Not quite.", "Sorry about that. I'm checking the address, and I'll keep things moving.", each followed by about 22 s of silence until the caller spoke up (live-call-06). The card line, then silence (live-call-03). The cancel replay's "No question for now. If you have the ZIP code ...", then 24.5 s.
- **Silences.** No reply at all after "What is the question?" and after "Hello?" (live-call-06). On live-call-05, which hit its turn limit, two 15 s silences followed the address.
- **Hang-up on the goodbye.** In 6 calls the line dropped 0.1 to 0.2 s after "Thanks for calling Summit Air. Have a great rest of your day." The first word was lost too: the rig's ears heard "For calling Summit Air."
- **Read-backs too fast.** Phone numbers at 3.5 to 4.2 digits a second and ZIPs at 3.3 to 4.1, against 3.0 (ASSUMED).
- **Times not said back.** Replay 1: "Friday at two PM" became "tomorrow between 2PM and 02:30PM". After the caller corrected it, the agent said "Today is Thursday, so Friday is tomorrow", on a Wednesday. Pushes-priority: "eight PM tonight" became a window "between eight and ...".
- **Repeats.** "I've moved it to Friday, November 6 between twelve and four." twice in one turn. The unit-correction read-back again unasked.
- **Record checks.** Replay 1 never ran the identity check and marked the number do-not-call when the caller said "don't call me then". Replay 2 never recorded the move. No-ZIP booking booked nothing. Pushes-priority wasn't raised after the wait.

## Where a check may be wrong

- `talk_over` on live-call-03 flags the agent stopping a card number mid-read. That interruption is designed (the card line), so those two flags don't count against the agent.
- `time_as_asked` on live-multi-book-then-callback flags "Friday between twelve and four" after the caller said "twelve to four is fine. Two PM works.". The caller had already accepted the window, so that flag is the check's mistake.
- Every threshold except the S-36 medians is ASSUMED. They get tuned against what Jackson hears on call 3 (docs/later.md C-67).

## Fixed in the rig during this round

- The first attempt had the caller cut in on every turn, because the cut-in timer kept running while the rig waited for the worker's "listening" signal after the agent had stopped. The caller now cuts in only while the agent is still talking. That run was stopped after one call, $0.13, and isn't counted.
- Calls that hit their turn limit are now checked too. Four calls ran out of turns (8 for gas and person-now, 18 for older-no-heat and sparks); their limits are now 14 and 28. **Pending:** those four rerun on the same code, appended below.

## Limits

- The worker writes "Now:" into the prompt from the wall clock, while the rig's tool server runs on the scenario's time. This run was near midnight, so the agent treated every call as after hours, as the live calls were (docs/later.md C-68).
- There's no background noise and no second voice, and the caller can't cut in on a cue (C-65, C-66).
- The caller is a model, so each rerun hears different turns. Compare runs check by check across the suite, not line by line.
