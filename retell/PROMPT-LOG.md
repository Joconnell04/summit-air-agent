# Prompt log

Every version of the agent prompt, oldest first. Each entry says what I changed, why (the call or finding behind it, with the call id) and what happened: kept, dropped or replaced, and what went wrong. The coordinator adds an entry here for every prompt change from now on, in the same shape, at the bottom of the section it belongs to. When nothing has measured a change yet, its outcome says so and gets filled in once a call or an eval does.

Times are UTC, from `retell/summit-air-jackson/CHANGELOG.md` and the arms' notes. Call ids are Retell's. Where I couldn't find the call or number behind something, it says unknown.

## How the prompt grew

| Version | Words | "never" | Snapshot |
|---|---|---|---|
| Baseline | 1,365 | 7 | fe9623e |
| Change set 1 | 1,451 | 10 | 8cf97ba |
| Change set 2 | 1,564 | 13 | fddc92e |
| Change set 3 | 1,579 | 14 | 6b5e400 |
| Change set 4 | 1,601 | 14 | c62b4bb |
| Change set 5 | 1,949 | 21 | 5cc05a3 |
| Change set 6 | 1,691 | 19 | 4698f0a |
| Change sets 7 and 8 | 1,763 | 26 | 3f5fcea |
| Change set 9 | 1,858 | 26 | 60e187b |
| Change set 10 | 1,860 | 26 | 1b18c18 |
| Change set 11 | 1,915 | 27 | 45334db |
| Change sets 12 and 13 | 1,971 | 28 | 060377c |
| Change sets 14 and 15 | 2,150 | 36 | e0fdb43 |
| Change set 16 | 2,254 | 41 | cc78d2a |
| Change sets 17 and 18 | 2,385 | 41 | 6cff10c |
| Change set 19 | 2,437 | 42 | live LLM, not snapshotted on this branch |

MEASURED: `wc -w` and `grep -oi never` on `retell/summit-air-jackson/prompt.md` at each commit. Change set 19 is measured on the live LLM's `general_prompt` (llm_12eba5f1d4a3b067f0635097de30, `last_modification_timestamp` 1793939241718, read with `GET /get-retell-llm`). Change sets 7 and 17 have no snapshot of their own; 7 was published between the commits for 6 and 8, and 17 is an agent setting.

The short version: almost every fix was one more rule, and the prompt went from 7 "never" lines to 42. The side-by-side (below) shows the rule list wasn't holding by change set 16.

## Summit Air Jackson (my live agent)

agent_a3e38e6aa3d4e75dfac12aafc8, llm_12eba5f1d4a3b067f0635097de30, Claude Haiku 5.5 on Retell. Retell shows agent v2 and LLM v2 for every change set, because each one was published in place, so the snapshot commits are the versions.

### Baseline (20:41)

What I built with Retell's builder assistant before any changes. 1,365 words. On the one call that ran on it, call_c1ecd6b5422e9576414cb84a3da, it did most of what I later spent the night fixing: "That background text isn't something I need to act on", "Thanks, Pat", "Now I'll confirm the booking details", "Next, I'll check the openings", 2-hour windows, a question about sensitive people in the home, and the name asked twice. Replaced by change set 1.

### Change set 1 (20:49)

The greeting became a `{{greeting}}` variable, `{{call_context}}` went at the end, safety lines were left to the server with a say-exactly rule, one sensitivity question on no-heat and no-AC calls, half-hour windows, Retell's hang_up swapped for our end_call, and an HVAC basics knowledge base attached (filter 0.8, top 2). This was the coordinator porting what our own server already does, with call_c1ecd6b5422e9576414cb84a3da as the before. The knowledge base was the mistake: it retrieved on every reply and pasted text into the turn, which is where all the "reference text" comments came from. Dropped in change sets 5 and 6. The sensitivity question was replaced the next change set.

### Change set 2 (20:59)

No comments on attached text, no narrated steps, knowledge base tightened to filter 0.9 and top 1. Priority by listening instead of asking: on no-heat and no-AC calls only "Is it completely out, or just not keeping up?" and "Since when?", no temperature or who's-home questions, and one open door ("If anything makes this more urgent for you, just let me know."). The caller's name goes to read_back so it isn't asked twice. Same driver, call_c1ecd6b5422e9576414cb84a3da. Listening and the open door are kept; both are still in change set 19. The "no comments on attached text" line backfired. The next live call still said "I'll set that knowledge base text aside", and I think the "never mention reference text" lines primed it.

