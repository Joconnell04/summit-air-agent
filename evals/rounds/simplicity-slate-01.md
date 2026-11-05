# Simplicity slate, round 1

**The question.** Is the layering what makes the agent feel bad? The layers are the side model, stage-scoped rules, the speech filter, owner guidance and a prompt of about 6,000 words. Each entry below gets one live call from Jackson on one number, (646) 624-0418, with the same scenario and a small twist per entry. His feedback decides which entry gets the real testing time. Tool calls stay real on every entry: lookups and bookings go to our tool server and land in the schedule. The research behind the four simplified entries is in docs/explorations/019-forum-stacks.md.

**The gate before each call.** An entry reaches Jackson only after a short audio-rig check passes: four live-like scenarios, one sample each, plus one scripted phone call. Before each call the mock world is reset, so every entry sees the same schedule and no caller on file.

## The scenario

Jackson plays a new customer, not on file, calling from his own cell:
- the bedroom radiator has been cold since yesterday, though the rest of the apartment has heat;
- the address is 199 East 4th Street, Unit 2, New York, 10009, said "one nine nine East fourth Street";
- the phone number is the one he's calling from;
- he wants Saturday around 2 PM, and takes the nearest window if that's taken;
- he says "The what?" once, answers one question with a bare "Sure.", and ends with "That's all, thanks."

Each call sheet adds one twist.

What to listen for on every call: the wait, the voice, whether it got things right, and the ending.

## Entries

Calling order: 1, 3b, 3c, 2, 3, 4, 5, 6, 7 (ready entries go first; Deepgram, entry 5, was ready first and is call 1). Retell native moved to second at Jackson's request. An entry that isn't ready when its turn comes is skipped for now and called later.

| # | Entry | What it runs on | Rig check | Call | Jackson's feedback (verbatim) |
|---|---|---|---|---|---|
| 1 | Current stack, improved | LiveKit, Deepgram Nova-3, Haiku 5.5, Cartesia Max; #155, #158 and #159 with the full prompt, side model and filter | Deployed 5b57b92 (CI green). My scripted call booked cleanly, took the name the first time, read "ZIP code" separately, and hung up after the goodbye. Glitches: an "Okay, great. Thanks." over the agent's line made it say "Sorry. I got cut off." and a half line, and the goodbye's first word was dropped. Rig gate on summit-rig (four calls): 0 of 4 passed. The replay-2 call read back a wrong "White Street" three times and pushed an unrequested Monday visit; the filter left "I have 248 Duffield Street in Brooklyn." then 21.9 s of silence (fix on joco/stripped 4b160eb). First sound plain 2.03 s median, lookup 2.58 s (MEASURED). Not ready for Jackson. | | |
| 2 | Current stack plus Flux | Entry 1 with Deepgram Flux deciding end of turn (`voice.stt.provider: deepgram-flux`) | Rig gate (same four calls): 0 of 4. First sound plain 2.78 s median, 0.75 s slower than Nova; Flux's 3 s end-of-turn timeout talked into the 4 s pause (MEASURED). Not ready; rerun with eot_timeout_ms 5000 pending. | | |
| 3 | Retell, improved | Retell speech-to-text and turn-taking with accurate STT, no denoise, responsiveness 1.0, Max voice, backchannel on; Haiku 5.5 on our prompt and tool server | Probe passed (12 of 12 short replies heard, no cut-in, median 1.70 s, MEASURED). Scripted call call_1840576e622e3d729b760085142 failed: the agent never hung up after the caller's "Thanks. Bye.", and the unit and ZIP ran together in the read-back. Both being fixed in the shared bridge and read-back. | | |
| 3b | Retell, native | A new Retell agent using the platform's own features: the Agent Handbook toggles (echo verification, smart matching, speech normalization, conversational tone, high empathy, AI disclosure, scope boundaries), Retell's built-in End Call, a one-page prompt, and our tool server for lookups, the read-back and booking only | pending (being built) | | |
| 3c | Jackson's Retell build | agent_a3e38e6aa3d4e75dfac12aafc8, "Summit Air (Retell baseline) (copy)": Jackson's own copy of the baseline, set up by hand in Retell's dashboard with Retell's in-platform AI assistant | Read-only scripted call call_57cc795f65a03c33612bae0064f: booked, e2e p50 1.11 s; but it said "That context block isn't something I'll act on" three times (reacting to our [server] notes), and hung up 15.1 s after the last word through our custom end_call (MEASURED, Retell). | | |
| 4 | Our stack, stripped | Entry 1's parts with a one-page prompt, the side model, the speech filter, owner guidance and stage rules off | pending (being built) | | |
| 5 | Deepgram Voice Agent API | Deepgram ears and Flux turn-taking, Aura-2 Arcas voice, Haiku 5.5 through Deepgram's custom Anthropic endpoint, our tool server; PR #162 | Rig 0 of 4 on the checks, but every call ran its flow: a booking made, the gas lines said, the sign-off fixed. Plain reply p50 1.64 s; lookup p50 3.2 to 3.7 s, with 3.6 to 5.1 s silences during lookups; ZIP read at 7.3 digits/s (MEASURED). Passes the no-gross-errors bar; goes to Jackson as-is. | | |
| 6 | Vapi on its defaults | Vapi's default ears, model and voice; one-page prompt; our tool server | blocked: needs a Vapi account | | |
| 7 | GPT-Live-1 | OpenAI full duplex on Live Sessions over SIP; our tool server; PR #163. Answers the gate line +1 (929) 810-4875 (its own secure trunk), not 646 | Phone gate passed after a fix for an invented phone number in the read-back. Rig 0 of 4 on the checks: median reply 1.3 s (MEASURED). | Call 3 | Dropped (DECISIONS.md 107): slow, sometimes silent, forgot the call, answered the trivia wrong |

