# 05. Pipeline shape and which model does which job

Issue: #5. Status: ready (revised after Jackson's first read)

## Question

Three linked choices. (1) How a call is wired: separate hear, think and speak steps, or one model that does all three. (2) Which size of model does each job, so we never pay for a big model on a small task. (3) Whether small side models judge narrow questions now or later. Blocks the latency budget (#7) and the build briefs (#8).

## 1. How a call is wired

### A. Three separate steps (cascaded)
Deepgram turns the caller's voice into text, Claude Haiku decides what to say, Cartesia speaks it. Our server sees every caller line as text before the agent answers, so it can check it (gas mentioned? read-back done?), pull in data, and stop a wrong answer. The safety lines are spoken straight from code, word for word. Any of the three parts can be swapped.

### B. One speech-to-speech model (OpenAI Realtime, Gemini Live)
One model hears audio and answers in audio. It isn't faster: published first-reply times are 0.96 to 1.14 s against about 1.0 s measured for option A on the earlier practice agent. It is more expensive, can't promise the safety wording word for word, and we only get the caller's words as text after the fact, so the server can't check a line before the model replies to it.

### C. Both at once (hybrid)
The speech-to-speech model talks while a separate text pipeline listens alongside for the server's checks. The problem isn't only complexity: the talking model has already started its reply by the time the text pipeline catches "gas", so the check can only interrupt after the wrong thing is half said. Two systems also hear the caller separately and can disagree on what was said, with no clear source of truth, and every call is paid for twice.

**What "caller text for gates" means.** A gate is a server rule that blocks an action until a condition is met (no booking until the address is read back; nothing but the safety lines once gas is mentioned). Gates read the caller's words as text. In A the text exists before the agent answers; in B and C it can arrive after the answer has started, which is too late for a gate.

## 2. Which model does which job

Revin cares about using the right size of model for each job. The plan:

| Job | When it runs | Model class | Why |
|---|---|---|---|
| Live conversation turns | during the call | small and fast (Haiku) | every reply waits on it |
| Safety lines | during the call | none, fixed text from code | must be word for word |
| Narrow yes/no judgments (did they agree, vulnerable person?) | beside the call | the fixed list now, a decision model later | see part 3 |
| Job summary for the technician, call notes | after the call | mid-size (Sonnet) | nobody is waiting, quality matters |
| Simulated callers in the evals | offline | small (Haiku) | cheap, many runs |
| Eval grader | offline | large (Opus) | judging quality needs the best judgment |

A large model never sits in the live path. Each job is a named profile in `config/models.yaml`, so swapping one is a one-line change.

## 3. Side models: now or later

A side model is a second, small model that answers one narrow question while the main conversation carries on, so the caller never waits for it.

### D. None at launch
The main model plus the fixed list and the server's gates make every judgment. Simplest, nothing new to break. Softer cues ("she gets confused when it's cold") rely on the main model and on the agent asking.

### E. A second Haiku call beside each turn
The same model, asked separately and in parallel: "Did the caller just agree to the read-back? Answer yes or no." Same vendor and key, but it returns a yes or no, not a confidence score, and its speed off the main path isn't measured yet.

### F. A dedicated decision model (OpenAI Decisions, Jev, Laya)
Purpose-built for quick typed answers with a probability. OpenAI Decisions was perfect on 30 emergency lines in the earlier practice agent's eval at about 0.16 s; stock Laya missed 6 of 10 emergencies; Jev (TypeSafe AI's hosted decision model) hasn't been tested here.

Where one could plug in later: "did the caller agree to the read-back" before booking, "is someone vulnerable" for priority, "is this commercial" early in the call, "did they ask for a person". A test suite for these is being designed now (`evals/decision-models/PLAN.md`) to measure accuracy, added time, and whether any beats plain Haiku.

## Context

Picking A over B gives up the "one model that just talks" simplicity and a possibly more natural voice, and gains control: the server checks every line, the safety words are exact, and each part can be swapped or right-sized. Picking D now gives up softer detection at launch, and gains a simpler first build; the scaffold leaves a slot so a side model drops in without rework.

Spanish and other languages aren't a deciding factor now. Deepgram's Flux Multilingual and Cartesia both handle Spanish, so option A doesn't close that door (noted under C-16 to come back to).

## Recommendation

Wire calls as three separate steps (A) with the safety lines spoken from code, and give each job its own right-sized model as in the table, with nothing large in the live path. Launch with no side model (D) but build the slot for one, and add a decision model only if the test suite shows it beats plain Haiku on accuracy without adding a delay a caller would notice.

## Research notes

Suggested `models.yaml` profiles (not applied):
- `fast`: `claude-haiku-5-5`, thinking off, low effort. Unmeasured for voice; Artificial Analysis shows 8.8 s to first answer token with thinking on, so thinking must be off.
- `fast-prev`: `claude-haiku-4-5-20251001`, the control that measured 1.0 s.
- `quality`: `claude-sonnet-5-5` with up-front thinking off. It rejects "disabled", so the `reasoning: false` field must become a `thinking` field.
- `grader`: `claude-opus-5-5` and `claude-fable-5-1`, offline only (simulated callers, scoring), never live.
- `realtime`: one OpenAI or Gemini profile, only after A passes, because it needs its own adapter.

Method: built-in search and fetch only; no Exa or Firecrawl calls were made. The `claude-api` skill was not loadable here, so model ids and prices come from Anthropic's docs directly.

