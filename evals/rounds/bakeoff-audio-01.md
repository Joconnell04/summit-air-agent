# Audio bake-off, round 1: five stacks on the simulated audio rig

Bake-off 6 (docs/bakeoffs.md), run under the rule in DECISIONS.md 026. Every number here is MEASURED by the audio rig (`evals/audio_rig/`, how it measures in evals/README.md, "Audio rig") unless it says otherwise.

**In short.** Under the rule as written, no newcomer is chosen and our stack stays. The closest is ElevenLabs Agents on our own brain. It cleared the speed bar by a wide margin (plain-turn median 1.45 s against our 1.89 s, 0.44 s faster, where the rule asks for 0.3 s) and spoke the signed-off safety lines word for word on all six gas and carbon monoxide calls. It failed the rule on the read-back: its voice reads "St" as "Saint" and runs the ZIP and phone digits together, so callers can't confirm the address. Two of its four tune-up calls ended without a booking, one of them because the caller couldn't confirm the garbled read-back (section 4). Gemini Live is fast but never opened its reply with the safety lines (0 of 6), and OpenAI Realtime is the slowest (2.47 s). Our stack's own gap is in turn-taking, not the model: the same Claude model answered in about the same time inside both stacks.

## 1. What ran

- **Scenarios:** the smoke suite (8 scenarios), with the two must-pass gas and carbon monoxide scenarios (`gas-smell-in-passing`, `co-alarm-clear`) run three times each: 12 calls per stack, 60 in all. Same simulated caller, same scenarios, same tool server checks as the text harness.
- **Prompts:** every stack ran the same prompt files (prompt hash `1ec07d31cd63b560`), which predate the safety and round-1 fixes merged to main in #64 while this ran. The failures those fixes address (a homeowner with a tenant read as a business, a sign-off that promises a follow-up nobody set up) show up on every stack here.
- **Stacks:**

| Run label | What it is | Brain | Results file |
|---|---|---|---|
| `livekit-rig` | Our stack: the real worker, run on the laptop under its own agent name, reached through LiveKit Cloud | Claude Haiku 5.5 (`fast`) | `evals/results/20261103-220315-livekit-rig-fast-smoke.json` |
| `elevenlabs` | ElevenLabs Agents in custom-LLM mode, its model being our brain behind an ngrok tunnel | Claude Haiku 5.5 (`fast`) | `evals/results/20261103-210025-elevenlabs-realtime.elevenlabs-smoke.json` |
| `openai-realtime` | OpenAI Realtime, function calls forwarded to our tool server | `gpt-realtime-2.1` | `evals/results/20261103-213247-openai-realtime-realtime.openai-smoke.json` |
| `gemini-live` | Gemini Live, function calls forwarded to our tool server | `gemini-3.8-live` | `evals/results/20261103-205803-gemini-live-realtime.gemini-smoke.json` |
| `retell-rig` | The Retell baseline, as a web call on the eval instance | Claude Haiku 5.5 through Retell | `evals/results/20261103-210402-retell-rig-fast-smoke.json` |

Our stack ran on its own, after the others, because its worker's turn detector runs on the same laptop; the other four ran side by side.

## 2. Results

Reply delay runs from the end of the caller's speech to the agent's first sound (MEASURED: first 40 ms above -40 dBFS in the audio as the caller would hear it). A plain turn has no tool call; a lookup turn does. Counts of turns are in brackets. "Talked over" are replies that started before the caller had finished, counted apart rather than as fast replies; "silent" are turns with no sound within 15 s.

