# Socket build-out, round 1: the server-led flow behind Retell's custom-LLM socket

Issue #176, PR #179. Arm D (the server-led flow, #171) with our own code between the model and Retell's voice: every sentence a tool result carries is spoken as written, and Haiku 5.5 writes only the rest. Numbers are MEASURED from `socket-buildout-01-data/` unless they say otherwise.

**In short.** The socket did what it was built for. On every phone call it ran, the server's sentences reached the caller word for word, and it never said a caller's name, narrated a step or asked two questions in one turn: 0 failures of those four checks over 7 scored calls, where arm D failed them on 6, 2, 1 and 1 of 9 calls in the side-by-side. But it is **slower than Jackson's agent**: e2e p50 1,436 ms and p90 2,066 ms (median of calls), against A's 1,168 and 1,701. And it is **not at the done bar**. Only 7 scripted calls scored, 3 of 7 passed their journey check, and the judge mean is 3.71. The failures were a dropped socket, an offer loop, a hold that never ended and a street the lookup couldn't find. Each has a fix in this branch, and none has been re-run on the phone yet. The slate stopped when a person called (646) mid-run, and the line went back to Jackson's agent as designed.

## 1. What ran

| | |
|---|---|
| Agent | "Summit Air socket (test)", `agent_9e575b3fa1457c8d4331a3b80a`. cartesia-Brooke, listening settings copied from Jackson's agent, `enable_dnc_detection` off, no webhook. Response engine: custom LLM at `wss://server-led.45-77-105-247.sslip.io/retell-llm/<key>` |
| Socket | `voice/retell_llm` with `RETELL_LLM_SERVER_LED=1`, the arm's prompt (`retell/arms/socket/prompt.md`, 883 words) and arm D's tools. Model: config/models.yaml `fast` (Claude Haiku 5.5) through the Anthropic API |
| World | `~/summit-air/server-led-data`, tool server with `SERVER_LED=1`, clock moved 13 hours (`SUMMIT_CLOCK_OFFSET_H=13`, about 11 AM to 1 PM New York) so the calls are daytime |
| Line | (646) 624-0418, pointed at the socket agent only for each call by `retell/arms/socket/run_slate.sh` and put back on Jackson's agent after it, on any exit |
| Callers | `evals.caller_variants` from joco/sonnet-buildout c5818da (pets-free), seed 176: 10 each of routine, elderly, gas_real, gas_retracted (`variants.yaml`). Scored with `evals.side_by_side report --personas` from the same commit |

Server version per call:

| Calls | Branch commit deployed | What changed before it |
|---|---|---|
| Smoke (routine-01, own persona) | 59052d8 | First phone call |
| Batch 1: r176-01, o176-01, g176-01, x176-01, r176-02 | b4b0368 | joco/stripped a679a7a merged (no pets, unit from MapPLUTO, estimates book); Jackson's live-call feedback in the flow and prompt |
| Batch 2: o176-02, g176-02; Jackson's own call | 548040f | A holding line after 1.5 s of tool time, 9 s tool timeout |
| Text round 5 only | 0959898 | Code acknowledgment when a look-up starts; "okay" takes the first window |
| Deployed now, not yet called | 0884b95 | A number-first line ends a hold |

## 2. Phone results (7 scored calls)

| Journey | Runs | Passed | Judge |
|---|---|---|---|
| routine | 2 | 0 | 3, 4 |
| elderly | 2 | 1 | 3, 3 |
| gas_real | 2 | 1 | 3, 2 |
| gas_retracted | 1 | 1 | 8 |

| Check (calls failing it, of 7) | Socket | Arm D, side-by-side (of 9) |
|---|---|---|
| Server's lines not said as written | **0** | 6 |
| Says the caller's name | **0** | 2 |
| Narrates its own steps | **0** | 1 |
| Two questions in one turn | **0** | 1 |
| Ends before the caller is done | 3 | 1 |
| Expected booking missing | 3 | 2 |
| Confirmed more than the caller earned | 3 | n/a |
| Unit not asked | 1 | n/a |
| Wrong job type (estimate) | 1 | n/a |

**Safety errors: 2, both on g176-02**, the call cut short when a person called the line. The server asked its gas check ("are you smelling gas in the house right now?"), the slate stopped the scripted caller before it answered, and the open phone leg then heard only check-ins. Leaving that invalid call out, there were 0 safety errors on 6 calls. On the two valid gas calls the safety line came 1.3 s and 1.4 s after the caller's line, from code and uninterruptible.

What failed, call by call:

- **r176-01, socket dropped.** find_slots took 4.2 s on a busy box and the caller heard silence. Retell ended the call with `error_llm_websocket_runtime` about 4 s after the caller's "Yes." Fixed in 548040f with a holding line ("Just give me one second.") after 1.5 s and a 9 s timeout.
- **o176-01, offer loop.** The caller said "okay" to a two-window offer, and the model asked which window eight times. The journey still passed. Fixed in 0959898: the flow's `next` says a plain yes or okay takes the first window offered.
- **o176-02, hold never ended.** After "One second.", the server took "215 Mott Street, Unit 4B." as said to someone else, so nothing answered it for two minutes. Fixed in 0884b95 (`app/dead_air.py`): a line that opens with a number brings the caller back.
- **r176-02, quote became a callback.** ASR heard "Highland" for Hylan Boulevard. The model passed `spelled="H, Y, L, A, N, Highland Boulevard"`, and find_address still found nothing. This is server address code, reported to the server lane and not changed here.
- **g176-01, robotic repeat.** The caller asked to hear the gas line again, and the model said "The safety instructions were already given." Not fixed.

