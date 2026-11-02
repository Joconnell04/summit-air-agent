# Scaling: how many calls at once, and how to raise it

The demo handles about 3 calls at the same moment. Raising that to 5, or to the 15 a bad heat-wave morning could need, changes plans and server size, not the stack: the same Twilio number, LiveKit, Deepgram, Claude, Cartesia and tool server, on bigger tiers. This page lists every limit in the chain and the plan for each step up. Call volume estimates and the math behind them are in docs/explorations/003-capacity-and-cost.md.

## How many we actually need

At about 150 calls a day (ASSUMED, exploration 003), the busiest hour has 1 to 2 calls going at once. With 5 lines, about 1 caller in 170 would find them all busy on a normal day and about 1 in 40 on the first heat wave; on a 3x surge morning it is 1 in 4. About 15 lines keeps that near 1 in 100 even on a 5x morning (Erlang B, MEASURED by script in exploration 003). So 5 is enough for most days and the demo; 15 is the production target.

## Every limit in the chain

| Part | What we're on | Limit at once | Source | Step up |
|---|---|---|---|---|
| **Cartesia (the voice)** | Pro, $5/mo | **3 voice streams. The bottleneck today.** A call only uses a stream while the agent is speaking, so 4 or 5 calls often fit, but 4 agents speaking at the same instant would make one wait. | SOURCE: cartesia.ai/pricing | Startup $49/mo: 5 streams. Scale $299/mo: 15 |
| **Our server** | Vultr 2 vCPU, 4 GB, $20/mo | About 5 calls (ASSUMED until load-tested with `lk perf agent-load-test`) | LiveKit sizing guide: 4 CPUs and 8 GB for 10 to 25 calls (SOURCE, exploration 003) | 4 vCPU, 8 GB: $40/mo, about 10 to 25 calls. Two of them for a hot spare |
| **LiveKit** | Build (free), our own worker | No cap on calls at once found for a self-hosted worker; the cap is **1,000 phone minutes a month**, about 285 calls | SOURCE: livekit.com/pricing | Ship $50/mo: 5,000 minutes, then $0.01/min |
| **Deepgram (the ears)** | Pay as you go | About 150 streams | SOURCE: Deepgram changelog Feb 2026, third-party trackers | Not a limit at this size |
| **Claude Haiku (the brain)** | Anthropic Start tier | 4,000 requests a minute; a call makes a few a minute | SOURCE: Anthropic rate limits, exploration 003 | Not a limit at this size |
| **Twilio (the number)** | Elastic SIP Trunk | No cap found for incoming calls at this size (ASSUMED) | Twilio trunking docs | Not a limit at this size |

Correction to an earlier note: LiveKit's free plan limits 5 calls at once only for agents LiveKit hosts. Our agent runs on our own server, so the free plan limits monthly minutes, not calls at once.

## Plan to reach 5 at once (a full rollout for Summit Air on a normal day)

1. Cartesia Pro to Startup ($49/mo): 5 voice streams.
2. Load-test the server at 5 calls. If it struggles, resize to 4 vCPU, 8 GB ($40/mo, a few minutes of downtime).
3. LiveKit Build to Ship ($50/mo) once real traffic passes about 1,000 minutes a month.
4. No code changes. The concurrency cap is one setting on the worker.

Added cost: about $45 to $135 a month on top of the demo setup (SOURCE prices above).

## Plan to reach 15 at once (a bad heat-wave morning)

1. Cartesia Scale ($299/mo, 15 streams), or switch the voice to a provider with higher limits behind the same interface (the voice is one profile in config).
2. Two 4 vCPU, 8 GB servers ($80/mo) behind the same LiveKit project: calls spread across both, and one keeps answering if the other fails.
3. LiveKit Ship, billed by the minute past 5,000.
4. Still the same stack and code; the per-call cost is in docs/cost.md.

## What the next caller hears when every line is busy

When the worker is at its limit, it still answers, plays a short pre-recorded message (so it doesn't need a voice stream) with the safety line first ("If you smell gas, hang up and call 911 from outside"), then takes a voicemail that lands on the owner view as a flagged callback. In production this becomes a short hold with the option to be called back. Nobody gets silence or a dead ring.
