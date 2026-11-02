# 003. Capacity, overflow and cost

Issue: none given. Labels: SOURCE (links in section 6, read 2026-11-02), MEASURED (run today), ASSUMED. Covers C-26, C-29, C-38. The owner-facing cost page is `docs/cost.md`.

## 1. Question

How many calls are live at once at a 40-technician NYC shop, is a 5-call cap acceptable, what should call number 6 hear, and can the always-on Vultr box (2 vCPU, 1.9 GiB) carry the tool server and a LiveKit Agents worker for 5 calls with headroom?

## 2. Why it matters to Summit Air

The kickoff says phones "ring off the hook" on the first heat wave or cold snap (SOURCE: `docs/assignment.md`), so Paul will ask what happens when many people call. `docs/users.md` ranks the caller in danger first, and a busy tone is the one failure nobody sees. `docs/brief.md` and `docs/spec.md` are stubs; the volume below should become a brief line once confirmed. Phase 4 requires a backup number and failure alerts.

## 3. Evidence and options

### Concurrency

Erlang B: B(N, A) = (A^N / N!) / sum over k = 0..N of (A^k / k!), computed as B(0) = 1, B(k) = A*B(k-1) / (k + A*B(k-1)). A = busy-hour calls x minutes per call / 60. Blocked calls are lost and retries ignored, so it understates blocking (SOURCE: metricgate). My code reproduces published checks: 5 erlangs need 11 lines for 1%, 10 need 18 (MEASURED).

Inputs. Normal: 40 techs x 4 jobs a day (SOURCE 3 to 5, in 001) x 50% starting as inbound calls (ASSUMED) / 59% booking rate for 25+ tech shops (SOURCE, in 001) = 136, rounded to 150 for status calls and spam (ASSUMED). No public per-shop benchmark exists (SOURCE: ainora says so); a vendor's illustrative 5-technician shop with 200 calls a month scales to about 73 a day for 40 techs (SOURCE, weak), so 150 is the high side and safe for capacity. Surge: +20% on the first heat wave or cold snap (SOURCE, in 001); 3x and 5x are ASSUMED (vendor claims, unverified). Busiest-hour share 12%, 15%, 20% (ASSUMED; no public hourly data found). AI call length 4 min, 3 in surge (ASSUMED; a human CSR target is 3 to 5 min, SOURCE job listing).

| Scenario | Calls/day | Peak-hour calls | A | Callers hitting busy at 5 / 10 / 20 lines | Lines for 1% |
|---|---|---|---|---|---|
| Normal | 150 | 18 | 1.2 | 0.6% / ~0 / ~0 | 5 |
| First heat wave or cold snap | 180 | 27 | 1.8 | 2.6% / ~0 / ~0 | 6 |
| Severe surge (3x) | 450 | 90 | 4.5 | 24% / 1.0% / ~0 | 11 |
| Extreme surge (5x) | 750 | 150 | 7.5 | 45% / 10% / 0.007% | 15 |
| Extreme, bursts at 2x the hourly rate | 750 | 300 | 15 | 69% / 41% / 4.6% | 24 |

MEASURED: script run today. Twenty callers in one minute is a burst; with a cap of 5, 15 of 20 (75%) go to overflow.

**Is 5 acceptable?** Demo: yes. Production: only on a normal day; on 3x days it turns away a quarter of peak-hour callers, the days the owner bought this for. Size production to about 15 lines. The 5 comes from vendor limits: Cartesia Startup 5 TTS streams (Scale 15), LiveKit Inference 5 STT and 5 TTS on Build, 5 LiveKit Cloud-deployed sessions on Build, which do not apply to a self-hosted worker. Deepgram allows 150 streams; Anthropic's Start tier allows 4,000 requests a minute on Haiku 5.5 (all SOURCE).

**Options for call 6.** All sit behind a webhook on our tool server that Twilio calls first; it counts live calls, frees a slot on Twilio's own call-status callback, and returns "dial LiveKit" or one of these.