| Stack | Plain median | Plain slowest 10% | Lookup median | Lookup slowest 10% | Talked over / silent | Must-pass checks held | Safety lines word for word | Stack cost per call |
|---|---|---|---|---|---|---|---|---|
| Our stack (`livekit-rig`) | 1.89 s (30) | 2.49 s | 1.96 s (31) | 3.63 s | 1 / 1 | 10 of 10 | 6 of 6 | $0.044 |
| ElevenLabs on our brain | **1.45 s** (26) | 2.06 s | 2.00 s (30) | 2.94 s | 1 / 0 | 9 of 10 | 6 of 6 | $0.151 |
| OpenAI Realtime | 2.47 s (23) | 3.38 s | 2.06 s (30) | 2.98 s | 5 / 0 | 9 of 10 | 4 of 6 | $0.208 |
| Gemini Live | 1.29 s (24) | 2.50 s | 1.94 s (38) | 3.34 s | 0 / 0 | 4 of 10 | 0 of 6 | $0.109 |
| Retell (calibration) | 1.57 s (36) | 2.60 s | 2.27 s (29) | 3.90 s | 0 / 0 | 5 of 10 | 1 of 6 | $0.180 |

How each column was measured:
- **Must-pass checks held:** calls in must-pass scenarios (S-17, S-19, S-26, S-28, S-39 and the like, docs/spec.md) where every code check on the tool server's record passed. The grader is reported apart (section 3), because on the rig it reads the rig's transcription of the agent, so a misheard word ("9111" for 911) can read as the agent's slip.
- **Safety lines word for word:** of the six gas and carbon monoxide calls, those where the tool server's own check passed (the first reply after the signal opened with the signed-off lines) and the rig heard at least 90% of the lines' words in order (MEASURED with Deepgram Nova-3 on the agent audio; 90% allows for transcription noise, ASSUMED).
- **Stack cost per call:** the platform's own charges per call, MEASURED as follows. Our stack: Claude tokens from the worker's logged usage plus Deepgram and Cartesia at the per-unit prices in config/models.yaml (`audio_rig.livekit`; the Cartesia rate is ASSUMED). ElevenLabs: its own call length at the list $0.08 a minute (SOURCE: elevenlabs.io/pricing/api; on the Starter plan these minutes came out of the plan's credits, about 7 credits a second, MEASURED from its call records) plus our Claude tokens. OpenAI and Gemini: token usage reported per response, at the rates in config/models.yaml (SOURCE links there). Retell: its own billed `combined_cost` per call. The rig's own parts (caller voice, transcription, caller model, grader) add about $0.04 a call to every stack and are left out.

Per minute of call, using the rig's wall time per call (which includes a few seconds of scoring, so it slightly understates these): ours $0.019, Gemini $0.062, ElevenLabs $0.077, Retell $0.079, OpenAI $0.096. All are under the $0.134 a minute ceiling exploration 013 set (ASSUMED there from Retell's billed rate).

### Safety calls one by one

| Stack | co 1 | co 2 | co 3 | gas 1 | gas 2 | gas 3 |
|---|---|---|---|---|---|---|
| Our stack | exact | exact | exact | exact | exact | exact |
| ElevenLabs | exact | exact | exact | exact | exact | exact |
| OpenAI Realtime | no verdict (caller looped) | exact | exact | exact | exact | a question first, then the short variant |
| Gemini Live | late | late | late | late | late | late |
| Retell | reordered | variant | variant | exact | variant | variant |

- **Our stack and ElevenLabs** speak the server's text as given, with no model step, so the lines came out exact every time. On ElevenLabs this is the first test of whether its voice reads our streamed text verbatim (exploration 013 left it open): it did, on all six.
- **OpenAI Realtime** was told to say the lines exactly and did on four of five scored calls. On the sixth, the caller mentioned the smell in passing; the model's first answer ignored it, and on the next turn it put a question first and then said the shorter variant instead of the full lines. The server's check accepted that turn, but the caller heard under half of the signed-off words. Telling a speech-to-speech model what to say is not a guarantee, as DECISIONS.md 006 assumed.
- **Gemini Live** can't be told to wait for the server's turn check, so it starts its own reply before the server sees the caller's words. When the server then asked for the lines it said them, in full on three calls and in part on the others, but never first; the server check fails all six. This is structural, not a prompt problem.
- **Retell** paraphrased or reordered the lines ("Get everyone out of the house now and call 911... If you can open a window on your way out, do it"), as on the earlier chat runs (voice/retell/README.md). Four of its six calls read as "no safety signal fired" on the server while Retell said the shorter signed-off variant. The likeliest cause, not confirmed, is the one eval tool server Retell shares across calls: the repeats used the same caller's number minutes apart, and a caller who raises a safety issue again gets the variant (spec S-17). Our stack and the other entrants get a fresh tool server for every call, so they can't carry this over.

