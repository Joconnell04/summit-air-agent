# Side model 2: does it make the agent oversensitive?

The check Jackson asked for before `SIDE_MODEL=openai-decisions` goes back on in production. It follows `side-model-01.md`, which measured the side model alone on written lines (precision 0.84 to 0.86 with the rules). This round runs the whole agent, with and without it, on the scenarios where priority matters. Every number is MEASURED by the runs named under Method unless marked ASSUMED.

**Bottom line.** Keep it on, as built. In 52 calls with the side model on, it said yes on 11 calls and every one had a person at risk in what the caller said; it said no on the other 35, none of which had one. It raised a call to Urgent that the phrase rules left at High on 2 calls, both the stroke scenario the rules were known to miss. The false Urgents in this check came from the phrase rules, not the side model: 6 of 76 calls with nobody at risk, in both arms, and the side model answered no on all six.

## Method

- **Code.** `origin/main` at `bc8b8a6`, in a scratch worktree (removed afterwards), with the main checkout's `.venv` and `.env`. `SIDE_MODEL` is not set in `.env`, so the "without" arm unset it explicitly and the "with" arm set `SIDE_MODEL=openai-decisions` in the environment. The side-model settings are the shipped ones: question `vulnerable_person`, threshold 0.5, Urgent (High on a mild day), 0.4 s wait (`tenants/summit-air/agent.yaml`, `app/side_model.py`).
- **Scenarios.** 26, chosen by id. All 13 tagged `flow:priority` or `flow:after-hours` (including `mild-after-hours-no-heat`, `elderly-no-heat`, `baby-no-heat-standard`, `stroke-in-passing-no-heat`, `grandchild-for-elderly-grandparent`, `asks-agent-to-call-911`, `no-cooling-standard`, `oven-heat-co-check`) plus `after-hours-tune-up`, and 12 plain no-heat or no-cooling calls where nobody is at risk (`no-heat-gas-stove`, `booking-system-down`, `arrival-before-ten`, `tenant-landlord-approves`, `tenant-legal-question`, `out-of-area-known-caller`, `same-problem-came-back`, `note-asks-to-skip-readback`, `dropped-call-resumes`, `injection-skip-readback`, `landlord-in-disguise`, `co-op-owner-mentions-board`).
- **Runs.** Profile `fast` (claude-haiku-5-5), the harness's own simulated caller and grader, 4 workers, 1 sample per scenario, `--no-upload`. Two runs per arm: with 1 and without 1 started together, then with 2 and without 2, so both arms ran in the same minutes. Run ids and command: `side-model-02-data/arm.sh.txt`.
- **What was read.** A 40-line scratch wrapper around `evals.run.main` (`side-model-02-data/sidecheck_run.py.txt`, not part of the repo) dumped, for every scenario, the server's call record (`/debug/calls/<id>`) and every side-model answer the priority gate read. The priority the server set is the highest of the booking priority and any flag priority on that record. "Side model on record" means the booking's `priority_reasons` holds "someone at home at risk from the heat or cold", which only the side-model slot writes. Everything per call is in `side-model-02-data/calls.json`; the four results files are in `evals/results/`.
- **Spend.** The harness reports $0.638, $0.597, $0.640 and $0.626 for the four runs, $2.50 in all (agent, simulated caller, grader). The OpenAI calls are not in that figure; at about $0.0004 per phone call (`side-model-01.md`, not re-measured here) the 52 calls add about $0.02 (ASSUMED). Within the $3 approved. The first run was checked 45 seconds in: the side model was answering.
- **Limit of the harness.** Weather lookups are off in the harness (`WEATHER=1` only on the deployed server), so the outside temperature is unknown and counts as cold. No scenario starts at Normal, and the mild-day branch (a side-model yes raises to High, not Urgent) was not exercised. `mild-after-hours-no-heat` (58 F inside) starts at High.

## Results

### Per run

| Run | Pass | Fail | Error | Pass rate (harness) | Urgent where nobody is at risk |
|---|---|---|---|---|---|
| With 1 | 25 | 1 | 0 | 96% | 1 of 19 |
| With 2 | 22 | 3 | 1 | 88% | 2 of 19 |
| Without 1 | 21 | 5 | 0 | 81% | 3 of 19 |
| Without 2 | 19 | 7 | 0 | 73% | 1 of 19 |
| **With, both** | **47** | **4** | **1** | **92% (47 of 51 judged)** | **3 of 38 (7.9%)** |
| **Without, both** | **40** | **12** | **0** | **77% (40 of 52)** | **4 of 38 (10.5%)** |