Numbers
- First audio, cascaded: about 1.0 s plain turns with Haiku 4.5, 2.1 s with GPT-4.1 mini, tool turns about 2.3 s. MEASURED by Bobert, one scripted run each, clean audio, not real phone audio (`from-other-projects/bobert/latency-notes.md`). Not yet measured with Haiku 5.5.
- First audio, speech-to-speech: Gemini 3.1 Flash Live Minimal 0.96 s, GPT-Realtime-2 High 1.14 s. SOURCE, Artificial Analysis arena, human-run scenarios.
- Tool task success in that arena: 74.6% (Gemini Minimal) to 94.7% (Grok); GPT-Realtime-2.1 Minimal 89.4%; one cascade (ElevenLabs Agents) 90.5%. Not like-for-like with ours. The article notes some failed conversations "sound as though the requested action was completed", the exact failure our booking gate exists for. A vendor-blog benchmark (JustCall) says speech-to-speech completed 26 to 38% of noisy simulated calls against 85% for text; methodology unverified.
- Cost: Anthropic per million tokens, Haiku 5.5 $0.10 in / $0.50 out, Haiku 4.5 $1 / $5, Sonnet 5.5 $2 / $10, Opus 5.5 $4 / $20, Fable 5.1 $10 / $50 (SOURCE). Flux Multilingual $0.0078 per minute, Aura-2 TTS $0.030 per 1,000 characters (SOURCE). Cartesia about 750 to 800 credits per spoken minute, Scale plan about $0.037 per 1,000 credits, so about $0.03 per spoken minute (SOURCE, computed). Realtime: gpt-realtime-2.1 audio $32 in / $64 out per million tokens, mini $10 / $20 (SOURCE); Artificial Analysis puts input audio at $10.75 per hour for GPT-Realtime-2.1 High and $1.50 per hour for Gemini Minimal.
- Cascaded cost per call minute is ASSUMED: agent speaks 40% of the call, 4 turns a minute, 4,000 input tokens a turn (Bobert's tool schema is 11 KB; Haiku 5.5's tokenizer counts about 30% more). Excludes telephony and platform fees.
- Decision models, Bobert eval (MEASURED, 30 emergency items, hand-labelled, English simulated text): OpenAI Decisions precision and recall 1.00; stock Laya recall 0.40; keyword rule 7 false alarms ("gas bill"). Agreement (40 items): OpenAI 0.925 accuracy, the old "does that work" gate 0.575. OpenAI latency p50 161 ms, p95 358 ms, max 1,023 ms over 139 requests; Laya in-process 82 ms. Cost $0.0061 for all 139 requests. A 100% on 30 items is a lower bound near 0.9 (ASSUMED rule of three).
- Jev: SOURCE, Firecrawl comparison post. $0.042 per million input tokens, p50 0.21 s and p95 0.34 s on OpenRouter telemetry (Oct 1); other writeups range from 70 ms to 920 ms, so unverified. Text only; languages not stated. Laya typed-decisions fine-tune: 0.766 accuracy on its own four workflows (invoices, security, support, traces), English only.
- OpenAI Decisions: `/v1/decisions`, only `gpt-6-luna`, $0.10 per million input, zero data retention supported, languages not stated.

Gaps
- Haiku 5.5 voice latency, Spanish accuracy of any decision model, real phone audio.
- Retell's support for these options is not checked; that belongs to #4. A is the shape both platforms are assumed to handle.
- "Jev" is Jackson's voice-transcribed word; TypeSafe's Jev matches C-35's description. Not added to the CLAUDE.md glossary.

Sources
- Anthropic models: https://platform.claude.com/docs/en/models/overview and pricing https://platform.claude.com/docs/en/about-claude/pricing
- Haiku 5.5 effort and thinking: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-haiku-5-5 and https://platform.claude.com/docs/en/build-with-claude/thinking
- Haiku 5.5 latency (thinking on): https://artificialanalysis.ai/models/claude-haiku-5-5-low/providers
- Speech Agent Arena (Aug 2026): https://artificialanalysis.ai/articles/announcing-the-speech-agent-arena
- Cascade vs speech-to-speech (Hamming, Dec 2025): https://hamming.ai/blog/are-speech-to-speech-models-ready-to-replace-cascade-models
- OpenAI pricing: https://developers.openai.com/api/docs/pricing; Realtime guide: https://developers.openai.com/api/docs/guides/realtime-conversations; Decisions: https://developers.openai.com/api/docs/guides/decisions
- Realtime transcript ordering: https://community.openai.com/t/how-to-get-input-audio-transcription-when-i-use-openai-realtime-api/1286361
- Gemini Live pricing: https://aistudio.google.com/docs/pricing
- Deepgram pricing: https://deepgram.com/pricing; Flux languages: https://developers.deepgram.com/docs/flux/language-prompting
- Cartesia pricing: https://cartesia.ai/pricing; models: https://docs.cartesia.ai/build-with-cartesia/tts-models/latest
- LiveKit `session.say`: https://docs.livekit.io/agents/build/audio/
- Jev vs OpenAI Decisions: https://www.firecrawl.dev/blog/openai-decisions-api-vs-jev; TypeSafe docs: https://docs.typesafe.ai
- Laya: https://huggingface.co/convaiinnovations/laya-typed-decisions
- JustCall speech-to-speech vs cascaded: https://justcall.io/blog/speech-to-speech-vs-cascaded-voice-agents.html
- Local: `from-other-projects/bobert/latency-notes.md`, `decision-model-eval.md`, `docs/considerations.md` C-09, C-10, C-12, C-28, C-35
