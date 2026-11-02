# 04. Which platform answers the phone?

Issue: #4. Status: ready

## Question
Which service takes the call and runs the conversation: LiveKit (we build the worker) or Retell (managed)? This blocks the build briefs (#8) and the pipeline and latency decisions (#5, #7).

## Options
### A. LiveKit Agents, self-built
Reuse Bobert's LiveKit worker (Deepgram, Cartesia, Haiku) on LiveKit Cloud, with a Twilio or Telnyx SIP trunk. We own the worker, hang-up, transfer and turn settings; the tool server stays the only place rules live, and the worker's own `end_call` and transfer tools can refuse until the server says the safety line was spoken.

### B. Retell, managed
Prompt, model and tools as config pushed to Retell, which owns telephony, turn-taking and simulations; the tool server is unchanged. Fastest to a working number, but Retell's built-in transfer and end-call never reach our server, so the safety hand-off depends on prompt order unless every exit goes through a wrapper tool (as Bobert's `log_handoff` did).

### C. LiveKit primary, Retell standby on the same tool server (issue #4's current plan)
A, plus a second number on Retell, both calling the same tool endpoints and the same prompt files in `prompts/`. The live go/no-go (#9) decides which one the demo number points at; the cost is keeping two voice configs in step.

### D. Vapi or Pipecat
Not a real contender. Vapi is the same managed trade as Retell with no Bobert history; Pipecat is the same build burden as LiveKit with no tie to Revin.

## Context
| | A. LiveKit only | B. Retell only | C. LiveKit + Retell standby |
|---|---|---|---|
| What you give up | A managed safety net; every phone problem is yours to debug | Control of hang-up and transfer; the live-debug story is "their platform" | One extra day and two configs to keep in step |
| Cost per minute | about $0.065, plus $50/mo plan (SOURCE pricing; LLM ASSUMED $0.01) | about $0.11 with Haiku (SOURCE pricing) | A's cost; Retell billed only on standby minutes |
| Latency evidence | MEASURED in Bobert: 1.0 s plain turns, 2.3 s tool turns, clean audio, laptop | None measured by us; vendor claims about 600 ms (unverified) | Both of the left |
| Effort and risk to the demo date | Worker and 23 tests exist; new work is trunk, transfer, hosting. Risk: real phone audio untested | Hours; Bobert's push script exists. Risk: platform behavior you cannot change | Highest effort, lowest risk of no working number |
| Signal to Revin | Likely matches their stack, but the only evidence is one Revin job post ("LiveKit + OpenAI Realtime"), since taken down | Reads as assembling a vendor product (ASSUMED) | Matches their stack, and shows a fallback plan |

## Recommendation
Pick C, with LiveKit on the $50/mo Ship plan (the free plan cold-starts an idle agent for 10 to 20 s, which would wreck the first demo call), and a written cut-over rule in #9: any wrong booking or missed flag, or median first response above 1.5 s (ASSUMED target), moves the demo number to Retell. This changes to B if the 10 calls fail on latency (tool turns are 2.3 s today) or if call transfer on the SIP trunk is not working a few days before handover.

## Research notes

Revin and LiveKit
- Supports LiveKit: a Revin posting says "Partner with the voice team on real-time agent behavior (LiveKit + OpenAI Realtime)" and lists LiveKit, Daily and Twilio Voice as a bonus (SOURCE: [Built In NYC listing](https://www.builtinnyc.com/job/ai-systems-engineer/9252017), read 2026-11-02; the listing was removed 2026-10-29).
- Does not name a stack: Revin's FDE posting ([aitrainer.work](https://aitrainer.work/jobs/startups/full-time/forward-deployed-engineer-us-revinai)) and [revin.ai](https://www.revin.ai). LiveKit's [customers page](https://livekit.com/customers) does not list Revin. So "Revin builds on LiveKit" rests on one job post; say "your voice team uses LiveKit, as I read your listing" in the demo, not a flat claim. Revin also uses OpenAI Realtime, while Bobert uses separate speech-to-text, model and text-to-speech.

Prices (all SOURCE, read 2026-11-02)
- Retell ([pricing](https://www.retellai.com/pricing)): voice infra $0.055/min, TTS $0.015/min, telephony $0.015/min, Claude 4.5 Haiku $0.025/min, GPT-4.1 mini $0.0128/min; 20 concurrent calls free, number $2/mo, silence billed. The page's calculator shows $0.11/min and lists telephony as $0.00, so it is not fully consistent. Sum used: 0.055 + 0.015 + 0.025 + 0.015 = $0.11.
- LiveKit Cloud ([pricing](https://livekit.com/pricing)): Build $0 (5 concurrent, 1,000 agent min), Ship from $50/mo (20 concurrent, 5,000 min, then $0.01/min), US local number 1 free then $1/mo, US inbound $0.01/min, third-party SIP $0.004/min, Deepgram Nova-3 $0.0048/min, Cartesia Sonic 3 $0.030/min (Build/Ship). Sum used: agent $0.01 + inbound $0.01 + STT $0.0048 + TTS $0.03 + LLM ASSUMED $0.01 = about $0.065; the page's own calculator adds $0.01/min observability.
- Vapi ([pricing](https://vapi.ai/pricing)): $0.05/min hosting plus STT, LLM and TTS gives $0.082 to $0.129/min before telephony (my sum). Pipecat Cloud: $0.01/min per active agent plus Daily phone minutes about $0.018, from secondary sources only ([summary](https://frontdeskreview.com/software/ai-voice-agents/pipecat-cloud/), unconfirmed on Daily's page).
- At demo scale (tens of minutes) the cost gap is under a few dollars; the $50/mo plan is the real line item.

Phone setup
- LiveKit with Twilio: buy a Twilio number, point it at a TwiML Bin that dials LiveKit's SIP endpoint, add an inbound trunk and a dispatch rule. That route does not support SIP REFER (transfer) or outbound; the docs point to Twilio Elastic SIP Trunking for those ([docs](https://docs.livekit.io/telephony/accepting-calls/inbound-twilio/)). Warm transfer to a person needs an outbound trunk ([docs](https://docs.livekit.io/telephony/features/transfers/warm.md)). Provider quickstarts exist for Twilio, Telnyx, Plivo, Wavix ([docs](https://docs.livekit.io/telephony/start/sip-trunk-setup.md)).
- LiveKit Phone Numbers: US local and toll-free, inbound only; the page does not mention SMS ([page](https://livekit.com/products/livekit-phone-numbers)). The spec's text confirmation therefore needs a Twilio or Telnyx number either way (ASSUMED).
- Cold starts: on the free Build plan, a scaled-to-zero agent adds 10 to 20 s before it joins; Ship and Scale keep production agents warm; instant rollback needs a paid plan ([docs](https://docs.livekit.io/deploy/agents/managing-deployments.md)).
- Retell: custom functions send a signed POST (`X-Retell-Signature`), do not cancel when the caller interrupts, and retry only if `max_retry` is set, so idempotency stays required ([docs](https://docs.retellai.com/build/single-multi-prompt/custom-function)). Batch simulations with mocked functions exist ([docs](https://docs.retellai.com/test/batch-test-simulation)). LiveKit unit tests can assert on tool calls turn by turn, and it has Agent Simulations ([docs](https://docs.livekit.io/agents/start/testing/)).
- Standby switch: the Twilio number dials LiveKit first and falls back to a Retell SIP endpoint if that dial fails (ASSUMED to work; prove it before #9).

Latency
- MEASURED in Bobert (laptop to Deepgram, Anthropic, Cartesia; clean TTS clips, one scripted run each): Haiku first audio about 1.0 s on plain turns, 2.3 s on tool turns; GPT-4.1 mini 2.1 s median; STT final 0.2 to 0.4 s after the harness bug was fixed (`from-other-projects/bobert/latency-notes.md`).
- Not measured: real 8 kHz phone audio, and Retell at all. Retell's about 600 ms is repeated across its own blog posts (180, 200 and 80 test calls, inconsistent) with no independent benchmark (SOURCE: [retellai.com](https://www.retellai.com/), unverified). Comparing 1.0 s with 600 ms is not apples to apples; #9 is the first fair test.

Gaps
- Telnyx current inbound rate: sources disagree ($0.0032 to $0.0075/min); check Telnyx's page if Telnyx is chosen.
- Retell's call-transfer doc page returned 404; the transfer bypass comes from Bobert's architecture notes (`from-other-projects/bobert/architecture.md`), not a fresh read of Retell's docs.
- Effort estimates are ASSUMED. Tools used: built-in search and fetch only; no paid fallback calls.
