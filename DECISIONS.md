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

## 118. Summit Air doesn't link a caller's calls

Decided: `repeat_callers.link_calls: false` for Summit Air. A call from a number that called a minute ago starts with nothing from the earlier call: no thread, so no earlier lines in priority or safety, no booking limits or bookings counted across calls, no resumed turns. Bookings still exist in the field service system, so an open visit at the same address is still found there. The linking code stays for a tenant that turns it on, and its tests run as such a tenant.
Why: five scripted calls from one number as different people (call_feed25cb0e5df1017695f9dc210 to call_f0846787038f654309219adb1e8): a boiler tune-up heard the previous call's sparks line and carried its priority reason, and the next call's booking was refused as over the call's limit. Jackson: state carrying between calls is "gross". DECISIONS.md 115 had already dropped the cut-off greeting and the safety carry-over; this removes the rest.
Considered: keeping the link and scoping each reader (priority, safety, limits, resume) to the current call, which leaves a link that does nothing and a thread every new reader has to remember to ignore.
Would change if: real callers drop and call straight back often enough that picking up where the call stopped matters more than the demo's back-to-back calls.
Discussion: docs/ai-process/coauthoring.md safety r2
Issue: #149

## 115. Each call stands alone: no cut-off greeting, no safety carried over, do-not-call after a yes

Decided: a call from a number that called a minute ago greets like any other call (no "we got cut off"), and nothing about an earlier gas or carbon monoxide scare carries into it: not its safety state, its sign-off, its priority reason, the earlier call's safety lines in the context, or the job summary's priority line. mark_do_not_call returns a confirm question first and marks only after the caller says yes; `undo` takes it back, and "just kidding" is not a request. A flag whose callback couldn't be stored (a do-not-call number) returns no callback time. "End this call", "hang up" and "I'm done" count as goodbyes. find_slots takes `moving_a_visit`, and a move hears no priority line. The live-world reset refuses while a call is live, and clears the do-not-call list.
Why: Jackson's call call_7255ab47e30637f207e49f5bdeb, 82 seconds after his gas test call: it opened with the earlier call's "make sure you're outside and have called 911", listed gas as a priority reason, wouldn't hang up, announced top priority on a move, refused his own visit after a reset one second into the call, promised a callback at 5:18 PM with none stored, and his number was on the do-not-call list from "don't call me then" said in passing on an earlier call. His words: "i want to get rid of the cut-off thing because likely in the demo he'll call the same number a few times."
Considered: keeping a dropped call's safety state when its lines weren't said yet; Jackson asked for safety per call, and a drop before the lines already flags a person to call back.
Would change if: real callers drop mid-safety call and call straight back often enough that the lines should carry over.
Discussion: docs/ai-process/coauthoring.md safety r2
Issue: #149

## 114. Arrival windows are 30 minutes by default

Decided: find_slots offers 30-minute arrival windows unless the caller asks for another length (`window_minutes`, any run of 30-minute blocks as in 108). A time the caller names starts a 30-minute window. Supersedes 108's two-hour default; nothing else in 108 changes.
Why: Jackson on his gas test call call_0246b91f77e01a4b18583e6ca8b heard "5 to 7": "by default, it should give a 30 minute way, not a 2 hour window."
Considered: one hour, as a middle ground; Jackson named 30 minutes, and the capacity blocks are already 30 minutes, so it needs no new unit.
Would change if: technicians can't keep 30-minute promises on real days (late arrivals in the live schedule or from Summit Air's dispatcher), in which case the default widens and the caller can still ask for a tighter one.
Discussion: docs/ai-process/coauthoring.md safety r2
Issue: #149

## 113. Safety lines are advice said once, a caller can take them back, and a safety call hangs up after its sign-off

