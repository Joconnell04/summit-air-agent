# 04. Which platform answers the phone

Issue: #4. Status: decided, option D (DECISIONS.md 004)

## Question

What runs the phone call: a stack we build ourselves on LiveKit, Retell's managed platform, or both built and measured against each other. Blocks the build briefs (#8) and the latency budget (#7).

A phone agent has four jobs: the **ears** (speech to text), the **brain** (the model deciding what to say), the **mouth** (text to speech), and the **switchboard** (connecting the phone line and deciding when the caller has finished talking). The options differ in who runs each job.

## Options

### A. Our own stack on LiveKit
We wire Deepgram (ears), Claude Haiku (brain) and Cartesia (mouth) together in a program on our Vultr server; LiveKit's free plan is the switchboard. We control everything, including how long to wait when someone reads out a phone number, and every hang-up and transfer goes through our own code, so the server can stop the agent hanging up on a gas call. We also own every bug.

### B. Retell does it all
Retell runs the ears, mouth, switchboard and the model; our server only answers tool calls (look up, book, flag). Fastest to a working number and polished turn-taking, but you can only turn Retell's dials, not change how it works, and our server can't veto a hang-up or transfer (a tool can't end a call; we can only remove the agent's ability to hang up at all).

### C. Retell's ears, mouth and switchboard, our brain
Retell's "custom LLM" mode: Retell handles the phone and the voice, and sends each caller line to our server, which runs the model and replies. Keeps Retell's turn-taking and gives our code the hang-up and transfer decisions. Still tied to Retell's platform and pricing.

### D. Bake-off: build A, use B as the baseline, keep the winner
Most of the work (tool server, mock ServiceTitan, prompts, evals) is shared either way. Add a thin Retell config (hours) as the baseline to beat, build A properly, score both on the same tests plus 10 real calls each, keep one and delete the other folder.

### Looked at and not chosen
**Vapi** is a managed platform like Retell where you pick your own speech vendors: the same trade as Retell, with no head start. **Pipecat** is free open-source software for building voice agents, like LiveKit: the same trade as LiveKit, with less sign that Revin uses it. Neither does anything A or B doesn't.

## Context

| | A. LiveKit, ours | B. Retell, all | C. Retell + our brain | D. Bake-off |
|---|---|---|---|---|
| Control of rules and hang-ups | Full | Partial | Full | Full in the winner |
| Turn-taking quality | Ours to tune | Polished, fixed | Polished, fixed | Measured, not guessed |
| Effort | Most (worker exists from the earlier practice agent) | Least | Middle | A plus a few hours |
| Cost per minute | about $0.065 (SOURCE pricing, LLM ASSUMED) | about $0.11 | about $0.10 (ASSUMED) | A's |
| Fixed monthly | $0 LiveKit (free plan) + Vultr we already pay for | $0 | $0 | same |
| Signal to Revin | Their voice team appears to use LiveKit (one job post, since removed) | Assembling a vendor product | Middle | Shows we measured |

Things that hold for every option:

- **One phone number.** A new NYC Twilio number (212, 646, 718, 347, 917...), $1.15 a month (SOURCE). The old 678 number lives inside Retell's own Twilio account and can't be moved or pointed at LiveKit (SOURCE: Retell staff, exploration 004), so it can only serve Retell; in a bake-off it is the Retell test line, and the NYC number goes to the winner. Booking texts from the NYC number need a one-time business texting registration (A2P 10DLC, about $20 and up to 5 business days, SOURCE), so it should start early.
- **No $50 LiveKit plan for the demo.** That plan only keeps LiveKit-hosted agents awake; our program runs on our own always-on Vultr server, so the free plan works. Production would need it: the free plan's 1,000 phone minutes cover only about 285 calls a month (exploration 003). The Vultr server (2 CPUs, 2 GB memory) is short on memory for 5 calls at once; expect 2 to 3 (ASSUMED until load-tested). Moving it to 4 GB costs $20 a month, billed hourly (SOURCE: Vultr). It also reboots itself at 4:30 or 5:30 a.m. New York time for updates, so the program restarts on boot and a health check alerts if it doesn't. Call number 6 goes to a safety line and voicemail in the demo, and to a short hold with a callback offer in production (exploration 003).
- **Backup.** Twilio can send calls to a second destination, but only if the first one's phone connection is completely dead (exploration 004). If our program crashes while LiveKit itself is up, calls won't fail over on their own; a health check has to flip the number to the backup. That check is part of the build.
- **Tidy repo.** Both voice layers sit in `voice/livekit/` and `voice/retell/`, hold no rules or prompts, and the losing one is deleted in one step (docs/architecture.md).

## Recommendation

D: build our own LiveKit stack (A) as the product, with a quick Retell setup on the existing 678 number as the baseline it has to beat, then keep whichever wins on the same tests and 10 real calls. This gets the control and Revin fit of A, with evidence instead of a guess, for a few extra hours; it changes to B or C if our stack can't match Retell's turn-taking or reply speed on real phone calls.

## Research notes

Revin and LiveKit
- Supports LiveKit: a Revin posting says "Partner with the voice team on real-time agent behavior (LiveKit + OpenAI Realtime)" and lists LiveKit, Daily and Twilio Voice as a bonus (SOURCE: [Built In NYC listing](https://www.builtinnyc.com/job/ai-systems-engineer/9252017), read 2026-11-02; the listing was removed 2026-10-29).
- Does not name a stack: Revin's FDE posting ([aitrainer.work](https://aitrainer.work/jobs/startups/full-time/forward-deployed-engineer-us-revinai)) and [revin.ai](https://www.revin.ai). LiveKit's [customers page](https://livekit.com/customers) does not list Revin. So "Revin builds on LiveKit" rests on one job post; say "your voice team uses LiveKit, as I read your listing" in the demo, not a flat claim. Revin also uses OpenAI Realtime, while Bobert (an earlier personal practice project: a voice agent for a made-up HVAC company, built before this assignment; the name is the project's, not a person's) uses separate speech-to-text, model and text-to-speech.

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
- MEASURED in Bobert (laptop to Deepgram, Anthropic, Cartesia; clean TTS clips, one scripted run each): Haiku first audio about 1.0 s on plain turns, 2.3 s on tool turns; GPT-4.1 mini 2.1 s median; STT final 0.2 to 0.4 s after the harness bug was fixed (`docs/history/from-other-projects/bobert/latency-notes.md`).
- Not measured: real 8 kHz phone audio, and Retell at all. Retell's about 600 ms is repeated across its own blog posts (180, 200 and 80 test calls, inconsistent) with no independent benchmark (SOURCE: [retellai.com](https://www.retellai.com/), unverified). Comparing 1.0 s with 600 ms is not apples to apples; #9 is the first fair test.

Gaps
- Telnyx current inbound rate: sources disagree ($0.0032 to $0.0075/min); check Telnyx's page if Telnyx is chosen.
- Retell's call-transfer doc page returned 404; the transfer bypass comes from Bobert's architecture notes (`docs/history/from-other-projects/bobert/architecture.md`), not a fresh read of Retell's docs.
- Effort estimates are ASSUMED. Tools used: built-in search and fetch only; no paid fallback calls.
