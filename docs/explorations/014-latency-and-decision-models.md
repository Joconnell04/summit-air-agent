# 014. Latency and decision models: where the reply delay goes, and ten ideas

Researched 2026-11-03 from existing result files plus built-in search and fetch; no paid calls, no model or eval runs. Jackson's ideas, investigated, not decided. Numbers are SOURCE, MEASURED or ASSUMED. Two data sets, named in every MEASURED label. **Rig**: the audio rig on our stack, 81 timed turns (`evals/results/20261103-220315-livekit-rig-fast-smoke.json` and `20261103-223046-livekit-rig-fast-custom.json`). **Text**: the text harness, 3,474 turns in 392 calls (the 26 `transport: text` files dated 20261104), which time the model and tool server only: no speech, no phone, a laptop to Anthropic and a local tool server with the mock. Turn kinds and call stages are labelled by text rules I wrote (ASSUMED labelling, MEASURED delays); the script is not committed.

## Recommendation

Cut the fixed wait first, not the model. A turn with no model at all (the safety lines, spoken from code) still takes 1.16 s on the rig, so the 1.2 s plain-turn target (S-36) cannot be met by a faster model; the model adds 0.8 s on top of that floor (plain turns 1.94 s). Do ideas 1 and 2 first (retune the end-of-turn waits, and let the server speak the read-back and booking text itself), then 4 (hand the model lookup results so it makes one request instead of two). Use the fast decision model for judgment, not seconds: extend it to "asked for a person" and "commercial" (idea 3, off the reply path, so it can run alongside), and run the bare-yes fast path (idea 5) in shadow mode only. Skip the faster-brain lane (idea 10); Anthropic's fast mode is not offered for Haiku 5.5 and speeds output, not first token.

| Rank | Idea | Saves | Effort | Cost | Worth (1-5) | Sloppiness (1-5) |
|---|---|---|---|---|---|---|
| 1 | Retune end-of-turn and silence waits | 0.1 to 0.4 s on every turn (ASSUMED) | 1 day | about $1 a rig run (ASSUMED) | 5 | 3 |
| 2 | Server speaks read-back and booking text itself | 0.47 s on 0.7 to 1.3 turns a call (MEASURED, text) | 1 day | none | 4 | 2 |
| 3 | Extend the side model: asked-for-person, commercial, can't-keep-up | feature; no seconds | 2 days (ASSUMED) | about $0.0004 a call per question (MEASURED, one question) | 4 | 2 |
| 4 | Hand the model lookup results, skipping its tool hop | about 0.7 to 1.0 s on 1.2 turns a call (MEASURED parts) | 3 days (ASSUMED) | none | 4 | 3 |
| 5 | Bare-yes fast path for the booking turn (shadow first) | about 2.5 s on 0.6 turns a call (ASSUMED) | 3 to 4 days (ASSUMED) | decision model, cents | 4 | 4 |
| 6 | Say a short line before a silent tool call | first sound 1.2 s sooner, the answer no sooner (MEASURED, text) | 0.5 day | about $2 evals (ASSUMED) | 3 | 3 |
| 7 | Deepgram Flux with eager end-of-turn | 0.1 to 0.2 s (SOURCE, vendor) | 2 to 3 days (ASSUMED) | about $0.0015 a call more (ASSUMED) | 3 | 3 |
| 8 | Voice side: shorter first chunk, preemptive TTS, Sonic 3.6 | 0.05 to 0.15 s (ASSUMED) | 1 day | cents | 2 | 3 |
| 9 | Our own small classifier on the server | none on the reply path | 4 to 5 days (ASSUMED) | none | 2 | 3 |
| 10 | Faster brain for some turns | 0 to 0.2 s (ASSUMED) | 1 day to try | cents | 2 | 4 |

## 1. Question

Where does the reply delay go on each kind of turn and each stage of a call, and which of ten ideas (a very fast decision model among them) cut it or add something useful without making the agent worse in ways the evals miss?

## 2. Why it matters to Summit Air

