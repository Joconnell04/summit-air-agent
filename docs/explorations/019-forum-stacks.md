# 019. What small voice-agent builders say they use, and whether we went in too hot

Researched 2026-11-05. Jackson's question, investigated, not decided. Reddit blocks direct fetches, so threads were read through a public snapshot mirror (reddit.sentinel-team.org) found with Exa; about 20 Exa calls in all, no Firecrawl, nothing sent to any vendor. Numbers are SOURCE, MEASURED or ASSUMED.

**In short.** The forums don't point to a stack we missed. Small agencies mostly sell Vapi or Retell with an ElevenLabs voice, glued to GoHighLevel and n8n; the people who build their own run almost exactly our parts (Deepgram, Claude or Gemini Flash, Cartesia, on LiveKit or Pipecat). The loudest shared view is that the platform matters less than turn-taking and conversation design, and that rules in a prompt break while rules on the server hold, which is how we built. The one thing we have not tested is GPT-Live-1, OpenAI's full-duplex phone model released 2026-09-10, which builders call the most natural thing they have put on a phone line and also the least obedient. If our agent feels stiff, the forum evidence says look first at our 1.9 s reply delay, our roughly 6,000-word prompt and reply length, not the providers. A cheap way to check: four quick stock builds with a one-page prompt and no gates, about $50 to $90 all in (ASSUMED, section 5).

## 1. Question

Are small deployment engineers and agencies using a different stack, and did Summit Air over-build to the point that the agent is less conversational and less general than a quick forum-style build would be?

## 2. Why it matters to Summit Air

Every bake-off so far (DECISIONS 004, 026, 038) tested rivals wearing our harness: our prompt, our tool server, our safety lines. That answers "which platform carries our design best", not "is our design making the calls feel worse". A stock build answers the second question.

## 3. What the forums say

Mostly r/AI_Agents, r/automation and r/VoiceAI, threads from 2026-03 to 2026-10. Several popular threads are vendor seeding (one account posts the same "LuMay" ranking across five threads; Smallest AI is plugged in most STT threads), so those are ignored below.

**What agencies sell.** Vapi is the most-named platform for client work ("VAPI is what I've used most for client projects", 1tvfbdz; "if you're not using Vapi, just start there", 1tc0f7g). Retell is the "faster to a demo, softer for non-engineers" pick (1uy52st) and is chosen for HIPAA pay-as-you-go (1v10zpg). The one HVAC build posted in full is Retell plus n8n, GoHighLevel, Zapier and ServiceTitan, with a word-normalizing step for ServiceTitan field names (1tt83db), the same problem as our exploration 006.

**What custom builders run.** "Deepgram STT + Claude + Cartesia has been pretty solid" (1u4lz71). A white-label reseller runs most clients on Deepgram Flux plus Gemini Flash and says those models "follow behavioral instructions much better than the realtime models" (1u4lz71). A solo builder with 8 clients is on Pipecat at about 600 ms (1wgdhj8, MEASURED by the poster). LiveKit and Pipecat are treated as equals; nobody argues one is clearly better.

**ElevenLabs.** Best-sounding voices is the consensus, but its own agent platform gets mixed reports: "good, just need to get the config and voice selection right" (1vezfqs) against "extremely buggy" call endings, with the agent hanging up without a goodbye or repeating it, and GUI tests disagreeing with live calls (1sm6zwd). On Retell, the advice for a flat-sounding agent is to swap Retell's default voices for ElevenLabs (1v10zpg).

**Speech-to-speech models.** GPT-Live-1 is "the most natural-sounding model we've put on a phone line": full duplex, backchannels, about 1.3 s to first audio over a real phone line with no turn logic on their side. It also treated a clear "yes" as "no", thought out loud into the phone, and added a letter to a claim number that was not in its own transcript (1wewmyk, ThunderPhone, a builder with disclosed interest). Others report OpenAI's realtime models ignore "don't say this out loud" (1u4lz71) and that the public Realtime API feels more bot-like than ChatGPT Voice (1w8i21y). Gemini 3.8 Live tops one speech-to-speech index but scores 35.1% on a multi-step banking task (1whd2v0). This matches our own round 1: Gemini Live 0 of 6 on safety lines, OpenAI Realtime slowest (DECISIONS 038).