## 3. Latency

| | Socket (7 scored calls) | A, Jackson's agent | D, server-led on Retell's LLM |
|---|---|---|---|
| e2e p50, ms (median of calls) | 1,436 | 1,168 | 1,154 |
| e2e p90, ms | 2,066 | 1,701 | 1,760 |
| LLM p50, ms | 924 | 612 | 601 |

A and D figures are SOURCE: evals/rounds/side-by-side-01.md. **The socket is slower, by about 0.3 s at the median and 0.35 s at p90.** Across all 9 socket calls (with the smoke call and Jackson's), the medians are 1,455 and 2,103.

Where the time goes (MEASURED, the socket's per-turn timings in the world's `turns`):
- Our tool server's turn takes about 30 ms.
- Haiku's first token takes 430 to 800 ms, with the prompt cache hitting (about 145k cached tokens read per call).
- A turn that calls a tool needs a second Haiku request after the tool. Those turns ran 2.0 to 3.9 s from the caller's last word.
- Retell's own overhead on the custom-LLM path (e2e minus LLM) is about 0.5 s, the same as on D.

The lever built after batch 2 is a short acknowledgment from code ("Okay." or "Got it.") the moment Haiku starts a look-up call. The caller then hears the reply begin at first-token time, not after the tool and the second request. On text runs through the deployed socket it moved p90 from 2,105 to 1,509 ms (text rounds 4 and 5, socket-side only, no Retell). It hasn't been on a phone call yet. Even with it, Haiku through the Anthropic API from this box starts about 0.3 s later than Retell's hosted Haiku. So the median is unlikely to beat A's without a faster first token (ASSUMED).

## 4. Text runs (no Retell cost)

`voice/retell_llm/eval_adapter.py` plays Retell's side of the socket protocol against the deployed socket, so the scripted text scenarios run through the real socket, model and filter. Six rounds (in `text/`) passed 0/1, 3/6, 3/5 scored, 4/5 scored, 4/7 and 2/6. Most failures were the scenario clock (the world is daytime, so an after-hours scenario doesn't apply), a mild day capping priority at High, the caller simulator saying goodbye mid-questions, and the stale `booking_lead_in` check, which looks for the old wording "You're all set for". Server sentences were verbatim in every round.

## 5. What moving the prompt out of Retell's dashboard means for tuning

- **Where it lives.** The prompt is `retell/arms/socket/prompt.md` in the repo. The socket reads it on every call, so a change is a file sync with no restart. The model, and anything else about it, is a config/models.yaml profile.
- **What's lost.** Retell's dashboard, LLM playground, simulation tests, batch tests and builder assistant don't reach a custom-LLM agent (SOURCE: https://docs.retellai.com/integrate-llm/overview). Jackson can no longer tune by editing in Retell, and Retell keeps no version history of the prompt.
- **What replaces it.** Git history is the version history. The text harness (`--stacks retell_llm`) runs scripted callers through the real socket at no Retell cost, in about 30 s a scenario. The phone slate covers what only audio shows.
- **Less to tune.** The prompt matters less. The rules Jackson tuned most (the visit questions, the open door, the read-back, the order of steps) are now server sentences and gates. Changing their wording is a tenant-line edit in `tenants/summit-air/agent.yaml`, not prompt work.
- **The cost.** Every change needs a deploy to the box, and someone who can run the harness. That suits this repo's workflow (PRs, tests) more than a dashboard user. It is the trade DECISIONS 112 weighed.

## 6. Spend

| | |
|---|---|
| Retell | $2.35 for 9 calls, 28.8 minutes on the socket agent (MEASURED from each call's `combined_cost`), $0.082 a minute. That includes Jackson's own 5-minute call and the 7-minute open leg of the cut call |
| Anthropic, socket | About $0.035 a call: 145k cache-read, 12k cache-write, 900 output tokens (MEASURED tokens; price ASSUMED at Haiku list rates) |
| Anthropic, text rounds and judge | $1.05 for text runs (MEASURED, results files' `cost_usd`), plus the Opus judge on 7 calls (not itemized) |

Caps were $20 Retell and $10 Anthropic. Both stayed well under.

## 7. Not done

- **The done bar.** It needs about 10 passing runs a journey; 7 calls scored in all. The phone slate stopped when a person called the armed line (04:35 UTC). It hasn't resumed, pending the coordinator's word on the line.
- **Regression personas.** b, g, h, i and j were queued after batch 2's variants, so none ran.
- **The latest fixes on the phone.** The acknowledgment, the okay rule and the hold fix are deployed and tested in code and text, but not yet called.
