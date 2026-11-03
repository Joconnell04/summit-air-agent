# Co-authoring log

Rounds on the documents that define the product. Each round: the feedback as spoken (verbatim from transcription), what changed and why, and the commit.

<!-- Template
## <doc> r<N>
Feedback (verbatim):
> ...
Changed:
- ...
Commit: <sha>
Status: open | locked
-->

## users r1
Feedback (verbatim):
> (none; version 1 drafted from docs/assignment.md, docs/considerations.md and from-other-projects/README.md)
Changed:
- First draft: nine caller types, cross-cutting caller traits, four Summit Air staff roles, and a ranking for when needs conflict.
Commit: 5660e98
Status: open

## users r2
Feedback (verbatim):
> Okay, so I'm just speaking and here's some feedback and considerations and stuff for the user's markdown file. So firstly, just do a little bit of background research on how these HVAC companies operate. We need to know their business, to know their users. You know, what does an on-call person do? Are they just like the person answering the phone? Do they know the HVAC people? You know, it's a 40-person company, so you can make some assumptions around that, but make sure we really know the different people involved on this. Also think through if we should implement some form of like playbook layer, you know, or like tool call thing or something, like a playbook. So that way it's like, if there's a surge in this zip code and a phone number from that zip code is calling, you know, maybe we know not to try to upsell them on that call because we know it's probably going to be related to a surge. I'm not sure if that's the best idea, but just something to consider and ideate on. Yeah, like vulnerable household. I like your wants and worries for all of these. That's a good way to do that. But also through this, you know, maybe take some notes or whatever on ways we can actually start to capture these things, like vulnerable household. Like, are there keywords that we can expect a vulnerable household will say that deterministically would mean we change our playbook or approach or something or label as priority? You know, I say there's probably not too many like 100% deterministic things we can build in, but maybe there's like a sentiment score or something like that that we can build in without breaking the latency budget. Not really sure. Another thing to ideate on. I'm not saying it just to tell you what to do. I'm saying it to just surface an idea I'm having. And make sure to ask around, are there vulnerable people? Like, don't make that weird. There needs to be some good like language use, like normal language. And make sure we're not implementing some like potentially damning system for stuff related to three, the safety caller. I.e., there should be like proper checks to ensure that we're not like hanging up on a safety caller or something like that. I don't know. Make sure you simplify language too. For example, for maintenance scheduler, you say, in a surge, these jobs yield to urgent ones. That is confusing wording and makes no sense to the reader. Definitely members expect to be recognized to get the plan's perks. And there should be a proper tracking, acknowledgement, and like playbookization around like repeat callers. Like if somebody's calling like 5 times within the same minute, like that's something that should be properly tracked and applied within the conversation or something. Yeah, so for commercial stuff, yeah, just make sure you filter them out properly. Don't add them in. Actually, for commercial callers, we should be tracking that a company called in case it ever becomes useful. We just shouldn't be like making it a big deal or spending a lot via tool usage on that, like, or whatever. But if anybody calls, we should be tracking something around that. Yeah, for number 9, not a customer call. That's good considerations to have. Make sure your handling for other languages is solid too, without requiring the need for like a million more agents or anything. Yeah, so anyway, take into account all the feedback. It's not too much. Make sure you do proper research on the industry and what these actual different stakeholders and people look for, you know, things like common requests for HVAC companies, especially like those in New York and stuff like that. Yeah, and then next. So do that and then slash next and lock
Changed:
- Staff section rebuilt from research (explorations 001, 002): call-taker, dispatcher, service manager, on-call technician and comfort advisor named, each with what they judge success by. The on-call person is a technician on a weekly rotation, not the person answering the phone.
- New section on how the agent tells callers apart: server facts, fixed phrases, and a score that runs beside the turn so it adds no delay. The server picks a playbook from these (C-32), e.g. no upsell for a caller in a surge ZIP.
- Each caller gets "How the agent knows". Vulnerable household gets the plain-words question and the phrase examples.
- Safety caller: separate gas and CO scripts from official wording, and checks so the end-call and spam paths can't drop a safety call.
- Repeat callers: calls linked by number, a dropped call resumes where it stopped, repeated calls with no progress go to a person.
- Commercial: one-line lead saved with the normal end-of-call record, no extra lookups.
- Languages: one agent, Spanish live, other languages get a callback.
- New caller type for replacement and install quotes (NYC Clean Heat questions); renter section grounded in the NYC heat law.
- Plain wording throughout, e.g. the surge line for tune-ups.
Commit: d9d04b7
Status: locked