## 3. What the grader found

The grader failed some calls whose code checks all passed: our stack 2 of 11, ElevenLabs 5 of 11, OpenAI 3 of 11, Gemini 1 of 6, Retell 1 of 7 (calls with a verdict and every check passed). On the brain-sharing stacks the notes are mostly the same two issues, both in the prompts and signed-off text rather than the platform: the agent promising "we'll get a technician out to you" or "we'll follow up once you're safe" on a safety call with nothing set up, and the safety sign-off repeating the 911 line after the caller already said goodbye. Both are round-1 failures that #64 addresses. ElevenLabs' higher count also includes notes on transcription slips ("9111") that come from the rig's ears hearing ElevenLabs' voice, not from the agent's text, which was right.

## 4. The must-pass miss on ElevenLabs, and what the reruns found

`tune-up-known-caller` on ElevenLabs ended with no booking in the round. The simulated caller asked for "a weekday morning next week", the mock only had this week's slots, and the brain flagged a person to call back instead. That path is the brain's and would be the same on our stack. To test whether the miss was noise, the scenario ran three more times on each of the two stacks:

| Stack | Rerun results | Results file |
|---|---|---|
| ElevenLabs | 2 of 3 booked | `evals/results/20261103-222058-elevenlabs-realtime.elevenlabs-custom.json` |
| Our stack | 2 of 2 scored booked; 1 no verdict (the worker had not joined the room when the rig first read the call record) | `evals/results/20261103-223046-livekit-rig-fast-custom.json` |

The rerun miss on ElevenLabs was not noise, and it is ElevenLabs'. Both stacks get the same read-back text from the brain, "801 Fenimore St, Brooklyn, 11206, and the best number is 347, 555, 0162". Through Cartesia on our stack the rig heard exactly that. Through ElevenLabs' voice it heard "801 Fenimore Saint Brooklyn 111206, and the best number is 347552" (MEASURED: the platform's text against the rig's transcription of the audio; of every read-back the rig heard in the round and the reruns, seven on ElevenLabs all had a wrong street word, ZIP or phone number, and four on ours were all clean). "St" read as "Saint" is the voice's own text normalization; the run-together digits may be partly the rig's ears on that voice. On the failed call the caller corrected the read-back three times, the server never got a confirmed read-back, and so it never allowed the booking (S-26): the gate worked, and the call failed. Our tenant's pronunciation rules (DECISIONS.md 023) were written for Cartesia; ElevenLabs would need its own (its text normalization setting or a pronunciation dictionary) before it could read addresses and numbers back reliably.

## 5. Calibration: does the rig agree with the platforms' own clocks?

- **Retell** reports its own end-to-end latency per call. Across the 12 calls, the rig's per-call median reply delay was a median of 0.25 s above Retell's own per-call median (MEASURED: `latency.e2e.p50` from `POST /v3/list-calls` against the rig's turn delays for the same calls; range -0.06 to +0.76 s). The difference is the network and audio path Retell's clock doesn't see: our audio reaching Retell and its audio reaching us.
- **Our worker** logs its own `reply_ms` for every agent line. The rig read a median of 0.39 s above it (60 turns, MEASURED: rig 1.90 s, worker 1.47 s). The worker starts its clock when its voice-activity detector decides the caller has stopped, which comes after the configured 0.35 s of silence (`voice.vad.min_silence_s` in config/models.yaml), so the worker's own figure leaves that wait out. **This means `reply_ms` in `make trace` reads about 0.4 s low against what a caller hears**, including the first real call in docs/latency.md.
- **The same model, in both:** Claude's first token took a median of 570 ms inside our worker and 622 ms behind ElevenLabs (MEASURED from each one's logged first-token time on plain turns). The 0.44 s between the two stacks is therefore not the model; it is hearing, deciding the caller has finished, and the voice. Our endpointing values (0.35 s of silence, then at least 0.3 s more before answering, `voice.endpointing.normal`) are ASSUMED starting points carried over from the earlier practice agent (a personal practice project, not a person; from-other-projects/bobert), never tuned on this stack.
- **Where the rig runs:** our worker ran on the laptop, so its audio crossed the internet to LiveKit Cloud and back twice more than it would on the server in New Jersey. That costs it something here (not measured), but less than the gap to ElevenLabs, whose audio also crossed an ngrok tunnel to the laptop for every model call.

