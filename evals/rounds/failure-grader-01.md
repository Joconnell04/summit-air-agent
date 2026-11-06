# Failure grader calibration

Issue #178, brief `docs/ai-process/briefs/failure-grader.md`. The failure grader (`app/failure_grader.py`, prompt `prompts/failure-grader.md`, profile `offline.grader`: Claude Opus 5.5, effort medium) reads a finished call and flags it only for one of five true failures. This doc is the prompt's tuning log: the labeled set, each round's change and why, and its scores. Every number is MEASURED from `evals/failure_grader/round-<n>.json` unless it says otherwise.

**Result: the grader meets the precision and recall bars and misses the stability bar on one call of 62.** On the last round, no call labeled clearly fine was flagged in any of 3 runs, and every clear failure was caught: 10 of 10 in two runs and 9 of 10 in the third, so recall is 90% to 100% by run and 100% by majority of runs. One call (call_7255ab47, the agent telling a caller their number is on the do-not-call list and can't be removed) was flagged in 2 runs of 3, so flags are stable on 61 of 62 calls, not all. The Anthropic cap for this work ($15) is spent ($14.80), so this is the version that ships, reported as the best one rather than one that passed. Two borderline calls go the other way in every run: a hang-up after the caller said only "sure" is never flagged, and a booking at "Green Street" after the caller spelled G-R-E-E-N-E is always flagged.

| Round | What changed | Calls | Runs | False flags (clear fine calls) | Clear failures caught | Unstable calls | Spend |
|---|---|---|---|---|---|---|---|
| 0 | First prompt | 24 | 1 | 7 | 4 of 5 | n/a | $0.73 |
| 1 | How the records work; vulnerable-person priority; trivia | 59 | 3 | 17 to 18 a run | 5 of 6 | 3 | $5.26 |
| 2 | Do-not-call as the call left it; strict booking rule; cleared hazards | 62 | 3 | 13 to 14 a run | 9 of 9 | 1 | $5.48 |
| 3 | No prompt change: harness fixed, 41 calls regraded | 62 | 3 | 0 | 10, 9 and 10 of 10 | 1 | $3.28 |

Round 2 and 3 counts use the labels as they stood after each round; round 3 is scored against the final labels (`--rescore`). Most of rounds 1 and 2's false flags were the test harness and one real bug in what the grader reads, not the prompt, as the log below says.

## The labeled set

`evals/failure_grader/labels.yaml`: 62 real Retell calls, each labeled by hand with a one-line reason, ids and labels only.

- 48 calls from side-by-side round 1 (`evals/rounds/side-by-side-01.md`), whose tool-server rows were dumped right after each call.
- 14 calls on the Summit Air numbers from Retell's recent list (`POST /v3/list-calls`), whose rows the coordinator read from the live tool server's database afterward. Those rows hold callers' real numbers, so they stay outside the repo; `LIVE_ROWS_DIR` points the script at them. The live callers' numbers are also redacted from the quotes in the committed round files.

11 true failures (10 clear, 1 borderline) and 51 not (3 of them borderline). The true failures:

- a do-not-call request the records don't hold (the agent said the number was added; Retell hung up and nothing was stored);
- five callbacks stored on a different day than the caller asked for ("Monday morning" told and stored as tomorrow before noon);
- two bookings whose address was stored garbled ("199 Eastthstreet Street" for 199 East 4th Street);
- a gas call where the agent kept telling the caller to stay outside and call 911 after they said the gas company had cleared it;
- an agent telling a caller, without checking, that their number is on the do-not-call list and can't be removed over the phone (it wasn't on the list, and it can be removed);
- a hang-up after the caller said "sure" to "anything else?" four times (borderline).

The hard cases asked for, and where each is:

| Hard case | Expected | Calls |
|---|---|---|
| Ends abruptly, but the caller said goodbye | not a failure | 8 (`hard: abrupt end after goodbye`) |
| Hang-up in the middle of a request | failure | call_3cd4c806 (borderline: the caller only said "sure") |
| Retraction handled right | not a failure | 7 (`hard: retraction handled`) |
| Retraction ignored | failure | call_0246b91f |
| CO or gas line cut off with the caller already outside | not a failure | call_72009cbe (CO), call_c5966951 and call_936dbf64 (gas) |
| Callback stored on the wrong weekday | failure | 5 (`hard: wrong weekday`) |
| Booking at Normal after the caller named an elderly person | failure | none in the 62 calls: every call that named one was raised or went to the on-call person |
| Booking at Normal when nobody vulnerable was named | not a failure | call_2dc9e56b, call_11f8ca0e |
| A wrong answer to off-topic trivia | not a failure | 3 (`hard: wrong answer to trivia`) |