**What actually makes calls feel human, per builders.**
- Endpointing and barge-in, not the model. "I wasted a week switching models and the issue was endpointing + STT finalization" (1u4lz71); "Endpointing." as the one-word answer to what broke first (1vgz9tf).
- Short replies. "Shorter response chunks help a lot more than faster models" (1rxt0aj).
- Fill the gap. A short filler while a tool runs, rather than chasing the latency floor (1wbti1d).
- Upset callers pause; one Retell builder moved to 0.4 interruption sensitivity and a 1.2 s silence buffer so the agent stops cutting them off (1v10zpg).
- Smart-turn (Pipecat's open turn model) is the named tool for end-of-turn (1ub6ctw).

**What keeps calls correct, per builders.** "A rule in a system prompt is a suggestion the model will route around under pressure. A rule on the server is a refusal" (1vco5m9, five weeks of real bookings for a cleaning company). The same post: a model that scored 8 of 8 in a text harness called tools once in three on voice. Others: confirm a slot by restating it, never accept a bare "yes" to a two-option question (1v2979j); read identifiers back from the server, not the model (1wewmyk); judge the audio, not the transcript (1vlfeuv). Each of these is already a Summit Air rule (spec R5, R6; memory "judge by what the caller hears").

## 4. Did we go in too hot?

On the stack, no: our parts are the forum's custom-builder default, and the rules we put in server code are the ones builders say they learned the hard way. On feel, possibly, and the likely causes are ours, not the vendors':

- Reply delay. Our plain-turn median is 1.89 s to 2.02 s on the rig (MEASURED, bakeoff-audio-01 and -02), against about 1.3 s for GPT-Live-1 (SOURCE, 1wewmyk) and 1.45 s for ElevenLabs on our brain (MEASURED, bakeoff-audio-01). Round 2 showed the end-of-turn wait is not where the time goes.
- Prompt weight. The assembled prompts are about 6,000 words (MEASURED, `wc -w prompts/*.md`). Forum builders who say their agent felt natural describe short prompts and short turns; nobody describes one this long.
- No filler or backchannel while tools run.

The gap in the evidence: we have never heard a stock build of anything. Every comparison carried our prompt.

## 5. Options

**A. Do nothing new.** Keep tuning delay and prompt length on our stack. Free. Risk: we never learn whether a plain build sounds better.

**B. Four scrappy stock builds (recommended if Jackson wants the comparison).** Each gets a one-page receptionist prompt for Summit Air (greet, take the problem, name, address, phone, offer a window), the vendor's defaults for voice and turn-taking, one fake "book" tool that just logs, no safety gates, and its own number. Jackson calls each and judges feel only; the rig times them. They are not candidates for the product unless one clearly sounds better, in which case the question becomes what of ours to strip back.

| Build | Stack | Why it's in | $/min (all in) |
|---|---|---|---|
| Vapi stock | Vapi defaults: Deepgram, a fast GPT model, ElevenLabs or Cartesia voice | The agency favorite | $0.13 to $0.31 (SOURCE, third-party roundups; platform fee $0.05) |
| Retell stock | Retell single-prompt agent, its default model, ElevenLabs voice | The other agency favorite; ours always ran Retell with our prompt | about $0.12 to $0.19 (SOURCE, third-party; base $0.07, ElevenLabs voices +$0.04) |
| ElevenLabs Agents stock | Its own model and turn-taking, a Flash voice | Best voices; we only ran it on our brain | $0.08 plus model, about $0.10 (SOURCE for $0.08; model ASSUMED) |
| GPT-Live-1 | Full duplex on a Twilio number | Never tested; called the most natural on a phone line | $0.05 plus backend and Twilio, about $0.07 (SOURCE for $0.05, OpenAI model page; rest ASSUMED) |

Cost for all four, ASSUMED: about 30 minutes of Jackson's calls per build (10 calls of 3 minutes) plus about 50 rig minutes per build is 80 minutes, so $6 to $25 per build in usage; numbers at about $1.15 a month each on Twilio, or free trial numbers on Vapi and Retell; possibly a $22 ElevenLabs plan and $10 minimum top-ups on Vapi and Retell. Total about $50 to $90. Build effort: about one to two hours of agent time per build, since prompts and the rig exist already; GPT-Live-1 is the most work because it uses a new Live Sessions endpoint, not the Realtime one.

A fifth build, Pipecat with smart-turn on Deepgram Flux, Gemini Flash and Cartesia, is the custom-builder favorite but is close to our own stack; worth adding only if the four say feel is a stack problem.

**C. Strip-back test on our own stack instead.** Run our stack with a one-page prompt and the gates still on, and compare by ear. Cheapest test of the "too much prompt" theory (a few dollars, ASSUMED), but it can't tell us whether a vendor's turn-taking sounds better.

## 6. Recommendation and what would change it

B and C together, in that order: B tells us whether anything off the shelf sounds clearly better; C tells us how much of the gap is our prompt. If a stock build sounds no better than ours, the stack question is closed and the work is delay and prompt length. If GPT-Live-1 sounds clearly better, the question becomes whether our server can still own the safety lines and hang-ups around it, which it could not for the older realtime model (DECISIONS 038).

## 7. Sources

Reddit threads, read through snapshots on reddit.sentinel-team.org (IDs are Reddit post IDs):
- 1u4lz71 "What STT/LLM/TTS stack are you using for production voice agents right now?" https://reddit.sentinel-team.org/posts/1u4lz71/snapshots/2026-06-19T20%3A07%3A29.810032Z
- 1tvfbdz "Which AI Voice Agent Platform Are You Actually Using in 2026?" https://reddit.sentinel-team.org/posts/1tvfbdz/snapshots/2026-06-05T18%3A20%3A01.179808Z
- 1tc0f7g "I've been building AI voice agents for 8 months" https://reddit.sentinel-team.org/posts/1tc0f7g/snapshots/2026-05-15T18%3A26%3A28.525889Z
- 1uy52st "Retell vs Vapi vs Plura ai for a production voice agent" https://reddit.sentinel-team.org/posts/1uy52st/snapshots/2026-07-18T06%3A29%3A38.426144Z
- 1v10zpg "Struggling with empathy using Retell AI for healthcare agents" https://reddit.sentinel-team.org/posts/1v10zpg/snapshots/2026-07-20T22%3A54%3A37.212707Z
- 1tt83db "just finished a full AI system for an HVAC company in Tucson" https://reddit.sentinel-team.org/posts/1tt83db/snapshots/2026-06-05T18%3A20%3A01.179808Z
- 1wgdhj8 "I built a voice AI agent with ~600ms latency" https://reddit.sentinel-team.org/posts/1wgdhj8/snapshots/2026-09-18T20%3A32%3A36.503524Z
- 1vezfqs "Best stack for natural real time calling?" https://reddit.sentinel-team.org/posts/1vezfqs/snapshots/2026-08-06T21%3A58%3A27.376445Z
- 1sm6zwd "Anyone successfully using conversational agents?" https://reddit.sentinel-team.org/posts/1sm6zwd/snapshots/2026-04-17T16%3A12%3A04.397573Z
- 1wewmyk "First impressions of GPT-Live-1 for voice agents" https://reddit.sentinel-team.org/posts/1wewmyk/snapshots/2026-09-18T15%3A53%3A02.036314Z
- 1w8i21y "OpenAI Realtime API, conversation quality worse than ChatGPT Voice?" https://reddit.sentinel-team.org/posts/1w8i21y/snapshots/2026-09-12T09%3A52%3A25.847484Z
- 1whd2v0 "Google Releases Gemini 3.8 Live" https://reddit.sentinel-team.org/posts/1whd2v0/snapshots/2026-09-16T09%3A31%3A40.087417Z
- 1vgz9tf "What STT API are you using for production voice agents, and what broke first?" https://reddit.sentinel-team.org/posts/1vgz9tf/snapshots/2026-08-07T06%3A10%3A44.151998Z
- 1rxt0aj "Those of you building with voice AI, how is it going?" https://reddit.sentinel-team.org/posts/1rxt0aj/snapshots/2026-03-20T16%3A29%3A00.7517Z
- 1wbti1d "on xAI's realtime engine it has a floor around 1.3 seconds" https://reddit.sentinel-team.org/posts/1wbti1d/snapshots/2026-09-12T06%3A44%3A11.472581Z
- 1ub6ctw "Building voice AI agents that take turns like humans" https://reddit.sentinel-team.org/posts/1ub6ctw/snapshots/2026-06-26T19%3A21%3A42.749466Z
- 1vco5m9 "Every guardrail we wrote as a prompt rule has since been broken" https://reddit.sentinel-team.org/posts/1vco5m9/snapshots/2026-08-07T06%3A10%3A44.151998Z
- 1v2979j "AI receptionist silently mis-book a caller" https://reddit.sentinel-team.org/posts/1v2979j/snapshots/2026-07-24T21%3A42%3A53.671605Z
- 1vlfeuv "clean transcript can hide a terrible voice-agent call" https://reddit.sentinel-team.org/posts/1vlfeuv/snapshots/2026-08-14T18%3A04%3A22.144593Z

Pricing:
- GPT-Live-1, $0.05 a minute, billed per second, backend extra: https://developers.openai.com/api/docs/models/gpt-live-1
- Retell, Vapi, ElevenLabs per-minute figures (third-party, verify before spend): https://www.morphllm.com/best-ai-voice-agent-platforms , https://www.11x.ai/guides/retell-ai-pricing , https://speechify.ai/blog/best-voice-agent-platforms-2026