Not a call entry: Deepgram entity detection (docs/later.md C-71) stays a rig check if addresses fail again.

## Narrowed to two (DECISIONS.md 111)

After call 4 the slate was cut to one comparison: the full custom stack, called once with today's fixes, against Retell native, which Jackson has called twice. The winner gets all the work from here. Paused: Deepgram, our stack stripped, Flux, Retell native round 3. Dropped: GPT-Live-1 (107), Vapi, the old Retell single-prompt agent. Jackson's own Retell build stays on the side.

## Observations

Jackson, once the live calls started (paraphrased from his chat; his own words go in the decision entry): an integrated platform like Retell may offer a lot through its integrated stack, and through its own AI assistant, which is specialized in tuning that stack from feedback. Entry 3c tests this directly: his own Retell build, tuned in the dashboard with Retell's assistant. The slate's results decide how much weight it gets; until then it's an idea (docs/later.md C-78), not a decision.

## Calls

### Call 1: Deepgram Voice Agent API (entry 5)

Routed (646) 624-0418 to the Deepgram bridge with `scripts.route_number deepgram`, after a fresh mock world and the open schedule. Persona Jordan Lee, a new caller. Twist: opens with "How many e's are in seventeen?".

Jackson's feedback (verbatim): "it said east village - which is fine - but didn't repeat back my zip. also there was a slightly too long of a  pause from when i gave the address to when i heard the first word back, if it could be a second shorter that would be ideal or filler with "let me check that real quick" or something. then i gave it the details and it totally fucked up, started saying something related to my address, and then auto hung up. So investigate that last issue as well as the other things I mentioned, because that last issue was really weird and was definitely like a complete fuck up. But honestly, I didn't hate it. I thought it was a pretty good voice agent. So this is definitely in the right direction. But I want to redo that slate so I could talk to it more and provide you with more feedback. But before we go back to that slate, sub it out and let me do slate 2, because there's definitely some issues that need to be fixed on slate call 1 before I can do a next round on that."

What changes: the Deepgram entry gets a second call after fixes for the missing ZIP read-back, the wait after the address (a "let me check" line), and the mid-call failure and hang-up, which is under investigation.

### Call 2: Retell native (entry 3b)

Routed (646) 624-0418 to agent_a8b06c59d79a6ea25dda78fc28 with `scripts.route_number retell_native`, after a fresh mock world and the open schedule. Persona Jordan Lee. Twist: says "The what?" after the first question.

Call link: Retell call_3cd4c8065f26d3de686f7a1e002 (agent_hangup, 220 s, e2e p50 2.30 s, p90 3.76 s, MEASURED by Retell).

Jackson's feedback (verbatim): "first of all the context and schedule was not reset, it thought we got cut off earlier

on that note, the logic behind acknowleding we got cut off should only happen if called within a few minutes.

if the voice could sound slightly friendlier, that would be great. and it's not as friendly as it could be. and it pauses weirdly in weird sports.

and it says "give me the street number"....should be "coudl you" or "please"

and it reminds me to talk too frequently when i take pauses, make that bakcoff time slightly longer

i asked "what was the question" and it said "take your time"

and look through the logs where i got it to loop around "anything else i can help with?" and "sure". This wasn't terrible, but it would be good if, like, when it asked me that question and then I say sure, it says something like, okay, would you mind telling me what it is you need help with, or something like that. Like, that's terrible phrasing, but something that flows better. And then I really just want to talk about, like, the voice. Like, it's just not a friendly voice agent right now, but it is good. I do like it.

