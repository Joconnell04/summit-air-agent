# Decisions

One entry per real choice, newest first. Each entry: what was decided, why, what else was considered, what would change it, and a link to the discussion or exploration behind it.

<!-- Template
## NNN. Title
Decided: ...
Why: ...
Considered: ...
Would change if: ...
Discussion: docs/ai-process/transcripts/... or docs/explorations/...
Issue: #N
-->

## 068. Deepgram Nova-3 stays as the ears; Cartesia Ink-2 stays out of the bake-off

Decided: the live stack keeps Deepgram Nova-3 for speech-to-text. Cartesia's Ink-2 doesn't replace it and doesn't join the bake-off. Ink-2 stays in `config/models.yaml` as a measured candidate (`voice.stt_candidates`), with the STT bench to rerun it (`make stt-bench`).
Why: on the same caller audio at phone quality, Ink-2 was slower on every turn and worse on local names (MEASURED, evals/rounds/stt-ink2-01.md, results file evals/rounds/stt-ink2-01-data/20261104-110914-ink-2.json):
- Final transcript a median 1.08 s after the caller's last word, against 0.44 s for Nova-3 (slowest 10%: 1.66 s against 0.50 s; 60 runs per arm). LiveKit can't use Ink-2 without waiting for its own turn end, so this is about 0.6 s more on every turn.
- Street names and addresses: 28 of 32 against 32 of 32.
- ZIPs and phone numbers: tied, 16 of 16 each. Spelled names: tied, 3 of 4 each. Safety phrases: 8 of 8 for Nova-3, 7 of 8 for Ink-2.
- Ink-2 ended the turn mid-number on all 4 runs of a phone number said with 1.5 s pauses.
- Cost: $0.0117 a minute for Ink-2 at our current overage against $0.0090 for Nova-3 with keyterms (SOURCE: Cartesia and Deepgram pricing pages, read 2026-11-04).
The test cost about $0.28, of which $0.13 was Cartesia.
Considered: Ink-2 with our own turn detector in place of Cartesia's (the installed plugin sends a final transcript only when Ink-2 ends the turn, so the wait stays); one vendor for voice and ears (saves a contract, not latency).
Would change if: Cartesia sends final transcripts on request, or a rerun of the bench shows Ink-2 within 0.1 s of Nova-3 with no loss on street names, digits or safety phrases.
Discussion: evals/rounds/stt-ink2-01.md, PR #123, issue #122

## 067. Every lane has a GitHub issue, and the lanes page is generated

Decided: each build lane gets an issue labeled `agent` before it starts, including lanes asked for in chat; its pull requests close it. `make lanes` writes docs/ai-process/lanes.md from those issues after each merge, and the brief links to it instead of keeping its own table. New ideas go in docs/later.md; docs/considerations.md stays as the closed design record, since its C-NN numbers are cited across the docs.
Why: lanes started from chat had no issue, so the board and the brief's table drifted from what was actually built. Lanes before this rule had issues backfilled (#103 to #119).
Would change if: the board itself is shown to reviewers, in which case the page is a view of it.
Discussion: PR #121

## 065. Cartesia's managed voice agents stay out