Decided: the gas and carbon monoxide lines are advice ("the safest thing is to get everyone outside and call 9-1-1..."), asked nothing, and said once per hazard (repeat_limit 0, no shorter variants). They count as said once the agent has spoken after they were handed out. A caller who takes the report back ("never mind", "false alarm", "the gas company said it's fine") clears the safety state at any point unless the same line still reports a smell or only says they've gone outside; the job gets a note and the call books as usual. end_call on a safety call says the sign-off once and hangs up. Lines write "9-1-1".
Why: Jackson's gas test call call_0246b91f77e01a4b18583e6ca8b: the lines were repeated, the agent asked "are you outside the house now?" and told him to get out, "No, I mean, I don't think it's leaking" didn't clear it, and the call never ended after "I'm done". His words are in docs/ai-process/coauthoring.md (safety r2).
Considered: keeping one shorter repeat (eval round 3's "offer one more time"), which Jackson heard as the same statement again; clearing only on "there was never a smell", which missed every way he took it back; leaving the call open after the sign-off, as the gas utility advice behind it is about the caller's phone, not ours.
Would change if: a retraction phrase clears a call that still had a real smell in the evals or live calls, in which case the phrase list narrows.
Discussion: docs/ai-process/coauthoring.md safety r2
Issue: #149

## 112. Summit Air runs on Retell, starting from Jackson's own agent

Decided: the product is a Retell agent, "Summit Air Jackson" (agent_a3e38e6aa3d4e75dfac12aafc8, LLM llm_12eba5f1d4a3b067f0635097de30), which Jackson built and tunes in Retell's dashboard with Retell's builder assistant. Retell runs the phone line, listening, turn-taking and voice; Claude Haiku 5.5 runs on a short prompt; our FastAPI tool server and mock ServiceTitan still own every fact and every rule (lookups, the read-back, booking, safety, priority, identity). Work stops on the LiveKit custom stack and on the other slate entries. Testing moves to Retell's platform: web calls from a laptop and Retell's own test tools, against our tool server.
Why: we tried many paths and gave each a fair test. We put the most time into an intelligent custom stack (LiveKit, Deepgram, Haiku, Cartesia, with a side model, stage-scoped rules, a speech filter and a prompt of about 6,000 words), because owning every layer promised more control. On the audio rig and on Jackson's calls, that layering was the problem rather than the fix: the speech filter left half replies and 21 s silences, re-read addresses and cut questions, and every fix to one layer exposed another. The custom stack's time to first sound stayed around 2.0 to 2.6 s (MEASURED, audio rig, evals/rounds/simplicity-slate-01.md); Retell native and Jackson's agent ran 1.2 to 1.7 s at the median on his calls (MEASURED, Retell call latency). Jackson liked Retell native on both calls ("I do like it"; "overall good"). The simplicity slate (one call per stack: Deepgram Voice Agent, Retell native, GPT-Live-1, and others) showed that an integrated platform with far more engineering behind its listening and voice beats what we could build in the time we have, while our server stays the part that makes the agent safe and correct. Splitting effort across two voice layers sharing one server had also made every Retell fix fight the custom stack.
Kept, not thrown away: the tool server, the mock, the gates, the eval harness, the owner view and the bench, all of which the Retell agent uses. The LiveKit worker and its measurements stay in the repo as the reference stack and the record of what was tried (DECISIONS 004, 087, 107, 111 and the round docs). Closed unmerged with their branches kept: the live-like audio suite (#157), the Deepgram Voice Agent entry (#162), GPT-Live-1 (#163).
Considered: continuing the custom stack (the layering kept producing new failure modes); Retell native as built by our lane (Jackson's own agent is further along, and tuning it in Retell's dashboard is how a real deployment would work).
Would change if: Retell can't meet a gate the job needs, in which case the custom stack is the fallback with today's fixes in it.
Discussion: evals/rounds/simplicity-slate-01.md, docs/ai-process/design-trail.md
Issue: none

## 111. One stack to push forward: the full custom stack and Retell native, compared on Jackson's calls

Decided: the project narrows to one choice between two stacks, then all work goes into the winner. The two are our full custom stack (LiveKit, Deepgram Nova-3, Haiku 5.5, Cartesia, our full prompt and server) and Retell native (Retell's ears, turn-taking and voice, Haiku 5.5 on a one-page prompt, our tool server for lookups, the read-back and booking). Jackson calls the full custom stack once with today's fixes, compares it with his two Retell native calls, and judges which one can improve the most. Paused until then: the Deepgram Voice Agent entry (PR #162), our stack stripped (#160's profile switch), Flux, Retell native round 3, and Jackson's own Retell build, which he keeps on the side. GPT-Live-1 was already dropped (107); Vapi and the old Retell single-prompt agent are dropped.
Why: the project had grown too wide to finish. Seven entries on one slate, five lanes, and two platforms sharing one tool server meant every Retell fix needed server changes that don't line up with the custom stack, and the reverse. Jackson's words: "We're trying to do too much at once, and we don't have enough time. I want to decide on one stack to work on." Choosing one lets the server's gates and the prompt be shaped for a single voice layer and refined from there. The question he is testing: whether a custom stack can compete with an integrated platform that has far more engineering behind its listening, turn-taking and voice.
Considered: finishing the whole slate first (more evidence, but too slow); picking Retell native now on its two good calls (skips the one stack he hasn't heard since today's fixes).
Would change if: neither stack is good enough on its next call, in which case the paused Deepgram entry is the fallback.
Discussion: evals/rounds/simplicity-slate-01.md
Issue: none

## 110. Retell's dials, set from measurement: accurate ears, no conversational rewrite, facts in the prompt, our hang-up
Decided: the Retell agents run `stt_mode` accurate with noise cancellation, `responsiveness` 1.0 without dynamic responsiveness, backchannel on, one reminder after 10 s, the Handbook's conversational personality off, the company facts in the prompt rather than a knowledge base, and our own `end_call` through the bridge rather than Retell's built-in End Call. Every dial is listed in docs/retell-knobs.md with its source.
Why: replaying Jackson's own replies from live call 3 into Retell (voice/retell/turn_probe.py): fast transcription missed his quiet "sure" 2 of 2 times and answered into his mid-sentence pause; accurate heard 12 of 12 and waited, median 1.70 s (MEASURED). The conversational personality split replies with a pause in 3 of 6, against 0 of 6 without it, at no speed cost (MEASURED). Retell puts knowledge-base text inside the caller's turn, and the model kept answering it aloud ("reference material", three times on one call); with the facts in the prompt, none (MEASURED, call_411b6aa0d8810886254f4d3e558). Retell's End Call hangs up 0.02 to 0.07 s after the goodbye, which Jackson found rushed, and skips the server's gate; ours waits 1.5 s.
Considered: Retell's Code tool for letter questions (it takes no arguments, and the model narrated the extra step aloud), a stricter knowledge-base threshold (0.75 still leaked), the professional and conversational Handbook presets, sonic-3.6 (questions fell at the end), Expressive mode on Retell's own Max (closest to Jackson's "Max happy" pick, kept on the native copy).
Would change if: a live call shows accurate transcription too slow on long answers, or Retell lets a knowledge base answer outside the caller's turn.
Discussion: docs/retell-knobs.md, voice/retell_native/README.md, evals/results/retell-turn-probe/
Issue: #154

## 108. Arrival windows are any run of 30-minute blocks, two hours unless the caller asks
Decided: capacity is held in 30-minute blocks from opening to closing (config.yaml `scheduling`), and an arrival window is any run of them, booked and stored by its start and end ("0730-0930"). find_slots offers the earliest open two-hour window by default; a caller who names a time is offered the window that starts at the half hour on or before it, two hours long and cut short at closing ("7:30" gives 7:30 to 8); `window_minutes` takes any other length in 30-minute steps, and arrival hours narrower than two hours ("before 9") get a window that fits ("8 to 9"). A technician can take a window when free in every block of it, the urgent hold applies in every block (2 on weekdays, 1 on weekends), and the mock answers capacity in blocks and refuses a technician whose live appointments overlap. A window that starts this minute is not offered. The owner view keeps 2-hour columns; a visit shows in every column its window overlaps. The night window is unchanged.
Why: Jackson on Retell call call_9826ff9f0dfa8bc835a3601368b asked for 7:30 and heard "4 to 8": "4 hours is too large a gap to be a window", then "the atomic unit becomes 30 minutes, not 2 hours".
Considered: fixed 2-hour windows (eight to ten, ten to twelve and so on), which still can't give 7:30; and making the seeded world book 2-hour visits, which would have changed every eval scenario's world. The seed keeps booking its filler visits as the same 4-hour runs, so the world stays as it was (MEASURED: the mock and eval tests that pin the world pass unchanged).
Would change if: real ServiceTitan's capacity can't be read in blocks for the tenant (its arrival windows are configured, not free-form), in which case the adapter maps the tenant's configured windows onto blocks.
Discussion: Retell call call_9826ff9f0dfa8bc835a3601368b
Issue: #149

## 107. OpenAI's full-duplex model (GPT-Live-1) is dropped

Decided: GPT-Live-1, on OpenAI's Live Sessions endpoint over SIP with our tool server, is off the slate and not tested further. Its lane is stopped. Its code stays on PR #163, unmerged, and the gate line (+1 929 810 4875) stays as a test number for the other entries.
Why: on its one slate call, Jackson's verdict was "it's not in a place right now where it's even worth continuing to iterate on". It answered the trivia wrong and didn't steer back to heating, took too long to respond, sometimes didn't respond at all, and didn't remember what had been said. Before that call it had also read back an invented phone number until our server's sentences were sent as exact-speech instructions. The forum research called it the most natural model on a phone line but weak at following instructions (docs/explorations/019-forum-stacks.md), and on this job instructions are what matter: safety lines word for word, gates, exact read-backs.
Considered: more prompt work, or playing our own audio for the server's sentences. Both add the layering the slate exists to test, for a model that failed on the basics.
Would change if: a later full-duplex release follows instructions and tool results reliably on a scripted phone call.
Discussion: evals/rounds/simplicity-slate-01.md (call 3)
Issue: none (slate entry 7)

## 106. A caller who says they're someone else isn't the account on the number
Decided: when the caller introduces themself by a name that isn't the matched customer's ("No, I'm Jordan Lee. I'm new."), the server stops treating that customer as the caller: no question about whose account a new address goes on, and the visit goes on a new account in the caller's name alone. The account question, when it is asked, is one plain yes or no: "Should I put this under your account?" The read-back says "ZIP code" before the digits, and a unit heard with the ZIP run into it ("Unit 210009") keeps only the unit. On Retell, a caller's goodbye-only line inside the hang-up grace keeps the hang-up, and a goodbye the agent repeats arms it again.
Why: Retell call call_1840576e622e3d729b760085142 (MEASURED, tool log on the server). The number was on file under Pat Rivera; the caller said they were Jordan Lee and new, was asked "Should that address go on its own account, with you as a contact, or under your account?", answered "I'm not sure what you mean by account", and was booked on a new account with Pat Rivera as its contact. The unit was read as "unit 210009", and after "Thanks. Bye." the line stayed open 22 seconds until the caller hung up.
Considered: matching any name the caller says (a caller booking "under Rosa" for a parent's house would lose their own account), and asking the identity check before every new-address booking (an extra question for the usual case).
Would change if: callers on file are often heard introducing themselves by a nickname that shares no word with the name on file, which would book them as new customers.
Discussion: Retell call call_1840576e622e3d729b760085142
Issue: #149

## 105. All five boroughs, addresses said any way, and no hang-up after an out-of-area answer

Decided: the service area is every residential ZIP in Manhattan, the Bronx, Brooklyn, Queens and Staten Island: 214 ZIPs from NYC Open Data's Modified ZIP Code Tabulation Areas (SOURCE: https://data.cityofnewyork.us/Health/Modified-Zip-Code-Tabulation-Areas-MODZCTA-/pri4-ifjk, read 2026-11-05, every one with a population estimate above zero), grouped by borough. The six zones the seeded world uses keep their ZIPs and the new ones are marked `seeded: false`, so the eval world is unchanged row for row (MEASURED, the same rows before and after); capacity isn't by zone, so a Manhattan or Bronx address books like any other. The address check matches a house number however it was said ("one nine nine", "one ninety nine", "a hundred ninety nine", "one hundred and ninety nine"), with a direction word ("East") between the number and the street, an ordinal said either way ("fourth", "4th", "4"), and a unit after it. `end_call` is refused as a hang-up right after an out-of-area answer until the caller speaks or says goodbye. The Retell bridge hangs up only after the agent's final line has played, plus 1.5 s, reading the call again if a longer final line has appeared. In speech, an off-topic question is answered correctly in one line and then steered back (a letter count spells the word out first: "Seventeen is s, e, v, e, n, t, e, e, n, so four e's."), Retell's agents use the same voice as ours (Max), "The what?" gets the last question again in simpler words, "No problem" follows only a thank-you or an apology, and "Maybe" to "is it the number on the account?" means try it.
Why: on Jackson's call on the Retell number (call_ca9bfcec0b968db42ac2f428bea), "One nine nine East fourth Street" was refused three times as an address the caller never said, then 199 E 4th St, 10009 was refused as out of area, the call ended at once with end_call, and Retell stopped it after "I'm sorry," because the hang-up delay was sized from that fragment. He also asked "How many e's are in seventeen?" and heard "three", and heard "No problem" to "The what?". Jackson: "you should make this agent capable of any nyc, any of 5 boroughs zip code should be in zone" and ""one. nine. nine." should be same as "one hundred ninety nine"".
Considered: adding the new ZIPs to the existing zones (it would reshuffle the seeded world and stale every reference); a fixed longer hang-up delay on Retell (it still cuts a long sentence, and slows every short goodbye).
Would change if: the company adds areas outside the city (then zones by county, and the seed told which to use).
Discussion: docs/ai-process/coauthoring.md (service area r1)
Issue: #149

## 098. A failed name check stays on its call, an old address is found without a guessed ZIP, and names and ZIPs are said back plainly

Decided: a name and address check that failed on an earlier call no longer refuses a change on a later linked call: the new call asks its own question (and a callback it offers goes to the number on file only if this call's check failed). `find_customer` by address leaves out a ZIP the caller never said, matching the house number, street and unit exactly. A mover from a number not on file who reaches `record_move` unmatched is told to find the account by the old address first, and "I moved from" now counts as saying they moved. A long sentence the caller already heard this turn, the server's or the model's, isn't said again. In speech, a name the caller just gave isn't said straight back ("Got it, Pat." becomes "Got it."), and "ZIP 11201" is said as "the ZIP code is 1 1 2 0 1".
Why: on live call 2 Pat Rivera, the customer on file, gave the right name and old address, confirmed the read-back, and was refused: the call was linked to call 1 three minutes earlier, where the caller had given a wrong name ("Sam Rivera"), and that refusal carried over (MEASURED: identity_checks has one row, call 1's mismatch, and call 2's `linked_call_id` is call 1). The refusal was then said twice back to back. Replaying call 2 in the eval with Jackson's lines, the model guessed ZIP 11211 for an old address in 11235 and never found the account, in 2 of 2 samples, until the guessed ZIP was left out (then 2 of 2 moves went through). Jackson: "didnt like how it said my name right back....and just say the zip code is, not just zip 11201".
Also from call 2: a callback window the model builds around one time the caller named ("Tomorrow at two PM" passed as 1 PM to 3 PM) is that time, said back as "tomorrow around 2 PM"; `find_slots` treats such a time as the window that holds it, not a limit on arrival (call 2 heard "Tomorrow has no openings" because no 4-hour window fits 1 PM to 5 PM); `find_slots` offers one choice, the caller's day first, else the soonest ("Friday I have 12 to 4. Does that work?"), with days in date order and a later week's day dated; and a mover whose name check failed hears in plain words why the visit can't be booked at the new address.
Considered: keeping a failed check across linked calls (it refuses the real customer when someone else called first, as here); refusing a guessed ZIP with "What's the ZIP code there?" (a mover often doesn't know the old one, so the search would stall).
Would change if: a caller tries name after name across calls (then a limit on checks per number per day).
Discussion: Jackson after live call 2, docs/ai-process/coauthoring.md; call `call-_+16784774024_MXJ5xsvpZQ5H`
Issue: #149

## 097. One wrong answer to the name check ends it, and a cancel needs no reason

Decided: from a number that isn't on the account, an answer to the name and address check that gives what was missing and doesn't match is final: the server refuses the change once, politely, and offers a callback to the number on file. It asks again only when the answer had no name or address in it ("Why do you need that?"). A cancel's reason is optional: the agent may ask once, lightly, and cancels whether or not the caller says. "I'll cancel it now" and "I need to find the booking first" are dropped from speech.
Why: on live call 1 the caller gave "Sam Rivera" and heard "And what's the name on the visit?", then the model's own "I need the name exactly as it's on the visit", then the refusal, three asks for one answer. The same call was told "I still need a reason to note on the visit before I can cancel" after saying no. Every gate from #134 is unchanged: only the caller's own words count, the agent never says the name or address on file, and nothing changes without a match.
Considered: keeping the second ask for every wrong answer (#134's rule; it reads as a hint that the first answer was wrong, and the caller had already answered); a reason required but never asked twice (a caller who won't say still can't cancel).
Would change if: live calls show real customers failing the first answer to a transcription error on their own name (then a second ask that says nothing about the first).
Discussion: Jackson after live call 1, docs/ai-process/coauthoring.md; call `call-_+16784774024_qSVS59jLcAPP`
Issue: #149

## 096. A holding line before a look-up, and no reply that leaves the caller in silence

Decided: a reply that calls `find_customer`, `get_customer_history` or `find_slots`, and would leave the caller with nothing before the wait but an acknowledgment ("Got it.") or nothing at all, ends with one of the tenant's holding lines, spoken in the same reply by the live voice: "Just give me one second.", "Let me pull that up." or "One moment." (`voice.holding` in agent.yaml), in turn and never the same one twice in a row. The prompt also asks the model for a short holding line of its own before a look-up; when it gives one, nothing is added. Bookings, moves and cancels get none: the server's confirmation is spoken the moment the tool answers. The pre-recorded bridge clips stay for a slow tool (after 0.7 s), re-worded the same way and taken in turn. Separately, a reply with no tool call and no question that promised work ("I'll look up the visit now"), that the caller heard none of, or that only acknowledged them ("Got it.", "Sure, I can set up an AC tune-up."), gets one follow-up from the model at once, with a note saying what is missing and no tool forced; never after a booking, callback or other request went through, and at most one until the caller speaks again.
Why: on live call 1 every tool answered in under 0.3 s (MEASURED, the call's tool log), so the timed clip never played, and the wait the caller heard was the model's second step. Jackson asked for "okay, I'm gonna do that, just give me 1 second... something very human". The model does open with its own line, but names the street in it ("Got it. I'll look up the tune-up at 142 Main Street now." in 4 of 5 offline replays of that turn, MEASURED), and that sentence is dropped as a restatement, leaving "Got it.". On turn 7 the same reply also had no tool call: "Got it." and then 19 s of silence until the caller asked "Is that all?". It happened twice more on Jackson's next call ("Sure, I can set up an AC tune-up." and "Yes.", each followed by about 20 s of silence until the caller spoke, MEASURED from the turn times of `call-_+16784774024_MXJ5xsvpZQ5H`).
Considered: a pre-recorded clip played at once when nothing was heard (it can't follow "Got it." without racing the reply, and adds its own length to a reply that is ready); lowering the clip's `after_s` (no tool is slow enough to trigger it); the prompt alone (the model's own line is dropped whenever it repeats the address).
Would change if: the holding line on every look-up feels like too much on live calls (then only on the first look-up of a turn, or every other turn).
Discussion: Jackson after live call 1, docs/ai-process/coauthoring.md
Issue: #149

## 095. Bookings seven days a week, and an open schedule on the live demo

Decided: the company hours are 8 AM to 8 PM every day, so Saturday evening and Sunday windows can be booked, with one technician a window held for urgent work on weekends as on weekdays. On the live server, `scripts/open_live_schedule.py`, run by hand at deploy and after a reseed, staffs the next two weeks (weekends with two in three service technicians, 8 AM to 8 PM) and cancels the seed's filler visits, keeping one in six and any visit a live caller changed; it changes nothing on a second run. The named customers' visits the eval scenarios use are never touched, and the seeded world (the eval world) is unchanged row for row: a window nobody is on shift for books nothing and draws no random number (MEASURED: the same appointment rows before and after, `tests/test_open_live_schedule.py`).
Why: on live call 1 Friday had nothing open, because the live world was seeded on November 3 and its nearly full days fell on Thursday and Friday this week. Jackson: "most times should be available... Saturday and Sunday should also be available", while keeping the active appointments the eval set needs and "a few extra ones" for his own runs. After the script, every window from Wednesday to the next Wednesday has 83% or more of its technicians free, Friday 92 to 95% and Sunday all of them (MEASURED on a copy of the seeded world, the mock's own capacity count); before it, Friday had 15 of 25 to 9 of 18 free on that copy, and Sunday nothing.
Considered: changing the seed itself (it is the eval world, so every reference result would go stale); a live-only copy of the tenant config (two configs to keep in step for one setting).
Would change if: an owner wants weekend hours that differ from weekdays (then a per-day setting the owner view can show).
Discussion: Jackson after live call 1, docs/ai-process/coauthoring.md
Issue: #149

## 094. A new voice: Cartesia's "Max"

Decided: the agent speaks with Cartesia's "Max" (voice id `3945925b-9143-4b7d-9ab5-cc4878001106`, listed by Cartesia as "Conversational Support", a warm, natural American male) on sonic-3, replacing Katie (`f786b574-daa5-4673-aa0c-cbe3e8534c02`), the LiveKit plugin's default. The bridge clips and the at-capacity message are re-recorded in it, and `voice.tts.name` carries the name for the Retell push.
Why: Jackson after live call 1: "I didn't like the voice... Just choose any other one." Max is clearly different from Katie (a man's voice rather than a young woman's), and Cartesia describes it for customer service conversations (SOURCE: Cartesia's voice list, `GET /voices`, read 2026-11-04).
Considered: Jameson ("easygoing support", male), Harlow ("warm support", female), Skylar ("friendly guide", female). Any of them would do; a woman's voice risked sounding like the same complaint.
Would change if: Jackson or Paul prefers another after a listen; one line in config/models.yaml and `python -m voice.livekit.clips` change it.
Discussion: Jackson after live call 1, docs/ai-process/coauthoring.md
Issue: #149

## 093. The schedule can be edited from the owner view and the bench, live into the agent

Decided: a Schedule tab on the owner view and on the bench shows the day as ServiceTitan holds it, a row per technician and a column per arrival window. A person can block a slot or a whole window, put a named event on it, add a job for a customer found by phone, change a job's window, type, notes and service location address, and (admins only) a customer's name and phone number. Blocks are ServiceTitan's non-job appointments, which the mock now takes out of capacity; every edit goes through the FSM adapter, is logged in `schedule_edits` with who made it, and clears the slot cache, so the agent's next find_slots, call start or find_customer sees it. The bench adds what the agent would offer in each window and a live list of what changed in ServiceTitan. A block is taken off the schedule, never deleted, and never goes over a technician's visit; Block all is one block per technician free in that window. A phone number already on another customer needs a confirm, and a shared number makes the person pick the customer.
Why: for the demo, Jackson wants to answer "what if that slot was booked?" by booking it on screen and showing the agent work around it on the next call. The slot cache would otherwise keep the old capacity for up to 60 s (`SLOT_CACHE_SECONDS`, MEASURED: `tests/tool_server/test_ts_schedule.py` blocks a window that find_slots offered a moment before and finds it gone on the next find_slots).
Considered: blocks as technician shifts of type TimeOff (ServiceTitan's shifts are the working pattern and the mock lists only Normal and OnCall; a non-job appointment is how an office takes one slot); writing the mock's database from the tool server (faster, but the agent's path would not be the one tested); a cache time of 0 (every turn would wait on capacity; clearing on write gives the same freshness for edits made here).
Would change if: a real tenant refuses a non-job appointment that takes a technician out of capacity, or the edits move to ServiceTitan's own screens, where a webhook or a short cache time would have to replace clearing on write.
Discussion: issue #152; docs/design-system.md, Schedule
Issue: #152

## 092. Lookup prefetch made no difference and stays on its branch

Decided: not merged. The code stays on branch `joco/lookup-prefetch-4106b2` (PR #153, closed), so main stays simpler; with it, the tool server can run the area or address lookup a caller line asks for before the model answers, and hand the model the result as a note (`voice.prefetch_lookups` in config/models.yaml, app/prefetch.py). It stays off. Turn it on only if live calls show lookup turns that feel slow, and then first try the next step below. It runs `check_service_area` on a line with one ZIP and `find_address` on a line with a street and no ZIP, through the real handlers, so every gate refuses as before; it never runs `find_slots`, because the read-back comes before slots and `find_slots` records the wait the caller heard and decides priority.
Why: on the audio rig it made no difference worth turning on. The lookup-turn median went from 2.60 s to 2.70 s and the plain median moved 0.19 s with nothing changed on those turns, so both are noise; the model still made a median of 3 requests on a lookup turn, because on lines with a street and a ZIP it still called `find_address` after the server's area check (MEASURED, evals/rounds/lookup-prefetch-01.md). The slowest 10% of lookup turns fell from 4.26 s to 3.34 s, on two or three turns out of about 20. With it on, must-pass failures on the text harness fell from 4 calls to 2, and no miss traced to the switch except one possible re-ask of the address (MEASURED, same file). One harm was found and fixed: a note that said never call the tool again froze a call whose result asked which borough.
Considered: running `find_slots` from the server too (it would offer times before the read-back and record a wait nobody heard); speaking the server's result directly (the model has to phrase the answer and ask the next question).
Would change if: live calls show slow lookup turns. The next try is running both lookups on a line with a street and a ZIP, so the model has nothing left to call there, measured on the rig the same way.
Discussion: evals/rounds/lookup-prefetch-01.md; docs/latency.md
Issue: #142

## 091. One simple Rules page, on the debug bench only

Decided: the owner view's Rules page and the bench's rule editor become one page at `/bench/rules`, in reading order: **Your rules** (a short list, each rule with its stage and where it reaches the agent, plus one row to add one), **Fixed responses** (the server's lines and hard rules, one row per trigger with the exact line, behind eight topic tabs so one topic shows at a time), **System prompt** (the sections `prompts/assemble.py` joins, in order, on a sample call, collapsed), then Versions. `/owner/rules` redirects there (308) and the owner view has no Rules tab. Every row is a trigger in plain words and the exact line; variants of one line share a row, a check that says a line shows it on its row, and each line is said on one row only, so 95 lines and 40 checks read as 83 rows. The 49 company settings and the prompt cut into 66 rows are gone from the page: settings are configuration, and the prompt is shown whole. The six lines the owner may reword are fields in place among the fixed responses. A version's note is written from the change. The page reads the prompt from `assemble.sections()`, which `instructions()` is now built from, so the two can't drift; the prompt text is unchanged byte for byte. Every server rule, the guidance check and the must-pass gate on publish (031, 055) are unchanged.
Why: Jackson found the two pages overcomplicated, "a hodgepodge of random rules that are disparately connected", and asked for less at once, on the bench only, with the assembled prompt on the page. The owner page was 251 rows and 17,032 px tall (MEASURED, headless Chromium at 1440 px on `make owner-demo`); the new page is about 1,860 px.
Considered: tabs for rules, fixed responses, prompt and versions (B: the least at once, but your rules never sit beside what they are checked against); fixed responses and your rules side by side (C: everything in one scroll, 6,975 px, cramped on a phone).
Would change if: the page's main use turns out to be an engineer scanning every fixed line rather than an admin adding a rule (then C's single scroll).
Discussion: docs/explorations/018-rules-page.md; Jackson picked A ("let's go with your recommendation of one topic at a time").
Issue: #140

## 090. The side model stays on OpenAI Decisions, at a 0.65 threshold

Decided: the side model that reads whether someone at home is at risk stays on OpenAI Decisions, not Jev, and a yes now needs a probability of 0.65 or more instead of 0.5.
Why, the threshold: Jackson saw it overfiring, and the question is written to allow implied mentions rather than requiring an explicit one, so a borderline line can cross 0.5. On the 80 labeled test lines, the only answer between 0.5 and 0.65 was a false alarm ("my daughter just turned five / the radiator in her room is cold", 0.63, labeled no), and every true yes scored 0.78 or more, so 0.65 removes that false alarm and misses nothing (MEASURED, `evals/decision-models/results/20261103-220655-vulnerable_person`). On the 131 answers from the 46 eval calls in side-model-02, every answer was 0, 0.01 or 1.0, so those calls are unchanged (MEASURED).
Why, the vendor: OpenAI offers zero data retention for eligible customers in writing (SOURCE: OpenAI's Decisions guide, via docs/explorations/014-latency-and-decision-models.md), and these are callers' words about older, sick or pregnant people at home; TypeSafe published no retention terms for Jev. Jev is faster (68 ms median against 223 ms, MEASURED, side-model-01), but the side model runs beside the reply, not in it: OpenAI's answer was already back on 47 of 47 reads when priority was decided, so the caller never waits on it and the speed gain never reaches them. Cost is about $0.0004 a call either way, about $1.60 a month at 4,000 calls (MEASURED for OpenAI; Jev about $0.70 a month, ASSUMED).
The question was also tightened, at Jackson's request, so a condition heat or cold doesn't make dangerous (a bad back, a broken wrist, allergies, ADHD) and people near the home rather than in it (a neighbor upstairs, the old lady next door) no longer count. On 12 new hard negatives the old wording raised 6; the new one raises none, and with the phrase rules alongside it still catches every at-risk line in both sets (MEASURED, evals/rounds/side-model-03.md).
Considered: keeping 0.5 (the measured eval calls never sat near it, but the test set did); a first rewrite that asked whether someone was at risk "from the heat or cold there" (it stopped counting plain facts like "my grandmother is 82", missing 8 of 43); Jev (equally accurate, faster where nobody hears it, no retention terms).
Would change if: the 480-line plan run shows true yeses between 0.5 and 0.65 (lower it back), or a decision model goes on the reply path, where the caller waits on it (Jev's speed then matters).
Discussion: Jackson in chat; evals/rounds/side-model-01.md, side-model-02.md; DECISIONS.md 037, 056

## 087. Retell's Conversation Flow keeps every gate but can't filter the model's speech, so our stack stays

Decided: the Retell Conversation Flow version of the agent (`voice/retell_flow/`, issue #133) does not go on (678) 336-6730 for live calls, and our LiveKit stack stays the product. The flow is kept in the repository, reproducible with `make retell-flow-push`, as the tested form of Retell for bake-off 7.
Why: built to mirror our design, with our tool server behind every gate, it said the safety lines word for word on every safety call (79 of 79 standard, 36 of 36 must-pass), booked only after a confirmed read-back, booked no commercial or out-of-area caller and ended calls only on the server's answer. That closes the gap that ruled out the single-prompt agent (038). But it passed 53% of the standard suite against our stack's 92% (61% against 92% where the eval instance sets scenarios up the same way) and 45% of must-pass against 88%, with must-pass code checks failing in 4 scenarios, and on the audio rig it was the slowest stack, 2.63 s median on a plain turn against our 1.90 s, because every caller line goes through our server before the reply (MEASURED, evals/rounds/retell-flow-01.md). Almost all of the gap is the model's own speech: repeated read-backs, two questions at once, internal words. Our speech filter (060) drops those before they are spoken; Retell streams the model's text straight to the voice, so a flow has nowhere to run it.
Considered: putting the flow on the number anyway for a side-by-side call (the issue's bar was every must-pass code check and within 5 points, and it missed both by far); stage-scoped prompts inside the flow (exploration 016's idea, a separate change that wouldn't add the missing filter); Retell's custom-LLM socket, where our server would see each reply before it is spoken and could filter it, at the cost of running the model ourselves, which is our stack again behind Retell's ears and voice.
Would change if: Retell adds a hook between the model's text and the voice, or a live-call round shows the filter matters less on the phone than in the grader's reading, in which case the flow is the Retell entrant to rerun.
Discussion: docs/explorations/017-retell-conversation-flow.md; docs/bakeoffs.md, bake-off 7. Jackson in chat: "if retell's conversation flow can do what we're trying to do better, then that's worth testing."
Issue: #133

## 086. A stale eval reference warns; it doesn't fail the build

Decided: the eval floor check reports a stale reference as a warning in `make check`, the pre-commit hook and CI, and still fails on what matters: a must-pass case that failed or errored in the reference run, fewer scenarios than the floor, or a pass rate under it. A must-pass scenario added after a stale reference can't be in that run, so its absence is part of the warning; on a current reference a missing must-pass case still fails. `make release-check` (the ratchet with `--strict`) fails on a stale reference too, for a release gate.
Why: 41 of the last 42 failed CI runs were this one check (MEASURED, `gh run list`, the failing test in each log). Every pull request that touched prompts or scenarios went red until a paid two-sample run, by design (one reference per batch, 066), so the failures carried no news, flooded Jackson's inbox with GitHub emails, and trained lanes to commit with `--no-verify`, which skips the real tests too.
Considered: a separate non-required CI job (the same emails on main); dropping the check from CI (loses the must-pass and floor checks on the committed reference).
Would change if: a stale reference sits through more than one batch of merges, in which case the coordinator's batch step runs `make release-check`.
Discussion: Jackson's note about CI failure emails

## 085. Two jobs on one call, and a technician asked for by name is a note

Decided: a call can book two jobs (`LIMITS["book"]` is 2). At the same address the second has to be a different job type the caller asked for, booked with `separate_problem`; the same visit twice is refused as `already_booked`, a third booking hits the limit and a person takes over. A second unit or address goes through its own read-back: a caller line naming a different unit after the read-back clears the confirmation (083), so the second job can't land on the first unit. Priority is decided per job from its own job type. When the second job has no opening, `find_slots` tells the agent to offer a named callback with `flag_person`, so it isn't dropped. A request for a technician ("can I get Mike again", "the same guy as last time") is read by the server from the caller's words and kept as a job note, "Caller asked for Mike. Not promised; dispatcher to decide." It goes into the job's notes (or onto the booked job, if asked after booking) and onto the technician summary as a fixed "Asked for" line. The agent says it will pass it on. The turn hands the voice layer the name, and any agent sentence naming that technician is dropped before it is spoken unless it passes the request on or says it can't be promised (a denylist of promise wordings leaked, PR #135 review). "I don't want Mike again" is noted as "asked not to send Mike". The server never assigns by name.
Why: callers bring two jobs ("my heat's out, and can you tune up the AC?", a super with 2A and 5C), and one booking per call lost the second. Repeating the same visit is the risk the old limit guarded against, so the guard stays per address and job type. A technician request is a preference for the dispatcher, who knows schedules and skills; a promise the phone agent can't keep is worse than none.
Considered: no limit at all (a retry or a model slip books a duplicate); a model flag for "second job" (a flag the model sets is not a gate, so the same-type and read-back checks carry it); matching the name to a technician and assigning them (the dispatcher's call, and a wrong match sends the wrong person).
Would change if: live calls show three-job calls are common (a super with a whole building), or the office wants requested technicians assigned when free.
Discussion: issue #129, Jackson's ideas 7 and 9
Issue: #129

## 084. Work Summit Air doesn't do is declined by the server from the service list

Decided: the trade config (`trades/hvac/agent.yaml`, `services.kinds`) lists how callers ask for work an HVAC company usually doesn't do: plumbing (a sink, a toilet, a clogged drain), appliances (a fridge, a dishwasher), a water heater (gas or electric) and duct cleaning. A phrase next to the trade's own equipment is HVAC work: "my AC drain is clogged" books. The tenant config (`services.not_offered`) says which of those Summit Air doesn't do and the general kind of business to suggest, with no brand. The server reads every caller line, outside safety and commercial calls; a match gets the tenant's `not_offered` line ("I'm sorry, we don't do plumbing, so a plumber would be the right call for that.") in a `[server]` note, once per kind per call, and a lead with call reason 706 (Work we don't do) that the jobs loop sends to ServiceTitan. The rest of the call goes on, so "my heat is out and my sink leaks" books the heat and declines the sink. Commercial phrasings were widened in the same config, with ownership attached to the business: "the bakery across from me is mine" (the business, then "is mine") and "I own the place, it's a deli" (owning the place, and the place said to be a business) are commercial; "I own the place, the bar next door is loud" is not. An office counts only with its heating or cooling ("the heat in my office"), so "my office called me" stays a home. A line that names the caller's home as the place ("my heat at home is out", "calling about my house") is residential whatever job words come first, which also fixes two declines already on main (PR #135 review). #126's landmark rule still blanks a business placed as a landmark, so "next to the restaurant" stays residential.
Why: the agent answered requests for plumbing or appliance work in its own words, sometimes offering a visit. A list in config is what a second company would change, and the office sees the demand as leads. Words that also describe heating ("pipes", "oven", "water") are left out of the lists, so a no-heat call is never turned away.
Considered: letting the model decide what is in scope from the prompt (no record, and no guarantee it declines); a referral to a named company (the assignment's company has no partners, and a brand is an endorsement); declining by job type alone (the caller's words come before any job type).
Would change if: live calls show a kind missed or a heating call declined, or Summit Air adds a service, in which case it comes off `not_offered`.
Discussion: issue #129, Jackson's idea 6
Issue: #129

## 083. A detail changed after the read-back resets it, and an ended window is never upcoming

Decided: on every caller line after a confirmed read-back, the server checks whether the line gives a different unit, house number, street, ZIP or phone (`app/readback.py`). One that does clears `readback_confirmed_at`, so `book_appointment` refuses with `gate_read_back` until the new details are read back and confirmed, and the agent gets a `[server]` note to read back what the caller just said. `read_back` with `confirmed` also refuses when a caller line since the sentence changed a detail, which covers "yes, but it's 4B" in the same breath as the yes. A plain yes, or the same value again ("yep, 142 Main"), changes nothing. A house number counts right before a street name, with or without its street word ("148 Main"), or right after "it's"; a one-digit number counts only before a street word or the read-back's own street, so "two healthy adults" names no house. A unit counts as "4B", "4 B" or "four B". A street counts only with its street word and a capitalized or numbered name, and a cross street ("off Flatbush Avenue") doesn't count. A value the caller rejects ("not 142") is left out (PR #135 review). Open visits whose arrival window ended before now (New York time) are marked `passed` in the lookup before hello and `find_customer`; the call note says the visit's window has ended and never calls it upcoming; booking over one refuses with `open_job_passed` and the tenant's `visit_passed` line, which asks whether the technician came.
Why: a caller who corrects the unit after saying yes was booked at the old address when the model didn't read back again, and a test call at about 2 PM heard "you're all set for today between eight and twelve". Both are facts the server already has, so they live in server code.
Considered: trusting the model to re-read after a correction (the bug); clearing the confirmation on any caller line with a number (resets on "it's been 3 days"; a false reset costs one more read-back, a missed one the wrong door, so the rule errs on resetting only for address shapes); closing a passed job automatically (the technician may be late or on site; the office decides).
Would change if: live calls show resets on lines that weren't corrections, or corrections in shapes the check misses (a unit said as "the fourth floor, back").
Discussion: issue #127 items 1 and 2, issue #129
Issue: #129

## 082. Sparks get their own calm safety route; water through the ceiling is Urgent, a tripping breaker High

Decided: an electrical route separate from gas and carbon monoxide (`app/safety.py` `electrical_line`, phrases in the trade's `safety.electrical`). Sparks or a single spark, arcing, burning plastic or wire, an electrical or burnt smell from the unit, or smoke from the unit, furnace or panel get the tenant's electrical lines once per call, said word for word by the voice layer: step away, turn the unit off, and the breaker if you can safely reach it; then the agent helps book. The route never sets the call's safety state, never says the gas or carbon monoxide lines or 911, and doesn't stop a booking. A gas or carbon monoxide signal on the same line wins, and an electrical report heard during their check question is said once that check clears. "No sparks" and a past spark the same sentence puts in the past ("it sparked once last year and it's been fine") don't fire; "it sparked when I turned it on" does. Burning dust on the first heat keeps the gas confirm question. Priority, all from the caller's words: water from the system coming into the home (through the ceiling, leaking into a room) is Urgent on any day, matched by phrases and by a water word within eight words of the cooling equipment, the ceiling or a wall (a rule's new `together` key: "water coming through my living room ceiling", "the AC is dripping water"), including a vent or register, a wet ceiling, a puddle under the indoor unit or an overflowed condensate pan, unless a faucet, sink, toilet, pipe or water heater is near the water word, since the first eval run showed callers never say the fixed phrases; a breaker that trips again and again ("my breaker trips when the AC kicks on", "it tripped when the AC kicked on", "I keep resetting the breaker") raises to High, and one that "tripped once" doesn't; sparks raise to High, quoting the caller's sentence. Jackson signed off the lines, and asked that the electrical line not make the visit wait on the unit or breaker being off: it ends "I can get a technician out to look at it", and booking needs neither.
Why: Jackson: sparks "should kind of get its own safety route because it doesn't need to be taken as seriously as a gas leak or carbon monoxide leak thing", and AC water through the ceiling "should get urgent priority", while a tripping breaker "isn't inherently urgent priority, but it's still an issue that should get some priority". Turning the unit and breaker off removes the hazard without emptying the house, and water damage gets worse by the hour whatever the weather.
Considered: reusing the gas and carbon monoxide state with a third kind (it would block booking and send the 911 sign-off, both wrong here); leaving sparks to the prompt (a rule that must happen lives in server code); raising a tripping breaker to Urgent (Jackson said not).
Would change if: live calls show callers describing electrical trouble in words the list misses, the water pairing raising calls it shouldn't ("the heat is running down" reads as water; a false raise is the safe direction), or the electrical lines' wording changes at sign-off.
Discussion: docs/ai-process/coauthoring.md (safety r1 on issue #130)
Issue: #130

## 081. A caller who steps away gets a check-in every 2 minutes and a goodbye at 8

Decided: a sentence that starts with "hold on", "give me a sec", "one minute please" and the like ("it runs for one minute then shuts off" is a symptom, not a request) puts the call on a patient hold (`app/dead_air.py`): the agent says a short yes and asks nothing. The voice worker counts the silence (`voice/dead_air.py`): every 2 minutes it says the tenant's `still_here` line ("Take your time, I'm still here."), and at 8 minutes its `stepped_away` goodbye, in Jackson's words, then hangs up with the end reason `stepped_away`. A call back from the same number the same day is linked as a dropped call is, and opens with "We got disconnected earlier when you stepped away; let's pick up where we left off," keeping what was gathered. Signals used: the count restarts only on a caller line the server calls a reply, or an agent reply; nothing is said while the agent speaks or thinks or a tool runs; voice activity (Silero) alone never restarts it, and while it hears sound a check-in waits but the goodbye doesn't; the interruption settings (two recognized words) keep a TV from cutting off a check-in. On a hold, a transcribed line counts as the caller back only when it is said to the agent: "I'm still here", "I'm back", "one more sec", "I can't find it", "almost done", a question to the agent, an answer with a number ("it says 48"), or another request to wait; anything else ("okay kids, come on", "honey, where's the bill?") is an aside the model doesn't answer, which still goes through the safety check. Timings are in `config/models.yaml` `voice.dead_air`; the text harness runs the same timer on a short clock.
Why: Jackson: "it should check in every 2 minutes. And then if it's been 8 minutes, it should say, I'm gonna hang up, but you know, once you're ready, feel free to call this number back... That way it doesn't just burn a ton of minutes." Logging the reason lets the next call say why it ended.
Considered: running the timer only after an explicit hold (a caller who walks off without saying so would keep the line open for good); letting voice activity restart the count (a TV would keep the call open); asking the model whether a line on hold was an aside (a rule about when a call ends belongs in code).
Would change if: live calls show real answers on hold read as asides (the return words are too narrow), or callers needing more than 8 minutes.
Discussion: docs/ai-process/coauthoring.md (Jackson's words on issue #130)
Issue: #130

## 080. Card numbers are never taken or recorded; billing disputes get the office

Decided: card digits are taken out of every caller line at the tool server's door and out of every tool argument before validation (`app/cards.py`), so the turns table, the tool log and its JSON lines, job notes, flags and the technician summary only ever hold `[card number removed]`. A card is found by its digit total, not line by line (PR #132 review): the window is the caller's latest line and the lines just before it that are mostly digits (digits plus filler such as "and", "okay", "my card number is"); a line of filler alone ("hold on", "um okay") stays inside it; after a card word a stretch of 12 to 19 digits in any grouping is a card, and otherwise the digits must be grouped as cards are read (fours, Amex 4-6-5 in any split, one block, or one digit at a time) and total 13 to 19 passing Luhn, except a full 4-4-4-4 or 4-6-5, which counts without Luhn since one misheard digit breaks it; a stretch is never joined by "and" on one line (a spoken ZIP "and" a phone stay); the card is taken out of every line of the window, with an expiry or a code of up to four digits, or four spoken numbers ("oh eight twenty eight"), right after it. Earlier lines that turn out to hold part of the card are rewritten in the turns table, the tool log and the JSON log on the line that completes it. A line with a street word, "dollars", "zip" or any other word isn't mostly digits, so it never joins a window, and a phone number split across lines never reaches 13 digits. Digits after "ending" or "ending in" stay: the last four alone don't identify a card, and the office may need them to find the charge. Phone numbers, ZIPs, house numbers and a phone followed by a ZIP stay. A caller who reads a card or asks to pay by card hears the tenant's `card_stop` line, spoken word for word, and a `payment` flag is stored once; when digits were read and the call is recorded, the recording stops and is deleted as for a caller who objects, with no extra line. A gas or carbon monoxide line said on the same turn comes first, but the flags and the billing callback are still stored, and a card offered after the gas lines still hears the card stop. A caller who disputes a charge hears `billing_callback`, with a callback stored first: a `billing` flag and a callback owned by the office, `billing_minutes` after it next opens (ASSUMED 120), so it is never the on-call technician's. This is the one exception to decision 064, where the bench shows everything as recorded: card digits are never recorded in the first place.
Why: a card number in a transcript, a log or a ServiceTitan note is a liability no front desk wants, and the agent has no way to take a payment anyway. A billing question needs someone who can see the invoice.
Considered: masking the digits only on the bench (they would still be stored); keeping the recording and muting the segment (no tool here can cut audio); letting the model call `flag_person` for billing (the office routing and the promise would depend on the model).
Would change if: Summit Air adds a payment system the agent can hand off to, or live calls show phone numbers or account numbers taken out by mistake.
Discussion: docs/ai-process/coauthoring.md (issue #130)
Issue: #130

## 077. Another number for the visit is the job's contact, and a number on two accounts books under the name said

Decided: when the caller wants a different number used ("call my daughter at this number instead"), the read-back takes `phone_for`, whom the number reaches in the caller's words (each word must be in a caller line), and reads "the number to reach my daughter Ana is ...". The booking keeps that number as the job's contact: the job summary says "Contact: my daughter Ana, (646) 555-0142" with "Account phone: ..." beside it, the technician's summary note gets the same line from the server (the model never sees a number), and the owner view's call page shows both. The customer's own numbers in ServiceTitan are never changed; a new customer gets the number they called from as their own. When the call's number matches more than one residential account and the address doesn't settle which (it's on file for both, or neither), the confirmed read-back's sentence is the server's question, "I see more than one account on this number. Whose name should the visit go under?", and the booking goes on the account whose first name the caller says (a name word only that account has). A name said earlier ("this is Hector") settles it without asking; two asks with no one name book the first account. An address on file for only one of them books there, as before.
Why: a job contact and the account's phone are different things in ServiceTitan (contacts belong to customers and locations, jobs have none), so writing the daughter's number as the customer's phone would send every later call and reminder to her. On a shared number the old code booked under whichever account came first, or under a new customer for a new address (#127 item 3).
Considered: adding the daughter's number as a contact on the customer's location (a lasting change made from one call, and the brief said not to overwrite the customer's own); reading both names out on a shared number (the question names nobody, so it can't tell a stranger on that line who lives there); the model passing which account (a choice the server can check against the caller's words instead).
Would change if: technicians in practice want the visit contact as a ServiceTitan location contact so it shows in their app's call button, in which case it is added there with its memo and still never replaces the customer's number.
Discussion: docs/ai-process/coauthoring.md spec r12
Issue: #128

## 076. A change from a number not on the account needs the caller to say the name and address

Decided: `cancel_appointment`, `reschedule_appointment` and `record_move` for a customer the call's number isn't on (found by an address or a number the caller gave) are refused with `identity_check` until the caller has given that customer's surname as a name and said the visit's (or account's) house number, street and any unit. The surname counts only after a name lead ("this is", "my name is", "I'm", "it's", "under") or the customer's first name, or as a line that is just the name, and never inside the address or in passing, so "620 Bedford Avenue" names no Maria Bedford and "what's the price" no Maria Price; the unit must be one the caller said, as with the ZIP (058) (PR #134 review). The question is the server's own polite sentence: "Of course. And just to confirm you're the person for the visit, could you tell me your name and the address?" Only the caller's lines count, and only what they said before any agent line held the same detail, so the agent reading "Maria Delgado?" out for a yes never passes. With one part right the server asks for the other; after two answers that don't match it refuses the change for the rest of the call (`identity_mismatch`), flags it for the office, and offers a call to the number on file; the callback the caller accepts goes to the account's own number, not theirs. A known number skips all of it. A change made after the check leaves a note on the job (or the account for a move): "Changed from an unknown number after a name and address check." This replaces the old rule that a confirmed read-back of the visit's address was enough.
Why: anyone who knows an address could cancel a stranger's visit. Name and address is a low bar (everyone in the household knows both), which is why the office gets the note; the realistic misuse is a neighbor or an ex, and the note plus the callback to the number on file catches that.
Considered: a code texted to the number on file (no SMS in V1, and the real customer may be the one calling from another phone); asking for the last four digits of the number on file (callers often don't know which number is on file); reading the details for the caller to confirm (gives them away).
A customer found from what a caller said (an address, or a number they read out) comes back from `find_customer` without its numbers, notes and job summaries until the check passes, and without its name until the caller gives it, so the agent can't read them out or tell a stranger whose account it is (eval run 20261104-200156, unknown-number-wrong-name: the agent judged the name itself and said "the name you gave doesn't match the account").
After a change made this way, the customer's number on file gets a heads-up call once the caller hangs up: the server's sentence, said once, the same on a voicemail ("This is Summit Air. Your visit on Monday between 12 and 4 was cancelled by a caller from another number. If that wasn't you, please call us back at ..."), one attempt, inside the outbound hours, and the outcome noted on the job and shown on the call page. Jackson approved it in his words: "Yes. As far as you can go on that one, that'd be a very good idea." The mock's customers aren't real people, so only a number on HEADSUP_ALLOWLIST in the server's environment (empty by default; an entry counts only as exactly ten digits, or eleven starting with 1, and anything else is logged and left out) is dialed, never the number the change came from, checked when the heads-up is stored and again when it is claimed; every other one is recorded as "would have been sent" and shown the same way. A known number, and a failed check, get none.
Would change if: live calls show real customers failing the check often (a misheard surname), in which case the surname match gets the same fuzzy match the street lookup has. The heads-up becomes a text once the company's number is registered for it (docs/later.md C-61).
Discussion: docs/ai-process/coauthoring.md spec r12
Issue: #128

## 075. A caller who moved: a new location, the old one inactive, and nothing dispatched there

Decided: a caller on file who says they moved (first-person phrases: "we just moved", "I have a new address", "I don't live there anymore") gets the new address found with `find_address` (or the ZIP checked with `check_service_area`) and confirmed in the read-back, then `record_move`. In the area it adds a ServiceTitan location for the new address (or makes active again an inactive one there), makes the old location inactive, moves every open job at the old address to the new location with a pinned note for the technician, and leaves a customer note. ServiceTitan keeps a location per address with its own history and equipment, and lists only active ones by default, so the old one is retired, not overwritten. Out of the area nothing changes on the account but a note, and every open job at the old address is cancelled, with the caller told plainly in the server's sentence; until then an out-of-area answer for a mover says nothing, so the decline and the cancellation come together. A job already dispatched is flagged for a person instead. The server refuses anything left pointing at the old address: a booking there (`location_superseded`, also for a booking queued before the move and sent later), a reschedule of a job still at an inactive location, and, while the caller said they moved and no move is recorded, any booking (`move_first`). A caller booking an address already on file ("we just moved in two years ago") is no move. The mock gained `PATCH locations/{id}` (active) and `PATCH jobs/{id}` (locationId), and its location list now hides inactive ones unless `active=Any`, as ServiceTitan's does.
Why: a mover's open visit would otherwise go to an empty house, and the old account choice ("under your account") added the new address while leaving the old one live for every later booking and reminder.
Considered: editing the old location's address in place (moves the old house's equipment and job history to the new one, and the next owner of the old house would inherit it); cancelling and rebooking the open job at the new address (loses the window and the technician, and can fail on capacity); a confirmation step before cancelling the out-of-area visit (the read-back already confirms the ZIP, and a visit to an empty house helps nobody).
Would change if: a real ServiceTitan tenant refuses PATCH jobs/{id} with a new location (ASSUMED to be allowed, as the mock README notes), in which case the open job is cancelled and rebooked in the same window.
Discussion: docs/ai-process/coauthoring.md spec r12
Issue: #128

## 074. The design system is saved to the Claude account, built from the repository

Decided: keep the design system's source in this repository (`tokens.css`, `components.css`, `/owner/design`, docs/design-system.md) and save a copy to the Claude account as a design system, "Revin owner view", rebuilt from the code by `scripts/export_design_system.py`. Its brand book is docs/design-system-rules.md, the short form of the design system page written as rules for an agent.
Why: later sessions, and other surfaces such as decks or a new page, read a saved design system directly instead of rereading the CSS and the long doc. Building the copy from the code, with a test that fails on drift, keeps one source.
Considered: the repository alone (every session rereads 700 lines of CSS); the saved copy alone (the app's CSS would drift from it); a live bundle of the page script (the previews would run code the app's tests don't cover).
Would change if: the owner view moves to a component library with its own build, in which case the copy is built from that library.
Discussion: Jackson in chat: "make sure you like properly save and add the design system for Revan to my Claude account, so like it will be easier to reference in future sessions."
Issue: none; follows #53

## 073. A caller who pushes for a higher priority gets a calm answer, not a raise

Decided: when a caller asks to be put first with no new fact ("it's an emergency", "make it urgent", "I'll pay extra", "put me first"), the server's turn check adds a hint and the agent gets a `[server]` note: one calm line that it'll get them the soonest opening, no arguing and no level named or promised, the at-risk question once if it hasn't been asked (`priority_pushed`, or `priority_pushed_asked` once an agent line has asked who's home), then the soonest window or a callback from a person, and 911 only if someone is in danger. Nothing is raised. The phrases are the trade's (`priority.pushes` in trades/hvac/agent.yaml). A negated ask ("it's not an emergency"), talk about the equipment ("the furnace is urgent business") and a line that also gives a fact a rule raises on don't count; on a safety call there is no hint. A fact the caller states always counts, even one they ask the agent to pass on ("tell them there's a baby in the house"); the model's own sentence saying it raised the call ("I've marked it as a priority") is dropped before it's spoken.
Why: insisting never raised priority, but nothing told the agent how to answer it. A false raise is the safe direction, and `raised_after_wait` (072) is what tells the dispatcher when a fact came late.
Considered: having the model notice pushes from the prompt alone (it already doesn't raise anything, but how it answers varied); raising one level for a caller who insists twice (rewards pushing, and the order should follow need); treating "just say there's a baby" as a claim rather than a fact (built, then dropped in the PR #126 review: "please tell them there's a baby in the house" is a real fact in the same words).
Would change if: live calls show callers pushing in words the list misses.
Discussion: docs/later.md C-58
Issue: #124

## 072. Priority rises only on the caller's own words, and a raise after the wait is flagged

Decided: priority rules fire only on the caller's lines on this call or a linked one, and the server's own readings (the outside temperature, the safety state). What the model writes in `issue` and `who_home` still goes into the job notes but raises nothing. The model's `inside_temp_f` counts only when a caller line carries that number within four words of a temperature word ("it's 52 in here", "fifty two degrees") or answers an agent line that asked about the temperature ("maybe 52", or "it says 48" a couple of lines later after "let me check"), and never when the line gives it as an age; "fiftyish", "five oh" and "the fifties" count, on the pattern of the ZIP rule (058); a booking that leaves the number out keeps one the caller gave earlier. Each rule's reason quotes the caller's sentence that raised it (`caller said "..."`), on the owner view and in the job's notes. The first time `find_slots` answers on a call, the server marks the wait as heard (`waits` table); a job whose level is above what the caller's lines before that would give is stored with `raised_after_wait`, a note line in ServiceTitan, a "Raised" row on the call page and a count per caller number in the call page's facts line. The flag is for the dispatcher to weigh and never lowers anything. The job type stays the model's: it sets the starting level and needs no caller cue.
Why: the model's own summary fed the same phrase rules as the caller's words, so pressure on the agent or a model slip could raise a call, and after hours ask the on-call technician, on words nobody said. A fact first given after hearing the wait is usually true and still raises, since missing an at-risk caller is worse than a false raise, but a person should see when it came.
Considered: a caller cue for job types that start higher (no_heat, no_cooling); not done, because callers describe a broken system in too many ways to gate on, a wrong job type is visible to the technician and dispatcher, and with the inside temperature now the caller's, a no-heat job on a mild day with nothing else said starts at Normal like a tune-up. Also considered: dropping a raise that came after the wait (rewards a caller who leads with the fact and punishes one who remembers late).
Would change if: live calls show the temperature rule missing real answers (a caller who says "low fifties" to a question it doesn't recognize), or the flag turns out to fire mostly on honest late facts, in which case it moves off the call page and stays in the job notes.
Discussion: docs/later.md C-58
Issue: #124

## 071. The eval world answers address lookups from a recorded book

Decided: in a scratch world `find_address` answers from `evals/addresses.yaml`, real GeoSearch results recorded with `uv run python -m evals.addresses "<house> <street>"`. Any other address is answered as an outage, so the agent asks for the ZIP as it did before the lookup existed.
Why: evals must not call out and must answer the same every run. Most older scenario addresses are made up: of 73, 25 have no such house in the city's directory and 12 sit in a different ZIP from the scenario's (MEASURED, docs/explorations/015-address-lookup.md), so a live lookup would question those calls for reasons unrelated to what they test.
Considered: the live service in evals (network in every run, and the older scenarios would start failing on made-up addresses); rewriting every scenario address to a real one (left for later, docs/later.md C-60).
Would change if: the older scenarios get real addresses, which would put the lookup in every booking scenario.
Discussion: PR #125, docs/explorations/015-address-lookup.md
Issue: #120

## 070. A form of address the caller asks for is used, and saved on their record

Decided: the server reads the form of address a caller asks for from their own words, on every caller line: a title and a surname ("call me Dr. Patel", "it's Ms. Rivera"), or a capitalized given name after "call me" or "I go by". The agent gets it as `call them` for the rest of the call and on a linked call, the greeting uses it, and after the call it goes on the booked or matched customer as the ServiceTitan note "Prefers to be called Dr. Patel.", which the next call reads back. Days, times, fillers ("call me Tuesday", "call me back") and possessives ("Dr. Patel's wife") are not names, and a name with a mocking word or abuse anywhere in a word ("Your Majesty", "Mr. Fuckface") is refused and never kept.
Why: callers who ask to be called "Dr." expect it, and remembering it next time is what a good front desk does. The server reads it rather than the model, so nothing abusive reaches the customer record.
Considered: a model tool to set the name (the model could write anything into the record); a dedicated ServiceTitan custom field (needs setup per company; a note is what the office already reads).
Would change if: live calls show real names missed for lack of a capital in the transcript, or the company adds a custom field for it.
Discussion: PR #125, spec S-53
Issue: #120

## 069. The ZIP can come from a lookup of the street the caller said

Decided: `find_address` looks up the house number and street the caller said with NYC Planning's GeoSearch, then the Census geocoder if GeoSearch fails. Only a result with the same house number and street counts. One match fills the ZIP and the read-back confirms it; several ask which borough or neighborhood; none asks the caller to spell the street, then for the ZIP; a slow or failed lookup asks for the ZIP, and a ZIP the caller gave stands. That ZIP passes the ZIP gate (decision 058) only when the house number and street came together in one caller line. A ZIP the caller gave that disagrees with the one match is read back against the address found and refused until the caller says theirs again or picks the lookup's; an address on the caller's record keeps its ZIP. The lookup starts as soon as a caller line has an address, and answers are kept by house and street.
Why: decision 058 said the server would look the ZIP up if callers often give none, and callers forget a new ZIP or mix up neighboring ones ("248 Duffield St" has one ZIP). GeoSearch is built on the city's own address directory, reads Queens house numbers and needs no borough; the Census geocoder needs a city and misses some Queens addresses (MEASURED, docs/explorations/015-address-lookup.md).
Considered: the Census geocoder alone; a paid geocoder (no gain for NYC addresses, and a key to keep); letting the model pick the ZIP from search results (the guessing 058 forbids).
Would change if: GeoSearch proves unreliable at volume (no published limits), or the company serves outside NYC, where a paid geocoder would go behind the same `address_lookup` config.
Discussion: PR #125, docs/explorations/015-address-lookup.md, spec S-13a
Issue: #120

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

## 066. The eval reference is measured at two samples per scenario

Decided: the standard-suite run that sets the eval floor runs every scenario twice (`SAMPLES=2`), and the floor is that run's pass rate rounded down to 5% under decision 011. The first such reference is `20261104-154616-livekit-fast-standard`, 93% over 104 calls, floor 90%.
Why: at one sample per scenario the pass rate moved about five points between runs on nearly the same code (91%, 96%, 90%, 85%, MEASURED, PR #102), which is the size of the fixes being judged, so one lucky or unlucky call could set or break the floor. Two samples halve that noise for about $3.30 a run instead of $1.70 (MEASURED).
Considered: one sample with a rerun of misses (hides a flaky scenario); three samples (about $5 a run for a further small gain).
Would change if: two-sample runs on unchanged code still move by more than five points, or the suite grows enough that the cost matters.
Discussion: PR #102

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
Discussion: Jackson's notes on docs/ai-process/past-briefs/brief-phase-3-onward.md

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
Discussion: PR #65; the flakiness is a phase 3 finding in docs/ai-process/past-briefs/brief-phase-3-onward.md
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
Discussion: docs/explorations/009-rules-by-call-stage.md, docs/ai-process/past-briefs/ideas-round-questions.md
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
