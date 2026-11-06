# Build brief: four-arm side-by-side on the live line

Issue #169 (label `agent`). Branch `joco/side-by-side-014a4c`. The pull request says `Closes #169`.

## Goal

One recommendation for which agent to build on, from our own automated eval of scripted calls over the real phone line. Scripted self-test calls keep catching the Retell-hosted Claude Haiku 5.5 agent breaking prompt rules (saying the caller's name, narrating its steps, two questions at once, rewording the server's lines, steps out of order). Four arms are compared on the same personas in the same order, and the recommendation weighs each arm's ceiling, not only its score today.

This is not a call slate for Jackson. Jackson may call the same number at any time, and his calls come first.

## Arms

| Arm | What it is | Retell agent | Line and world |
| --- | --- | --- | --- |
| A, as-is | Jackson's agent, unchanged (Claude Haiku 5.5) | agent_a3e38e6aa3d4e75dfac12aafc8 | (646) 624-0418, live |
| B, Sonnet 5.5 | A copy of A's LLM and agent with only the model changed to `claude-5.5-sonnet` (Retell's id, accepted by create-retell-llm), and its tools and webhook on the eval bridge | agent_871ef6fafe2636244c4bbaca67 | (678) 336-6730, eval |
| C, behavioral Haiku | A copy of A with only the general prompt replaced by `retell/arms/haiku-behavioral/prompt.md` (issue #170) | agent_d72df072302c81f75d1b7fadf4 | (646) 624-0418, live |
| D, server-led big fix | Its own agent and server instance (issue #171) | from the big-fix lane | (678) 336-6730, server-led |

B and C are made by `voice/retell/side_by_side/make_arm.py`, which copies every setting of A as it is at that moment and changes one thing; it runs again right before the slate so both copies are level with A.

## Two lines, three worlds

Two calls at once never share a server world. Line 1 runs A and C interleaved per persona against the live world (`~/summit-air/app`), so a server change mid-run lands on both. Line 2 runs B against the eval world (`summit-retell@eval-tools` and `eval-mock`, the same code as the app; `diff -rq` clean), then D against its own instance. The eval world is reset with the same `scripts.reset_live_world`, pointed at its databases, so both worlds start from the same seed and the same opened schedule. One difference stays: the eval tool server runs with the slot cache off. At the end 646 is back on Jackson's agent and 678 on agent_a8b06c59d79a6ea25dda78fc28.

## How a run works

- Personas: `evals/side-by-side/personas/` (the coordinator's `/tmp/qa` set a to j, plus k, Siobhan Nguyen, no heat with a newborn), their expected records in `evals/side-by-side/personas.yaml`. The core slate is e, b, c, g, h, i, j, k, one pass; b follows e with no reset, since it moves the visit e booked. The reruns add l, a real gas smell, for the DECISIONS 119 gas journey.
- `voice/retell/side_by_side/run_slate.sh <slate> <out> <number>` runs on the summit-air server with `setsid -f`, from a folder holding that directory's files and the personas. Per slate line it waits until no call is live in that world, resets the world, points the number at the arm's agent and the world's inbound webhook (`PATCH /update-phone-number`), dials the persona with `voice.livekit.test_call` (`run_persona.py --to`), puts the number back right after the call and on any exit, and dumps the world's rows for the call before the next reset.
- `voice/retell/side_by_side/collect.py <run>` saves Retell's call data (`POST /v3/list-calls`, `GET /v2/get-call`); `evals/side_by_side.py judge | report <run>` adds the Opus 5.5 verdicts and the checks and per-arm table. The phone and Retell pieces sit under `voice/retell/` so nothing in `evals/` imports a voice layer.

## Measured per arm

- Gross-error checks per call: one question per turn; no caller name; no narration; the server's lines said as written; closing once; read-back before slots; no double confirm; no hang-up on "no thanks"; safety advice once; retraction honored; nothing carried over from an earlier call; the right bookings, callbacks, leads, flags and do-not-call state; the name stored as intended, and whether the agent asked for a spelling or first and last name.
- Opus 5.5 judge: 1 to 10 against Jackson's feedback in `evals/rounds/retell-live-calls-01.md`, with a one-line vibe note.
- Latency (`latency.e2e` p50 and p90, `latency.llm` p50) and cost (`call_cost.combined_cost`).

## Spend

Under $30 of Retell plus Anthropic API for the lane. MEASURED rate for a Haiku call: $0.089 a minute (call_c1ecd6b5422e9576414cb84a3da). One pass of 8 calls on four arms is about $11 of Retell (ASSUMED: about 3.5 minutes a call, Sonnet about 1.6 times Haiku's rate); the judge about $0.50 (MEASURED: $0.014 for a one-minute call, so ASSUMED about $0.02 for a long one). A second pass only if time and budget allow, and none if the first pass passes $22.

## Done when

`evals/rounds/side-by-side-01.md` has the per-arm numbers (each labeled MEASURED, with how), a JSON of raw results sits beside it, the recommendation says which arm and what work raises its ceiling, `make check` passes, and the number is back on Jackson's agent.