"Nobody at risk" is the 19 scenarios whose script has no person at risk and a temperature of 55 F or more (so the other 7, listed in the next table, are excluded). Errors are left out of the pass rate, as the harness does.

### Per scenario: priority the server set, and pass or fail

`+side` means the side model's reason is on the call record. FAIL is a code check or the grader. Footnotes follow the table.

| Scenario | Person at risk in the script | Should be | With 1 | With 2 | Without 1 | Without 2 |
|---|---|---|---|---|---|---|
| after-hours-no-heat-cold | none (48 F inside) | Urgent | Urgent, pass | Urgent, pass | Urgent, FAIL | Urgent, FAIL |
| after-hours-tune-up | none | not Urgent | Normal, pass | Normal, pass | Normal, pass | Normal, pass |
| arrival-before-ten | none | not Urgent | High, pass | High, pass | High, pass | High, pass |
| asks-agent-to-call-911 | oxygen machine | Urgent | Urgent+side, pass | Urgent+side, pass | Urgent, pass | Urgent, pass |
| baby-no-heat-standard | 5-month-old | Urgent | Urgent+side, pass | Urgent+side, error | Urgent, pass | Urgent, pass |
| booking-system-down | none | not Urgent | High, FAIL | High, FAIL | Urgent, FAIL | High, FAIL |
| co-op-owner-mentions-board | none | not Urgent | High, pass | High, pass | High, pass | High, pass |
| dropped-call-resumes | none | not Urgent | High, pass | High, pass | High, pass | High, pass |
| elderly-no-heat | age 84 | Urgent | Urgent+side, pass | Urgent+side, pass | Urgent, pass | Urgent, FAIL |
| grandchild-for-elderly-grandparent | age 86 | Urgent | Urgent+side, pass | Urgent+side, pass | Urgent, pass | Urgent, pass |
| heat-pump-defrost | none | not Urgent | High, pass | High, pass | High, pass | Normal, FAIL |
| injection-skip-readback | none | not Urgent | High, pass | High, pass | High, pass | High, pass |
| landlord-in-disguise | none | not Urgent | High, pass | High, pass | High, pass | High, pass |
| mild-after-hours-no-heat | none (58 F inside) | not Urgent | High, pass | High, pass | High, pass | High, pass |
| no-cooling-standard | none | not Urgent | High, pass | High, pass | High, pass | High, pass |
| no-heat-gas-stove | none | not Urgent | High, pass | High, FAIL | Urgent, FAIL | High, FAIL |
| no-heat-unknown-caller | none | not Urgent | High, pass | High, pass | High, pass | High, pass |
| note-asks-to-skip-readback | none | not Urgent | High, pass | Urgent, pass | Urgent, pass | High, pass |
| out-of-area-known-caller | none | no booking | none, pass | none, pass | none, pass | none, pass |
| oven-heat-co-check | none (oven used for heat) | Urgent | Urgent, pass | Urgent, pass | Urgent, pass | Urgent, pass |
| renter-landlord-unknown | none | not Urgent | High, pass | High, FAIL | High, FAIL | High, pass |
| same-problem-came-back | none | not Urgent | High, pass | High, pass | High, pass | High, pass |
| stroke-in-passing-no-heat | mother-in-law after a stroke | Urgent | Urgent+side, pass | Urgent+side, pass | High, FAIL | High, FAIL |
| tenant-landlord-approves | none | not Urgent | High, pass | High, pass | High, pass | High, pass |
| tenant-legal-question | none | not Urgent | High, pass | High, pass | High, pass | High, FAIL |
| wrong-zip-corrected-at-once | none in the script (see below) | not Urgent | Urgent+side, pass | Urgent, pass | High, pass | Urgent, pass |

- `after-hours-no-heat-cold` is Urgent in every run because the house is at 48 F (the below-55 bracket), not because of a person.
- `wrong-zip-corrected-at-once`, With 1: the simulated caller went off script and said "my husband has a heart condition". The side model and the rules both said yes. That Urgent is right for what was said.
- `baby-no-heat-standard`, With 2: the simulated caller did not finish in 16 turns. The record still shows Urgent.
- `heat-pump-defrost`, Without 2: no booking; the call ended in a callback flag, so no priority above Normal was set.

### How the side model answered (with arm, 52 calls)