Decided: we don't build on Cartesia's hosted agents, and they don't join the bake-off. Cartesia stays as the voice only.
Why: the hosted agent runs the whole call on Cartesia, so our server couldn't force the signed-off safety lines word for word or decide hang-ups and transfers; its end-call tool is model-driven, and a flag the model can set is not a gate. It offers Cartesia's own model list rather than our brain and tool server, and its per-agent prompt and settings are no substitute for the trade layer each company folder builds on. Cartesia is also ending hosting for its code-first Line agents on 2026-12-01 (SOURCE: Cartesia's Line migration page, via docs/explorations/013-more-bakeoff-entrants.md).
Considered: Cartesia Line agents in code (being retired); Ink-2 speech-to-text inside our own stack, which is a separate question tested on the audio rig.
Would change if: Cartesia lets a hosted agent call our server before every reply and hang-up and speak our text verbatim.
Discussion: docs/explorations/013-more-bakeoff-entrants.md

## 064. Admins see everything on the debug bench

Decided: the bench shows calls as recorded, phone numbers, addresses, codes and tool arguments included. Only the replay draft, which is meant to be committed to the eval suite, stays redacted. Environment secrets still show only as set or not set. This replaces the redaction in decision 059.
Why: Jackson's direction: "the debug bench should be fully privileged to view everything". It's admin only, and debugging a wrong ZIP or a misheard number needs the real value.
Would change if: the bench is ever shared on screen with real customer calls, in which case a "presenting" mode masks again.
Discussion: Jackson's notes after the check-ins

## 063. The end-of-turn wait stays at 0.35 s; the digits wait no longer sticks

Decided: the silence wait before the agent replies stays at 0.35 s. A bug is fixed where the longer wait used after asking for a ZIP or phone number stayed on for the rest of the call.
Why: on the audio rig, 0.25 s (the lowest the turn detector accepts) cut the wait by 39 ms and the caller-heard median didn't move beyond noise, because the turn can't end before Deepgram's final transcript about 0.3 s after speech. The sticky digits wait cost about 0.55 s on every later turn of a booking call (MEASURED, evals/rounds/bakeoff-audio-02.md, $2.07).
Would change if: real calls show the 1.5 s turn-detector maximum cutting in often, which is the next setting to try after the live calls.
Discussion: PR #98

## 062. Simulated calls are saved as audio for listening

Decided: every audio-rig call is saved as one full-quality stereo file (caller left, agent right, with each reply's real pause kept) plus a timestamped transcript, in a gitignored local folder and in the private recordings bucket under `sim/`. `make sim` runs a batch on our stack and `make listen` opens a local page with stable short codes (S1, S2, ...), a player and numbered lines that seek the player, so Jackson can pause and dictate "S3, line 7" feedback. A simulated call costs about $0.15 (MEASURED, 11-call batch).
Why: Jackson can judge how the agent sounds without phoning it each time; the files keep simulated calls for later comparison.
Would change if: the debug bench hosts the listener, which it is set up to do.
Discussion: PR #95

## 061. A caller who demands a person is handed off at once

Decided: when a caller insists on a person (an explicit repeated or "right now" request, or anger in the same line as the request; swearing about the equipment doesn't count), the server decides a cold handoff: the agent says "Transferring you to a person now." and the worker transfers the call straight to the office in hours or on-call after hours, with no summary or key press. The flag and a callback are stored first, so a failed or unset transfer gives the caller the stored callback line, never silence. On a gas or carbon monoxide call the fixed safety lines come first; a caller who then demands a person is handed off. This supersedes decision 051's "never on a safety call". Calmer requests keep the warm transfer, and the owner's warm-transfer switch applies to both. The model can't trigger a transfer.
Why: Jackson asked for it after reading that Revin hands complaints, safety issues and anyone who asks for a person straight to a person; an angry caller held by an AI makes things worse.
Considered: warm transfer only (adds a delay an angry caller won't wait for); transferring on any mention of a person (fires on "last time I talked to someone").
Would change if: the office can't answer fast enough, in which case cold handoffs go to the callback queue.
Discussion: PR #90

## 060. The server speaks fixed lines itself, and a speech filter guards what the model adds

Decided: from eval rounds 3 and 4. The voice layer speaks the read-back, booking, move and cancel confirmations and a queued booking's line straight from the server, word for word, with a short model line before a booking ("Perfect, booking that now"). A speech filter drops what the model adds that the server didn't raise: read-back lead-ins, restatements of a confirmed address, ZIP or phone, tool talk, priority claims on calls the server didn't mark Urgent, unprompted hazard advice, and a second question in one turn. Each turn's server note lists what's already known so nothing is asked twice. After the safety lines a caller hears one shorter repeat at most, then the agent helps; a worry with nothing described gets the check question at most. "After 5" is promised as about 5:30 (ASSUMED). The grader keeps Jackson's wording standards as fail rules (repeated acknowledgments no longer count) but still can't fail a must-pass call alone (042).
Why: Jackson failed repeated read-backs, re-asking, stacked questions and overdone safety advice in rounds 3 and 4. The model kept doing them despite prompt rules, so the fixes moved into code; speaking server text directly also removes a model request on the slowest turn. The standard suite went from 53% to between 85% and 96% across five single-sample runs on nearly the same code (MEASURED, PR #82).
Considered: softening the grader instead (the failures were real); prompt-only fixes (tried first; the model kept slipping).
Would change if: the filter drops something a caller needed to hear, which the debug bench shows per turn.
Discussion: evals/rounds/03-judge-labels.md, evals/rounds/04-wording-standards.md, PR #82

## 059. A debug bench for engineers on the owner view's server

Decided: `/bench`, on the same server and URL as the owner view and for admins only, shows each call as a node graph (the caller's words, the exact prompt and messages the model got, tool calls and results, the server rules that fired, what the speech filter dropped, what was spoken, with timings and missing ones shown as missing), the live tuning settings with their measurements and sources, eval runs against the floor with their failures, a live view of a call in progress, a rule check per call, search, and replay of a call as a draft eval scenario. Phone numbers keep their last four digits; addresses, codes and anything key-like are hidden, and no full number appears in a URL or a replay file.
Why: the interview ends with debugging live; a page that shows why the agent did something beats reading `make trace` in a terminal, and it doubles as proof of the server rules.
Considered: extending the owner view's technical switch (mixes owner and engineer views); a separate service (another moving part).
Would change if: the bench shows real customer data beyond the demo, in which case redaction is reviewed again.
Discussion: PR #89

## 058. The server acts only on a ZIP the caller said, and street names aren't businesses

Decided: a ZIP used for the service-area check, the read-back or a booking must have been said by the caller on this call or a linked one, or be on a matched customer's location; otherwise the tool refuses and the agent asks. A business word that's part of a street or place name ("540 Church Ave", "Grand Army Plaza") doesn't make a caller commercial; "it's a church" or "we run a restaurant on Church Ave" still does.
Why: a test run caught the model inventing ZIP 11226 for "1520 Flatbush Ave" and the server ending the call as out of area; in later runs it guessed again 2 times in 3, and the gate refused each time and the call booked (MEASURED, PR #88). Brooklyn has a Church Avenue, which the round 2 commercial phrases had made a business.
Would change if: callers often give no ZIP, in which case the server looks it up from the street and house number.
Discussion: PR #88

## 057. Two calls booking the same slot are tested end to end

Decided: availability comes from the mock ServiceTitan's real capacity (technicians on shift with the right skills, minus booked, minus held for urgent), and a booking writes a job there. A harness mode runs two simulated calls at once against one world, with two evals: two callers for the only open window (exactly one gets it; the other is told plainly it just went and offered another time, never told they're booked) and two callers in one window with two technicians on (both book, on different technicians). The checks read the mock directly: one job per technician-window across the whole mock. For now the pairs run only on our stack.
Why: Jackson's round 4 ask; in a cold snap callers race for the same opening, and the unit race test didn't show what the caller hears.
Would change if: a real ServiceTitan sandbox counts capacity differently, in which case the adapter and the checks follow it.
Discussion: PR #86, evals/rounds/04-wording-standards.md

## 056. The side model stays on after a with-and-without check

Decided: the side model (decision 037) stays on for live calls. Its first eval check with it on found no false alarms, so no narrower trigger is added.
Why: 26 priority and no-heat scenarios, twice with and twice without (MEASURED, evals/rounds/side-model-02.md, $2.50): it said yes on 11 of 46 calls, all with a person at risk, and no on 35, none at risk (a true false-alarm rate of 8.2% or lower at 95% confidence). It fixed `stroke-in-passing-no-heat` (High without, Urgent with) and every answer was back before the gate read it. A yes doesn't trigger the on-call alert or the after-hours booking hold. The Urgent calls with nobody at risk came from the phrase rules reading "nobody's elderly" as a yes, fixed alongside.
Considered: requiring a phrase match too (would switch it off for the calls it exists for); cold days only (a mild-day yes only reaches High anyway).
Would change if: third-party hard negatives ("my neighbor is 90") or the mild-day branch, neither tested yet, show false alarms.
Discussion: evals/rounds/side-model-02.md

## 055. Rule edits move to the debug bench, with an override

Decided: owner rule edits move off the owner view and onto the debug bench. Publishing an edit reruns the must-pass eval gate by default; an override publishes it at once and logs it as an overridden instant change. Two-call booking evals run only on our stack for now.
Why: Jackson's direction, given in the booking-concurrency session (PR #86). Rule edits are an engineer-facing tool more than an owner one, and an override keeps a quick fix possible while the log shows it skipped the gate.
Considered: keeping edits on the owner view behind the edit login (DECISIONS.md 022).
Built in PR #93: the editor is at `/bench/rules`; an override needs a reason, still runs the AI guidance check and every structural check, and shows "Check skipped" in both histories with who, when and why.
Would change if: owners ask to edit wording themselves again.
Discussion: PR #86, PR #93

## 054. Owner accounts with two roles, and a settings panel with a consent switch

Decided: the owner view gets named accounts with two roles. Admins change settings and publish rules; staff view everything and change nothing. A settings panel, changed by admins only and kept with an audit trail, holds a two-party consent switch: on, every greeting adds a short recording notice; off (the default), no notice, since Summit Air is in New York, a one-party consent state. Other owner settings (call recordings, warm transfer, technician summaries, after-hours alerts) can be switched there; none can loosen a gate.
Why: Jackson's direction; a real shop has more than one person in the office, and recording-consent rules differ by state when callers or the company move beyond New York.
Considered: the shared read token and a separate edit code (no way to tell who changed what).
Would change if: Summit Air needs finer roles (a dispatcher who can edit rules but not settings).
Discussion: Jackson's notes on the phase 3 brief

## 053. The server sets the speaking rate per call, and remembers it

Decided: the agent speaks more slowly (0.85 of normal) when a caller says they're elderly or gives an age of 65 or more, and changes pace when a caller asks to slow down or speed up, or says "sorry, what?" twice. The change is decided by the tool server from the caller's lines and applied by the worker for that call only. A requested pace holds for the rest of the call, carries to a linked call the same day, and is remembered on the caller's record for their next call. A request to slow down just for the address applies only to the read-back sentence, then the pace returns. Faster and slower are treated alike; nothing assumes callers only want slower.
Why: Jackson's direction; older callers and anyone hearing an address or number benefit, and pace is manner, so a server trigger is enough and keeps it testable. The 65 line for pace is Jackson's own; priority's older-adult line stays at 60 (decision 019).
Considered: a model tool alone (harder to test); a fixed slower voice for everyone (slows every call).
Would change if: live calls show the slower pace sounds unnatural, or callers ask for it often enough to make it the default.
Discussion: docs/tuning.md

## 052. Call recordings go through a bucket to the server and are kept 30 days

Decided: the LiveKit worker records each call's audio (audio only, through LiveKit egress) into a private Supabase storage bucket; the jobs loop copies each finished file to the server's data folder and deletes it from the bucket, and the copy is deleted with the transcript after 30 days (S-45). The call page plays it with the design-system Player. Recording stops when the agent leaves, so a conversation after a warm transfer isn't recorded.
Why: story O-6 on the owner view; LiveKit Cloud can only write to cloud storage, and keeping the copy on the server keeps call data in one place (SQLite and the data folder). The free LiveKit plan allows 60 recorded minutes a month (SOURCE: livekit.com/pricing, PR #69), enough for the demo.
Considered: keeping files in the bucket (call data in two places); recording after a transfer (the person's side of the call wasn't agreed to by the agent's greeting).
Would change if: Summit Air moves past the demo, in which case recorded minutes need the paid LiveKit plan and the greeting needs a recording notice where state law asks for one.
Discussion: PR #69

## 051. Warm transfer is decided by the server, and the demo rings Jackson's phone

Decided: the server, not the model, decides a warm transfer: in office hours only on the caller's own words, after hours only when the server's own priority and cutoffs say the on-call technician is needed, never on a safety or commercial call, and only when the role's number is set. The person hears a short neutral opener and a summary, and presses 1 to be joined, so a voicemail box is never put through. The flag and a named callback are stored before anyone is dialed, so a missed or failed transfer leaves the caller with that callback, never silence. For the demo, both roles ring Jackson's personal phone; the number is set only in the server's `.env`.
Why: C-11 moved into V1 (DECISIONS.md 034); a flag the model can set is not a gate.
Considered: the model deciding when to transfer (the reviewer found it could ring the on-call phone by marking a call urgent); bridging on answer (a voicemail would be bridged).
Would change if: Summit Air has a real office line and on-call rotation, which replace the demo number.
Discussion: PR #69

## 050. Round 2 part B: times, return visits and new scenarios

Decided: a bare hour means daytime (1 to 7 is afternoon, and a range ends after it starts, so "8 to 2" is 8 AM to 2 PM). A completed job for the same problem at the address within 30 days (ASSUMED) is flagged to a person as a possible return visit. Priority phrases respect a plain negation, with a short look-back that errs toward raising. Second opinions on a replacement go to a sales advisor who comes out to quote, through the install lead, never a price on the phone. The new round 2 scenarios aren't must-pass until they have a few clean runs.
Why: Jackson's round 2 notes; the knowledge folder says replacements are quoted in person.
Would change if: callers mean evening hours often enough to need a question, or the 30-day window doesn't match Summit Air's warranty.
Discussion: evals/rounds/02-seeds-and-holdout.md, PR #75

## 049. Same number, same day links only a dropped call

Decided: a call from the same number links to an earlier call that day only when the most recent earlier call dropped; the agent confirms the caller by their own name before resuming, and the two calls can't book twice. A finished call in the morning doesn't link an afternoon call.
Why: Jackson's round 2 note asked for resuming within the day; linking finished calls would block a legitimate second booking.
Would change if: callers often call back after a finished call to change what they booked.
Discussion: PR #75

## 048. A second address goes on its own account by default

Decided: a caller with a job at their own home can book a visit at another address in the service area. The server asks first whether it goes on its own account (the usual answer, with the caller as a contact) or the caller's own.
Why: Jackson's round 2 note; a separate account keeps each home's history with that home.
Would change if: the office prefers one account per caller.
Discussion: PR #75

## 047. Burning dust gets one lighter check question

Decided: a burning or dusty smell (common on the first cold night) gets one check question asked the negative way: "Just to confirm, it doesn't smell like sulfur or rotten eggs, right?" Agreement words alone ("yeah", "right", "no") clear it; the smell named again, a symptom or an unsure answer bring the lines.
Why: Jackson's round 2 note; a burning-dust smell is usually harmless, and asking the gas question the usual way read "yeah" as a smell.
Would change if: real calls show a gas smell described as burning dust and cleared by a "yeah".
Discussion: evals/rounds/02-seeds-and-holdout.md, PR #74

## 046. A safety call can book a visit for after the problem is shut off

Decided: on a gas or carbon monoxide call the fixed lines and the person flag come first; the agent then recommends staying outside and calling 911 or the gas company and offers once to book a technician for after it's shut off, with the signed-off line "The technician can only work on it once the gas company or 911 has shut it off and said it's safe, so please stay outside until then." The caller doesn't have to insist. This updates decisions 009 and 033.
Why: Jackson's round 2 note: the agent should still help the caller get a technician once it's safe.
Would change if: callers take the booking as a reason to stay inside.
Discussion: evals/rounds/02-seeds-and-holdout.md, PR #74

## 045. Unclear approval gets a callback, and a requested booking a call before dispatch

Decided: when it's unclear whether a co-op or apartment caller can approve the work, the agent offers a callback to confirm who can; if the caller asks to book, it books and the server schedules a call 2 hours before the window (ASSUMED) inside calling hours. If that call can't happen before the window, the booking is flagged to hold the technician and the caller is told the technician won't start until it's settled.
Why: Jackson's round 2 note; approval is a question of authority, not of residential or commercial.
Would change if: the office finds the pre-dispatch call adds work without catching problems.
Discussion: PR #74

## 044. Residential is about equipment, not unit count

Decided: residential means residential-grade equipment: homes, apartments, co-op and condo units and whole apartment buildings, including a super or building manager calling about the building. Commercial means a business with commercial-grade equipment (offices, stores, restaurants, hotels, churches, shopping centers). Property managers and co-op boards are residential accounts in ServiceTitan. The agent can explain the difference briefly when a caller is unsure. This replaces the three-unit line from call-journeys round 2.
Why: Jackson's round 2 note: a NYC residential HVAC company works on residential-grade units, and multi-tenant buildings have them.
Would change if: Summit Air starts taking commercial-grade work.
Discussion: evals/rounds/02-seeds-and-holdout.md, PR #74, docs/brief.md

## 043. The eval hash covers only what the agent hears on a call

Decided: the run hash's prompt files are the parts `prompts/assemble.py` joins, plus `tools.yaml` and the assembler. A prompt that is never part of a call, such as the technician summary's, is left out, so editing it doesn't make the voice agent's results stale.
Why: staleness should track what changes calls.
Would change if: a post-call prompt starts feeding back into a call.
Discussion: PR #72

## 042. Must-pass failures count code checks only, and the grader sees what the server set up

Decided: a grader fail on a must-pass scenario lowers the pass rate but is not a must-pass failure, in the harness, the ratchet and the publish gate alike; a must-pass scenario with no verdict still blocks. The grader is given what the server set up on the call (flags, bookings, the tools' sentences and the signed-off safety wording), so it can tell a real callback from an invented one. This supersedes the last sentence of 031.
Why: the must-pass lines are code checks; at one sample the grader failed one or two must-pass calls a round on manner, a different one each time, and failed calls for saying the signed-off sign-off. Round 3 also showed the grader agrees with Jackson on 3 of 5 explicit verdicts, so it shouldn't decide must-pass alone.
Considered: counting the grader too; loosening its "promise not set up" rule (would let real invented promises through).
Would change if: the grader catches real must-pass problems the code checks miss.
Discussion: PR #72, evals/rounds/03-judge-labels.md

## 041. The eval floor is 90%

Decided: reference run `20261104-013438-livekit-fast-standard`, 29 of 31 judged (94%), sets the floor at 90% under decision 011; `ratchet.py --adopt` sets the floor this way from now on and never lowers it. Every must-pass scenario passed 3 of 3 on the defaults before the reference was taken.
Why: three standard runs on nearly the same prompts gave 93%, 94% and 94% (MEASURED, evals/rounds/reliability-01.md), steady enough to set the floor.
Considered: waiting for three samples per scenario.
Would change if: a later reference run is higher, which raises it.
Discussion: PR #72, evals/rounds/reliability-01.md

## 040. The technician summary is a pinned job note the server checks

Decided: after a booked call, a model (profile `post_call.summary`) drafts a short summary (the problem, anything risky, access, recent work, what the customer was told) and the server writes it as a new pinned job note through the FSM adapter, leaving the intake and every other note as they are. The server drops any line with nothing in the record behind it, with a number the record doesn't hold, or with a phone number. A failed summary is tried once more, then left out and logged; nobody is flagged, since the job already carries the full intake. On the server with `SUMMARIES=1`.
Why: spec S-16; a technician reads a few lines in the truck, not a transcript. ServiceTitan's job notes are append-only and can be pinned, so the summary sits on top without editing anything. The model writes the sentences; the server decides which reach the job, which catches invented work or codes, though not a wrong statement built from real facts, so the intake stays underneath.
Considered: editing the job summary field (overwrites the intake); a template with no model (stiff, and loses the caller's own words).
Would change if: technicians find a summary line wrong, in which case the summary moves behind the owner's review.
Discussion: PR #73
Issue: none (spec S-16)

## 039. Owner guidance is refused only when a model finds it contradicts a locked rule

Decided: the keyword filter on owner guidance is gone; no single word refuses a note. When a draft is saved, one call to a small fast model (profile `owner_edits.guidance_check`, thinking off, JSON) reads the new or changed notes and lines against the locked rules built from the rulebook, and refuses only one that contradicts a locked rule, tells the agent to skip or override one, or has it tell callers something a locked rule makes untrue, with a one-sentence reason on the editor. A note that only mentions a subject passes. If the check can't run or answers out of shape, the save is refused, never let through. Structural checks stay in code: plain ASCII after NFKC, no markup characters, length and per-stage limits, exact `{first_name}`, the AI disclosure in the greeting. This supersedes the keyword part of decision 032.
Why: the word list refused reasonable notes ("if someone mentions 911, stay calm") and a reviewer found phrasings that passed it, so it was wrong both ways. Containment never rested on it: the server gates hold whatever the prompt says, and the eval gate runs the must-pass calls on every draft (031). In a live check the model got 33 of 33 right at about $0.002 and 1.4 s a save (MEASURED, PR #71).
Considered: a shorter word list (still refuses mentions, still misses rewordings); no check, leaving it to the eval gate (the owner waits minutes for a blocked publish with no reason).
Would change if: owners hit refusals they think are wrong, or a contradiction passes the check and only the eval gate catches it.
Discussion: PR #71, docs/template.md ("Owner edits")
Issue: #56

## 038. Audio bake-off round 1: our stack stays

Decided: under the rule in decision 026, no newcomer is chosen. Gemini Live, OpenAI Realtime and Retell are out of the real-call round: Gemini Live never said the safety lines first (0 of 6), OpenAI Realtime was slowest (2.47 s plain median) and missed the lines twice, and Retell said them word for word on 1 of 6 (a safety failure rules a candidate out, docs/bakeoffs.md). ElevenLabs on our brain was 0.44 s faster with the lines exact on 6 of 6 but misread addresses and numbers on read-back ("St" as "Saint", digits run together) and booked 2 of 4 tune-ups; it gets a fix and a rerun before it can be a finalist. Our stack's plain median was 1.89 s on the rig (MEASURED, evals/rounds/bakeoff-audio-01.md), above the 1.2 s bar; the same Claude model answered in about the same time in both stacks, so the gap is our end-of-turn wait, which is tuned next.
Why: the rule was set before the run, and the results were read against it. The rig reads about 0.4 s above the worker's own `reply_ms`, since the worker starts timing after its silence wait, so real-call traces need that added.
Considered: promoting ElevenLabs on speed alone (it would read wrong addresses back to callers).
Would change if: ElevenLabs reads back cleanly after its fix and stays ahead after our endpointing is tuned, in which case the two get the real calls.
Discussion: PR #70, evals/rounds/bakeoff-audio-01.md
Issue: #62

## 037. A side model reads whether someone at risk is home

Decided: a side model (OpenAI Decisions) answers one question on each caller line of a no-heat or no-cooling call: is someone at risk at home? The priority gate reads whatever answer is back within 0.4 s. A yes raises priority the way the vulnerable-person rule does (Urgent, or High on a mild day); a late, failed or missing answer leaves today's rules to decide, and it can never lower priority. On on the server with `SIDE_MODEL=openai-decisions`.
Why: on 80 test lines written without seeing the phrase lists, the rules found 24 of 37 people at risk; the side model with the rules found 36 or 37 (recall 1.00, precision 0.84 to 0.86, MEASURED, evals/rounds/side-model-01.md). It was back in time on 47 of 47 gate reads (p50 about 250 ms). The new scenario `stroke-in-passing-no-heat` went from High on 3 of 3 runs to Urgent on 3 of 3. Callers here are test callers, so sending lines to the provider is acceptable for the demo; a real deployment would confirm zero retention on the account first.
Considered: Jev (as accurate and faster, no retention answer yet); a narrow Haiku call (accurate, p50 1.0 s, too slow for the gate); none (misses a third of at-risk people said in passing).
Would change if: the full plan run (480 lines, a second labeller) puts recall under 0.95 or false alarms up by more than 5 points, or Jev confirms zero retention.
Discussion: PR #68, evals/rounds/side-model-01.md, evals/decision-models/PLAN.md

## 036. Haiku 5.5 stays the live model

Decided: after the model matrix, Haiku 5.5 with thinking off stays the live brain.
Why: on the standard suite, two runs each, Haiku passed 91% against Sonnet's 84 to 87%, tied on must-pass, answered plain turns in 0.57 s against 1.6 s (the bar is 1.2 s) and cost about 26 times less per call (MEASURED, evals/rounds/model-matrix-01.md). Sonnet scored 0.14 higher with the grader, within noise, but told callers about its tools and pushed the residence check until it turned renters away.
Considered: Sonnet 5.5 as the live model.
Would change if: a faster Sonnet-class model appears, or a prompt change makes Haiku's question pacing clearly worse.
Discussion: PR #68, evals/rounds/model-matrix-01.md

## 035. Eval co-authoring in three rounds, not five

Decided: the co-authoring plan in exploration 008, section 7, shrinks to three rounds. Round 1 (real failures) is done. Round 2 combines seeds and the hold-out: Jackson edits or rejects about 10 seed scenarios drafted from his round 1 notes, and dictates 5 short caller cases that stay unread until a release gate. Round 3 combines judge labels and the scoreboard: he marks about 10 graded transcripts pass or fail with the grader's verdict hidden, then reads the standard-run summary and agrees the floor.
Why: Jackson's time; the two dropped rounds fold into the others without losing their purpose (seeds from a human, a hold-out the AI never saw, a grader checked against a person).
Considered: the original five rounds (more of his time for a small gain at this size).
Would change if: round 3 shows the grader disagrees with Jackson often, in which case a second labelling round is added.
Discussion: docs/explorations/008-eval-set-quality.md, section 7

## 034. What's in V1 and what waits

Decided: in V1, built in phase 3: the technician summary (S-16), the model matrix (Haiku against Sonnet), a side model where the evals show a bottleneck, warm transfer to a person, call recordings on the call page, the eval floor, and reply-delay targets with measured latency values. After V1: Spanish and other languages, playbooks per call, production capacity past the demo, booking texts and anything else that needs SMS, and the extra owner-view editing (editable numbers, condition rules, after-call guidance). The local-name pronunciation is closed as good enough: Houston Street is said HOW-stun, which is the one that matters; the 30 clips weren't reviewed by ear. Owner logins last as long as a browser keeps a cookie, with no limit on wrong guesses.
Why: the assignment is a voice agent, not a front-end platform, so phase 3 time goes to what callers and technicians notice. Texting waits on carrier registration (decision 007), and the owner view stays as built.
Considered: keeping the earlier later-list as it was; building the extra owner-view editing now.
Would change if: the interview asks for one of the deferred items, or phase 3 runs out of time before the in-V1 items land.
Discussion: Jackson's notes on docs/ai-process/brief-phase-3-onward.md

## 033. The agent's safety job is to escalate politely, not to coach

Decided: on a safety call the agent says the fixed lines, politely recommends getting safe, flags a person and gets off the line. It doesn't give on-the-fly safety guidance or grow new safety logic; fixes to failing safety scenarios prefer simplifying over adding rules.
Why: it's an AI voice agent that tells callers what it is, and its value on these calls is a fast, calm handoff to 911 and a person. Jackson: "It doesn't need to give, like, on-the-fly safety guidance. It just needs to, like, politely elevate the situation to the caller or person being called, and then politely instruct them to, you know, be careful or whatever. But it doesn't need some crazy logic."
Considered: more trigger phrases and follow-up questions per hazard (more chances to overfire, as eval round 1 showed).
Would change if: real calls show a hazard the fixed lines and the check questions miss.
Discussion: Jackson's notes on the phase 3 brief

## 032. Owner guidance has four stages and steers clear of locked subjects

Decided: owners can add guidance for the opening, the whole call, booking and closing; nothing runs a model before hello, and after-call guidance waits for the technician summary. Guidance that names a locked subject (safety, gas, carbon monoxide, 911, the read-back, commercial, price, fees, priority, urgent, service area, cancelling, or words that try to talk the model out of its instructions) is refused. Text is folded to plain ASCII first, so look-alike characters can't slip past. The four editable lines are six fields, since the greeting and the callback opening each have a known-caller and an unknown-caller form; the unknown-caller greeting must keep its AI disclosure. Booking and closing guidance reaches the model only when the server says the call has reached that stage.
Why: guidance can't break a server gate, but it could contradict one in what the agent says, and a leaked edit login would otherwise be a path for prompt injection. The filter is a keyword list and a reviewer found phrasings that pass it, so the real containment is the server gates plus the eval gate; the filter only keeps the obvious cases out of the history.
Considered: any text, relying on the prompt preamble; after-call guidance stored now and shown as pending.
Would change if: real owners hit false refusals ("free" and "emergency" are the likely ones), or the technician summary is built.
Discussion: PR #65, docs/template.md ("Owner edits")
Issue: #56

## 031. The publish gate blocks on code checks, with one rerun of a miss

Decided: a draft is published only if every must-pass scenario passes its code checks. A scenario that fails or ends with no verdict is run once more on the same draft, and a second miss blocks. Both runs are kept on the version with the rerun count and cost. Grader fails are kept as notes but don't block. This applies to the publish gate only; an ordinary eval run still fails on a single must-pass miss.
Why: at one sample per scenario the must-pass set is noisy: three gate runs failed five different must-pass scenarios, none twice (MEASURED, PR #65). A rerun of only the misses costs about $0.02 a scenario, and a failure that repeats still blocks, so a known failure can't ride along. A gate relative to the live version was ruled out: it doubles the cost of a publish and lets an existing failure go live again. The must-pass lines are code checks; the grader is the noisiest signal and an owner edit may change manner on purpose.
Considered: blocking on a single miss (blocked every edit tried); comparing against the live version; blocking on grader fails too.
Would change if: a scenario keeps passing only on its rerun across publishes (flakiness to fix in the scenario), or a grader-only fail turns out to be a real regression the code checks missed.
Discussion: PR #65; the flakiness is a phase 3 finding in docs/ai-process/brief-phase-3-onward.md
Issue: #56

## 030. "Never suggest unsafe heat" is a prompt rule, checked by evals

Decided: the rule that the agent never suggests an oven, stove, generator or grill for warmth lives in the prompt (`prompts/safety.md`) and is covered by eval scenarios, not by a server check.
Why: agent lines reach the server only after they are spoken, so the server has no point at which to stop one. The gates that matter on these calls (the check question, priority, the safety lines) are still server code.
Considered: a filter in the LiveKit worker's speech step (LiveKit only, not Retell, and it would be the voice layer holding a rule).
Would change if: a voice layer gives the server a hook before speech, or evals catch the agent suggesting unsafe heat.
Discussion: PR #64

## 029. The safety sign-off is a polite recommendation

Decided: the sign-off is "For your safety, I'd recommend hanging up now, getting outside, and calling 911 from there. We'll follow up once you're safe." The first safety lines are unchanged.
Why: by the sign-off the caller may have said they are alone, and the first lines already say to get everyone out. Jackson signed off on "get outside" and asked for a polite recommendation rather than a demand.
Considered: "Please hang up now, get outside, and call 911 from there" (a demand); a second sign-off for callers alone (the server would have to guess from words).
Would change if: evals or real calls show callers treating the softer wording as optional.
Discussion: PR #64, call-journeys r4 in docs/ai-process/coauthoring.md

## 028. After hours, the on-call technician is alerted before a booking

Decided: after hours, a no-heat or no-cooling call that needs the on-call technician tonight can be booked only after an urgent flag has alerted them; the morning visit is then held as the fallback.
Why: journey 11 says the on-call technician is asked first. The server already worked out the "tonight" outcome but nothing enforced it, and eval round 1 caught a 48 F house booked for the morning with no alert.
Considered: refusing the booking on the tonight path (leaves the caller with nothing certain if the technician can't come); a prompt rule only (the round 1 failure).
Would change if: the office wants no morning booking held when the on-call technician is coming.
Discussion: PR #64, evals/rounds/01-real-failures.md

## 027. How a company folder sits on its trade

Decided: a tenant's `agent.yaml` is laid over its trade's. Mappings merge key by key with the tenant winning, rule lists merge by `id`, and other lists are the trade's items plus the tenant's own. The trade holds safety signals and at-risk priority rules only; the signed-off safety wording, NYC-sourced numbers, commercial phrases, `config.yaml`, knowledge and voice lines stay with the company. A tenant fails to load if it lacks its safety lines, check questions or sign-off, drops or lowers a trade rule, adds denials, or mixes rules with and without ids. Summit Air's merged config is checked against a committed golden copy.
Why: a company adding a local phrase is common; a company quietly dropping a gas phrase or lowering a priority would loosen a gate, so the loader refuses it rather than trusting review. Each company signs off its own safety script (decision 009), so a trade edit must never change signed words. The golden copy proved the split changed nothing and shows when a trade edit changes a company's behavior.
Considered: tenants replacing whole lists (one edit could drop every trade phrase); moving the safety wording and job types into the trade (ties signed-off words and ServiceTitan ids to the trade).
Would change if: a second HVAC company signs off on shared wording, or genuinely needs to drop a trade item, in which case an explicit, tested removal list is added.
Discussion: PR #63, docs/template.md
Issue: #57

## 026. Three more bake-off entrants, scored on a simulated audio rig

Decided: ElevenLabs Agents (in custom-LLM mode, on our Haiku brain and tool server), OpenAI Realtime and Gemini Live join the bake-off beside our stack, Retell and the Vapi test call. All are scored first on a simulated audio rig that streams synthesized caller speech to each platform and times the first sound back, so nobody dials. Real calls go only to the final two. A newcomer is chosen only if its plain-turn median is at least 0.3 s lower and every must-pass scenario passes with the safety lines word for word on three repeats; anything less is a tie, and a tie keeps our stack.
Why: nothing published beats our design's reply delay (about 1.0 s, DECISIONS.md 006, against a best managed median of 1.30 s, SOURCE: OpenBenchmarks via exploration 013), so the claim needs measuring against the strongest alternatives on the same brain and scenarios. ElevenLabs keeps hang-ups on our server in custom-LLM mode; the two speech-to-speech models test whether one model doing everything can be fast enough. Gemini Live stays in although it was slow in one independent test (3.05 s mean, SOURCE: exploration 013), since on the rig it costs little to measure ourselves.
Considered: ElevenLabs only; adding nothing; Pipecat, Deepgram's Voice Agent API and Telnyx (kept as reserves); Bland, Synthflow, Hume and Cartesia's managed agents (our server can't control the call).
Would change if: the rig's delays disagree with real calls by more than the gap between entrants, in which case the phone loop is used for every entrant.
Discussion: docs/decisions/62-bakeoff-entrants.md, docs/explorations/013-more-bakeoff-entrants.md
Issue: #62

## 025. Real test calls by Jackson; non-Anthropic seats in the eval harness

Decided: Jackson makes the real test calls himself (the 10 live calls in #9 and the human test pass) rather than recruiting 6 to 8 outside callers. The eval harness may use a non-Anthropic model as the simulated caller or the grader, in a limited scope, and paid runs that test a hypothesis or compare options are approved when kept small.
Why: outside callers need scheduling and briefing for little gain at this size, while the self-grading risk in exploration 008 is better reduced by not having Claude play every seat: a caller or grader from another provider is less likely to share the agent's blind spots.
Considered: outside callers who haven't seen the spec (exploration 008, section 5); keeping every seat on Claude (cheapest, but the suite would grade itself).
Would change if: Jackson's calls and the mixed-provider runs keep passing while real calls later find failures, a sign the suite needs callers who think less like its author.
Discussion: docs/explorations/008-eval-set-quality.md, section 5
Issue: #9

## 024. Carbon monoxide check for unsafe heating, asked only when it applies

Decided: when a caller says the home is being heated with an oven, stove, generator, grill or another fuel-burning source indoors, the agent asks the carbon monoxide check question once; symptoms or an alarm in the answer bring the fixed lines as today. The trigger is narrow, so an ordinary mention ("the gas stove works") asks nothing. The agent never suggests one of those as a way to get warm.
Why: indoor fuel-burning heat is a common cause of carbon monoxide poisoning in a no-heat emergency, and the question costs one turn on the calls where it matters. Asked on every no-heat call, it would alarm callers for no reason and slow the call.
Considered: no question, only the raised priority already in place (misses a caller already feeling symptoms); asking on every no-heat call (overfires).
Would change if: evals or real calls show the question firing on ordinary mentions, or a caller with symptoms getting past it.
Discussion: morning brief, item 3; the round logged in docs/ai-process/coauthoring.md

## 023. Local names said and heard right from one tenant list

Decided: 15 sourced NYC street and neighborhood names in one per-tenant pronunciation list that feeds both the voice (a Cartesia pronunciation dictionary) and hearing (Deepgram keyterms, on now). Kosciuszko is spoken koss-ee-OSS-koh, and all three common readings are accepted when heard. Substitution happens at the voice, never inside the server's read-back text, so the stored address stays as written. A 30-clip Cartesia check (about $0.09, ASSUMED) confirms the readings; more names are added as real calls turn up misses.
Why: a wrong "HOUSE-ton" or a misheard street name marks the agent as not local within one sentence, and a misheard street can book the wrong address. One list keeps speaking and hearing in step.
Considered: spelling names phonetically in the prompt (the model would write the phonetic form into the read-back); a larger unsourced list (guesses about how locals say names).
Would change if: keyterms cost more than about $12 a month at real volume (SOURCE: deepgram.com/pricing, exploration 011) without fewer misheard streets, or Cartesia's dictionary changes meaning between API versions.
Discussion: docs/explorations/011-local-pronunciation.md
Issue: #58

## 022. Rules by call stage: a rulebook the owner reads, guidance the owner edits, gates nobody loosens

Decided: the owner view shows every rule the agent follows, grouped by call stage (before hello, opening, whole call, booking, closing, after the call), each marked locked or editable and showing where it is enforced. The owner can then add short guidance per stage and reword four lines (greeting, callback opening, anything-else and goodbye). An edit is a draft until the must-pass evals pass against it; only then can it be published. No screen loosens a gate or changes the safety script; those change only through engineers and `/round`. Edits use a separate edit login, live in a versioned SQLite table over the YAML defaults (audit trail and rollback, no git writes from the server), and the server, not the model, decides the stage. The demo shows a version already in the history with its gate result rather than a live edit.
Why: owners of HVAC shops tune their call scripts, and ServiceTitan's own voice agent lets them edit opening and closing messages and follow-up questions (exploration 009). Of the 28 rules, 13 include a server gate and only 4 rest on the prompt alone, so the editable part is the manner of the call, not its rules. Two edits that look harmless would break a gate without touching code: rewording the read-back, which doubles as the home-or-business check, and lowering a priority bracket, so both are locked.
Considered: a read-only rulebook only (cheaper, but the owner can't make the agent theirs); condition and action rules from a fixed menu (powerful, but an owner could build a rule that undoes a gate); edits written straight to YAML (no audit trail, and the server would commit to git).
Would change if: owners ask to change numbers such as callback windows, in which case tighten-only numbers are added behind the same gate.
Discussion: docs/explorations/009-rules-by-call-stage.md, docs/ai-process/ideas-round-questions.md
Issue: #56

## 021. Templates layered by trade, then by company, in the repo

Decided: a `trades/hvac/` layer holds what any residential HVAC shop shares, and each company folder under `tenants/` extends it with its own values. Not built as Vapi assistants.
Why: a second HVAC company then starts from the trade, not from a copy of Summit Air, and a plumbing trade is one new folder. Vapi has no inheritance between assistants (exploration 010), and building templates there would move rules out of server code into the prompt.
Considered: Vapi assistants per trade and company; copying the tenant folder, as S-46 does today (works, but every fix has to be made in each copy).
Would change if: the bake-off chooses a managed platform, in which case its own template features are weighed against this layer.
Discussion: docs/explorations/010-vapi.md
Issue: #57

## 020. Vapi in the bake-off as a comparison and one test call

Decided: Vapi is added to the bake-off as a written comparison plus one test call on Vapi's free number after the main run (about $0.20, ASSUMED), not a full entrant (about $3 and 2 to 3 days, ASSUMED).
Why: Vapi's one advantage over Retell for this build is that a tool result can be spoken word for word with the model skipped, which could solve the paraphrased safety lines Retell showed. One gas call tests that claim directly; the rest of Vapi is the same trade as Retell.
Considered: a full entrant on the eval suite; leaving Vapi out (decision 004 set it aside before this capability was known).
Would change if: the test call shows the exact-wording claim holds and Vapi matches our stack on reply delay, in which case it becomes a full entrant.
Discussion: docs/explorations/010-vapi.md
Issue: #57

## 019. An older adult is 60 and over, in line with NYC Health

Decided: for priority, an older adult is 60 and over (was 65, the CDC line).
Why: alignment with local standards: NYC Health's heat and cold guidance names adults 60 and over as at risk (exploration 007), and Summit Air serves New York callers under New York rules.
Considered: 65 (CDC); 70 (the first draft).
Would change if: NYC Health changes its line.
Discussion: docs/explorations/007-priority-brackets.md, morning brief item 2

## 018. Owner view second markup round: indexed search, technical mode, custom ranges

Decided: from Jackson's notes on the revised pages. Search moves to a full-text index of every call on record (SQLite FTS5, `app/search.py`): calls, jobs, flags, leads and callbacks, and every line said, kept current by the jobs loop and before each search, with results grouped by kind, matches highlighted, and suggestions as you type. Technical details return as a switch on the call page, off by default, in place of a "Details" disclosure on every step. Results puts the chart first, renames the ledger "Performance" with one line per measure, and takes a custom range up to 92 days. The Jobs table's columns can be resized and Job is widest by default. The report box sits at one height with its select and button, and the Jobs head names ServiceTitan once.
Why: the notes asked for search that is "pre-indexed" and finds "a wide variety of stuff", a plain call page with technical detail as a mode rather than a control on every step, and a Results page that leads with the picture. FTS5 ships inside SQLite, so the index needs no new service, and indexing only the calls that changed keeps each refresh to one query when nothing is new.
Considered: a separate search service (another moving part for a few thousand calls); scanning every call per search, as the first version did (slow as calls grow and blind to what was said); keeping per-step "Details".
Would change if: calls grow past what one SQLite file serves well, in which case the index moves with the database to Postgres full-text search.
Discussion: Jackson's notes on the design canvas, "Revised" row (https://claude.ai/artifact/MFSZ4p6oo871gh7U2Ss8hL)
Issue: #53

## 017. Owner view markup round: rows are links, one tag, the call as a graph

Decided: the changes from Jackson's notes on the built pages. Today opens with five figures (label, number, what it is made of) instead of a generated sentence; counts sit in pills apart from their labels; a whole row opens its call and the "Open call" links go; the Jobs table keeps each row to one line with the reason in a tooltip, priority a tag only at Urgent and High, and windows as plain text; "Handled too" becomes "Show resolved"; callbacks say who calls back and by when; the now line runs over the marks; the ServiceTitan logo is larger. On a call: one status tag with the rest as facts, the conversation drawn as a commit graph, the report box under the conversation, compact side panels, and each step's detail behind its own "Details" plus a downloadable log in place of the page-wide technical toggle. A search field in the header finds calls by name, number, address, ZIP or job over the last 30 days.
Why: the notes asked for less clutter (fewer colors and tag kinds, steady row heights), the AI-generic sentence gone, rows that act like ServiceTitan's, and technical detail either different enough to earn its own view or folded into the call. The toggle only added the raw data under the same notes, so it is folded in.
Considered: keeping the technical view as its own page (it would repeat the conversation with JSON added); a summary sentence rewritten in a warmer voice (still a generated sentence on top of the numbers).
Would change if: the owner wants the day summed up in words again, in which case it comes from the post-call summaries rather than a template.
Discussion: Jackson's notes on the design canvas, "Built" row (https://claude.ai/artifact/MFSZ4p6oo871gh7U2Ss8hL)
Issue: #53

## 016. The owner view is the dispatch board, on a full design system

Decided: version A of the timeline and dispatch desk mix (the day across the top, the tables below), with five changes Jackson asked for: a less cluttered board, no row of metric cards, filters on the board and on Results, a better font than Inter, and a full design system the pages are built from. Public Sans replaces Inter: it comes from Franklin Gothic, one of GT America's two parents, and Google's file has tabular figures (MEASURED: its GSUB table lists `tnum`).
Why: the dispatch board reads like the board a ServiceTitan dispatcher already uses, and version B pushed the jobs table down the page. A design system keeps three pages and any later one consistent, and is what a reviewer reads to see the choices were made on purpose.
Considered: version B (the calendar day); Schibsted Grotesk, Instrument Sans and Archivo as the font (all have tabular figures; Schibsted spaces punctuation oddly when tabular figures are on everywhere).
Would change if: Revin licenses GT America for the product, in which case the type tokens point at it and nothing else changes.
Discussion: docs/decisions/45-owner-view-design-direction.md
Issue: #53

## 015. The owner view is a day timeline with the dispatch desk's tables

Decided: direction 5 (Day timeline) mixed with direction 1 (Dispatch desk). The day is drawn on a timeline like a dispatch board, and the dispatch desk's number tiles, "Needs attention" table and jobs table carry the detail. Two versions of the mix go on the design canvas; the choice between them comes next.
Why: the timeline shows the shape of the day at a glance, which a table of rows doesn't, and the dispatch desk's tables are what an owner who uses ServiceTitan reads fastest. Together they cover both the glance and the follow-up.
Considered: developing directions 1 and 3 (Dispatch desk and Work queue), which the readout recommended; the shift report, big board and work queue on their own.
Would change if: the timeline proves too hard to read on a phone, in which case the phone layout falls back to the dispatch desk tables.
Discussion: docs/decisions/45-owner-view-design-direction.md
Issue: #45

## 014. The owner view is rebuilt from ten stories for a non-technical owner

Decided: option A. Ten stories on three pages: Today (morning check, emergencies first, the team's to-do list, what the agent put on the schedule, whether it is answering now), Call (read or hear the call, why it did each thing in plain words, tell Revin something was wrong) and Results (is it paying off, what it turned away). Tool names, JSON and ids move behind a technical details toggle.
Why: the brief 06 page was built for a software engineer; the owner knows HVAC, not logs. Each owner worry in docs/users.md gets a place on screen, and the "tell Revin" note shows how the agent improves.
Considered: a lean seven-story cut without the status line, recordings or the note; adding owner settings (service area, on-call person, hours), which would end the read-only page.
Would change if: build time runs short, in which case call recordings are cut first.
Discussion: docs/decisions/40-owner-view-stories.md
Issue: #40

## 013. A callback the system places opens by asking for the caller by name

Decided: option A. The opening is "Hi, this is Summit Air calling back for {first_name}. Is that you?", with the first name of the customer on the number being called, the same match the placed call makes. With no name on that number, two customers with different first names, or ServiceTitan unreachable, it keeps the plain line.
Why: a callback is resolved only when the right person is confirmed on the call (010). The plain opening never checked who answered, so most system callbacks would have been tagged open and handed to a person. A short yes to the name is the confirmation the server already accepts, and it mirrors the inbound greeting for a known caller.
Considered: keep the line and confirm later in the call; leave it and let a person close most callbacks.
Would change if: callers find the name question off-putting on live calls, in which case the check moves after the reason for the call.
Discussion: docs/decisions/49-callback-opening-name.md
Issue: #49

## 012. Retell's bake-off calls run on the same brain, with a few on fast fallback

Decided: option C. The 10 Retell bake-off calls use the same setup as our stack (Haiku 5.5, standard capacity, no backup), and about 3 more run with Retell's fast fallback on, reported separately. Retell offers no faster tier for Claude 5.5 Haiku, so fast fallback is the only extra.
Why: the 10 calls keep the bake-off fair under its own rule (keep our stack unless Retell is more than 0.3 s faster), and the few extra calls answer "did you give Retell a fair shot?". The extras cost little: about $0.20 to $0.24 a two-minute call against $0.19 (readout). They don't apply to chat, so they don't change a suite run.
Considered: the same brain only; Retell's extras on every call.
Would change if: Retell adds a fast tier for Haiku 5.5, in which case the variant calls use it too.
Discussion: docs/decisions/32-retell-speed-extras.md
Issue: #32

## 011. The eval pass-rate floor comes from the first standard run on the real prompts

Decided: option C, with the floor set only after the standard suite has run on the real prompts. That run is adopted as the reference and the floor is its pass rate rounded down to the nearest 5%; only a later adopted run can raise it. Until then the ratchet reports that no reference run is adopted, and must-pass cases, stale results and fewer scenarios still fail the check.
Why: the two smoke runs used the harness's stand-in prompt, so a floor from them would describe a different agent. Rounding down absorbs one or two noisy calls, and safety is held by the must-pass rule whatever the number is.
Considered: no floor (a slow slide on ordinary calls would go unnoticed); a fixed 90% (one flaky call in about 30 blocks a merge).
Would change if: repeat runs of the standard suite turn out noisy, in which case the floor waits until three samples per scenario are affordable.
Discussion: docs/decisions/27-eval-pass-rate-floor.md
Issue: #27

## 010. A callback counts as done only when ServiceTitan logs a call back that resolved it

Decided: option B, with an outcome on every call log entry. A background job marks a promised callback done when ServiceTitan's call log shows an outbound call to that number after the promise whose outcome is resolved. Every call log entry carries an outcome: resolved, still open (the caller needs more, or the wrong person answered) or no contact (no answer, voicemail, failed). When the system places the callback itself, the server writes the outcome at the end of the call: resolved only if the right person answered and the reason for the callback was handled; anything else leaves the callback open. When the office calls back, the person records the outcome in ServiceTitan as they would a call disposition. A failed call, a voicemail or the wrong person answering can never mark a callback done.
Built as: ServiceTitan has no outcome field on a call (its call type is empty for outbound calls, and its phone system creates the call), so the outcome is a call tag: three tag types per tenant ("Callback resolved", "Callback open", "No contact", named in `tenants/<company>/config.yaml` and `agent.yaml`). The office puts one on a call in ServiceTitan as it would dispose any call; the server puts one on the callbacks it places, resolved only when a person answered, the person who asked was confirmed (the read-back or a name check) and the reason was handled (a booking, reschedule or cancel went through, or the agent ended the call as resolved and the server saw nothing against it). The callbacks job marks a callback done from the call log. Detail in docs/decisions/26-callback-done.md.
Why: the owner view stays read-only and counts calls that actually happened and resolved something, which is what an owner means by "do callbacks happen". Without the outcome, any call to the number, including a missed one, would count.
Considered: a Done button on the owner view (counts clicks, and the page stops being read-only); leaving it for later (every person callback reads as overdue).
Would change if: Summit Air's office calls back from cell phones that ServiceTitan doesn't log, in which case the Done button is added as the fallback.
Discussion: docs/decisions/26-callback-done.md
Issue: #26

## 009. Safety lines signed off, with the caller told to hang up

Decided: option B. The gas and carbon monoxide lines, their shorter variants and check questions are signed off as written, with one change: the sign-off tells the caller to hang up and call 911 from outside, and no longer offers to stay on the line. `tenants/summit-air/agent.yaml` has `safety.signed_off: true`.
Why: it was the one place the wording disagreed with the gas utility's guidance for Brooklyn, Queens and Staten Island (National Grid: don't use anything electrical inside, including a phone). Everything else already matched Con Edison, National Grid and NYC Health (exploration 002).
Considered: signing off as written (keeps "I'll stay on if you need me"); rewriting through a round first.
Would change if: a real Summit Air reviews the wording with their own safety officer or utility contact.
Discussion: docs/decisions/24-safety-lines-signoff.md
Issue: #24

## 008. What V1 builds: the triage of every consideration

Decided: option A of the triage, with Jackson's changes. In: everything that touches the six requirements, safety, call quality, the template, least privilege, privacy (a real retention policy, with full logs kept in development) and success measures (tracked by the system and shown on the owner view for the meeting). Later: languages, decision models, playbooks, warm transfer and texts (docs/later.md). Out: a separate surge mode, because high call volume is handled by the scaling plan (docs/scaling.md) rather than a special mode. The aim is to scaffold the whole system first and come back to the later items once it works end to end.
Why: the demo is judged on the requirements, how the agent handles the unexpected, and how the cuts are explained; the later items become the rollout story.
Considered: a leaner cut that also moved the owner view and repeat callers to later.
Would change if: the build runs short, in which case the owner view is the first to shrink.
Discussion: docs/decisions/10-considerations-triage.md
Issue: #10

## 007. No texting in V1

Decided: the agent confirms bookings out loud on the call and the owner view shows them; no confirmation texts in V1, and no business texting registration.
Why: the assignment asks the agent to "book the appointment or confirm next steps with the caller", not to text. US business texting needs carrier registration that takes 1 to 3 weeks, and the only sole proprietor brand Jackson can hold is already used for unrelated client work. Calls need no registration, so the phone number is unaffected.
Considered: a sole proprietor registration for this demo (blocked by the one-brand limit), a second Twilio account, a texts-only toll-free number.
Would change if: a real customer goes live, in which case they register their own business brand with their tax ID and texting is switched on. The public terms and privacy pages stay up for that (docs/decisions/15-texting-registration.md).
Discussion: docs/decisions/15-texting-registration.md
Issue: #15

## 006. Three separate steps, a right-sized model per job, no side model at launch

Decided: calls run as three separate steps (Deepgram hears, a Claude model decides, Cartesia speaks), with the safety lines spoken from code as fixed text. Each job gets its own model size: a small fast model (Haiku) for live turns, a mid-size model (Sonnet) for after-call summaries, a large model (Opus) only offline as the eval grader; nothing large in the live path. Thinking stays off on every live profile and is on where nobody is waiting: the after-call technician summary and the eval grader (added after the latency review). Anthropic is the default provider and OpenAI and Gemini profiles can be switched in by config. The server is the brain's harness: lookups before hello, about six small tools, cached slots, gates, time limits, idempotent bookings and least-privilege checks on every write. No side model at launch, with a slot built for one; a decision model is added only where a measured bottleneck appears and a bake-off shows it fixes it.
Why: separate steps let the server read every caller line before the agent answers, keep the safety wording exact, and swap or right-size each part. Speech-to-speech is not faster: published first-reply times for OpenAI Realtime and Gemini Live are 0.96 to 1.14 s (SOURCE, in the readout) against about 1.0 s measured for this design on the earlier practice agent, and it costs more and can't guarantee fixed wording.
Considered: one speech-to-speech model (OpenAI Realtime, Gemini Live); a hybrid where a speech-to-speech model talks and a text pipeline checks (two of everything, more ways to fail, and the check still trails the reply); a second Haiku call beside each turn; dedicated decision models (Jev, a post-trained Laya, OpenAI Decisions).
Would change if: the small speech-to-speech bake-off matches this design on tool success and the safety gates; or the side-model bake-off finds a bottleneck a decision model fixes (docs/later.md tracks adding one in at least one place).
Discussion: docs/decisions/05-pipeline-and-models.md, docs/bakeoffs.md, evals/decision-models/PLAN.md
Issue: #5

## 005. A new Vultr server in New Jersey, just for Summit Air

Decided: run the tool server and the voice program on a new Vultr server (2 vCPU, 4 GB, New Jersey, Ubuntu 24.04, $20 a month billed hourly), not on the existing shared server.
Why: the shared server runs another project's services, is too small for 5 calls at once, gives us no admin rights (so it needed an ngrok tunnel) and sits in Atlanta. The new one is the cheapest option in the New York area, uses the existing Vultr account, isolates Summit Air from other work, and can be deleted after the interview.
Considered: keeping or resizing the shared server; DigitalOcean (New York) and Akamai Linode (Newark) at about $24 a month; Hetzner (prices unclear after its 2026 increases, not near New York); usage-billed platforms such as Fly.io (no saving for an always-on program).
Would change if: call volume needs more than about 5 calls at once (resize, or add a second server as a hot spare).
Discussion: docs/decisions/16-server.md
Issue: #16

## 004. Build our own LiveKit stack, with Retell as the baseline it has to beat

Decided: option D. The product is our own stack: Deepgram, a Claude model and Cartesia, run by our program on our own server, with LiveKit's free plan connecting the phone line. A quick Retell setup on the existing 678 number is the baseline. Two numbers exist only so both can be called during the bake-off: 678 rings Retell, (646) 624-0418 rings our stack through a Twilio Elastic SIP Trunk. Both are scored on the same eval scenarios plus 10 real phone calls each; afterwards there is one number, the winner keeps it, and the other voice folder is deleted.
Why: our own stack lets server code decide every hang-up and transfer (Retell's tools can't end a call or veto its built-in transfer), lets each part be swapped or right-sized, and appears to match how Revin's voice team builds. Retell is the strongest managed option, so beating it on real calls is the evidence; it costs a few hours because the tool server, prompts and evals are shared.
Considered: LiveKit only (no evidence it beats a managed platform), Retell only (polished, but our code can't stop a hang-up and we can only turn its dials), Retell with our own model through its custom LLM mode (keeps Retell's turn-taking but still tied to Retell), Vapi (the same trade as Retell with no head start) and Pipecat (the same trade as LiveKit, with less sign Revin uses it).
Would change if: the Retell baseline beats our stack on turn-taking or reply speed on real phone calls, in which case Retell, possibly in custom LLM mode, becomes the product.
Discussion: docs/decisions/04-voice-platform.md, docs/explorations/003-capacity-and-cost.md, docs/explorations/004-call-control-and-numbers.md
Issue: #4

## 003. The mock sells technician-windows, not counters
Decided: In the mock ServiceTitan, a slot is one technician in one arrival window on one day. Open capacity is technicians on shift with the right skills, minus those booked, minus a few held for urgent jobs. A database constraint allows one live appointment per technician per window.
Why: A counter can go wrong under parallel requests; a unique row cannot. It also gives skill matching, an on-call technician and a real technician to put in the job.
Considered: A capacity table with a count per window and business unit (simpler, but needs extra locking to be safe).
Would change if: Real ServiceTitan capacity turns out to be count-based and the adapter needs the same shape from the mock.
Discussion: docs/ai-process/briefs/01-mock-servicetitan.md
Issue: #13

## 002. Test with our own harness, scored by code first

Decided: our own pytest harness. A simulated caller talks in text to the real prompts and tool server on a scratch database. Code checks on the tool-call log and saved records decide pass or fail; an LLM grader scores conversation quality and can fail a run the code passed, never rescue one it failed. About 10 real audio calls are added once the voice platform is chosen. A quick run is about 8 scenarios (about $1, ASSUMED); the full suite runs only at a release gate (about $15, ASSUMED).
Why: the rules that matter (safety lines before anything else, read-back before booking, no commercial bookings) live in server code, so only a harness that reads server state can prove them. The earlier practice agent's worst bugs were ordering bugs that a transcript judge misses and a tool-log check catches exactly.
Considered: the voice platform's own simulator (Retell's can't check tool arguments or the database; LiveKit's only works if LiveKit wins), a general eval framework such as promptfoo or Braintrust (adds a dependency without removing the harness), hosted voice testing such as Cekura, Coval or Hamming (judges from outside the call, unpublished pricing).
Would change if: LiveKit wins the platform decision and our harness grows past about 500 lines, in which case LiveKit's test helpers take over the per-turn checks.
Updated after Jackson's review: the same harness scores both voice stacks (Retell through its text chat interface, not its own simulator), scenarios are realistic (stutters, corrections, transcription errors) and labelled by flow and difficulty so suites are chosen by tag, and LangGraph and Langflow were considered and not used (docs/decisions/06-eval-design.md).
Discussion: docs/decisions/06-eval-design.md
Issue: #6