### Change set 3 (21:11)

From the first run of the Retell eval suite: a business caller is always recorded as a lead, flag_person asks when to call back, and the caller's name is never said. The eval run's ids aren't recorded in the repo (unknown). The lead and callback lines held. The name rule didn't. Names came back on call_7727509d3289c4434b27efdad5b ("Thanks, Jackson") and call_509466d21b01b0a88d265b3ef0d ("Thanks, Pat"), and it took change sets 13 and 16 to tighten it.

### Change set 4 (21:25)

Also from the eval suite: on gas or CO, call flag_person at once and say only the exact lines that come back, never safety advice of our own. Replaced by change sets 7 and 8. On call_0246b91f77e01a4b18583e6ca8b it still added its own demands ("PLEASE get outside now") and looped the safety lines.

### Change set 5 (21:39)

My first live call, call_6890b248561157d61f2ace3be4b, ran on change sets 3 and 4 and was rough. "Mm-hmm" as an opener, "is that right?" about something I'd just said, "I'm not going to repeat that text", "the caller accepted the 6:30", "so I'll move on to the visit details", and a closing that sounded like a question. So: knowledge base detached and its facts moved into a Heating and Cooling Basics section, the prompt talks to the caller as "you", no narrating or confirming back, never ask for the borough or ZIP, book without a lead-in, a new closing. On the agent side, Handbook filler words, high empathy, echo verification and NATO off, backchannel words without "mm-hmm", Cartesia Max voice. Biggest single jump, +348 words. Kept, except the Basics section (cut in change set 6) and the closing (changed again in change set 10).

### Change set 6 (21:48)

Retell's knowledge base detached for good, since it retrieves on every reply. Technical questions get answered from general knowledge until a lookup_hvac_info tool lands on our server; the knowledge document stays in `kb-source/`. Same driver as change set 5. Kept. The prompt dropped 258 words, the only change set that made it shorter. The lookup tool landed in change set 12.

### Change set 7 (21:52)

Safety lines now arrive at the start of flag_person's `say` (bridge a0d7f97) and get said exactly once. After the goodbye, anything the caller adds gets only "Take care." The call behind it is unknown. Replaced: call_0246b91f77e01a4b18583e6ca8b, which ran on this version, repeated the safety loop "way too many times" and said "take care" without hanging up. The safety path was overhauled in the server and the bridge after that.

### Change set 8 (22:00)

From call_0246b91f77e01a4b18583e6ca8b: the agent is Brooke with a female voice (cartesia-Brooke) and a more enthusiastic greeting. Speech normalization off, because it said "nine hundred and eleven" and slowed the phone read-back. No safety advice of its own and booking left to the server, "or any pets", every sentence of a tool's `say` (it had dropped "I've moved you up"), partial addresses asked for warmly instead of "that's not an address", and interruption sensitivity 1.0. Brooke and the safety rules are kept. The pets question came out again in change set 18.

### Change set 9 (22:10)

From call_7255ab47e30637f207e49f5bdeb: "one second" gets "No worries.", do-not-call is confirmed first and can be undone, "am I a member?" gets a yes or no instead of the plan pitch, and asking to end the call ends it. "No worries." and the member answer are kept. The do-not-call confirm was replaced in change set 15, because the agent's own confirm plus the server's made two (call_926427e1d1f2a80cb78dc6a4e04).

### Change set 10 (22:17)

The closing ends on a plain word, "Thanks so much for calling Summit Air today. Have a great day!", so the voice doesn't lift on "Summit Air" (my note on call_0246b91f77e01a4b18583e6ca8b). The web-call greeting says "I'm Brooke, the virtual assistant". Kept; the closing is the same in change set 19. The double goodbye kept happening though ("Have a great day!Take care." on call_d1cc0960d82f375869bde11dcb4).

### Change set 11 (22:34)

