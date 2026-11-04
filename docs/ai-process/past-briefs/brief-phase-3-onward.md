# Brief: what's left for Jackson, phase 3 onward

Updated after Jackson's notes on the first version. What's settled is in DECISIONS.md 033 to 035; this file keeps only what still needs him, and what is running.

## Needs Jackson

1. **Tonight's calls.** Make the 10 scripted calls in `evals/live-calls.md` to our number, (646) 624-0418 (the go/no-go in #9). Retell's 10 calls wait until the audio rig picks the final two.
2. **Eval co-authoring round 2** (of 3, DECISIONS.md 035): `evals/rounds/02-seeds-and-holdout.md`. Say keep, cut or a one-line change for each of about 10 seed scenarios, then dictate 5 short caller cases for the hold-out. About 15 minutes.
3. **Retell balance.** Spend so far is $4.34 (MEASURED from Retell's call and chat lists): about 8 cents a minute, mostly today's rig calibration calls. Still to come: the rest of the rig run and 10 real calls of about 3 minutes, roughly $5 (ASSUMED), plus the number's monthly fee. Recommend adding $20 so a run never stops halfway.
4. **Where a warm transfer rings.** The transfer lane builds it with the target numbers left unset, so today's flag-and-callback behavior holds until a number is set. For the demo, which phone should "connect me to a person" ring? Your cell is the obvious choice; the number goes only in the server's `.env`.

## Running now

- **Technician summary (S-16)**, a short note at the top of the job written after the call.
- **Model matrix and side model.** Haiku against Sonnet on the standard suite, two repeats each, through the Claude API. Driving fresh Claude Code sessions on your subscription was considered and not used: Claude Code wraps the model in its own system prompt, tools and caching, so neither the tool calls nor the reply delay would match how our agent calls the model. Then one side model where the evals show a bottleneck, or "stay with none" with the evidence.
- **Must-pass reliability, the eval floor, and the demo gate run.** Every must-pass scenario run 3 times and fixed until 3 of 3, the floor set from a fresh standard run, and a passing version added to the rules page's history.
- **Warm transfer and call recordings.**
- **The bake-off audio rig** (ElevenLabs, OpenAI Realtime, Gemini Live, with our stack and Retell to calibrate). Not finished; no PR yet.

## Paused (phases 4 and 5)

Phase 4 is prep for the call and phase 5 is delivery, sharing, the cost summary and the shut-off list. Both wait until Jackson picks them up. The one change made now: feasibility questions replace planted-bug drills in `phases.md`.

Open items to bring back then: the demo order and whether Paul calls with a script; the rehearsal; whether a known caller's greeting should also say it's a virtual assistant; the backup number; how Revin gets the repo; what stays in it; running costs and the shut-off checklist.