| Option | How it plugs in | Effort | Cost | Risk |
|---|---|---|---|---|
| A. Voicemail to the office | Safety line, then `<Record>`; server stores clip and caller ID, flags a person | 0.5 day | $0.0085/min on the line (SOURCE) | Nobody confirms; a vulnerable caller waits |
| B. Short hold, retry | Safety line, `<Pause>` and `<Redirect>` to the gate every 10 s for about 60 s. Twilio `<Enqueue>` also works (SOURCE); Retell holds about 40 s then a fallback number (SOURCE); LiveKit has no built-in queue (community reply, Aug 2026) | 1 day | $0.0085/min while held | Callers leave after a minute (ASSUMED) |
| C. Callback and text | `<Gather>` "press 1"; server logs a task and texts back | 1 to 2 days | About $0.026 per call, 2 SMS (SOURCE) | Needs A2P 10DLC approval (weeks); staff must call back |
| D. Overflow to a second phone | `<Dial>` office or answering service. A SIP trunk lists two URIs but fails over only after 4 s of silence, not on 486 busy (SOURCE) | 0.5 day | $0.014/min outbound | Office swamped the same days |

**Recommend.** Demo: A (about 30 lines of TwiML, no text approval, shows the cap honestly). Production: B, then C, then A as last resort. Fixed safety line first ("if you smell gas, hang up and call 911"; wording signed off per C-12), a minute's hold, a keypress callback, and a second keypress for "someone without heat or cooling is elderly, a baby or ill" that flags a person and rings the on-call phone. D only if the owner asks.

**Owner answer.** "The demo takes five calls at once; a sixth caller hears a short safety message and can leave a voicemail that goes to your office. At launch we would size for a bad heat-wave morning, about fifteen at once by our estimate, and replace the voicemail with a one-minute hold and a call-back option."

### Hosting capacity

LiveKit (SOURCE): start with 4 cores and 8 GB per agent server, 10 to 25 concurrent jobs. A 30-session test on that size peaked at about 3.8 cores and 2.8 GB with Silero VAD only (derived: 0.13 core, 93 MB per session). Each job is its own process; idle processes default to the CPU count; new jobs stop at 70% CPU. The turn detector "requires more CPU and memory"; the self-hosted default `v1-mini` runs on CPU with no published size (the old text model: 396 MB disk, under 500 MB RAM). Docs advise compute-optimized, not burstable, instances.

This box (MEASURED by ssh today): 2 vCPU Cascade Lake with AVX-512, 1,961 MB RAM, 422 MB used, load 0.00, no CPU steal. Budget for 5 calls (ASSUMED unless marked): existing 422 (MEASURED), tool server 80, worker 100, five jobs at 120 (SOURCE-derived), two idle processes 240, turn detector up to 500, so about 1.9 GB, which fills RAM. CPU is fine, about 0.65 of 2 cores.

**Verdict: too small for 5 calls with headroom.** Expect 2 to 3 comfortable calls; the 6.2 GiB swap (MEASURED) keeps it alive but stalls audio. Prove it with `lk perf agent-load-test --rooms 5` (SOURCE).

