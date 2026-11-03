# What the Summit Air phone agent costs

Every number is labelled SOURCE (link at the end, read 2026-11-02), MEASURED (counted or run by us) or ASSUMED (our estimate, to be replaced by Summit Air's real figures). Prices are list prices before tax.

## If the budget were halved

Cheaper parts cut the monthly bill from about $648 to about $388, and to about $327 with the cheapest voice; that is roughly half. We would give up Spanish speech recognition (Spanish callers get a call back), the premium voice, text confirmations, and the one-minute hold with call-back for a sixth caller. We would keep the safety script, the booking rules, the priority flags and five lines at once, because those are the six requirements and the part Summit Air cannot do without. The other way to halve it is to answer fewer minutes: only after-hours and overflow calls (about 35% of calls, ASSUMED) cost about $260 on the recommended setup.

## The numbers we track

| Metric | What it means |
|---|---|
| Cost per call minute | Usage charges for one minute on the phone: phone line, speech to text, the voice, the model. Monthly fees excluded. |
| Cost per call | Total monthly cost divided by calls answered. |
| Fixed monthly cost | Charges that stay the same whether 10 or 4,000 calls arrive: platform plans, the server, the number. |
| One-time setup cost | Registration and sign-up fees paid once. Build labour is not priced here. |
| Cost per booked job | Total monthly cost divided by jobs the agent booked. |
| Cost of a missed call | The job value lost when a caller reaches no one. |

Volume used for every row (ASSUMED, high side, from `docs/explorations/003-capacity-and-cost.md`; at half the volume the usage part halves and the fixed part stays): 4,000 calls a month (150 a business day, plus about 12% after hours, SOURCE ServiceTitan for the after-hours share, plus surge days), 3.5 minutes each on average, so 14,000 minutes; 40% of calls end in a booked job, so 1,600 jobs (SOURCE: HVAC booking rates run 38% overall and 59% for shops with 25 or more technicians).

## Three ways to run it

| | Recommended | Half budget | Managed (Retell) |
|---|---|---|---|
| What it is | Our own stack: Twilio number, LiveKit, Deepgram Flux (speech to text), Claude Haiku 5.5, Cartesia voice, our server | Same shape with cheaper parts: Twilio direct to LiveKit, Deepgram Nova-3 English, Deepgram Aura-2 voice, no texts | Retell end to end, Claude 4.5 Haiku |
| Per call minute | about $0.040 | about $0.024 | $0.11 |
| Fixed per month | about $122 | about $71 | about $2 |
| Monthly at 14,000 minutes | **about $648** | **about $388** | **about $1,566** |
| Per call / per booked job | $0.16 / $0.41 | $0.10 / $0.24 | $0.39 / $0.98 |
| One-time setup | $19.50 to $61 (text registration) | $0 | about $10 number fee, plus $19.50 to $61 if texting |
| Lines at once | 5 (15 for about $130 more) | 5 | 20 included |
| What you get | Hold and call-back for overflow, texts, Spanish, safety and booking rules enforced in our own code, a failover number | The six requirements, five lines, voicemail for overflow, Twilio's voicemail if the server is down | Fastest to launch, less to run; weaker control of hang-up and transfer, so the safety hand-off depends on prompt order |

How the monthly totals add up (ASSUMED volume, SOURCE rates):

- Recommended: phone $176, LiveKit Ship plan $86 ($50 plus $0.004 a minute past 5,000), Deepgram $109, Cartesia $189 (Startup plan, 5 simultaneous voices), Claude $30, a small side model $5, texts $31 plus $1.50 registration, server $20 (Vultr 2 vCPU, 4 GB, SOURCE).
- Half budget: phone $49, LiveKit $86, speech to text $67, voice $131, Claude $30, side model $5, server $20. The cheapest voice we found, Amazon Polly Neural at $16 per million characters, would bring the voice to $70 and the total to about $327; it sounds more robotic and is untested.
- Managed: $0.11 a minute is Retell's list sum ($0.055 platform, $0.015 voice, $0.025 Claude Haiku 4.5, $0.015 phone), plus $2 for the number and $24 for texts at $0.01 each.
- Call recordings (voice/livekit/README.md, "Recordings"): LiveKit bills audio-only recording as transcode minutes, 600 included on Ship then $0.005 a minute (SOURCE: https://livekit.com/pricing), about $0.01 for a two-minute call (ASSUMED length), so about $42 a month at 150 calls a day (9,000 minutes, 8,400 past the included 600; ASSUMED volume, docs/brief.md); not in the LiveKit $86 above. The Build plan's 60 included minutes are a hard cap. The bucket holds each file only until the next jobs run, so its storage is negligible.
- Warm transfers: the person's leg is an outbound Twilio call, $0.014 a minute (SOURCE: docs/explorations/003-capacity-and-cost.md), for as long as they talk with the caller.
- Deepgram keyterms for local street names (voice/livekit/README.md, "Local names") add $0.0013 a minute (SOURCE: deepgram.com/pricing), about $12 a month at 150 two-minute calls a day (ASSUMED volume); not in the Deepgram $109 above.
- Raising the Recommended setup from 5 to 15 lines adds about $130 a month (Cartesia Scale $299 instead of $189, Vultr 4 vCPU 8 GB $40 instead of $20). Using Claude Haiku 4.5 instead of 5.5 adds about $205 a month. LiveKit's free plan covers a pilot of about 1,000 minutes (285 calls) for roughly $47 a month, but not full volume.

## Phone and text rates (Twilio, SOURCE)

| Item | Rate |
|---|---|
| Local number | $1.15 a month |
| Inbound call, local | $0.0085 a minute; through our call counter add $0.004 a minute for the connection to LiveKit |
| Inbound over a SIP trunk straight to LiveKit | $0.0034 a minute |
| Outbound call | $0.014 a minute |
| Text, each way | $0.0083 per segment plus a carrier fee of about $0.0025 to $0.007 |

**Business texting needs A2P 10DLC registration.** Twilio's rule: anyone sending texts from an application over a US local number must register; unregistered traffic gets extra carrier fees and filtering. Fees: brand $4.50 (low volume) or $46 (standard, includes vetting), campaign vetting $15 once, then $1.50 a month for low volume or about $10 for standard. Approval takes time: customer profile 72 hours or more, a brand sent to manual review seven business days or more, campaign review up to five business days. Our estimate is one to two weeks if clean, up to four if not (ASSUMED), so registration starts first and is not on the critical path for the demo.

## What a missed call costs

A missed call is worth about $165 to $570 (derived: average repair job x share of callers who would have booked x share who do not call back). Inputs: $1,205 average repair job revenue in 2025 (SOURCE Housecall Pro) or $350 national average repair price (SOURCE Fixr, Oct 22, 2026; Angi); 59% of calls booked in 25+ technician shops (SOURCE ServiceTitan); 80% of callers who reach no one do not call back (ASSUMED; the often-quoted 85% is not a study, and a CallRail 2025 survey found 82% of consumers say they would call a competitor, which is self-reported, SOURCE via ainora). A vendor's own floor is about $130 per missed call ($350 x 38%). At those values the recommended setup pays for itself with 2 to 4 saved calls a month, the half budget with 1 to 3, and Managed with 3 to 10.

## Sources

- Twilio voice https://www.twilio.com/en-us/voice/pricing/us ; SIP trunking https://www.twilio.com/en-us/sip-trunking/pricing/us ; SMS https://www.twilio.com/en-us/sms/pricing/us ; A2P brand fees https://www.twilio.com/docs/trust-hub/registrations/a2p-10dlc-brand ; onboarding timelines https://www.twilio.com/docs/messaging/compliance/a2p-10dlc/direct-standard-onboarding ; the $15 vetting and monthly campaign fees come from Twilio's fee article, https://support.twilio.com/hc/en-us/articles/1260803965530 (blocked to us; read through a search summary)
- LiveKit https://livekit.com/pricing and https://docs.livekit.io/deploy/admin/quotas-and-limits.md ; Deepgram https://deepgram.com/pricing ; Cartesia https://cartesia.ai/pricing ; Claude https://platform.claude.com/docs/en/about-claude/pricing ; Retell https://www.retellai.com/pricing ; Vultr https://api.vultr.com/v2/plans ; Polly https://aws.amazon.com/polly/pricing/
- Housecall Pro 2025 repair revenue https://www.housecallpro.com/resources/hvac-industry-trends/ ; repair price https://www.fixr.com/articles/hvac-service-how-much-does-hvac-repair-cost ; missed-call analysis https://ainora.lt/blog/hvac-service-call-statistics-2026 ; ServiceTitan booking rates https://www.servicetitan.com/blog/data-call-booking-rates and https://www.servicetitan.com/guides/call-trends-in-the-trades
