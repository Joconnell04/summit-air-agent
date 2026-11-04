# Brief: where things stand, and the live calls

Written at the end of the check-ins while Jackson was away (about 23:00 to 01:10). What's live, what's open, what was decided without him, and what needs him.

## Needs Jackson

1. **The 10 live calls: the number is ready now.** (646) 624-0418, script in `evals/live-calls.md`. Everything below is deployed and a scripted test call passed at 01:10. Recommend calling now rather than waiting for the last fix pass (below): real-call evidence is the biggest gap, and the remaining fixes are small. Three things to listen for, seen on the test call:
   - The relative day is wrong just after midnight: a November 4 visit was called "tomorrow" at 1 AM. Fixed in the open pass; daytime calls aren't affected.
   - The first words of the goodbye may be clipped when the call ends ("...for calling Summit Air").
   - "Sure, Pat" ran together; listen for whether names after "Sure" sound natural.
   If a caller asks for a person, the transfer rings your cell.
2. **After the calls, one feedback pass.** Say what felt off; I'll map each complaint to a setting in `docs/tuning.md` (end-of-turn wait, interruption sensitivity, reply length cap, sampling temperature, speaking rate) and try the obvious fix first. One is already queued: a lower turn-detector maximum (now 1.5 s), since on about one turn in seven the detector waited the full 1.5 s on a finished line (MEASURED, PR #98).

Nothing else needs a decision. No balance needs a top-up: no run failed for credit. Cartesia overages are on at $65 per 1M credits; added Cartesia spend stays under $20. Tonight's paid runs come to about $27 (from the lanes' reports: round 3 and 4 about $16.60, the side-model check $2.50, end-of-turn runs $2.07, the listener batch $1.60, speaking rate and ZIP $1.25, the rest under $1 each), with up to $8 more for the last fix pass.

## Your questions (answered)

**Cartesia's managed voice agents.** Looked at in exploration 013 and left out. They'd host the whole call on Cartesia, with their own model list and a model-driven end-call tool, so our server couldn't force the exact safety lines or decide hang-ups and transfers, the rule everything here rests on. Templating would be weaker too: per-agent prompts and settings, nothing like our trade layer that each company folder builds on. Cartesia is also ending hosting for its code-first Line agents on 2026-12-01 (SOURCE: Cartesia's migration page via exploration 013).

**Cartesia for speech-to-text.** Possible: Cartesia's Ink-2 is a streaming speech-to-text model with built-in turn detection and up to 100 keyterms (SOURCE: [Cartesia keyterms docs](https://docs.cartesia.ai/use-the-api/stt/keyterms.md)). We use Deepgram Nova-3 because it was chosen on phone audio first, has keyterms we already tuned for local street names, and its endpointing is what our end-of-turn timing is measured against. We found no phone-audio (8 kHz) guidance for Ink-2. Worth one audio-rig comparison after the live calls (about $2); not before, since it changes the ears under every safety and read-back rule.

**Why Cartesia for speaking but not hearing.** Each was picked as the best at its own job: Cartesia sonic-3 for natural, fast speech with per-call speed and a pronunciation dictionary; Deepgram for accurate hearing of phone audio with keyterms. One vendor for both would save a contract, not latency.

The reasons for leaving out Cartesia's managed agents are now decision 065. The Ink-2 comparison you approved is running in its own lane (under $3): street names, ZIPs and phone digits, safety phrases and end-of-turn timing at phone quality. I'll only bring it to you if it clearly beats Deepgram.

**Picking a model by how hard the task is.** We pick a model per job, not per turn:
- Live replies: Haiku 5.5, thinking off, on every turn. It's the fastest Claude model, and the agent's tokens cost about half a cent a call (MEASURED: $0.25 across 52 standard-suite calls).
- The easiest turns use no model at all. The safety lines, the read-back and the booking confirmations are spoken straight from the server, so the plainest kind of routing is already in place.
- Side model: one narrow yes-or-no question on each caller line, answered off the reply path.
- Technician summary after the call: Sonnet 5.5 with thinking, because nobody is waiting.
- Owner rule check: Haiku. Evals: Haiku plays the caller and Opus grades.

There's no switching between models mid-call, and I don't think it pays. A bigger model on hard turns adds a slower first word and changes how the agent sounds partway through a call. A cheaper model on easy turns saves a fraction of a cent. Exploration 014 rejected both (idea 10).

