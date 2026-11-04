# Bake-offs

Choices settled by measurement rather than opinion. Each bake-off states its hypothesis, what it measures and the rule for deciding before it runs, so the results are read against a plan instead of picked over afterwards. Runs are small by default (a cheap subset of scenarios and a few real calls); a full run is saved for a release gate. Results go in DECISIONS.md. Multilingual runs are left out for now (docs/later.md).

## What every bake-off measures

The same scorecard, so results compare across bake-offs. Numbers in brackets are the bar, ASSUMED until the latency budget (#7) and the spec (#3) set them.

1. **Safety gates hold (must pass).** Every gas, carbon monoxide and "delete everything" scenario: safety lines first and word for word, no booking, a person flagged, nothing destroyed. One failure rules a candidate out.
2. **Books correctly.** Share of booking scenarios that end with the right job in the mock ServiceTitan (right customer, address, slot, priority) and no double booking [at least 90%].
3. **Feels like a person.** Grader score for one question at a time, no robotic phrasing, no invented policy [at least 4 of 5].
4. **Reply delay a caller would notice.** Median and slowest-10% time from the caller finishing to the agent's first sound, on plain turns and on turns with a lookup or booking [plain under 1.2 s median, lookup turns under 2.5 s].
5. **Handles interruptions.** On the real calls: stops talking when cut off, doesn't talk over the caller.
6. **Cost per call** at the estimated call length.

## The bake-offs

### 1. Voice platform: our LiveKit stack against Retell (DECISIONS.md 004)

- **Hypothesis:** our stack matches Retell on books-correctly and feels-like-a-person, beats it on control (the safety gates hold because the server owns hang-ups and transfers), and is within about 0.3 s on reply delay. Retell may sound smoother at turn-taking out of the box.
- **Run:** the same eval scenarios through each, plus 10 real calls each from a script of mixed callers (evals/live-calls.md). Retell's 10 use the same brain as our stack; about 3 more run with Retell's fast fallback on and are reported apart (DECISIONS.md 012).
- **Decide:** keep our stack unless Retell beats it by more than 0.3 s median reply delay or clearly on interruptions; a safety failure on either side rules that side out.
- **Known Retell limit, found in the build:** Retell's own model speaks the safety lines, so it can't guarantee the signed-off wording; on the first gas call it changed ", and call 911" to ". Call 911" and gave the exact lines only on its fourth reply (MEASURED, voice/retell/README.md, "Gaps Retell can't close"). Our server still refused to book or hang up until the exact lines were said, so the gate held, but the caller heard a paraphrase. On our stack the worker speaks the server's text as given. The same limit applies to any fixed wording a customer wants said exactly: greetings, disclosures, slogans, the brand's own phrases (docs/client-notes.md). Caveat: this is Retell's single-prompt agent, which we ran on the same shared prompt as our stack so the brain was the only thing held equal. Retell's Conversation Flow agent has a static-sentence setting on a conversation node that says the exact sentence first (SOURCE: https://docs.retellai.com/build/conversation-flow/conversation-node, read 2026-11-04); we didn't test it, because it means rebuilding the agent as Retell's own graph instead of our shared prompt, and its forum has unconfirmed reports of static nodes being skipped before a function node (https://community.retellai.com/t/conversation-flow-agent-ignores-static-text-nodes-llm-generating-responses-instead-of-speaking-hardcoded-text/2078). So the fair claim is that Retell as we ran it paraphrased the lines, not that Retell can't say them exactly.

### 2. Speech-to-speech check: OpenAI Realtime and Gemini Live against our stack (DECISIONS.md 006)

- **Hypothesis:** no faster than our stack on first reply, worse on fixed safety wording and on tool reliability.
- **Run:** about 8 scenarios and 3 real calls each (a few dollars, ASSUMED).
- **Decide:** reopen decision 006 only if one matches our stack on the safety gates and books-correctly and is clearly faster.

### 3. Side models: none against a second Haiku call against dedicated decision models (DECISIONS.md 006)

- **Run only after** evals or live calls show a bottleneck: a slow gate, or a judgment the main model gets wrong (for example missing a vulnerable person).
- **Hypothesis:** a dedicated decision model (Jev, a post-trained Laya, OpenAI Decisions) is as accurate as a second Haiku call on that one question and answers before the gate needs it; a second Haiku call is accurate but slower and gives no confidence score.
- **Run and decide:** evals/decision-models/PLAN.md (quick run about $0.08). Add a side model only if it fixes the bottleneck without a delay a caller would notice; "stay with none" is a valid result.

### 4. Model matrix (later)

- Haiku, Sonnet and Opus, plus the OpenAI and Gemini profiles, as the live brain, on the full eval suite.
- **Hypothesis:** a larger model improves feels-like-a-person a little and costs too much reply delay to use live; Haiku stays the live model.
- Designed once the agent works end to end (docs/later.md).

### 5. Vapi: a written comparison and one test call (DECISIONS.md 020)

- **Hypothesis:** a Vapi tool result spoken word for word, with the model skipped, keeps the safety lines exact where Retell paraphrased them; otherwise Vapi is the same trade as Retell.
- **Run:** after the main run, one gas call on Vapi's free number against our tool server (about $0.20, ASSUMED), plus the written comparison in docs/explorations/010-vapi.md.
- **Decide:** promote Vapi to a full entrant only if the lines come out exact and reply delay matches our stack.

### 6. ElevenLabs Agents, OpenAI Realtime and Gemini Live on a simulated audio rig (DECISIONS.md 026)

- **Hypothesis:** none beats our stack by 0.3 s on plain turns while keeping the safety lines exact and hang-ups on our server; ElevenLabs on our brain comes closest.
- **Run:** the rig in `evals/audio_rig/` streams synthesized caller audio to each entrant over its WebSocket for 8 scenarios, three repeats of each must-pass one, and times the first agent sound; our stack and Retell run the same set to calibrate (about $15 in all, ASSUMED). The final two then get the real calls. This supersedes the 3 real calls each planned in bake-off 2.
- **Decide:** the rule in DECISIONS.md 026. Research: docs/explorations/013-more-bakeoff-entrants.md.
- **Round 1 results:** evals/rounds/bakeoff-audio-01.md. No newcomer chosen under the rule; ElevenLabs on our brain was 0.44 s faster on plain turns with the safety lines exact, and failed on reading addresses and numbers back (MEASURED there).

## Considered and not tested

The bake-off tests one entrant per kind of architecture, so each hypothesis is checked once at the lowest cost. With more time we would run every option below; we don't expect them to change the result, for these reasons (research in docs/explorations/013-more-bakeoff-entrants.md).

- **Pipecat.** It is the same architecture as our LiveKit stack (we pick the ears, brain and voice; the framework handles turn-taking), so swapping frameworks with the same parts mostly changes the plumbing, not the delay or the gates. Our LiveKit stack stands in for it.
- **Deepgram Voice Agent API.** A vendor-hosted version of the same cascade with the same Deepgram ears; ElevenLabs Agents on our brain already tests "a vendor runs the turn-taking, our server runs the rules".
- **Ultravox.** A speech-native model, the same bet as OpenAI Realtime and Gemini Live, which are tested. It runs only its own open-weight model, so Claude can't be the brain, and its testing tools were unfinished when checked.
- **Telnyx AI Assistants.** The fastest published median for a managed stack (1.30 s, SOURCE: OpenBenchmarks via exploration 013), but vendor-assembled like Retell and ElevenLabs, with no Cartesia voice and no confirmed way for our server to veto a hang-up. Kept as the first reserve if ElevenLabs drops out.
- **Twilio ConversationRelay.** A transport, like LiveKit, that doesn't promise to speak our text exactly as written.
- **Bland, Synthflow, Hume EVI, Cartesia's managed agents.** Our server can't own the hang-ups or force the exact safety lines, so they would fail the must-pass gate by design.
- **Amazon Nova Sonic, Azure Voice Live.** Speech-to-speech models, the same bet as Realtime and Gemini Live; not researched.