The phone fallback only fires on a real `no_caller_number` refusal, the last four digits are said as words ("four, zero, two, four"), a non-member gets one offer to hear about the plan, the goodbye is said once, and the web greeting doesn't end a sentence on "Summit Air". From call_7727509d3289c4434b27efdad5b ("I can't see the number you're calling from on my end... Could you read it out", and the goodbye twice) and call_d1cc0960d82f375869bde11dcb4 ("The one ending in 4024." in digits). The number lines are kept. The goodbye-once line didn't hold: call_88868cbba5037b5931064b487c5 still said "Have a great day!Take care." Fixed properly in change set 16.

### Change set 12 (22:56)

The lookup_hvac_info tool, `moving_a_visit` on moves, do-not-call confirm and undo, and 30-minute windows in the prompt and in find_slots. Drivers: my "it should give a 30 minute window, not a 2 hour window" on call_0246b91f77e01a4b18583e6ca8b, and the radiator question on call_7727509d3289c4434b27efdad5b, which was answered from general knowledge. Kept.

### Change set 13 (23:16)

Say read_back's sentence exactly, since the server now speaks addresses in words, digit by digit only for phone numbers, and no name after "Thanks." From call_509466d21b01b0a88d265b3ef0d ("Thanks, Pat."). The read-back line is kept. The name kept coming back after "Thanks" (call_ad292013eb2db2c6323a003ecf9, call_79af0414dc77fcabc47b36f49cd), so change set 16 went at it again.

### Change set 14 (23:56)

From four scripted self-test calls, call_b1fa369abb81ba74c1486c1fd67, call_5a9c9d3ba91ffd87a2b885bccf5, call_dae2c547494be1234ff94cf274a and call_987ce70cbd5de2782b39b27ecbf. "$199" from speech-to-text is a house number (it asked for it three times), no narration after find_address, the two how-bad questions in separate turns ("Is it completely out...? And since when...?"), no urgency examples and no acknowledgement of its own ("like someone elderly, a baby..."), the open-door line word for word with the first offer, read-back before find_slots, and a "no thanks" to an offer isn't a goodbye. Mostly kept. Two questions in one turn was still there on 3 of A's 8 calls in the side-by-side, and A skipped the open door on persona e.

### Change set 15 (00:00)

One do-not-call confirmation, the server's, and no "let me look that up" or "Next,". From call_926427e1d1f2a80cb78dc6a4e04, which confirmed do-not-call twice and said "Let me look that up". Kept. On the side-by-side reruns, A asked once and the undo held (MEASURED, `evals/rounds/side-by-side-01.md`).

### Change set 16 (00:30)

From a second batch of self-test calls. Sparks advice comes from the system only (call_de4fe39a91aee4db1c3e61a611f told the caller to move away from the furnace and turn it off at the breaker). Never volunteer what's on file (call_79af0414dc77fcabc47b36f49cd, call_f0846787038f654309219adb1e8). No name even after "Thanks" (call_ad292013eb2db2c6323a003ecf9, call_79af0414dc77fcabc47b36f49cd, call_f0846787038f654309219adb1e8). end_call alone, after every other result is back (call_ad292013eb2db2c6323a003ecf9 said "Have a great day!Take care."). Commercial callers get one question at a time ("What kind of business is it, and what's the address?").

This is the prompt arm A ran in the side-by-side: 2,254 words, 41 "never" lines, 1 clean call of 8, 15 gross errors, Opus judge mean 4.38 (MEASURED, side-by-side round 1). It reworded the server's sentences on 5 calls and asked two things at once on 3. One more rule per failure had stopped paying off by this point. Built on by change sets 18 and 19; A, which ran it, is the demo fallback.

### Change set 17 (01:40)

Not a prompt change: Retell's built-in do-not-call detection turned off (`enable_dnc_detection` false). On the side-by-side, A-b (call_8df5ad10287f0d52fa5a8f50a65) ended `user_requested_dnc` the moment the caller confirmed, so "actually, you can still call me" never got heard. It also doubled the confirmation. Our server's mark_do_not_call owns this. Kept: on the reruns A and B asked once, confirmed, kept the undo, and the do-not-call list ended empty (MEASURED, side-by-side round 1).

### Change set 18 (03:46)