## users r3
Feedback (verbatim, given after the r2 lock):
> I'm going to give you more feedback and stuff, but just another consideration to throw on to the ideas or consideration stuff is if we could use Jev or Laya or a decisioning model at any point without breaking the latency budget to make the system better. That's just something I want to throw out there. Yeah, and regarding what I said about like a zip code based surge, I'm not really confident that's like how that really works, but that was just an idea to throw down. So every time I have an idea to throw down, it doesn't mean just go and implement verbatim what I said or based on your interpretation. It's just like look into stuff and whatnot. But I do think Jev or decisioning model would be a good way to show the interviewer that I'm up to date on those types of things and am constantly looking for the best ways to do stuff. Yeah, make sure that the fix list of phrases to raise priority isn't like overly ambitious and stuff. And make sure that the safety caller wording is not too weird or like overly formal. Should still sound like a human, properly explain things without going overly too technical, stuff like that. Yeah, and regarding the repeat callers, I think that's like just a good point to approach this work with in general is like item potency. So keep that in mind. And this system shouldn't be overly specific to New York City, but like obviously it should be somewhat specific. Like it should have proper context on each of the boroughs and neighborhoods and stuff like that, if it makes sense. But it shouldn't be like mentioning state programs unless it's like super relevant.
Changed:
- ZIP-based surge playbook taken out as a design; C-32 now says it is an unverified idea.
- The fixed list is cut to gas, CO, smoke or burning, and medical equipment like oxygen. Age, babies and illness go to the score or a decision model (C-35).
- Safety scripts rewritten as two short lines a calm person would say, with the agent ending the call so the caller can get outside.
- Idempotency stated for repeat callers and made a general rule (C-34).
- Service-area context comes from config; state programs mentioned only when they change what the caller does (Clean Heat line removed, C-36).
Commit: 2c8e355
Status: locked

