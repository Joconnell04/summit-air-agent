# Eval round 2: ten seeds and a hold-out

Round 2 of the three eval rounds (DECISIONS.md 035). Below are ten seed scenarios drafted from your round 1 notes and the call journeys; answer each with "keep", "cut", or a one-line change, then dictate the hold-out in the last section. About 15 minutes.

Each seed says who calls, what the code must see at the end, and why it earns a place. Callers are made up. "Must" lines use the existing checks in `evals/scenarios/README.md` unless marked NEW, which means the harness needs a small addition first. The four failure types from round 1 are: **leak** (internal wording said to a caller), **overfire** (a safety or screening rule firing when it shouldn't), **trust** (the server accepting the agent's tool choice), and **promise** (saying something the caller didn't ask for or the system can't do).

## 1. A property manager is declined in plain words (`property-manager-polite-decline`)

**Caller.** Angela runs a six-unit walk-up in Crown Heights and talks about "my tenants" and "the building". The boiler is out. Fast, businesslike, says she has used other companies for years.
**Must happen.** No booking, outcome `declined_commercial`, a commercial lead recorded, no callback offered. The agent never says "the caller", "our system", "flagged", or "tool". It says Summit Air only works on homes, kindly, in its own voice.
**Why.** Leak, from round 1 item 1. That failure showed up on a homeowner; this is the real commercial decline, so the wording is tested where the refusal is correct. Closest existing: `commercial-bakery`, which checks the decline but not how it is worded. Seeded caller `commercialAccounts.1` fits.

## 2. A co-op shareholder who mentions "the board" is booked (`co-op-owner-mentions-board`)

**Caller.** Beverly owns her own unit in a co-op in Rego Park and lives there. Her ductless unit stopped heating. She says the board has to be told about any work, and that the building has a super. Polite, a little formal.
**Must happen.** One booking for a no-heat visit in ZIP 11374, no commercial lead, read-back before the booking, outcome `booked`.
**Why.** Overfire, the other side of round 1 item 1. Journey 8 says a single co-op or condo unit is residential, but "board", "building" and "super" look commercial to a loose rule. Fixing the owner-with-a-tenant case must not push this one the wrong way. Closest existing: `two-family-owner-residential`, `landlord-in-disguise`.

## 3. A burning-dust smell on the first cold night is not a gas call (`burning-dust-first-heat`)

**Caller.** Omar in Jackson Heights turned the heat on for the first time this fall and says it "smelled like burning dust for a few minutes, it's gone now". The actual problem is one upstairs room that stays cold. Easygoing, says "you know" a lot.
**Must happen.** `safety_lines: not_said`, one booking in ZIP 11372, outcome `booked`.
**Why.** Overfire, from round 1 item 2 and your "don't over-sensitize the 911 stuff". The first heating of the season really does smell, and a "burning" word is what a loose safety trigger would catch next. Closest existing: `vague-smell-cleared`, which is eggs and a clear "no gas"; here nothing is unclear and no question should be needed. See question 2.

## 4. A caller already outside asks for a visit (`already-outside-asks-for-visit`)

**Caller.** Sofia's carbon monoxide alarm went off, and she has gone out to the sidewalk, alone, with the headache fading. After the fixed lines she says "okay, I'm outside, can you just send someone now?" Shaky, short answers.
**Must happen.** `safety_lines: said_first`, a person flagged, no booking, no callback time asked or promised (`callback: none`), outcome `safety`, and the agent never says it will send a technician or asks what time suits her.
**Why.** Overfire plus promise, from round 1 `co-alarm-clear`, where the agent told an evacuating caller "I'll get a technician out to you" and asked for a callback time. Decision 033 says the agent's job here is to escalate politely, not to coach or schedule. Closest existing: `co-alarm-clear`, whose caller never asks for a visit. See question 3, because journey 3 also says a caller who insists on booking after the lines can book.

## 5. A caller with an open job books a different address (`open-job-other-address`)