From my call call_2fb4e053ba8b76b57c348845990, on server 28707de after hours. It was decent, but it didn't ask my unit at 199 East 4th Street, it routed a new-unit estimate to a callback (I pushed back, "Why can't they just come out?"), "like an apartment or a house?" sounded aggressively inquisitive, and I wanted a "gotcha" between the rapid-fire visit questions. The bigger point: I want this to be the appointment booking system, not an AI that gets you a callback. So booking visits is the job and callbacks are only for what a visit can't solve, a new system or quote books an estimate visit, the unit gets asked, the residential question is "And this is for your home, right?", the pets question is gone, no approval question on estimates, short acknowledgements between visit questions, system type asked once. Kept. Not measured on its own: Sonnet's v3 (below) is the first scored prompt that carries it, and change set 19 followed about 40 minutes later.

### Change set 19 (04:27)

From my 11:20 PM call, call_549de89ecc2428e409b64072e34. Today and tomorrow are said as just that, with no weekday or date ("tomorrow, 8 to 8:30", not "tomorrow, Friday, November 6"). The two access questions are one ("Will someone be home to let the technician in, and is there a gate code or buzzer they should know about?"), asking only for the missing half if I answer half. Backchannel frequency up from 0.4 to 0.8 with "mm-hmm" and "yeah" added, because I want quicker backchanneling. Live now (2,437 words, MEASURED above). Outcome not measured yet. Its snapshot isn't committed on this branch yet; the CHANGELOG line is from the coordinator's working copy.

### Change set 20 (04:47)

I switched the voice to Cartesia's Brian and renamed the assistant Brian to match. The prompt and the default greeting changed here; the phone greeting comes from the server, which now reads the name from one setting (`assistant_name`, c458944). Kept. Not a quality fix, just the voice I want.

### Change set 21 (04:58)

