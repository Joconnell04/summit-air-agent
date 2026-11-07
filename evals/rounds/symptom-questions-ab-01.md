# Symptom questions: server-written sentences against fixed sentences (6 November)

Question: the reliability loop built a word map (`SYMPTOMS` in `app/flow.py`) so the server writes the catch-all ("Besides the banging and the leak, anything else the technician should know, like it shutting off on its own?") and the how-bad question ("You mentioned the leak. Is the system still working, or has it stopped?"). Jackson asked whether that input-for-output rule is worth keeping over plain fixed sentences.

Arms: A, the word map (commit c992972); B, fixed sentences with no symptom named (commit d49d9c1). Same 8 scenarios (6 new symptom personas in `evals/scenarios/ab-*.yaml` on the arm checkouts, plus `ac-leaking-into-bedroom` and `no-heat-unknown-caller`), 3 samples each, on the server-led eval instance through the real socket (`--stacks retell_llm`), once after hours and once at a midday clock (`SUMMIT_CLOCK_OFFSET_H=-9`). 96 runs, about $5.40 of model tokens (MEASURED, the runs' cost_usd).

How it was scored: every caller line that called out a repeat ("I already told you", "like I said") was judged by hand against what the caller had said earlier in that call. The scripted callers call out freely, so most callouts were the caller's mistake (a question they had never answered) and are not counted. Pass and fail from the code checks are not used: the `booking` check fails every correct after-hours or urgent dispatch, and three scenario caller IDs matched seeded accounts (fixed for the midday pass).

Real re-asks the two arms handle differently (how bad, the catch-all), of 24 runs per pass (MEASURED, hand-judged):

| Pass | A, word map | B, fixed sentences |
|---|---|---|
| After hours | 4 (+1 borderline) | 8 |
| Midday | 2 (+1 borderline) | 5 |
| Both | 6 (+2) | 13 |

Why A does better: a whole sentence from the server is said word for word; a sentence with room to fill is rewritten ("like the banging getting louder or the water getting worse"). Most of what remains in both arms is the how-bad question after "the heat's still on" or "it's still warm".

Found in both arms, not part of the A/B: the dispatch sentence asked "Is that the right address?" after the caller had confirmed the read-back (fixed with this round); the catch-all offers "it shutting off on its own" to a caller whose furnace keeps turning on and off; a narrated line, "Since this is a heating issue, the next question is the catch-all." (once). "What street is that on?" after "one nine nine east 4th street" came from the simulated caller spelling the number in words; on the phone Retell writes numerals, so it is left alone.

Hand verdicts per callout: below.

# A/B callout verdicts (judged by hand against what the caller said earlier in the call)

## armA (phrase rules, c992972)
Real re-asks: #4 address (street after full address), #5 how-bad after "heat's on", #7 how-bad after "still warm", #11 steady-or-drip after "dripping" (borderline), #12 generic how-bad after "heat's still on", #13 generic how-bad after "only getting to 65", #14 since-when after "since yesterday", #20 address (street after full address).
Caller's mistake (agent fine): #1 #2 #3 #6 #8 #9 #10 #15 #16 #17 #18 #19.
Arm-related (how-bad or catch-all): #5 #7 #12 #13 = 4 (+ #11 borderline).

## armB (stripped, d49d9c1)
Real re-asks: #1 #3 #5 how-bad after "still warm / heat's working", #7 generic how-bad after "heat's still on", #8 "is the water still dripping?" after "dripping since last night", #10 how-bad after "heat's still on", #15 #16 how-bad after "still running"; #2 #12 street after full address; #6 dispatch question after "I can wait for a scheduled visit"; #13 catch-all "shutting off on its own" after "turning on and off"; #17 unit after "apartment 5B".
Caller's mistake or scenario artifact: #4 #9 (caller ID matched a seeded account) #11 #14 #18.
Arm-related (how-bad or catch-all): #1 #3 #5 #7 #8 #10 #15 #16 = 8.

## Both arms, not part of the A/B
- "What street is that on?" after the full address (A 2, B 2).
- Catch-all offers "banging pipes" right after "no weird noises" (A 3/3, B 3/3 on ab-no-heat-denies-noise).
- Dispatch sentence asks "Is that the right address?" after the read-back was confirmed (control, both).
- Scenario artifact: caller IDs (347) 555-0146, (718) 555-0142, (718) 555-0143 match seeded accounts; moved to (917) 555-074x for the day pass.
- Checks: booking job type fails on every correct after-hours dispatch (eval check, not the agent).

## armB-day (stripped, business hours, clock -9 h)
Real re-asks: #1 how-bad after "still warm"; #6 #7 #8 how-bad after "the heat's still on" in the opening line; #11 catch-all with the model's own examples, both things the caller said ("chilly inside", "the noise every time"); #9 #10 catch-all "shutting off on its own" after "turning on and off" (same line in both arms); #17 "house number and street" after "4517 Bell Blvd".
Caller's mistake: #2 #3 #4 #5 #12 #13 #14 #15 #16.
Arm-related: #1 #6 #7 #8 #11 = 5.
Also seen: narrated reasoning said aloud, "Since this is a heating issue, the next question is the catch-all." (#10); an unasked aside about landlords and 311 (#4).

## armA-day (phrase rules, business hours)
Real re-asks: #8 #9 generic how-bad after "the heat's still on"; #7 steady-or-now-and-then after "dripping" (borderline); #4 unit after "unit 2"; #11 catch-all "shutting off" after "turning on and off" (both arms).
Caller's mistake: #1 #2 #3 #5 #6 #10 #12 #13 #14 #15 #16 #17.
Arm-related: #8 #9 = 2 (+ #7 borderline).

## Totals, arm-related real re-asks (of 24 runs each)
| Pass | Phrase rules (A) | Stripped (B) |
| After hours | 4 (+1 borderline) | 8 |
| Business hours | 2 (+1 borderline) | 5 |
| Both | 6 (+2) | 13 |
