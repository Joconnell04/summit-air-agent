# 05. Pipeline shape and model profiles

Issue: #5. Status: ready

## Question
Should a call run as separate speech-to-text, LLM and text-to-speech stages or as one speech-to-speech model; which model profiles does the eval matrix compare; and can a typed decision model (Jev, OpenAI Decisions, Laya) run beside the turn without costing latency? Blocks `config/models.yaml`, the latency budget (#7) and the build briefs (#8).

## Options
### A. Cascaded: Deepgram Flux, a Claude Haiku-class model, Cartesia Sonic
Each stage can be swapped, the server reads the caller's text before the model answers, and the safety script goes from code straight to the voice with no model in between. Bobert measured about 1.0 s to first audio this way with Haiku 4.5 (tool turns about 2.3 s), at roughly $0.02 to $0.04 per call minute.
### B. Speech-to-speech (OpenAI Realtime, Gemini Live)
One model hears and speaks, but published first-audio times (0.96 to 1.14 s) are no better than the cascade we measured. The caller transcript comes from a separate recognizer that can land after the reply starts, fixed wording cannot be guaranteed verbatim, and cost runs about $0.025 (Gemini) to $0.18 (OpenAI) per minute.
### C. Hybrid: speech-to-speech talks, a cascade runs safety and gates
A parallel recognizer feeds the server gates and a separate voice plays the safety script. Keeps the natural feel, but pays for two stacks and doubles what the evals must cover.
Options A to C are the pipeline; D to F are the side models that run beside each turn.

### D. Side models: none at launch (keyword floor plus server gates)
Bobert's starting point; no new vendor, and the gas, CO and smoke floor over-triggers on purpose. It leaves "did the caller really agree to the read-back" and soft vulnerability signals ("she gets confused when it's cold") to the main model alone.
### E. Side models: a Claude Haiku-class call beside the turn
Same vendor and key, structured yes/no output, no new failure mode. It gives a label rather than a calibrated probability, and its speed off the reply path is not yet measured.
### F. Side models: a decision model (OpenAI Decisions, Jev, Laya)
Jev is TypeSafe AI's hosted decision model; it returns typed probabilities in a single fast call. Only OpenAI Decisions has Summit evidence (perfect on 30 emergency lines, 160 ms median); stock Laya failed the safety gate and is English only; Jev is untested here.

## Context
| | A. Cascaded | B. Speech-to-speech | C. Hybrid |
|---|---|---|---|
| First audio | about 1.0 s (MEASURED, Bobert) | 0.96 to 1.14 s (SOURCE) | as B |
| Safety wording | spoken verbatim by code | prompt asks, no guarantee | verbatim |
| Caller text for gates | before the reply | async, may arrive after | before |
| Cost per call minute | $0.02 to $0.04 (ASSUMED) | $0.025 to $0.18 (SOURCE) | both |
| Spanish | Flux Multilingual and Sonic both cover it (SOURCE) | likely, not checked | as A or B |

Decision models fit without touching first audio: the caller's text arrives 0.2 to 0.4 s after they stop (MEASURED), the side call takes about 0.16 to 0.36 s (MEASURED), and the model's first word comes about 0.8 s after that. Only the booking gate waits on the answer, and by then it is back. A timeout falls back to the safe side: not agreed means re-ask, unknown urgency means the keyword floor stands. Cost: a new vendor sees caller text (add to C-30), and 3 of 139 calls exceeded 600 ms.

Model profiles: `fast` moves to Haiku 5.5 with thinking off, Haiku 4.5 stays as a control because it's what Bobert measured, Sonnet 5.5 is `quality`, and Opus or Fable only grade and play callers offline, never live. Details in research notes.

## Recommendation
Build A, and put the safety script on a code path that speaks fixed text with no model involved. Launch with D, and run E and F (OpenAI Decisions, then Jev) in the eval on two questions only, "agreed to the read-back" and "vulnerable person or soft urgency", adding Spanish lines; promote one only if it beats the keyword floor and rules, and drop Laya.
Would change if: Haiku 5.5 measures over 1.5 s with thinking off (use `fast-prev`); the `realtime` profile matches A on tool success and persona evals (revisit B for the after-hours line); or Spanish lines show a decision model missing vulnerable-person cases.

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