DECISIONS.md 026 says the rig should give way to the phone loop if its delays disagree with real calls by more than the gap between entrants. Against the platforms' own clocks the rig reads 0.25 to 0.39 s high, similar for both, so the ranking between our stack and ElevenLabs (0.44 s apart) holds; the absolute numbers are a little above what each platform reports. The real calls for the final two will settle the absolute figure.

## 6. Verdict against the decision 026 rule

The rule: a newcomer is chosen only if its plain-turn median is at least 0.3 s lower than ours and every must-pass scenario passes with the safety lines word for word on three repeats; anything less is a tie, and a tie keeps our stack.

| Newcomer | Plain median vs ours | Safety lines, 6 calls | Every must-pass passed | Verdict |
|---|---|---|---|---|
| ElevenLabs on our brain | 0.44 s faster | exact on 6 | no: the tune-up booking check, then 1 of 3 reruns failed on its voice garbling the read-back (section 4) | tie, keep ours |
| Gemini Live | 0.60 s faster | first on 0 | no | tie, keep ours |
| OpenAI Realtime | 0.58 s slower | exact on 4 of 5 scored | no | tie, keep ours |
| Retell (baseline, not a newcomer) | 0.31 s faster | exact on 1 | no | (bake-off 1 decides Retell) |

Proposals that follow from this (for Jackson; not decided):
1. **The final two for real calls are our stack and ElevenLabs on our brain**, as bake-off 6 plans, once ElevenLabs reads addresses and numbers back cleanly (its own pronunciation rules, then a rerun of the tune-up and lapsed-member scenarios, about $2, ASSUMED from this round). Gemini Live and OpenAI Realtime stop here: one can't put the server's check ahead of its reply, the other is slower than everything else and still missed the lines once.
2. **Look at our turn-taking before the real calls.** The gap to ElevenLabs is in endpointing, not the model. A rig run of our stack with shorter silence and minimum-delay values (about $1, ASSUMED from this round's $0.08 a call all-in) would show how much of the 0.44 s is settings, while the digit-reading longer wait (C-48) stays as it is.
3. **Read "must-pass" on the rig as the code checks,** with grader notes reported beside them: the grader reads the rig's transcription and the same prompt issues appear on every stack that shares our brain.
4. **Correct docs/latency.md:** the worker's `reply_ms` starts after the 0.35 s silence wait, so callers wait about 0.4 s longer than it says.

## 7. Cost of the round

| Item | Cost |
|---|---|
| Round, five stacks, 60 calls (all-in, rig parts included) | $10.48 (MEASURED: sum of `cost_usd` in the five results files) |
| Tune-up reruns, section 4 | $1.55 (MEASURED: ElevenLabs $1.28, ours $0.27) |
| Trial calls while building the rig, and two OpenAI runs stopped for adapter fixes | about $2.5 (MEASURED from the trial results files; the stopped runs from their logs) |

About $14.50 in all. Of the round's $10.48, $1.78 is ElevenLabs minutes at list price that the Starter plan covered with credits rather than cash. Inside the $15 estimate (ASSUMED, exploration 013) and the $25 cap; no stack passed twice its estimate.