**Labels changed twice, each time after the grader flagged a call and the call itself showed it was right.** That is the obvious risk in this method, so every change is listed with its evidence. After round 1: the two garbled addresses (labeled fine before their rows arrived) and the gas call that ignored the all-clear (labeled from its ending only). After round 3: the do-not-call claim (labeled from its ending only). Two borderline calls (call_9d6182c1, call_1889f3f9) were set to not a failure so the booking rule is one rule: a booking is missing only when the agent said it was booked. No label was changed from failure to fine because the grader missed it.

## Ship bar

Asked for by the coordinator: zero false flags on calls that aren't failures, in every run; at least 90% recall on true failures; the same flag in all 3 runs of every call. Each round grades every call 3 times.

## Rounds

Each prompt is saved as run in `evals/failure_grader/prompts/round-<n>.md`; each round's grades, with the model's raw answer, in `evals/failure_grader/round-<n>.json` (round 0's grades were not kept; its numbers are from the run's log).

**Round 0** (first prompt; 24 calls, 1 run). Caught 4 of 5 failures and flagged 7 fine calls. All 7 were one misreading: a callback's phone column holds the number the caller called from, so a caller who said "call me at 917-555-0142" looked like a callback to the wrong number. The number they gave was in the callback's reason, where the person calling back reads it.

**Round 1** (59 calls, 3 runs). Added a short "how the records work" section (the callback phone, a person flag as the follow-up on a safety call, the after-hours on-call path in place of a booking, what raises priority), the vulnerable-person priority failure, and "trivia is not a failure". Result: 17 to 18 false flags a run. 14 of them came from the do-not-call status, not the prompt. The record said whether the caller's number is on the do-not-call list *now*, so another call's do-not-call request (every scripted caller uses the same test number) made a call look as if it had promised a callback to a blocked number. That would happen in production too, when a caller asks to stop calls a day after being promised a callback, so it is a real bug in what the grader reads; round 2 shows the harness made it worse. It also hid the one real do-not-call failure. The rest: a priority above Normal read as wrong (it was raised for sparks), a caller who left before anything was booked, and three that were real failures (the two garbled addresses and the gas call that ignored the all-clear; relabeled).

**Round 2** (62 calls with the live calls' rows, 3 runs). Fixed the record: `caller_on_do_not_call_list` is now the list as this call left it (`app/failure_grader.py` `call_rows`, with a test). Prompt: a booking is missing only when the agent said it was booked; a priority above Normal is never a failure; a hazard "checked and cleared" by the gas company counts as taken back. Result: all 9 clear failures caught in every run, but still 13 to 14 false flags. Reading them showed a second harness bug: the script loaded all 62 calls into one scratch database, so do-not-call rows from separate scripted test worlds mixed, and rows whose ids repeat across dumps (bookings 548 and 549, several flags) were silently dropped. Two calls were flagged for "no booking" and "no flag" that the real records hold.

**Round 3** (same prompt as round 2). Each call now gets its own scratch database. The 41 calls whose record the fix changed (checked by building every record both ways, no model calls) were regraded 3 times; the other 21 keep their round 2 grades, which were made from the same record and prompt. Result: zero false flags on clear fine calls; the only remaining flags on calls labeled fine are the borderline Green/Greene booking (3 of 3) and the do-not-call claim (2 of 3), which reading the call showed to be a real failure (relabeled). The borderline "sure" hang-up is missed in all 3 runs.

**What was not tried, for budget:** a fourth round aimed at the unstable call. Two levers to try next, each to be measured against this set: effort high on the grader profile (each call costs more, and medium already reaches the precision bar), or grading each call twice and flagging only when both agree (halves the chance of an unstable false flag, at the cost of an unstable true one).

## Spend

MEASURED from each round's model usage at the `offline.grader` prices ($4 and $20 per million tokens): round 0 $0.73 (with two smoke calls), round 1 $5.26, round 2 $5.48, round 3 $3.28, and a $0.04 smoke check. **Total $14.80 of the $15 cap.** A call costs $0.027 to $0.030 to grade (MEASURED, 486 grades), so the daily run over a 40-call day costs about $1.20 (ASSUMED call volume).