| Measure | Value |
|---|---|
| Calls where the gate read a side-model answer | 46 (the other 6 had no no-heat or no-cooling lookup: two tune-ups, two out-of-area, and two flag-only calls) |
| Answers read by the gate | 131, none late and none failed |
| Longest wait at the gate | 2 ms (the answer was back before the agent asked) |
| Calls with a yes (probability 0.5 or more) | 11 of 46 |
| Yes calls with a person at risk in what the caller said | 11 of 11 (10 as scripted, 1 where the simulated caller added a husband with a heart condition) |
| No calls | 35 of 46; none had a person at risk in what the caller said |
| Yes calls where the rules had already raised to Urgent | 9 (the side model changed nothing) |
| Yes calls where the side model alone moved High to Urgent | 2, both `stroke-in-passing-no-heat` |
| Answer values seen | 0.0, 0.01 and 1.0 only |

Zero false alarms in 35 calls puts the true rate at 8.2% or lower with 95% confidence (one-sided exact bound, MEASURED from the 35).

### Calls that should not be Urgent but were

Six calls in 76 (38 per arm), all from the phrase rules. The side model answered 0.0 on every one.

| Run | Scenario | What the caller said | Rule reasons on the record |
|---|---|---|---|
| With 2 | note-asks-to-skip-readback | "nobody's elderly or sick" | an older person or a baby at home |
| With 2 | wrong-zip-corrected-at-once | "nobody's a baby and nobody's sick" | an older person or a baby at home |
| Without 1 | booking-system-down | "nobody's elderly or sick" | an older person or a baby at home |
| Without 1 | note-asks-to-skip-readback | "nobody's sick or elderly" | an older person or a baby at home |
| Without 1 | no-heat-gas-stove | "my dad helped me with the down payment", "I live alone" | a parent at home, age not given; someone at home alone; two risk factors together |
| Without 2 | wrong-zip-corrected-at-once | "nobody's elderly" | an older person or a baby at home |

Cause, checked by calling the shipped rule on the caller's words: `words()` in `app/text.py` drops the apostrophe, so "nobody's" becomes "nobodys", which is not in `NEGATIONS` in `app/priority.py`, and "elderly" or "baby" after it counts as said. "nobody is elderly" and "no babies" are read correctly. The seventh Urgent in the table above (With 1, `wrong-zip-corrected-at-once`) is the legitimate one.

### Why each run failed, and whether the side model is the cause

| Scenario | Fails with / without (of 2 runs each) | Cause read from the transcripts | Side model? |
|---|---|---|---|
| stroke-in-passing-no-heat | 0 / 2 | Without: the rules read "a parent at home, age not given" (High) and booked High; the check wants Urgent. With: the side model answered 1.0 and the booking was Urgent. | Yes, the intended fix |
| after-hours-no-heat-cold | 0 / 2 | Without 1: the simulated caller asked about a gas smell, the agent ran the gas safety path and no callback was set. Without 2: grader, the agent offered a Tuesday visit that was never booked. | No; the side model answered 0.0 on both with-arm runs and the safety path never reads it |
| booking-system-down | 2 / 2 | Grader every time: the agent told the caller nothing was booked and promised a callback "by 10 AM", but the record shows a queued booking and no callback. | No; fails in both arms |
| no-heat-gas-stove | 1 / 2 | The agent said "carbon monoxide" while telling the caller not to use the stove or oven (check `agent_never_says`). | No; model noise |
| renter-landlord-unknown | 1 / 1 | With 2: grader, read-back said twice and two questions stacked. Without 1: no person flag and no callback. | No |
| elderly-no-heat | 0 / 1 | Without 2: the caller said the oven door was open, the agent ran the carbon monoxide path. | No |
| heat-pump-defrost | 0 / 1 | Without 2: the caller was unsure who approves, the agent offered a callback instead of booking. | No |
| tenant-legal-question | 0 / 1 | Without 2: grader, the agent promised a call to the landlord that nothing set up. | No |

Of the 12 fails in the without arm, 2 are the stroke scenario; the other 10 have no route from the side model, and the with arm had 4 of its own (booking-system-down twice, no-heat-gas-stove, renter-landlord-unknown). The 10-to-4 gap on those is run-to-run noise from one sample per scenario and a simulated caller (the same configuration moved from 81% to 73% and from 96% to 88% between its two runs). No scenario flipped the other way because of the side model.

### Behavior that depends on priority

| Behavior | With (2 runs) | Without (2 runs) |
|---|---|---|
| "I've marked this call top priority" line played | 17 calls | 14 calls |
| Urgent on the 12 calls that should be Urgent (the six scenarios above, twice) | 12 of 12 | 10 of 12 (stroke missed twice) |
| On-call-first gate fired (`after-hours-no-heat-cold`) | 2 of 2 | 1 of 2 (the other run took the gas safety path) |
| On-call alert or on-call-first gate on `mild-after-hours-no-heat` | 0 of 2, booked the morning | 0 of 2, booked the morning |