From my 11:40 PM call, call_041c4161b20fbd44cd92d3ede64, which actually reached the socket test agent on its daytime clock (that's why it offered "today, 1 to 1:30" at night). What I wanted on every arm anyway: short connectors before most questions ("Got it.", "Okay, thanks.", "Perfect.", "And..."), never the same twice in a row, and a check-in after 8 seconds of silence instead of 20, up to twice. The socket arm got its own number after this so my calls stop landing on test agents. Too early to say if it holds.

### Change set 22 (05:02)

One catch-all question for the technician on heating, cooling and airflow problems, with two short examples that fit the issue: leaking water or turning on and off a lot for cooling, banging pipes or shutting off for heating, weak airflow or dust for vents. I kept "smell" out of the heating example so a yes doesn't set off the gas check. Not tested on a call yet.

### Change set 23 (05:10)

Caller ID first. The server already looks up my number before hello, so a match gets greeted by name and isn't asked for it again; more than one match gets "Am I speaking with ..."; no match gets "I don't see an account under the number you're calling from. Can I get your name?", once, and only when a name is needed. I'd asked for it to say it's trying, but the lookup is done before the call, so a "let me check" turn would be a fake pause. The no-match line covers it. The server lane is adding a plain call note for each case so the model doesn't have to read it out of the JSON. Not tested on a call yet.

### Change set 24 (05:20)

From my 12:02 AM call. The goodbye had three things stacked ("Thanks so much for calling Summit Air today. Have a great day! Take care."), so now it's "Thanks for calling Summit Air." plus one nicety that changes with the time of day. "So much ... today" is gone from the greeting too. It thanked me for waiting when I was the one making it wait, so that's out. "Are you still there?" fired six times at 8 seconds, so it's back to 16. The catch-all question about water leaking or turning on and off landed well, so it stays. The read-back connector ("Perfect. So that's ...") is a server line, so the server lane is adding it.

### Change set 25 (05:40)

The combined access question made me answer "yes, and no" (yes someone's home, no code). I picked a version where a single yes covers the usual case: "Will someone be home to let the technician in? If there's a gate code or buzzer they'll need, just let me know." I came to it from a call on the socket line, but it applies everywhere, so it's on my agent, the server and both arms. Not tested on a call yet.

### Change set 27 (06:35)

"Thanks for calling Summit Air." sounded like a question again on the socket line. The version I liked earlier had "today" after the brand, and that's what kept the line falling. So the goodbye is "Thanks for calling Summit Air today." plus one nicety, and it never repeats the booking. The socket lane is checking candidate closings for a falling pitch so this stays fixed.

### Change set 28 (06:55)

Not a prompt change, a tools change, but it explains a bug I kept hearing. After the socket turned out to be using an old copy of the tool definitions, we diffed my agent's tools against the server's. The callback day only allowed "today" or "tomorrow", so "Monday morning" could never be stored as Monday. The model had no way to say it. Two newer fields were missing too (when the problem started, and my answer to the catch-all question). All synced, 0 drift now, and a drift test is coming so the tools can't fall behind the server again.

## Side-by-side arms (issue #169)

Four versions of the agent on the real phone line, scripted callers, scored by our checks, an Opus 5.5 judge and Retell's own latency and cost. One call per arm and persona, so judge means within about half a point are noise. Source for every number in this section unless it says otherwise: `evals/rounds/side-by-side-01.md` on joco/side-by-side-014a4c, MEASURED from `side-by-side-01-data/results.json`.

| | A | B | C | D |
|---|---|---|---|---|
| Clean calls | 1 of 8 | 3 of 8 | 1 of 9 | 1 of 9 |
| Gross errors | 15 | 8 | 34 | 19 |
| Safety errors | 0 | 0 | 0 | 0 |
| Judge mean | 4.38 | 4.5 | 3.44 | 4.67 |
| End-to-end p50, ms | 1168 | 1552 | 1160 | 1154 |
| Cost a minute, $ | 0.093 | 0.328 | 0.095 | 0.105 |

### Arm A, as-is

My agent on change set 16, unchanged. The baseline for the others. Warm and quick, but it broke its own rules on most calls: reworded server sentences on 5 calls, two questions on 3, skipped the open door on e, so the 85-year-old mother never came up and the visit booked at Normal, and said a name on g. Kept as the fallback.

### Arm B, Sonnet 5.5

agent_871ef6fafe2636244c4bbaca67, llm_dae2724433a06cf424b622d2bea9 (`claude-5.5-sonnet`). Version history from `retell/arms/sonnet/NOTES.md` on joco/sonnet-buildout.

v0 (01:17). My prompt as it was, only the model changed. Best arm on the round: fewest gross errors, no safety style issues, never said a name, best on the retracted gas call (judge 8). But it narrates its tools in a way Haiku doesn't: "I used the placeholder ZIP there by mistake", "The Record underscore Lead tool only accepts today or tomorrow as the callback day". Slowest (1087 ms LLM p50) and 3.5 times A's cost a minute.

v1 (01:45:57). One line I asked for: fix a mistake silently and never tell the caller about it. It didn't hold. The reruns said the same kind of thing again: "I guess the placeholder ZIP there? So let me retry the lookup properly" (B-e rerun, call_daecc57ee95de4ca98a4dd9a6db) and "The callback tools only accept today or tomorrow as a day" (B-i rerun, call_89c1c4a057c0914e567e962564b). Kept in later versions anyway, but it isn't what stops the narration.

v2 (03:30:36). A first How to Speak bullet saying every word it writes is spoken aloud the moment it writes it, so think silently and never write notes or plans; and "Offer exactly the windows find_slots returns. Never judge them against the time of day." From the probe call call_bbfbed1552e4f96e41400b1897f, where it spoke its working notes ("Name given Fatima. Address given. Residential not yet confirmed.") and argued with the server's slot because of the time. The time part was a server bug (the bridge's "Now:" line ignored the test clock offset), sent to the server lane. Replaced by v4: it still narrated ("The address came through as three hundred seventy two thousand... I'll run it through the lookup").

v3 (03:47:35). Rebased on my change set 18, keeping B's three lines on top. Scored on six daytime calls: all six journeys passed, no safety errors, 3 clean, judge 4.67. Replaced by v4 because it still narrated on 3 of 6: "For an estimate, I skip the repair approval question" and "Since I already have the name, address, and the residential question is implied by the apartment, I'll go to the read back."

v4 (04:11:55). The spoken-aloud line moved to the top, in the Role section, and widened: write only what Brooke says out loud, never notes, plans, what a lookup returned, or why a question is asked or skipped, and when something is unclear, ask one plain question. Retell's hosted LLM has no reasoning setting, so the prompt is the only place to stop it (SOURCE: Retell's create-retell-llm reference). It didn't stop it. On 12 daytime calls (server a679a7a): 11 of 12 journeys passed, no safety errors, 3 clean, judge 4.08, narration on 5. One new kind: "The stay offers only the morning slots, but the data shows afternoon windows are available. I'll re query." The one miss was an elderly booking lost to a misheard street, a server bug since fixed (c458944). Replaced by v5.