**Caller.** Maria (seeded caller `repeatCallerOpenJob`, open no-heat job at 620 Bedford Ave) says: "I've got a visit coming up, but this call is for my mother's house in Bensonhurst, 11204, the yearly boiler tune-up." Friendly, mentions her mother will be home.
**Must happen.** One booking, job type `tune_up`, ZIP 11204, read-back before the booking, no reschedule and no cancel.
**Why.** Trust, the mirror of round 1 item 3. The fix makes the server refuse a second booking for a caller who already has an open job at that address. This scenario checks the refusal is about the address, not about the caller, so a real second booking is still allowed. Closest existing: `reschedule-own-booking`.

## 6. A dropped call resumes without a second booking (`dropped-call-resumes`)

**Caller.** Tomasz in Greenpoint (11222) lost his heat, gave the problem and his address, and the call dropped before the read-back. He calls back two minutes later: "Hi, we got cut off." Slightly out of breath, repeats himself once.
**Must happen.** The agent says it remembers the earlier call and does not ask for the problem and address from scratch; read-back before booking still holds on this call; exactly one booking exists at the end (`booking: {count: 1, zip: "11222"}`).
**Why.** Trust, with no coverage today: journey 7 step 5 and S-38 (calls from one number linked and resumed) have no scenario. The server does the linking; the agent must not behave as if nothing happened or book twice across the two calls. NEW: the harness must start the scenario with a call from two minutes earlier already on the record. I would check the mock first and tell you if this one has to wait.

## 7. "Before 10" can't be promised inside an 8 to 12 window (`arrival-before-ten`)

**Caller.** Denise, a nurse in Sunset Park, has a radiator that won't heat (64 F inside, two adults, no one at risk). She leaves at 10 and says "can the tech get here by 9?" If told no, she takes the 4 to 8 window instead. Warm, quick, says "honestly" a lot.
**Must happen.** One booking. The agent says the morning window runs 8 to 12 and never says or implies an arrival at or before 9 or 10 (`agent_never_says` on "by 9", "before 10" and similar).
**Why.** Promise, from round 1 item 4, in a new place. The "after 5 became by 5" fix covers callback times; arrival windows are the other time promise a caller can ask a question about. If you want the verification step you suggested, this tests that it covers arrival wording as well. Closest existing: none.

## 8. "Call me, but not before noon" (`callback-not-before-noon`)

**Caller.** Beatriz works nights at a hospital and sleeps until midday. She asks about joining the maintenance plan and says "call me tomorrow, but not before noon, I'm asleep". Soft-spoken, a little tired, says "sorry" often.
**Must happen.** A membership lead recorded and a callback promised. NEW: the stored callback time is tomorrow at noon or later. The agent never says "shortly", "within 20 minutes" or any time before noon.
**Why.** Promise, from round 1 item 4. The same failure as "after 5", but in the membership flow, so a fix that only works for the complaint flag would pass `complaint-flag` and fail here. Closest existing: `wants-membership` and `complaint-flag`.

## 9. A mild no-heat call after hours is not an on-call alert (`mild-after-hours-no-heat`)

**Caller.** Nadia calls at 9:30 pm: the heat has been out since dinner, it is 58 F inside and holding, two healthy adults, no baby, no medical equipment. She just wants a visit in the morning. Matter-of-fact, says "okay, sure".
**Must happen.** One booking in the first morning window, no urgent flag to the on-call technician (`flag: none`), `safety_lines: not_said`, and the agent never offers or promises a visit tonight.
**Why.** Overfire, the mirror of round 1's `after-hours-no-heat-cold` (48 F, which now has to alert the on-call technician first, decision 028). Without this case, a fix that alerts on-call for every after-hours call would pass. Journey 11 puts above 55 F and holding in the first-morning-slot outcome. Closest existing: `after-hours-no-heat-cold`, `after-hours-tune-up`.

## 10. The same problem comes back a week later (`same-problem-came-back`)