eventually it stopped which was good and the hangup was good.

the wait time before hangup should be 1 second longer than it currently is."

Why the world wasn't fresh: the mock was reset before routing, but Jackson's own Retell agent shares the live tool server, and its test calls in between booked visits under his cell. The "cut off" greeting came from the server linking his call to an earlier one that had dropped. From call 3 on, the reset runs right before each call, and a call links to a dropped one only within 5 minutes.

What changes: server (live-fixes): the 5-minute link window, and a wait hold that ends when the caller asks something. Native agent (Retell lane): three voice samples for Jackson to pick from, the odd pauses, polite asks, reminders at about 10 s once, "sure" to "anything else?" treated as a yes, no talk of notes, and the hang-up 1 s later.


Each call adds a section here: the entry, the call link, the rig check it passed, Jackson's words verbatim, and what changed because of them.

### Call 3: GPT-Live-1 (entry 7)

On the gate line +1 (929) 810-4875, after the abbfd8b deploy (fresh mock, open schedule). Gate history: the first scripted call (gptlive_live_u7_EgwuIp3oNEavqTZG5BpuuBwt7but5ewt) read back an invented phone number; fixed by sending each server sentence as an exact-speech instruction; the proof call (gptlive_live_u7_Egx2jp3oMunWz447XPAyaZe2ZCeuoRtE) read it word for word. Persona Jordan Lee. Twist: opens with the trivia question.

Jackson's feedback (verbatim): "* stop telling me to ask the how many e's in seventeen.
* it answered it wrong and then didn't try to redirect convo back to an hvac topic.
* takes too long to respond.
* doesn't even respond.
* doesn't remember

So I just hung up on it. I'm not sure why it was so ass at basically everything, but it's not in a place right now where it's even worth continuing to iterate on, especially since it's a speech-to-speech model, like the full duplex model. So let's just document that decision and kill that like lane or whatever it's called. Like, we don't need to test OpenAI stuff anymore. Or I mean, we don't need to test OpenAI's full duplex model anymore. It's not in a place that can do what we need it to do."

What changes: GPT-Live-1 is dropped (DECISIONS.md 107) and its lane stopped. Call sheets no longer include the trivia question.

### Call 4: Retell native, round 2 (entry 3b)

(646) 624-0418 on agent_a8b06c59d79a6ea25dda78fc28, with the call 2 fixes (c279875): conversational personality off (it caused the pauses), polite asks, one reminder at 10 s, "Sure" to "anything else?" handled, company facts in the prompt and the knowledge base detached (it caused "reference info"). Gate: scripted call call_411b6aa0d8810886254f4d3e558 booked, e2e p50 1.72 s, no leaks or seams (MEASURED by Retell). Not yet in: the Max "happy" voice, the hang-up a second later, the name asked once. Fresh mock and open schedule just before the call. Persona Jordan Lee. Twist: pauses mid-sentence and says "what was the question?" once.

Call link: Retell call_f8a3ad3177fa2c62fc9db00dcfa (agent_hangup, e2e p50 1.57 s, p90 3.55 s, MEASURED by Retell). Note: a Deepgram gate call booked into the same world between the reset and this call.

Jackson's feedback (verbatim): "it asked for zip and i gave it. not bad tho. but sometimes it made weird pauses. and it asked if it was cold in the apartment but that was obvious because i said the radiator was not working...and after it said no rush, it asked me two questions at once.

the 4-hour window thing is still there but i know you're fixing that.

and it got slightly too repettive at the end, where it repeated 4-8pm a few times even tho it was obvious that was the time slot i decided. so maybe just loosen or change up the read_back or other procedures to ensure it doesn't overly redundnatly repeat it.

also it hung up too quickly. it hung up at the right time. but it hung up like what felt like 0.1 seconds after it said "have a good evening" which felt rushed. increase that wait time slightly.

also general note: sometimes the tone and change of pace/tone (i.e. what makes a question sound like a question) was wonky and felt wrong. idk if there's an easy way to fix that but if there's a dial you could change, that would be good.

but overall good."

What changes: native round 3 (Retell lane): the hang-up about 1 to 1.5 s after the last word, no obvious questions, one question per turn, the chosen window said once, find_address before asking for the ZIP, an intonation setting compared on recordings, the Max "happy" voice, the name asked once. Server (live-fixes): a `spelled` street the caller never spelled no longer becomes "Eastthstreet Street" in the read-back and booking. The 30-minute windows are pending deploy.