v5 (05:00:27). Rebased on my change sets 19 to 23, with B's lines on top, plus one more: if a tool's say and its data seem to disagree, say the say. Agent on Brian's voice, backchannel 0.8, 8 s check-in. On 17 after-hours calls (server c458944): all 13 journeys passed, including the estimate visit and a returning caller who was never asked a name; no safety errors; 8 clean; judge 4.94, the best of the round. But narration was still on 6. The placeholder-ZIP line came back on call 1 ("I used a placeholder zip there, so I'll redo that lookup"), from find_customer wanting a ZIP before find_address returns one, sent to the server lane. Kept as the last measured version.

v6 (06:29:28). A fresh copy of my agent through change set 27, with B's three lines, the Sonnet model and the eval URLs. B's final configuration. Not measured: the lane's $40 was spent. Across v1 to v5 B passed 35 of 36 done-bar journeys with no safety error, but no prompt line stopped it narrating its reasoning when a tool surprised it (1, 1, 3, 5 and 6 calls). The same callers on `fast` speech-to-text and on dynamic responsiveness made neither worth turning on for my agent: `fast` didn't shorten the turn and cut in more, and dynamic made every turn about 1.1 s slower (DECISIONS 125). Round: evals/rounds/sonnet-buildout-01.md.


### Arm C, behavioral Haiku

`retell/arms/haiku-behavioral/prompt.md`, PR #172. Same model, tools and server as A; only the prompt changed. I rewrote the prompt around a person and her habits instead of a list of rules: Brooke as an experienced New York front-desk person, seven "How you talk" habits standing in for the 19 "never" and "don't" lines in How to Speak, and one rule for server text (a `say` is the whole turn, every sentence, once). 1,093 words against 2,254, 2 "never" against 41 (MEASURED, `wc` and `grep`). The idea came from call_6890b248561157d61f2ace3be4b, where the "never mention reference text" lines seemed to prime the very phrase they banned.

Dropped. Worst on every measure: 34 gross errors over 9 calls and a 3.44 judge mean. It said the caller's name on 7 calls, even "Thanks, BioPond" for Siobhan Nguyen (C-k, call_6930525bbbd0de20271b03ad9a3). It narrated on 5 and ended 4 calls early. On the b rerun (call_b8641369a8b7e72f85cfaecc267) it said "We won't call you" and hung up in the same breath, so the do-not-call undo never happened. My read: a shorter prompt that describes a person left Haiku more room to improvise, and every habit I wrote it for came back.

### Arm D, server-led

`retell/arms/server-led/prompt.md`, PR #173. agent_e758054f5009c5106edd882d81, Haiku 5.5 at temperature 0, on its own tool server with `SERVER_LED=1`. The booking order moved out of the prompt and into code: every result carries `next`, find_slots refuses before a confirmed read-back, book_appointment refuses until the visit questions are answered, end_call waits for the outcome sentence. The prompt shrank to 828 words (37% of 2,254, MEASURED with `wc -w`).

Five text rounds through Retell chat before any phone call (`evals/rounds/server-led-01.md` on joco/server-led-flow-979e54). Round 3 put the open-door line before the offer's closing question, since it got dropped after it every time, and added known-caller and echo lines. Round 4 went to temperature 0 and took out a commercial line that contradicted the server. Round 5 used my sharper wording for names, the address on file, narration and gas advice. Over rounds 4 and 5 (12 calls) it still reworded 21 of 40 server sentences, narrated on 8 calls and said a name on 3 (MEASURED, `gross.py`). Prompt wording stopped moving those numbers after round 2, and temperature 0 didn't change the rewording.