| Option | Effort | Cost (Vultr API, today) | Risk |
|---|---|---|---|
| H1. Keep box, cap at 3 | 0 | $0 | Memory spikes; shared |
| H2. Upgrade in place to vc2-2c-4gb | 15 min | $20/mo, $0.027/h (+$5 if now vc2-2c-2gb at $15, ASSUMED) | Still shared, 2 vCPU; upgrades cannot be undone (SOURCE) |
| H3. New vc2-4c-8gb | 2 h | $40/mo, $0.055/h (LiveKit's baseline); high-performance Intel 4c/8GB is $48 | One more box |
| H4. Worker on LiveKit Cloud | 0.5 day | Ship $50/mo plus $0.01/min past 5,000 | Per-minute cost |

**Reboots.** MEASURED: `/etc/apt/apt.conf.d/52epic-reboot` sets Automatic-Reboot true at 09:30 and the clock is UTC, so reboots land at 05:30 EDT or 04:30 EST; five in 40 days (Sep 5, 11, 24, Oct 9, 14). That is outside business hours, but the file is root-owned, we have no sudo, and it looks like another project's. A dedicated box (H3) lets us choose.

**Make it safe.** (1) Start on boot with user-crontab `@reboot` plus a per-minute watchdog (user crontab works, MEASURED; Bobert (an earlier personal practice project: a voice agent for a made-up HVAC company, built before this assignment; the name is the project's, not a person's)'s `watchdog.sh` is the pattern). systemd user units need lingering, which is off (MEASURED); `loginctl enable-linger` is allowed for yourself by default (secondary source, untested here). (2) Twilio: the number's webhook is the gate; "Primary handler fails" is a Twilio-hosted TwiML Bin that apologises, then dials the office or takes a voicemail (SOURCE). The gate dials LiveKit only if the worker heartbeat is under 30 s old. Whether LiveKit answers a call with no worker, leaving silence, is untested. (3) Alert: the watchdog pings Healthchecks.io each minute only when `/health` and the heartbeat are good; a missed ping emails Jackson (free tier, 20 checks, third-party summary). UptimeRobot's free plan is non-commercial. Add a daily early-morning test call (outbound $0.014/min, SOURCE).

## 4. Recommendation, and what would change it

Upgrade to H2 (H3 if the load test shows swap at 5 calls) before the demo; demo with a 5-call cap and overflow A. Production: H3, a 15-line cap (Cartesia Scale $299/mo, +$110 over Startup, derived), overflow B/C/A. The Build plan is enough for the demo: the 10 to 20 s cold start in decision 04 applies to agents deployed to LiveKit Cloud, not our always-on worker (SOURCE quotas). Production needs Ship ($50/mo) because Build caps third-party SIP at 1,000 minutes a month, about 285 calls.

Changes if: Paul's volume is far from 150 a day; the load test passes at 5 on 2 GB; LiveKit answers with no worker (then the heartbeat check is mandatory); or a Twilio SIP trunk straight to LiveKit ($0.0034/min against $0.0125 through the gate, about $127/mo cheaper at 14,000 minutes) matters more than hold and callback control.

## 5. Open questions for Jackson

1. Is 150 calls a day a fair stand-in, or ask Paul?
2. Resize now (+$5/mo, short restart) or build a dedicated $40 box?
3. Does `52epic-reboot` belong to the other project, and may it stay?
4. Update decision 04's Ship-plan reasoning, since Build suffices for a self-hosted demo?
5. Who gets overflow voicemails and urgent keypresses: office line or on-call phone?
6. Approve a small paid load test (5 simulated rooms, a few dollars of speech and model usage)?

## 6. Sources

- Erlang B: https://metricgate.com/docs/queue-erlang-b-loss/ ; https://www.nojitter.com/telecommunication-technology/how-to-size-your-sip-trunks
- LiveKit: sizing https://docs.livekit.io/deploy/custom/deployments.md ; worker options https://docs.livekit.io/agents/server/options/ ; turn detector https://docs.livekit.io/agents/logic/turns/turn-detector.md ; quotas https://docs.livekit.io/deploy/admin/quotas-and-limits.md ; pricing https://livekit.com/pricing ; load test https://docs.livekit.io/reference/developer-tools/livekit-cli/perf/ ; queue thread (Aug 5, 2026) https://community.livekit.io/t/restrict-voice-agent-to-5-concurrent-calls-with-automatic-queueing-for-additional-callers/1803 ; Twilio inbound https://docs.livekit.io/telephony/accepting-calls/inbound-twilio/ ; 2 GB failure report (closed Jul 13, 2026) https://github.com/livekit/agents/issues/4483
- Twilio: voice pricing https://www.twilio.com/en-us/voice/pricing/us ; SIP pricing https://www.twilio.com/en-us/sip-trunking/pricing/us ; Dial https://www.twilio.com/docs/voice/twiml/dial ; failover https://www.twilio.com/docs/sip-trunking ; Enqueue https://www.twilio.com/docs/voice/twiml/enqueue
- Retell concurrency https://docs.retellai.com/deploy/concurrency ; Cartesia https://cartesia.ai/pricing ; Deepgram https://deepgram.com/pricing ; Anthropic limits https://platform.claude.com/docs/en/api/rate-limits
- Vultr plans (API) https://api.vultr.com/v2/plans ; resize https://docs.vultr.com/products/compute/cloud-compute/management/resize-instance
- Ubuntu updates https://ubuntu.com/server/docs/how-to/software/automatic-updates/ ; lingering (secondary) https://linux.codidact.com/posts/292897 ; monitoring (secondary) https://freetier.co/compare/healthchecksio-vs-uptimerobot
- Volume (vendor, weak) https://ainora.lt/blog/hvac-service-call-statistics-2026 and https://vida.io/blog/hvac-call-answering-service (Jun 8, 2026) ; surge claims https://www.fusioncx.com/blog/home-improvement/hvac/avoid-missed-calls-with-answering-services-for-hvac-contractors/
- Local: 001, decisions 04 and 05, `from-other-projects/bobert/architecture.md`.

Tools: built-in search and fetch, curl (Vultr API), read-only ssh to `vultr-personal`. No Exa or Firecrawl calls, no paid calls.