## call-journeys r1
Feedback (verbatim):
> (none; version 1 drafted from docs/users.md, docs/considerations.md, the #10 triage proposal and DECISIONS.md 001 to 007)
Changed:
- First draft: a shared routine every call follows, eleven journeys with the server check that fires in each, and six open choices marked inline with a recommendation.
Commit: f0ccc61
Status: open

## call-journeys r2
Feedback (verbatim; Jackson noted it was transcribed with typos):
> Okay, now I'm going to read through the call journeys markdown file and just tell you my thoughts on that. Yeah, so just keep in mind that this system should work both if it's called or if it's calling somebody. But yeah, the lookup before hello, perfect. The greeting should still state that it's Summit Air, even if it's saying the name. So it's like, hi, this is Summit Air. Is this Maria calling? Or something like that. Yeah, listen first. If it's been a few seconds, then the AI can ask, you know, is everything all right? Or whatever, you know, make it realistic without being like too pushy. And I want the journeys to like be able to jump between them. So it's like if somebody triggers the safety journey, but then they continue to talk and it's realized that that's not the case, it needs a way to jump back and get off of that strictly safely journey or whatever. And make sure you ask questions in like a priority order. For example, you know, pets, that's something that's really not super relevant. Maybe, you know, ask that at the end or only ask if it's needed. Like you should not be asking questions like just because it could be helpful. Only ask questions like when it will be helpful, if that makes sense. I just don't want to flood the user with questions. Yeah, always have the readback gate too. And make sure that if the person's like, you know, the best phone number is the one I'm calling from, that the AI can actually read the phone number that's calling from and document that, and that it's not like some proxy phone number or something. And then allow or like enable the agent to use like semantic shorthands. Like if the person's like, you know, and use the phone number ending in 4024, and the AI agent sees like three available phone numbers and one of them ends in 4024, just be like, okay, yeah, cool, that one. Because it should be able to kind of connect those dots. And for the booking, try to make it be good. Make it not just, here's the three next 1 hour availability windows. It should be like, I have a lot of availability on Tuesday and Thursday, as well as Thursday morning. Let me know which of those works and I can give you the list of time slots available or something like that. But yeah, then confirm out loud for sure. Anything else for sure. Yeah, I'm just make sure everything's logged properly. I'm not going to read through every single call journey, but I'll continue giving some feedback. So for like no heat or no cooling, just make sure that when the agent says, I'm sorry, that's no fun, when did it stop? That it sounds like a human and is not super like body, and is that phrase, that's no fun, shouldn't be reused every single time. There should be like some variability, I guess. Don't immediately ask who's home, because that can come off as like creepy. So follow just a more like service-oriented flow. And, you know, don't hard code anything in about the area code of the phone number being mapped to the zip code. Some people might just have a different phone number. Obviously check if the zip is in the service area, but yeah, politely decline, maybe offer flagging to a supervisor or something, only if that's like common practice. Don't do that if it's not. Yeah, make sure proper questions are asked about like membership and stuff in like a respectable manner, given the person might be going through a sensitive situation. Now, and the question about, is everyone at home who's older, very young, or health condition? I don't know. Definitely look up like industry best practices for that type of thing, but that's just not sounding too right. It's, to me, that sounds abruptly like, should I care about your concern or not? And I want it to seem more inquisitive and human and personable, and let the answers decide the priority, but not to be like, give me a reason to make your case low priority or high priority, if that makes sense. And the stuff like, I've marked this as a priority, you know, give them more than that. Be like, I marked this as a priority and just notified the on-call service representative or something like that, as long as it's true, but just make them feel heard and stuff. Yeah, and for number two, I agree with your recommendation to book the first window. So the caller has something certain, and then flag a person, try to move it up, and then have some system in the back end be like proactively managing that. So that way, if somebody gets their call moved up, they get a call back or something like that. Make sure that the decision has no ability to call 911, like for the demo. Like, I don't want, yeah, that to ever happen. Make sure that agent doesn't just end the call without signing off in the safety situation. It should just be like, I recommend calling 911 immediately. You know, happy to stay on the line, but really think you should blah, blah, blah. Yeah, and then after the caller is safe, I agree with your recommendation that yes, someone here should offer a follow-up visit on a later call and say we'll treat it like super high priority or whatever. Make sure for the members that it like thanks them for being a member without being like too cliche. Like, I see you're on the comfort plan, just want to thank you for, you know, being a loyal customer and happy to get you set up some priority scheduling or something like that. Yeah, but I agree with basically all your recommendations here. Also ensure there's like some proper handling of like a gross error. Like, I'm thinking if somebody is in a place with spotty cell service and they keep calling back, the AI agent should at some point be like, hey, I see that we keep getting disconnected. Then be like, you want to call back later, something like that. Just for those gross errors. But yeah, your repeat caller number 7 logic is good as well. Make sure you're nice for the commercial callers too. Just be like, you know, just be like, unfortunately, us at Seminar, we only work on residential, so we're not the right fit for this situation. I can have a member of our team call you back. Actually, don't offer a call back, but yeah, you know what I'm saying. Also, make sure there's like some way we can include like company specific heuristics or information or context. For example, let's say that this hypothetical 40 person HVAC company in New York City had a slogan at the end. We should be able to add that in to some like markdown file or something somewhere, and that way it can use it. Or if the company wants their agent to like actually know specifics about certain HVAC machine models, like it should be able, that should be a possibility to load that in if they want that, to an extent at least. For the after hours, yeah, same spine. It says the office is closed, so yeah, it's good.
Changed:
- Covers calls Summit Air makes as well as receives (new journey 12: earlier slot, requested callback, safety follow-up), with one open choice on which V1 places automatically.
- Greeting always names Summit Air, including for known callers; a gentle check-in after about 5 seconds of silence.
- Journeys can switch; an unclear safety mention gets one check question, and a cleared false alarm returns the call to where it was.
- Questions asked in order of need and only when needed; pets, access and parking folded into one open question at the end.
- Caller ID used for "the number I'm calling from", read back; shorthand like "the one ending in 4024" matches numbers on file.
- Times offered as the shape of availability first, then exact windows.
- Varied acknowledgments; no phrase repeated in a call (checked in evals).
- No area-code-to-ZIP guessing; out-of-area declined kindly with no supervisor flag (normal practice).
- Vulnerable-household question rewritten after checking call-handling guides: asks about the people once the problem is understood, and lets the answer set priority.
- The agent says what it actually did ("let our on-call team know"), only once the server confirms it; it can never call 911 itself.
- Safety calls signed off rather than ended; booking first window plus a background job that offers earlier openings by callback.
- Member thank-you made natural; membership asked only after the urgent part.
- Repeated drops get a callback offer.
- Commercial decline made warmer, with no callback.
- Company knowledge folder for slogans, plan wording and equipment notes (C-41); outbound calls added as C-40.
- All open items from r1 settled as recommended.
Commit: d3692e0
Status: open

## call-journeys r3
Feedback (verbatim):
> Your recommendation for Journey 12 looks good. and good to implement the C42-46 stuff, just make sure you document the rationale and the blog posts corroborating the story. and im confused what you're asking by "Which outbound calls should V1 place automatically?" so rephrase and ill give an answer and then progress forward
Changed:
- Journey 12 settled as recommended: the earlier-slot offer and requested callbacks are automatic; the safety follow-up is a task for a person.
- Findings from Revin's blog folded in, each with its reason and the post it comes from (exploration 005), marked as vendor practice: owner view shows booking rate by call type, callbacks promised vs done, and booked-and-kept (C-42); after-hours no-heat ends in one of three named outcomes with temperature cutoffs in tenant config (C-43); heat pump defrost and furnace lockout look-alikes, with no reset instructions (C-44); every callback names a time (C-45); cancels ask why and offer two windows, second-opinion, refrigerant and rebate calls handled, and a new complaint or warranty journey (C-46).
- No open items remain.
Commit: 4d4de20
Status: locked

## spec r1
Feedback (verbatim):
> (none; version 1 drafted from the six requirements, docs/users.md, docs/call-journeys.md, docs/considerations.md and DECISIONS.md 001 to 008)
Changed:
- First draft: 47 testable statements grouped under the six requirements plus cross-cutting rules, each with how it's tested (unit, eval tags, grader, live) and where it comes from; 10 marked must-pass. Assumptions behind the numbers moved into docs/brief.md.
Commit: bd77be5
Status: open

## spec r2
Feedback (verbatim):
> Okay, feedback for spec markdown file. It's good so far. I'll include some more feedback. One thing I do want you to change is I don't like how the membership plan is called Comfort Club. Let's just call it premium membership or premium member or a summit VIP or something like that. Actually, I don't like VIP, but maybe just some combination of premium and summit and membership, something like that. Not Comfort Club. That's weird. Yeah, make that decision on your own. If somebody wants to become a member, that should also be a flow. We don't need to re-go over it. I trust that you can handle implementing that and updating the documentation for like the user stories for that appropriately. For the fix safety lines, I think it's good that there's like some level of determinism in that, but make sure that there's some also variability. So that way, if somebody keeps saying the same thing about their carbon monoxide signal or something, that the agent does not repeat the same exact phrase verbatim multiple times in a row to the same person within the same, you know, few minutes or whatever. And then for S18M, there should be some capability of overriding that if the person on the phone is like, stop giving me the safety lines, just book the appointment. Like if they're demanding the appointment be booked, just book it for them. Obviously don't make that something that makes prompt injection easier, but you get what I mean. Ensure proper logging as well for like failed tool calls. Like this will make it easier to debug, but if somebody's like book an appointment and then it doesn't work for some reason, you know, the agent should follow the same thing. Be like, I'm not able to book it right now, but like I'll note for somebody to book it and follow up with a call later. Like it should be able to honestly be like, oh, it's not working right now, but I'll send a note to a human and they'll follow up or something like that. Because if like the booking database is down for a little bit, the agent should still be able to like do its own thing and essentially queue actions, if that makes sense. But think through that. That might be a little bit out of scope, but it might be easy to do. So think through it. Yeah, and the word gas is not an immediate trigger for a safety thing. Like if somebody says gas bill or gas furnace, like, or just gas, you know, it's not enough to be like immediately inquiring on if they're in an active safety scenario. For S28M about out of area zips, you should document those too, because if enough people are calling with out of area zips, you know, maybe that could eventually become a decision for leadership or management to start serving that market of previously out of area zips. Just something that should be like logged and recorded. We don't have to do anything with it now. And if they mention on the call regarding S29, if they mention, like the customer mentions a specific day or time, like morning, afternoon, night, at some point in the call, that should inform the first recommended set of availability options. But a lot of these are good. They're good. Also, just something I'm thinking about that I want you to like research lightly, but let's say that the customer is calling with a lot of background noise. How do we prevent the model from thinking it's interrupted and needs to stop talking every time background noise spikes? Maybe that's not a concern because the speech to text model won't translate that, but I'm not entirely sure. So just something to think about, something to document in considerations. Ensure proper do not call logging. Like if the person on the phone is saying, do not ever call me again. And previously it asked for a callback, just be like, okay, I'm going to respect your decision. But just so you know that the callback can't happen. I don't know, like figure that out nicely. But yeah, you get it. I can't really speak to the number of seconds for the S36 reply delays, but they sound good to me right now. And then if I give feedback later related to like delays and speaking or something, just know to come back to that and change that number. But good for now. Okay, take into account all of this and then consider that decided. And then what's the next thing? Is it brief MD? Figure that out.
Changed:
- Plans renamed: Summit Premium Membership, Summit Standard Membership, and a closed Summit Legacy Plan, in the docs, tenant config, seed, tests and Postman collection (128 tests and 101 Postman checks pass).
- New joining flow (call journeys journey 14, spec S-12a, C-50): a sign-up lead and a named callback; the agent takes no payment and offers the plan only at the end of a routine call.
- Safety lines: word for word the first time, a shorter signed-off variant if raised again (S-17). After the lines and the flag, a caller who insists can have the booking made, logged as an override; nothing a caller says skips the lines (S-18).
- "Gas" alone, a gas bill or a gas furnace triggers nothing; only a described smell, leak, alarm or symptom does (S-20, spine step 4).
- Failed tool calls logged; if the FSM is down the agent says so, the request is queued and retried idempotently, and a person is flagged if it still fails (S-48, C-47).
- Out-of-area ZIPs recorded and counted on the owner view (S-28). A time of day the caller mentions shapes the first options (S-29).
- Background noise: light research on LiveKit's interruption settings recorded (C-48, S-50).
- Do-not-call: marked at once, pending callbacks cancelled, said kindly (S-49, C-49, journey 12).
- Reply delay targets kept, with a later.md entry to revisit on any delay feedback.
- Locked docs amended for consistency (users.md and call-journeys.md: plan name, joining flow, gas trigger, override, do-not-call, tool failures), recorded in this round.
Commit: 2cec070
Status: locked

## spec r3 (amendment after lock)
Feedback (verbatim):
> Cut out the Summit legacy plan. That just is creating confusion. And let's just have one membership category, a Summit standard membership. So cut out the Summit premium membership too. So it should be like, you know, net new callers that are in the area scope and then Summit members. And that's it. But tell me if the assignment said something that makes you want to do something differently, because maybe I'm missing something, but I don't want to overcomplicate this. Everything else looks good.
Changed:
- One plan only, Summit Standard Membership (priority scheduling, two tune-ups a year, 15% off repairs, no after-hours fee); a caller is a member or not. Premium and Legacy removed from the docs, tenant config, seed, tests and Postman collection; the two-plans-at-one-address scenario replaced by a test that every membership uses the one plan (128 tests, 101 Postman checks pass).
- Spec S-37a (bridge line only for slow tools) and S-37b (stage timings logged, eval runs flag delay changes) added from the latency feedback.
Commit: 7bd624b
Status: locked

## spec r4 (amendment after lock: tool contract)
Feedback (verbatim):
> Okay, some thoughts and questions for you to address. First, is there not a check membership tool? And if not, why not? Would that like speed that process up? I'm not really sure. Yeah, I feel like find customer should do that. Also, I just think it would be helpful if there could be like notes attached to a customer, like in the CRM. I'm thinking if someone's like had a note of like they prefer warm and friendly, you know, then warm and friendly intro should be used, or I don't know, something like that. But just an idea. Maybe you can throw it on the later markdown file, but I'm sure. Also, stuff like find last service date or find last call. I feel like the model should be able to, if it wants to like know that information too, which is just pretty structured queries to the CRM and other connected databases, right? Maybe it could also document like just free text notes. I don't know, like record note. I don't know. I want to make sure that this system is very versatile and good at handling a wide variety of requests while still holding true to its like underlying contract, if that makes sense. All of that feedback was for the tools markdown file.
Changed:
- No separate membership tool: the first lookup and `find_customer` already return membership, open jobs, last service date, recent calls and customer notes in one round trip (faster than two calls).
- New `get_customer_history` (read-only, caller's own records) and `add_note` (append-only, never an instruction); customer notes shape tone and save questions but never bypass a gate. Spec S-15a, C-51, docs/tools.md.
- Stale texting wording fixed in users.md, architecture.md, template.md and considerations.md.
Commit: 897fddd
Status: locked

## spec r5 (amendment after lock: build review)
Feedback (verbatim, the parts this round acts on):
> Make no heat on its own, make that rule like bracketed. So if it's like under 45, it's urgent. If it's between 45 and, or if it's 45 and under, it's urgent. If it's, or no, maybe if it's under 55 inside, it's urgent. If it's 55 to, I don't know, like 75, it's high. And if it's above 75 outside, then it's like medium or something. Look up, maybe there's like a standard for that. Let's see. But yeah, implement that stage gating if you can.

> Make sure you also implement proper questioning so that if it's very vague, if it's a business or a resident that the voice agent just asks, and then maybe also make it, just throw in like a confirmation question, like, and just confirm this is a residence or individual household or something like that, right? That way we can satisfy the assignment's bullet point on that.

> And figure out a fix to the and as if the caller only asks about but doesn't live there still counts towards the out of area demand.

> regarding the number two about the I'm 84 years old, review the assignment text and don't take that example so literally. It should just be like compromising individual or situation for a family or something and issue, you know, that elevates priority, not strictly old and no heating. You know, there's other situations, so maybe we can ideate like some way to like consistently serve proper priority.
Changed:
- S-21: temperature brackets set where a no-heat or no-cooling call starts (no heat: below 55 F inside Urgent, below 65 F High, warm inside with cold outside or tonight High, otherwise Normal; no cooling: above 85 F Urgent, 79 F or more High, 80 F or more outside High, otherwise Normal; unknown fails safe to High). Cutoffs come from the sourced bands in exploration 007 rather than the 45/55/75 first guess, all ASSUMED in tenant config. Outside temperature is read hourly from the National Weather Service (app/weather.py), never during a turn.
- S-21: "vulnerable" read broadly: older adults (65, CDC; however the age is said), infants, medical equipment, bedbound, symptoms now and indoor oven or generator heating are Urgent; young children, a parent of unknown age, limited mobility, living alone, chronic illness, dementia, pregnancy, recovering from surgery, and frozen pipes are High, and two of those together are Urgent. On a mild day the Urgent ones raise to High.
- S-07: the agent asks once when home or business is unclear, and the read-back sentence names the address as the caller's home, so every booking confirms it; a caller who says it's a business can't book.
- S-28: a known caller whose every address on file is out of area is counted at the start of the call; a call that booked a visit isn't counted toward out-of-area demand.
Not changed: the carbon monoxide check question on oven or generator heating (exploration 007's suggestion) would change the safety list in docs/users.md, so it is left as an open question.
Status: open

## spec r6 (amendment after lock: telling the caller about urgency)
Feedback (verbatim):
> Make sure that when like that vulnerable situation is identified, that it is mentioned to the user that their case has been put or labeled as like the highest urgency ones. And then you just don't mention urgency for when it's not that. Or give them reassurance without saying you labeled it urgent. Maybe just say you labeled it high urgency.
Changed:
- S-21a: when the server marks a call Urgent, `find_slots` opens its sentence once per call with the tenant line "I've marked this as one of our most urgent calls, so you get our soonest opening." Below Urgent nothing names a priority; the prompt (prompts/tool-use.md) says to reassure without a label. The line comes from the server, so the model can't overstate it.
Status: open

## agent voice r1 (callback opening line)
Feedback (verbatim): Jackson picked option A on the #49 readout ("A. Ask by name (Recommended)").
Changed:
- `callback_opening` in tenants/summit-air/agent.yaml: "Hi, this is Summit Air calling back for {first_name}. Is that you?" so a yes confirms the person (DECISIONS.md 010, 013).
- New `callback_opening_unknown` keeps the old line for a caller with no single first name on file, so the opening never guesses between people.
Commit: 34dcda1
Status: open

## spec r7 (amendment after lock: the ideas round, before phase 3)
Feedback (verbatim, the parts this round acts on):
> i approve of all of them but cut out idea 5 completely.

> properly document and backfill the system design with those ideas, though note politely/subtly they were after thoughts that were retroactively put back into the design stuff before testing was done. frame it as a good thing tho because it is.

> use 60 as the age thing for older adult tho to be in line with NYC Health, note that as a decision and the rationale is alignment with local standards/laws.

> regarding your question on a caller and heating the house: don't let the agent recommend sloppy/potentially unsafe solutions like using an oven to heat the house. but yes, that question should be asked when relevant but not overly hard coded...i don't want it to come up more than it needs to

> also i approve of all the recommendations so go forward with that too.
Changed:
- S-21: an older person is 60 and over, NYC Health's line (DECISIONS.md 019).
- S-21b (new): the carbon monoxide check question on indoor fuel-burning heat, asked once and only then; the agent never suggests unsafe heat (DECISIONS.md 024). This settles the question r3 left open.
- S-46: tenants extend the shared `trades/hvac/` layer (DECISIONS.md 021).
- S-51 (new): rules by call stage in the owner view, editable guidance and four lines behind the must-pass evals, gates never loosened (DECISIONS.md 022).
- S-52 (new): local names spoken and heard the local way, with the written form kept in the read-back (DECISIONS.md 023).
Status: open

## call-journeys r4 (amendment after lock: heating with an oven or generator)
Feedback (verbatim):
> don't let the agent recommend sloppy/potentially unsafe solutions like using an oven to heat the house. but yes, that question should be asked when relevant but not overly hard coded...i don't want it to come up more than it needs to
Changed:
- Spine step 4 and journey 3: a caller who says they're heating the home with something that burns fuel indoors (oven on or open for heat, stove, grill, generator, kerosene or propane heater) gets the existing carbon monoxide check question once per call. Only wording that says it's used for heat counts, so "my oven works", a gas stove or "the generator is outside" asks nothing. Symptoms or an alarm in the answer fire the carbon monoxide lines as today; a clear no carries on.
- Journey 3 step 9: the agent never suggests an oven, stove, grill, generator or unvented heater to get warm and never agrees one is fine to keep using; if asked, layers, blankets, one warm room or somewhere warm to go.
- Journey 3 step 5: the safety sign-off says "get outside", not "get everyone outside", because by then the caller may have said they're alone (eval round 1, `co-alarm-clear`). Wording taken from the signed-off variants; needs Jackson's sign-off like any safety line change.
- users.md (the fixed list) amended to match; the spec line is S-21b from spec r7 (DECISIONS.md 024). Server rule in `app/safety.py` and `tenants/summit-air/agent.yaml` (`safety.heating_check`); prompt rule in `prompts/safety.md`.
Commit: 6bf8359
Status: open