On the phone: 1 clean call of 9, 19 gross errors, the best judge mean (4.67, inside the noise), the fastest (1154 ms p50) at close to A's cost. Order held on every call. Haiku still rewrote what the server handed it, including the open door on e (call_b4c83f184b5cb64748332a2e3c3) and k (call_11f8ca0e3ab110857663e60edcc). On e it also looped six turns asking for a house number the caller had given, which is D's flow, not the prompt. Kept as the base: the round's recommendation (DECISIONS 120, pending my call) is D behind the custom-LLM socket.

### Socket build (issue #176)

The socket agent ("Summit Air socket (test)", agent_9e575b3fa1457c8d4331a3b80a) runs on our own custom-LLM socket, so this prompt isn't in Retell. It's `retell/arms/socket/prompt.md`, read by the socket on every call, with Claude Haiku 5.5 through the Anthropic API (config/models.yaml `fast`). The server speaks the read-back, the offers, the booking line, the visit questions, the safety lines, the greeting and the goodbye itself, so most of the rules that kept growing on my live agent aren't in this prompt at all.

| Version | Words | "never" | Commit |
|---|---|---|---|
| v1 | 813 | 15 | 468a331 |
| v2 | 855 | 17 | 59052d8 |
| v3 | 883 | 17 | b4b0368 |
| v4 | 965 | 18 | ce89c64 |
| v5 | 1,035 | 19 | a9e098c |
| v6 | 1,098 | 20 | daaf602 |

MEASURED: `wc -w` and `grep -oi never` on the file at each commit. Arm D's prompt, which v1 started from, was 828 words with 14.

#### v1 (468a331)

Arm D's prompt with one section swapped. On Retell's LLM the model had to copy each tool result's `say` word for word, and it reworded about half of them (21 of 40 in arm D's rounds). On the socket the server says those sentences itself, so the model now sees them as `said_to_caller`, already heard, and only does what `next` says. Kept. On every socket phone call since, the server's sentences went out word for word (MEASURED, socket-buildout-01).

#### v2 (59052d8)

The gas test in text runs had the model giving its own safety talk ("Once the gas smell has been checked", "I'm not going to treat this as fine just yet"). I added my own rules from my live agent: never repeat the advice, never talk about whether the smell is dangerous, take a false alarm at its word, go on with booking if they want a visit. Kept, with one line I made up for when they ask if it's dangerous ("I can't say from here, but I can get a technician out."). Not checked on a call yet.

#### v3 (b4b0368)

My change set 18 from call call_2fb4e053ba8b76b57c348845990: "And this is for your home, right?" instead of the residential question, a quote books an estimate visit, and callbacks only for billing, complaints, prices or when nothing can be booked. The visit questions moved out of the prompt into the server, which asks them itself. Kept.

#### v4 (ce89c64)

Change sets 19 to 22. The assistant is Brian. Most questions open with a short connector that changes each time. The name question depends on what the caller ID matched before hello. Never ask for the ZIP. The goodbye came out of the prompt because the socket says it. Mostly kept. The model still asked for a ZIP once and asked for the system type after I'd said air conditioning (call_046f37112cac691a50019893717).

#### v5 (a9e098c)

Change sets 24 and 25, plus the prompt lane's review (#183): never thank them for waiting, one ask to repeat when a reply can't be made out, "Bye." or "You too." after the goodbye, a request to end the call ends it, "$199" is the house number 199, membership answers yes or no first, the renter 311 line, sparks get an Urgent flag, and no card line of its own because the server says the card stop. The two narration rules became one. On call_046f37112cac691a50019893717, "$199" worked. The model still opened with two questions and some narration ("Actually, let's get you booked."), which the socket now cuts in code instead (6c585f3). Being measured in the done-bar runs.

#### v6 (daaf602)

From my call call_046f37112cac691a50019893717 on 516. When I first say what's wrong, Brian opens with a short line in my words ("Oh no, sorry it's leaking."). The no-account line got "but no problem, I'll get you set up". "Wait" and "hold on" get "No worries, take your time." and the last question again when I'm back. Every question is built from what I already said, and never asks for something I told it (it asked me for the system type after I'd said air conditioning). The server's flow carries the matching question lines: how bad in my own words for a leak, a system type that fits heating or cooling (no furnace for an AC), and a catch-all that doesn't hand back my own example. Not measured yet; the done bar was stopped by the test line's Twilio account.