The one place I'd test is the summary: Haiku with thinking against Sonnet on about 20 eval transcripts, scored by the server's line-by-line check. Sonnet costs roughly 20 times Haiku per token (SOURCE: config/models.yaml prices). At about 2.5 cents a summary (ASSUMED), that's about $40 a month at full volume, more than the live brain's $30, and docs/cost.md doesn't count it yet. The test costs cents. The eval grader is the other candidate (Haiku first, Opus only on failures), but evals cost about $1.70 a run today and the grader was matched to your labels on Opus, so it isn't worth it unless evals start running on every change.

## Live on the server

Health checks pass. Every lane, with its pull requests and decisions, is in [lanes.md](lanes.md), generated from GitHub by `make lanes` after each merge. Since the morning brief: the bench shows calls as recorded ([#101](https://github.com/Joconnell04/summit-air-agent/pull/101), decision 064), deployed at 16:24 UTC.

Signing in: https://45-77-105-247.sslip.io/owner (owner view) and https://45-77-105-247.sslip.io/bench (debug bench), both as `jackson`; the password is in the macOS Keychain (`security find-generic-password -a summit-air -s owner-admin-password -w`). The side model is on. Warm transfer and recordings are on.

## Still open

- **Address lookup, preferred names, five easy scenarios** (new lane): a server address lookup finds the ZIP from "248 Duffield St" (one match fills it and the read-back confirms; several ask which borough; none asks to repeat or spell; a caller's ZIP that disagrees with the lookup gets read back and settled before booking). Callers who ask to be called "Dr. Patel" are, for the call and next time. Five plain scenarios join the standard suite. Ends with a two-sample eval run.

- **Decisions 057 to 064 were missing from DECISIONS.md** (the design trail had them; the entries never landed). Restored from the session log, word for word.
- **Last fix pass (round 3 lane), now [#102](https://github.com/Joconnell04/summit-air-agent/pull/102):** the two-sample reference is 93% (96 of 103 judged; every must-pass code check passes; $3.27). Of the 7 misses, 2 were server phrase bugs fixed after the run ("no babies, older people or medical equipment" losing its negation; "do you smell gas, do I?" firing the gas lines), 1 check is too strict, and 4 are the agent re-asking. Review held it back on two safety regressions, both reproduced and now being fixed in the lane. The new "no" carry dropped "No heat, baby in the house" and "No heat, elderly mother lives with me" from Urgent to High. The new hypothetical rule turned "What do I do? I smell gas" into a check question instead of the gas lines. It merges only after those are fixed, pinned as tests, and the must-pass and safety scenarios pass again. The original scope: the agent slips still seen in eval runs (re-asking given details, an apartment building declined, an invented fee line, a repeated 911 line, flagging instead of booking after hours, warming advice for a baby), the after-midnight "tomorrow" bug, and two at-risk phrasings ("just me at home", "a cane"). Then one eval run with two samples per scenario becomes the reference. Main's eval check is red only on that stale reference until then. Deploys when it lands.

## Decided without Jackson

- **Eval measurement moves to two samples per scenario.** Five single-sample runs on nearly the same code scored 91, 96, 90, 90 and 85%, so about plus or minus 5 points is noise. If the two-sample rate sets the floor below 90%, it's a change in measurement, not in quality.
- **One reference run per batch, not per PR.** Every merge that touches prompts, company settings or scenarios made the reference stale at about $1.70 a run.
- **The side model was switched off, checked, and switched back on.** The evals had never run with it; with and without on 26 scenarios, it raised no false alarms in 46 calls (decision 056).
- **Speaking rate:** review caught false triggers ("you don't need to speak slower", "send someone faster", "my heat is slow, I live on Main Street"); fixed before merge.
- **Accounts:** an old deploy with only the shared token would have locked everyone out; it now says exactly what to set. A caller who objects to recording is never recorded, consent switch on or off.
- **Cold handoff:** "Can I talk to someone now?" no longer triggers it; it needs "right now", "right away" or a repeated request. Swearing about the furnace doesn't count as anger at the call. A gas caller who demands a person after the safety lines is handed off (supersedes decision 051's "never on a safety call").
- **Debug bench:** phone numbers keep their last four digits; the full number never appears in a URL or a replay file. The publish gate runs the must-pass evals only, not the two-caller race.
- **ZIP rule:** a phone number containing the ZIP's digits doesn't count as the ZIP.
- **Recording-notice wording** lives in the company folder, not app code.