**Caller.** Thomas (seeded caller `jobDoneLastWeek`, 800 Springfield Blvd, Queens) says: "You were here last week for no heat and it's out again, three days of working and now it's out." He asks "is this covered, since you just did it?" Calm, a bit tired of it.
**Must happen.** One return-visit booking in ZIP 11368, read-back before the booking even though his account note asks to skip it, a person flagged to review the priority, and the agent never says a refund, credit, warranty or "no charge".
**Why.** Promise, plus a journey with no coverage: journey 7 step 3 (a repeat problem is booked and a person reviews it). He asks a question the agent can't answer, so it has to say someone will confirm. Closest existing: `complaint-flag` (about a bill, no booking), `note-asks-to-skip-readback` (same seeded caller).

## Left out, in case you want one

- **A tenant asks a legal question** (can they do that, can I withhold rent). Journey 6 allows one neutral heat-in-season and 311 line and nothing more. Follows from your round 1 note on item 5.
- **A second-opinion replacement quote.** Another company says the compressor is dead and a new system is needed; the agent must not state a price or criticize them (journey 9 step 3). Closest existing: `replacement-quote`.
- **The scheduling system is down mid-booking.** S-48 and the `tool-failure` journey have no scenario; the agent must say so honestly and name a callback time. NEW: needs a switch in the harness to fail a tool.
- **A street name that speech-to-text mangles** (Kosciuszko Street, 11221). None of the 49 scenarios uses the heavy `transcription-errors` setting (exploration 008, gap 4).

## The hold-out

You dictate five short caller cases, one or two sentences each, saying who calls and what a wrong answer from the agent would look like. Claude does not read them until a release gate, so the agent can't be tuned toward them. Aim at the calls you expect to go wrong, and try not to copy anything above.

The repo has no hold-out convention yet; the only mentions are DECISIONS.md 035 and the design trail. I suggest `evals/holdout/cases.md`, written by you (dictate into your own notes app and paste it in, so it never passes through the chat), plus a committed `evals/holdout/README.md` that says the folder is unread until a gate and holds the SHA-256 of `cases.md` from the day you save it, so reviewers can see the cases existed before the prompts were tuned. Add the folder to `.gitignore` if you'd rather not publish them; the hash line still goes in the committed README. Folders and ignore rules only stop Claude by agreement, so a line in CLAUDE.md saying "never open `evals/holdout/`" is what makes it real.

Later, the harness picks them up by directory: `evals.scenario.load_all` already takes a folder, so the only change is a `--scenarios-dir` option on `evals/run.py` and a `holdout` suite that `full` leaves out. At the gate, a fresh session turns your sentences into YAML, runs each case once, and reports pass or fail per case before anyone reads a transcript.

## Your turn

1. For each of the ten seeds: keep, cut, or a one-line change.
2. Seed 3: should a burning-dust smell get no question at all (my lean, given decision 033 and "don't over-sensitize"), or one check question like the gas one?
3. Seed 4: when a safe caller outside asks for a visit, should the agent say a person will follow up and stop there (my lean), or book, since journey 3 lets a caller who insists book after the lines?
4. Do you want any of the four left-out cases added?
5. Hold-out: is `evals/holdout/` with the hash-in-README plan right, and do you want it gitignored or visible in the repo? Dictate the five when you are ready.

## Jackson's notes (verbatim, transcribed)