Jackson's bar: the agent should be good, not only pass the evals. spec S-36 sets the delay targets (median under 1.2 s plain, under 2.5 s with a lookup or booking, on real phone audio), and the brief says ASSUMED values are replaced by measured ones. Today the plain median misses by 0.7 s (MEASURED, rig). Anything that speeds a turn must leave the server-owned rules alone: the read-back gate S-26, the safety lines S-17 and S-18, priority S-21 and S-22. A decision model may inform one of those gates; the server decides (DECISIONS.md 006, 037).

## 3. Where the time goes now (Part 1)

**By turn kind, on the phone path (MEASURED, rig; reply delay from the end of caller speech to the agent's first sound, talked-over turns left out):**

| Turn kind | n | p50 | p90 |
|---|---|---|---|
| Safety lines, spoken from code (no model) | 6 | 1.16 s | 1.51 s |
| Plain (no tool) | 34 | 1.94 s | 2.49 s |
| Sign-off (`end_call`, spoken from code after one model hop) | 12 | 1.80 s | 2.11 s |
| Lookup (find_slots, service area, customer) | 19 | 2.01 s | 3.20 s |
| Read-back | 6 | 1.99 s | 2.57 s |
| Booking confirmation (`book_appointment`) | 4 | 4.13 s | 4.60 s |

**By call stage (MEASURED; rig p50 / p90, and text first word p50 / p90):** opening 1.92 / 2.38 s (n=13) and 0.48 / 0.75 s (n=392); intake 1.97 / 2.58 s (17) and 0.49 / 1.31 s (1,404); booking 1.98 / 3.81 s (36) and 0.59 / 2.27 s (1,279); closing 1.84 / 2.62 s (15) and 0.48 / 0.71 s (399). Medians are flat across stages; the booking stage is where the slow tail lives.

**Where each part goes (p50):**

| Part | Plain turn | Silent tool turn (no words before the tool call) |
|---|---|---|
| End-of-turn wait and speech start (the floor) | 1.16 s: VAD silence 0.35 (SOURCE, config/models.yaml), end-of-turn decision 0.35 and voice first byte 0.22 (MEASURED, the one real call in docs/latency.md, n=11), about 0.24 left over for the audio path (derived) | same floor (ASSUMED) |
| Model first token | 0.47 s (MEASURED, text, 1,962 turns); 0.57 s in the worker (bakeoff-audio-01 section 5) | 0.51 s |
| Rest of the model's first request (writing the tool call) | none | 0.56 s; about 1.0 s for `book_appointment` (derived) |
| Tool server round trip | 4 ms on the caller line (MEASURED, text) | 13 ms; 38 ms for booking (MEASURED, text, mock) |
| Second model hop (reads the result) | none | 0.47 s |
| First words reaching the voice | about 0.25 s (derived; the Cartesia plugin sends only whole sentences of 20 or more characters, read in installed livekit-plugins-cartesia 1.8.10) | same |
| Total | 1.94 s (MEASURED, rig) | text 1.66 s (p90 2.55), so about 3.1 s on the phone (ASSUMED, additive); rig booking turns 4.13 s |

Findings:
- **The tool server is about 1% of a silent tool turn.** The time is two model requests. Of 1,407 tool turns, 661 spoke before the tool call (first word 0.48 s, MEASURED, text) and 746 did not (1.66 s). That is 1.9 silent turns a call; the 746 are 453 lookups, 195 bookings, 90 read-backs and 8 sign-offs.
- **The fixed floor is 59% of a plain turn.** The 0.8 s the model adds is the only part a model swap can touch.
- **Rig against worker.** The rig reads 0.58 s above the worker's own `reply_ms` on plain turns (n=33) and 0.43 s over all turns (n=80), against 0.39 s in DECISIONS.md 038; the worker leaves out the 0.35 s silence wait, which a caller hears. The worker's `reply_ms` is also wrong on tool turns: 26 of 41 tool-turn lines read more than 0.5 s above what the rig heard, up to 11.4 s. The cause is not checked; the stamp probably lands on the line spoken after the tool. Read `make trace` delays on plain turns only until that is fixed.
- **Not stored in rig results.** The worker's per-turn end-of-turn, first-token and voice times are logged but not written to the results files (`model_first_token_ms` is 0 on every rig turn), so the split inside the 1.16 s floor is approximate. Saving those fields is the cheapest next step.

## 4. Options: ten ideas

**1. Retune end-of-turn and silence waits.** Today a caller waits a 0.35 s VAD silence plus an end-of-turn wait of at least 0.3 s before the model starts (`voice.vad`, `voice.endpointing.normal`, ASSUMED values from the earlier practice agent, a personal project, not a person; docs/history/from-other-projects/bobert; never tuned here). Run the rig at shorter values and try LiveKit's dynamic endpointing, which adapts the wait to how each caller pauses (SOURCE: LiveKit tuning page). The longer wait after asking for digits stays. *Saves* 0.1 to 0.4 s a turn (ASSUMED; ElevenLabs on the same Claude brain was 0.44 s faster, MEASURED, rig; LiveKit says in VAD mode the wait is the larger of the two values, not the sum, so part of it may overlap). *Effort* 1 day. *Cost* about $1 a run (ASSUMED, from $0.08 a call). *Worth 5. Sloppiness 3:* callers who pause mid-sentence get cut off, the text suite cannot see it and the rig only counts it; older callers pause most, so judge it on cut-offs in the hard-of-hearing and noise scenarios, not only the median.

**2. Server speaks read-back and booking text itself.** `read_back` and `book_appointment` already return the sentence to say; the worker already speaks `end_call`'s that way. Extend it to those two so there is no second model hop. *Saves* 0.47 s (second-hop p50, MEASURED, text) on the 510 read-back and booking turns that have a second hop (1.3 a call); the first sound moves only on the 285 silent ones (0.7 a call). It also stops doubled openers: in 2 of the 6 rig read-back turns, the agent said "Let me read that back" and the server line said it again (MEASURED by reading the transcripts, tiny n). *Effort* 1 day with tests. *Cost* none. *Worth 4. Sloppiness 2:* the wording is already the server's; the risk is sameness and losing the model's clause ("the downstairs bell"), so the server text must carry the caller's notes, and any caller line longer than a yes goes to the model.

**3. Extend the side model (feature).** The side model (DECISIONS.md 037) asks one question on each caller line and its answer was back before the gate on 47 of 47 reads (MEASURED, side-model-01), so extra questions cost no reply time. Candidates: "did the caller ask for a person" (a trapped caller), "commercial" (the model matrix found the server's phrase list matching "apartment building" from a renter, MEASURED, model-matrix-01), "is the caller having trouble hearing or keeping up" (the trigger docs/tuning.md wants for slower speech), and "agreed to the read-back" (shadow only, idea 5). Plugs into `prompts/side/questions.yaml`, `app/side_model.py` and the tenant slot in `agent.yaml`; each question needs its lines and bar from `evals/decision-models/PLAN.md`. Vendors: Jev answered in 68 ms p50 and OpenAI Decisions in 223 ms (MEASURED, side-model-01); Jev still has no written retention answer; OpenAI Decisions is generally available with zero data retention for eligible customers (SOURCE: OpenAI guide). Haiku as a side call took 1.0 s p50, too slow to matter here (MEASURED). *Effort* 2 days for three questions (ASSUMED). *Cost* $0.0004 a call per question (MEASURED for one question), against $0.0027 for the agent's own tokens (MEASURED, model-matrix-01). *Worth 4. Sloppiness 2:* flags and offers only. For "commercial" the model may only stop a decline: the server declines on a phrase match unless the side model says residential, in which case it asks one question. It never stands a safety line down.

**4. Hand the model lookup results.** Prefetching alone saves nothing here: tools answer in 13 ms. The saving is skipping a model request. The caller-turn endpoint already reads every caller line; when it sees a ZIP or address it runs `check_service_area`, then `find_slots` if the area passes, and attaches the result as a `[server]` note so the model answers in one request. *Saves* the rest of the first request plus the second hop, 0.56 + 0.47 = 1.0 s (MEASURED parts, text), on 1.2 silent lookup turns a call; net about 0.7 s, because a note stops LiveKit reusing the early reply it starts on the final transcript (docs/latency.md, issue #30; the 0.3 s lost is ASSUMED). With a real ServiceTitan call at 300 to 800 ms (ASSUMED, docs/latency.md) the saving grows. *Effort* 3 days (ASSUMED): server logic, note format, prompt, scenarios. *Cost* none. *Worth 4. Sloppiness 3:* the model may trust a note about an address the caller is still correcting, or offer slots before the read-back. Booking still passes through the server gates.

**5. Bare-yes fast path for the booking turn.** The slowest turn is the one after "yes, that's right" (4.13 s p50, n=4). If the caller's line is a bare yes (a phrase list, and a decision model at 0.97 or better) after a spoken read-back, the server confirms the read-back, books, and speaks the confirmation from code: about 1.16 s floor plus 0.04 s for the mock booking, or 0.3 to 0.8 s more with a real ServiceTitan (ASSUMED), so about 1.3 to 2.0 s. Anything else goes to the model, which is slower, never wrong. *Saves* about 2.5 s on 0.6 turns a call (234 booking turns in 392 calls, MEASURED; the saving ASSUMED). *Effort* 3 to 4 days (ASSUMED), after ideas 2 and 3. *Cost* cents. *Worth 4. Sloppiness 4:* it touches the booking gate. Hard negatives ("yes, but it's 4B", "yeah? and the tech comes Saturday?") must fall through, and a model-set flag is still not a gate; the server's own check decides. Run in shadow mode first: log what it would have done on every call and compare.

**6. Say a short line before a silent tool call.** Make the model open with a few words before the tool call, as it already does on 661 of 1,407 tool turns. *Saves* first sound 1.2 s sooner on silent tool turns (1.66 s against 0.48 s, MEASURED, text; narrated turns may be the easy ones). The answer arrives no sooner, so S-36 improves without the caller waiting less. The earlier practice agent's spoken filler pushed the real answer back 7 to 10 s (MEASURED, docs/latency.md), so it must come from the same model response, not a second reply. *Effort* 0.5 day. *Cost* about $2 in evals (ASSUMED). *Worth 3. Sloppiness 3:* repeated stock phrases; add a "first word after the tool" metric before judging.

**7. Deepgram Flux with eager end-of-turn.** Flux decides the end of a turn itself (about 260 ms) and can start the model on a medium-confidence guess, cancelled if the caller keeps talking (SOURCE: Deepgram docs). Plugs into `build_session` in `voice/livekit/worker.py`: `deepgram.STTv2` with `flux-general-en`, `turn_detection="stt"`, replacing `inference.TurnDetector()`. 8 kHz mulaw and keyterms are supported (SOURCE: Deepgram), `flux-general-multi` covers Spanish (SOURCE: LiveKit). *Saves* 0.1 to 0.2 s (SOURCE: Deepgram's estimate; Telnyx reports about 150 ms), at 50 to 70% more model requests (SOURCE: Deepgram), about $0.0015 a call (ASSUMED). I did not find whether LiveKit starts its early reply on the eager event. *Effort* 2 to 3 days. *Worth 3. Sloppiness 3:* it changes the ears under every safety and read-back rule; rerun the word-for-word safety calls, digits and local names.

**8. Voice side.** (a) Pass the Cartesia plugin a tokenizer with a smaller `min_sentence_len` than 20 characters so the voice starts on the first clause. (b) Set `preemptive_tts` to true (now false in `worker.py`); LiveKit says it cuts more latency for wasted compute, and the earlier practice agent put it near 0.15 s, unverified. (c) Try Sonic 3.6, which Cartesia calls its fastest; the sub-90 ms claim is secondhand, and we measured a 216 ms first byte (MEASURED, one call). Sonic Turbo stopped working after 2026-10-20 (SOURCE: Cartesia). *Saves* 0.05 to 0.15 s (ASSUMED). *Effort* 1 day. *Cost* cents. *Worth 2. Sloppiness 3:* a new voice snapshot can read addresses differently; the ElevenLabs run read "St" as "Saint" (MEASURED, bakeoff-audio-01), so rerun the read-back checks.

**9. Our own small classifier.** A fine-tuned small encoder on the Vultr box for the side questions, with no vendor and no retention question. The reply path gains nothing: the API answer is already back in time. For scale, Smart Turn v3, a small audio model, runs in 12.6 to 94.8 ms on CPU (SOURCE: Daily), and LiveKit already ships its own turn detector, so Smart Turn adds nothing here. I found no CPU figure for ModernBERT-sized text models. *Effort* 4 to 5 days; 480 labelled lines is thin (PLAN.md). *Cost* none. *Worth 2. Sloppiness 3:* overfits our written lines and its probabilities are uncalibrated.

**10. Faster brain for some turns.** Haiku 5.5 is already "Fastest" in Anthropic's lineup (SOURCE: models overview). Fast mode is Opus only and raises output speed, not first token (SOURCE: Anthropic). Priority Tier gives no latency guarantee (secondary source, ASSUMED). Cheap checks only: the same model from another cloud that lists it (Bedrock, Google Cloud, Foundry), via `make measure-delay`, cents. A different model for easy turns (an open model on a fast host): I found no controlled benchmark, and published times conflict. The model matrix showed a swap changes behavior (Sonnet narrated its tools, MEASURED), and a mid-call switch can change the voice of the agent. *Worth 2. Sloppiness 4.*

Also considered. A shorter prompt: the 9.4k tokens are 99% cache reads (MEASURED, docs/latency.md), Anthropic advises fewer tokens (SOURCE) but gives no number; expect 50 ms or less (ASSUMED), checkable with `make measure-delay` for cents after trimming unused tool descriptions. Not reopened: lookup before hello, the slot cache, thinking off, the bridge line, on-prem (docs/latency.md), Retell and Vapi (DECISIONS.md 038, exploration 010).

## 5. Recommendation, and what would change it

Order: 1 and 2, with 3 alongside, then 4. Record the worker's stage times in the rig results first, so each change shows which part moved. Hold 5 until a decision model passes its read-back bar in the full plan run (precision 0.97 or better, recall 0.85 or better, PLAN.md) and shadow mode agrees with the model on live-style calls. Do 6 only with the extra metric. Treat 7 as a trial after 1, because both act on the same wait. Skip 9 and 10.

This changes if: the sweep in idea 1 moves little (the wait is then mostly network and Twilio, and real calls on the Vultr box are the test, since the rig's worker ran on a laptop, DECISIONS.md 038); real ServiceTitan calls are slower than 0.3 s (idea 4 rises); Jev gives a written zero-retention answer (it becomes the side model, 68 ms against 223 ms); or a reviewer prefers a slower, steadier voice to a faster one (ideas 1 and 7 fall).

## 6. Open questions for Jackson

1. **The speed target.** The floor alone is 1.16 s, so S-36's 1.2 s plain median is out of reach unless the waits drop. (a) Keep 1.2 s and attack the waits (ideas 1 and 7). (b) Reword S-36 around the rig number and add time to the first word after a tool. (c) Leave it. Recommend (a), then (b) if the sweep falls short; S-36 is a high-touch doc, so it goes through `/round`.
2. **Fast path on a bare yes (idea 5).** (a) Shadow mode only. (b) Live once it passes. (c) Never. Recommend (a) now.
3. **Which side-model questions first (idea 3).** (a) Asked-for-person and commercial. (b) Add can't-keep-up for speaking rate. (c) None. Recommend (a), with the read-back question in shadow only.
4. **A second timing metric.** Add "first word after the tool" before changing how the agent opens tool turns (idea 6). Recommend yes.
5. **Spend.** About $6 for the first three experiments: three rig runs at about $1, a Flux trial at about $2 and $1.50 to label lines (ASSUMED, from bakeoff-audio-01 and PLAN.md). Recommend approving it.

## 7. Sources

Repo (MEASURED unless noted): docs/latency.md, docs/tuning.md, evals/rounds/bakeoff-audio-01.md, model-matrix-01.md, side-model-01.md, DECISIONS.md 006, 036, 037, 038, evals/decision-models/PLAN.md, docs/history/from-other-projects/bobert/latency-notes.md, the result files named above, `voice/livekit/worker.py`, `turns.py`, `evals/stacks.py`, `evals/audio_rig/rig.py`, and the installed `livekit-agents` and `livekit-plugins-cartesia` 1.8.10 source.

Web, read 2026-11-03: [LiveKit turn tuning](https://docs.livekit.io/agents/logic/turns/tuning) (preemptive generation, delays, dynamic endpointing); [LiveKit turns](https://docs.livekit.io/agents/build/turns/); [LiveKit Deepgram STT](https://docs.livekit.io/agents/models/stt/deepgram/) (Flux options); [Deepgram eager end-of-turn](https://developers.deepgram.com/docs/flux/voice-agent-eager-eot); [Deepgram Flux quickstart](https://developers.deepgram.com/docs/flux/quickstart); [Deepgram keyterms](https://developers.deepgram.com/docs/keyterm) (search snippet only); [Telnyx on eager end-of-turn](https://telnyx.com/release-notes/automatic-eager-end-of-turn-deepgram-flux) (snippet, vendor figure); [Smart Turn v3, Daily](https://www.daily.co/blog/announcing-smart-turn-v3-with-cpu-inference-in-just-12ms/); [Cartesia latest models](https://docs.cartesia.ai/build-with-cartesia/tts-models/latest) and [older models](https://docs.cartesia.ai/build-with-cartesia/tts-models/older-models.md); Sonic 3.6 latency claims from search snippets only (the [Marktechpost article](https://www.marktechpost.com/2026/08/18/cartesia-ships-sonic-3-6-a-streaming-tts-model-that-now-leads-both-artificial-analysis-speech-arenas/) returned 403); [Anthropic fast mode](https://platform.claude.com/docs/en/build-with-claude/fast-mode); [Anthropic models overview](https://platform.claude.com/docs/en/models/overview); [Anthropic reducing latency](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-latency); [OpenAI Decisions guide](https://developers.openai.com/api/docs/guides/decisions); [TypeSafe docs](https://docs.typesafe.ai) (intro page only: no latency or retention figures); [SPORK, speculative tool calls](https://arxiv.org/pdf/2607.03333) (snippet).

Gaps. Jev's retention policy, Sonic 3.6's measured latency, ModernBERT CPU latency, and a controlled TTFT benchmark for small models on fast hosts were not found; no Exa or Firecrawl calls were made (built-in search was enough). Whether LiveKit starts its early reply on Flux's eager event is not documented in what I read. No GitHub issue exists for this exploration, so none was commented on.

## Decided (Jackson approved the recommendations)

| Idea | Decision | Where |
|---|---|---|
| 1. Retune the end-of-turn wait | Do now | End-of-turn lane, with the worker's tool-turn timer fix and per-turn stage times in rig results |
| 2. Server speaks the read-back and booking text | Do now | PR #82 (read-back, booking, reschedule and cancel confirmations) |
| 3. Extend the side model | Do two questions: "asked for a person" (warm transfer) and "can't keep up" (speaking rate); not "commercial" | A lane after PR #83 merges |
| 4. Hand the model lookup results | Do after the live calls | A second latency lane |
| 5. Bare-yes fast path | Shadow mode only: log what it would do, never act | The second latency lane |
| 6. A short line before a silent tool call | Do, from the same model response | PR #82 for the booking turn; the second latency lane for the rest |
| 7. Deepgram Flux | Not before the demo | |
| 8. Voice-side tweaks | Test preemptive speech and a smaller first sentence on the rig; keep what helps | End-of-turn lane |
| 9. Our own classifier | No | |
| 10. A faster model for some turns | No: Haiku is already the fastest Claude model, the model is 0.8 s of a 1.94 s turn, and switching mid-call changes voice and judgment | |

Ears, tested after the decision (idea 7's neighbour): Cartesia Ink-2 against Nova-3 on the same scripted 8 kHz caller turns, same keyterms (evals/rounds/stt-ink2-01.md). Kept Deepgram: Ink-2's final transcript came a median 1.08 s after the caller's last word against 0.44 s (MEASURED, 60 runs each), and LiveKit waits for that final, so every turn would be about 0.6 s slower; it also missed 4 of 32 street spans to Nova-3's 0, and tied on digits and spelled names.