The side model did not change any after-hours routing. Reading the code and calling it with the shipped config confirms why: `after_hours_outcome` counts a call as at risk only for the rules in `after_hours.at_risk_rules`, which are `vulnerable_person` and `medical_equipment` (`trades/hvac/agent.yaml`), and the side-model rule is named `side_model`. With the stroke call's words, 58 F inside and after hours, the rules alone give High and `first_morning_slot`; the side model gives Urgent and still `first_morning_slot`. A side-model yes alone therefore never sends the on-call technician an alert or trips the `on_call_first` gate (DECISIONS.md 028). It does change the booking priority, the top-priority line, which windows are offered (Urgent may use the after-hours window), and the earlier-slot flag.

## Findings

1. **No oversensitivity showed up.** 0 side-model-only false alarms in 35 no-answer calls (upper bound 8.2% at 95%), and 11 of 11 yeses matched a person at risk in the transcript. Urgent where nobody was at risk was 3 of 38 with and 4 of 38 without; the side model added none.
2. **It earned its place on one scenario.** `stroke-in-passing-no-heat` went from High on 2 of 2 runs to Urgent on 2 of 2. On the other 9 yes calls the rules had already raised to Urgent.
3. **The rules, not the side model, are the false-alarm source here.** Five of the six false Urgents come from "nobody's" slipping past the negation list; the sixth is "my dad" plus living alone counted as two risk factors. The side model read all six correctly. This is a defect in `app/priority.py`, separate from the side model.
4. **Pass-rate differences are noise except stroke.** The side model could change a result only through priority, and priority was the cause of a fail in one scenario.
5. **Answers arrive in time.** 131 of 131 gate reads had an answer, the longest wait was 2 ms, because the question goes out on each caller line while the agent is still speaking.
6. **A side-model yes does not reach the on-call path.** This is the safer direction for oversensitivity, and it also means an after-hours call with a cue only the side model catches is Urgent but gets no tonight alert.

## Recommendation

**Keep it on, unchanged: `SIDE_MODEL=openai-decisions`, threshold 0.5.**

- Do not add a "side model plus a phrase" trigger. The calls the side model exists for are the ones with no phrase the rules know (the stroke call), so the requirement would turn it back off for them.
- Do not limit it to cold days. With the weather lookup on, a mild-day call starts at Normal and a side-model yes raises it to High only, so the cost of a false alarm there is already capped, and the high-risk case it was built for is a cold day anyway.
- Keep the on-call path as it is for now. Adding `side_model` to `at_risk_rules` would let one model answer page the on-call technician, which is the most disruptive thing a false alarm could do. That is Jackson's call, best made after a few weeks of production calls.

Before relying on it:

1. Review production calls whose only raise reason is "someone at home at risk from the heat or cold" (that string is on the booking's `priority_reasons` and appears only for the side model). The first false alarm there is the signal to revisit.
2. Fix the negation gap in `app/priority.py` (add "nobodys" to `NEGATIONS`, and decide whether "my dad" with "live alone" should combine). It caused every false Urgent measured here and is independent of this decision.
3. Add hard-negative scenarios to the suite before treating 0 of 35 as the real rate: a neighbor or relative who is elsewhere ("my neighbor is 90 and she told me to call you", the one new alarm in `side-model-01.md`), a nurse or caregiver caller, a pet or an old boiler named as "older". This check had none, and the offline test's precision of 0.84 to 0.86 says some will exist.

## Not covered

- One sample per scenario per run and two runs per arm. The pass-rate gap and the false-alarm bound are small-sample figures.
- No third-party hard negatives in the scenarios (see item 3), and no truly mild day (the harness has weather off).
- OpenAI latency from the deployed host, and the real caller's speech-to-text errors. Latency here is laptop to OpenAI; `side-model-01.md` measured 219 to 281 ms at the median.
- The Spanish slice and the threshold choice on a dev split, still open from `side-model-01.md`.

## Files

- `evals/rounds/side-model-02-data/calls.json`: per call, the server's priority, reasons, flags, whether the top-priority line and on-call-first gate fired, and each side-model answer with its wait.
- `evals/rounds/side-model-02-data/side_answers.jsonl`: every answer the gate read in the two with-arm runs.
- `evals/rounds/side-model-02-data/sidecheck_run.py.txt`, `arm.sh.txt`: the wrapper and the launcher.
- `evals/results/20261104-040923-…`, `-041427-…` (with 1, with 2) and `-041026-…`, `-041423-…` (without 1, without 2), all `-livekit-fast-custom.json`: the harness results with transcripts.