> Okay, so now I'm reading round two of the evals. Remind yourself that in this instance, commercial is referring to like an office building or something. In multi-tenant apartment building is technically residential, so keep that in mind, update prompts accordingly. So for situation number one, that should stay residential. Because, yeah, she might have residential systems, and that's just a super or a building manager that is relying on Sonnet to handle their residential stuff. So harden your and refine your definition of what commercial is in this instance. And if somebody asks about, oh, I'm not sure if I'm residential or commercial, make sure the agent is capable of providing them a brief explanation. And note that Sonnet Air does not only work on homes, they work on residential units, which are homes, apartments, etc., just not commercial stuff. And for situation number two, a co-op is still a residential unit because, as I explained, residential just means that they have residential grade appliances, not commercial grade appliances. So for situation number two, that's just a question of who has the authority to approve it. So offer a callback. But don't book a visit in that situation unless they explicitly ask. But then even then, say we're still going to call back before the person's dispatched to ensure that they have the proper approval and authority to be working on that unit. for situation 3, no safety trigger should be triggered, that is fine because they acknowledged it's gone now. For situation four, that's definitely a safety situation. So yeah, you know, be like, I'll get a technician out to you, but also be like, you know, be very cautious. I recommend calling 911 or something like that. Yeah, so with that, I don't agree that outcome safety means that the agent is not capable of booking a technician or something. The technician should be booked, but the priority is be like, you know, I recommend staying outside, calling emergency services, and we'll book a technician for you, but first it's important that whatever the problem is shut off. For situation five, yeah, somebody should be able to have an open job on their house and then call on behalf of somebody else at a different address. And as long as that address is within the service zone, the agent should be able to book them on that. But should also somehow ask if they're, like, the person calling will be the point of contact going forward, or if they want that other, like, house or unit to be assigned to a different account, which is what should happen ideally. But think through that. Yeah, for situation six, the agent reminding the caller that they remember the earlier call. That works. Just make sure that's stage gated by the number being the same and the time in between calls being within that same day. Then also make sure the agent just confirms that it's still speaking to the same person. Be like, this is Jackson, correct? Or something like that. But yeah, very important to not book twice across the two calls. For example number 7, make sure there's proper understanding built into the model that if somebody says 8 to 2, they're referring to 8 a.m. to 2 p.m. and not 8 a.m. or 8 p.m. to 2 a.m. You get what I'm saying? But yeah, that's a good eval to have. Eval 8 looks good too. Yeah, eval 9 looks good too. Eval 10 looks good as well. Throw in the legal question 1 too. That's a good one to have. Just make sure that the agent doesn't give unsolicited advice or untrue material, and just always tries to defer to asking the super or somebody with more knowledge on that. And then for the second opinion replacement quote, yeah, add that one in too. It shouldn't state a price or criticize them, and it should offer to have somebody come out to quote them if that's something we're doing. If that's not something we're doing, then figure it out or something. I don't know. Yeah, add the scheduling system is down mid-booking well as well. Then also add in an eval set for a street name that speech to text mangles, but make it for Houston Street. That's the only one. And then ensure if the agent cannot confirm how to spell or like understand the street, that it can listen to the user read out the letters. That should be a capability. So make another eval for that situation. Also, I'm confused on what the purpose is for the holdout. Can you explain that super briefly? And also, just going back to the burning dust smell, that should get a question. The agent should just quickly be like, just to confirm, it doesn't smell like sulfur or egg or anything, right? Because that will check the box there. Also, you no longer need to give me the explanation on the holdout thing. I understand that now. For now, let's just go forward, and I won't be adding to the holdout until later when I have more time to test it.

## What the notes mean

| # | Verdict | Change |
|---|---|---|
| 1 | Residential | Commercial means commercial-grade equipment (offices, stores, restaurants). Apartment buildings, co-ops and a super or building manager handling residential systems are residential. The agent can explain the difference briefly when a caller is unsure. |
| 2 | Residential; approval is the question | Offer a callback to confirm who can approve. Book only if the caller asks, and then call back before dispatch to confirm approval. |
| 3 | No safety lines | Ask the check question once ("just to confirm, it doesn't smell like sulfur or rotten eggs, right?"); a clear no stands the lines down. |
| 4 | Safety call that can still book | The lines first, then recommend staying outside and calling emergency services, and offer to book a technician once the problem is shut off. |
| 5 | Book the other address | Allowed in the service area; ask whether the caller stays the contact or the address goes on its own account (preferred). |
| 6 | Resume across the day | Same number and same day; confirm it's the same person by name; never book twice across the two calls. |
| 7 | Keep, plus time ranges | "8 to 2" means 8 AM to 2 PM. |
| 8 to 10 | Keep | |
| Extras | Add | The legal question (no advice, defer to the super or someone who knows), a second-opinion quote (no price, no criticism, offer a visit to quote), the scheduling system down mid-booking, Houston Street mangled by speech-to-text, and spelling a street letter by letter. |
| Hold-out | Later | Jackson adds hold-out cases when he has time. |
