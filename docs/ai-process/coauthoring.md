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
> regarding your question on a caller and heating the house: don't let the agent recommend sloppy/potentially unsafe solutions like using an oven to heat the house. but yes, that question should be asked when relevant but not overly hard coded...i don't want it to come up more than it needs to
Changed:
- Spine step 4 and journey 3: a caller who says they're heating the home with something that burns fuel indoors (oven on or open for heat, stove, grill, generator, kerosene or propane heater) gets the existing carbon monoxide check question once per call. Only wording that says it's used for heat counts, so "my oven works", a gas stove or "the generator is outside" asks nothing. Symptoms or an alarm in the answer fire the carbon monoxide lines as today; a clear no carries on.
- Journey 3 step 9: the agent never suggests an oven, stove, grill, generator or unvented heater to get warm and never agrees one is fine to keep using; if asked, layers, blankets, one warm room or somewhere warm to go.
- Journey 3 step 5: the safety sign-off no longer says "get everyone outside", because by then the caller may have said they're alone (eval round 1, `co-alarm-clear`).
- users.md (the fixed list) amended to match; the spec line is S-21b from spec r7 (DECISIONS.md 024). Server rule in `app/safety.py` and `tenants/summit-air/agent.yaml` (`safety.heating_check`); prompt rule in `prompts/safety.md`.
Commit: 6bf8359
Sign-off feedback (verbatim, Jackson on the sign-off wording):
> get outside is fine...but should also be polite and more like a recommendation than a demand.
Changed:
- Sign-off signed off by Jackson as a polite recommendation: "For your safety, I'd recommend hanging up now, getting outside, and calling 911 from there. We'll follow up once you're safe." In `tenants/summit-air/agent.yaml` (`safety.sign_off`) and journey 3 step 5. The first safety lines and their shorter variants are unchanged.
Commit: c019538
Status: open


## spec r8 (amendment after lock: eval round 2, residential, approval and safety calls)
Feedback (verbatim, Jackson's eval round 2 notes, the parts this round acts on are on seeds 1 to 4):
> Okay, so now I'm reading round two of the evals. Remind yourself that in this instance, commercial is referring to like an office building or something. In multi-tenant apartment building is technically residential, so keep that in mind, update prompts accordingly. So for situation number one, that should stay residential. Because, yeah, she might have residential systems, and that's just a super or a building manager that is relying on Sonnet to handle their residential stuff. So harden your and refine your definition of what commercial is in this instance. And if somebody asks about, oh, I'm not sure if I'm residential or commercial, make sure the agent is capable of providing them a brief explanation. And note that Sonnet Air does not only work on homes, they work on residential units, which are homes, apartments, etc., just not commercial stuff. And for situation number two, a co-op is still a residential unit because, as I explained, residential just means that they have residential grade appliances, not commercial grade appliances. So for situation number two, that's just a question of who has the authority to approve it. So offer a callback. But don't book a visit in that situation unless they explicitly ask. But then even then, say we're still going to call back before the person's dispatched to ensure that they have the proper approval and authority to be working on that unit. for situation 3, no safety trigger should be triggered, that is fine because they acknowledged it's gone now. For situation four, that's definitely a safety situation. So yeah, you know, be like, I'll get a technician out to you, but also be like, you know, be very cautious. I recommend calling 911 or something like that. Yeah, so with that, I don't agree that outcome safety means that the agent is not capable of booking a technician or something. The technician should be booked, but the priority is be like, you know, I recommend staying outside, calling emergency services, and we'll book a technician for you, but first it's important that whatever the problem is shut off. For situation five, yeah, somebody should be able to have an open job on their house and then call on behalf of somebody else at a different address. And as long as that address is within the service zone, the agent should be able to book them on that. But should also somehow ask if they're, like, the person calling will be the point of contact going forward, or if they want that other, like, house or unit to be assigned to a different account, which is what should happen ideally. But think through that. Yeah, for situation six, the agent reminding the caller that they remember the earlier call. That works. Just make sure that's stage gated by the number being the same and the time in between calls being within that same day. Then also make sure the agent just confirms that it's still speaking to the same person. Be like, this is Jackson, correct? Or something like that. But yeah, very important to not book twice across the two calls. For example number 7, make sure there's proper understanding built into the model that if somebody says 8 to 2, they're referring to 8 a.m. to 2 p.m. and not 8 a.m. or 8 p.m. to 2 a.m. You get what I'm saying? But yeah, that's a good eval to have. Eval 8 looks good too. Yeah, eval 9 looks good too. Eval 10 looks good as well. Throw in the legal question 1 too. That's a good one to have. Just make sure that the agent doesn't give unsolicited advice or untrue material, and just always tries to defer to asking the super or somebody with more knowledge on that. And then for the second opinion replacement quote, yeah, add that one in too. It shouldn't state a price or criticize them, and it should offer to have somebody come out to quote them if that's something we're doing. If that's not something we're doing, then figure it out or something. I don't know. Yeah, add the scheduling system is down mid-booking well as well. Then also add in an eval set for a street name that speech to text mangles, but make it for Houston Street. That's the only one. And then ensure if the agent cannot confirm how to spell or like understand the street, that it can listen to the user read out the letters. That should be a capability. So make another eval for that situation. Also, I'm confused on what the purpose is for the holdout. Can you explain that super briefly? And also, just going back to the burning dust smell, that should get a question. The agent should just quickly be like, just to confirm, it doesn't smell like sulfur or egg or anything, right? Because that will check the box there. Also, you no longer need to give me the explanation on the holdout thing. I understand that now. For now, let's just go forward, and I won't be adding to the holdout until later when I have more time to test it.
Changed:
- S-06 and S-07: residential means residential-grade equipment (houses, apartments, co-op and condo units, whole apartment buildings, including a super or building manager calling for the building); commercial means a business with commercial-grade equipment (offices, stores, restaurants). The "three or more units" and "property manager" lines are gone. A caller unsure which they are gets a one or two sentence explanation, and the decline says Summit Air works on homes and apartments, not businesses.
- S-07a (new): a caller who says a board, landlord or management has to approve is offered a callback to confirm who can approve; a booking they ask for gets a server-scheduled call before the arrival window to confirm approval before dispatch.
- S-18: after the lines and the flag, the agent recommends staying outside and calling 911 or the gas company, and may offer a visit for once the problem is shut off without the caller having to insist; the booking carries the server's shut-off line.
- S-20: burning dust and similar smells get a lighter confirm question once per call ("Just to confirm, does it smell at all like sulfur or rotten eggs?"). Asked as a yes or no question rather than Jackson's "it doesn't..., right?", because a "yeah" to a negative question can mean either, and the server reads a yeah as a smell.
Not changed: the read-back still says "your home at" (DECISIONS.md 022).
Follow-up feedback (verbatim, Jackson on the PR's three questions):
> yeah means it's not a safety issue in that instance for your first question. for your second question, yes change those two to be residential. for question 3, no keep that wording as most people calling aren't building managers. but include in the docuemntation somewhere that we are interpreting commercial as truly commercial and residential as either an individual tenant, unit, or even a full residential building because we expect an HVAC company in NYC to operate on individual/residential-grade units and just not commercial-grade units, and individual/resitdential-grade units are present in multi-tenant buildings
Changed:
- S-20: the confirm question is Jackson's own negative wording, "Just to confirm, it doesn't smell like sulfur or rotten eggs, right?", and the server reads "yeah", "right" or "no" to it as all clear (app/safety.py `answer_to_confirm`); the smell named again, "it does" or an unsure answer still bring the lines.
- The mock's property manager and co-op board accounts are typed Residential.
- The read-back keeps "your home". docs/brief.md gains "How residential or commercial is read", with the equipment reasoning.
Sign-off feedback (verbatim, Jackson on the safety booking line):
> regarding your question for PR #74, i sign it off, that makes sense.
Changed:
- `safety.booking_note` in tenants/summit-air/agent.yaml is signed off: "The technician can only work on it once the gas company or 911 has shut it off and said it's safe, so please stay outside until then."
Status: open

## call-journeys r5 (amendment after lock: eval round 2)
Feedback (verbatim): the same notes as spec r8 above.
Changed:
- Spine step 4: the lighter confirm question for burning dust and similar smells, once per call; a clear no carries on with no safety lines.
- Journey 3 step 4 and step 8: after the lines the agent recommends staying outside and calling 911 or the gas company, then offers once to book a visit for after the gas or problem is shut off; the caller doesn't have to insist. The follow-up call books a visit only if none was booked on the call. This reverses the round 2 line "the agent doesn't offer one on the safety call".
- Journey 6: a board, landlord or management that has to approve gets a callback offer instead of a booking; a booking the caller asks for gets a call before dispatch.
- Journey 8: where the line falls is now residential-grade or commercial-grade equipment, with an explanation for a caller who isn't sure, and the new decline wording.
Status: open

## users r4 (amendment after lock: eval round 2)
Feedback (verbatim): the same notes as spec r8 above ("commercial is referring to like an office building or something. In multi-tenant apartment building is technically residential").
Changed:
- Section 9, commercial caller: a restaurant, a store, an office; a super or building manager of an apartment building is no longer in this group. "Our building" and "our tenants" alone point to an apartment building. The agent says Summit Air works on homes and apartments, not businesses, and explains when a caller is unsure.

## spec r9 (amendment after lock: eval round 3, wording and safety tone)
Numbered after the two r8 rounds in PRs #74 and #75.
Feedback (verbatim, Jackson's notes on the ten graded calls in evals/rounds/03-judge-labels.md, the parts this round acts on):
> 3. good but express urgency....so like "Agent: I'm sorry about the bill and the mud in your hallway. I'll flag this for a person to call you back. What time works best for a call back today?" becomes "Agent: I'm sorry about the bill and the mud in your hallway. I'll flag this for a person to call you back as soon as they can. What time works best for a call back today?" also if possible, try to add some specificity to "Agent: I can't change the bill or take anything off on this call. I'll get that callback set for after 5 p.m. I've let our team know, and someone will call you after 5 PM. Anything else?" so it would be more like "Agent: I can't change the bill or take anything off on this call. I'll get that callback set for after 5 p.m. I've let our team know, and someone will call you around 5:30pm. Anything else?" and "stay warm at there" is a terrible ending, change it to be generic and kind

> for situation 4: use better pacing and be less demaning. i.e. "Agent: Good, get outside and stay there. Stay on the line if you need to, and call 911 from outside if you haven't already." becomes "Agent: Okay, that's good, now I recommend getting outside and staying there until emergency services can confirm it's not a dangerous situation. Please call 911 from outside if you haven't already." and don't offer to stay on the line because what would the ai agent be able to do? nothing and make the ending thing "Agent: Okay. Please stay outside and call 911. Goodbye. For your safety, I'd recommend hanging up now, getting outside, and calling 911 from there. We'll follow up once you're safe." better and less repetitive. so fail but partial.

> for situation 5: [...] for "Agent: You're all set for today, with the technician arriving between 12 and 4. They'll call or knock since the buzzer's broken. Anything else?", make sure the agent restates the day/date with the time

> for situation 6: fail in a few ways. [...] "Agent: Please get everyone out of the house now. Don't flip any switches or use the stove, and call 911 or the gas company once you're outside." shouldn't be said because the caller didn't say gas was going off, they were just saying they're worried about it theoretically. so make it less senstiive. this is a complete over sensitivty and thus a failure. and even when the caller clarifies they didn't smell gas, the agent continues to fail and over emphasize safety concerns. also im confused why [...] the agent says it can't schedule a callback....why not?

> and in situation 8, the safety is appreciated but is overdone. the caller should always be in control. if they say it smells like gas but it's fine, elevate your concern but then if they reject your advice, just let it be and maybe offer one more time. but don't keep demanding to get everyone outside. [...] and "Agent: Stay safe, Gil. Thanks for calling Summit Air. Stay warm out there. Please hang up now, get everyone outside, and call 911 from there. We'll follow up once you're safe." is terrible. so i'd say failure in. many ways on this one

> in situation 9, [...] "I've marked this as one of our most urgent calls" should be "I've marked this call top priority"...
Changed:
- S-17: the shorter safety line is said once per call (`safety.repeat_limit`), then the caller stays in control. The signed-off wording is unchanged.
- S-19: the sign-off is the call's one goodbye, said once.
- S-20: a worry with nothing described gets the check question at most, never the lines; a caller who says after the lines that there was never a smell stands the call down.
- S-21a: the urgent line is now "I've marked this call top priority".
- S-30: the confirmation names the date as well as the day.
- S-32: "after 5" is promised as "around 5:30", worked out by the server; a callback can be set on a safety call when the caller asks.
Commit: 83e06e1
Status: open

## call-journeys r6 (amendment after lock: eval round 2, second half)
Feedback (verbatim, the parts this round acts on, from evals/rounds/02-seeds-and-holdout.md):
> For situation five, yeah, somebody should be able to have an open job on their house and then call on behalf of somebody else at a different address. And as long as that address is within the service zone, the agent should be able to book them on that. But should also somehow ask if they're, like, the person calling will be the point of contact going forward, or if they want that other, like, house or unit to be assigned to a different account, which is what should happen ideally. But think through that.

> Yeah, for situation six, the agent reminding the caller that they remember the earlier call. That works. Just make sure that's stage gated by the number being the same and the time in between calls being within that same day. Then also make sure the agent just confirms that it's still speaking to the same person. Be like, this is Jackson, correct? Or something like that. But yeah, very important to not book twice across the two calls.

> For example number 7, make sure there's proper understanding built into the model that if somebody says 8 to 2, they're referring to 8 a.m. to 2 p.m. and not 8 a.m. or 8 p.m. to 2 a.m. You get what I'm saying?

> Throw in the legal question 1 too. That's a good one to have. Just make sure that the agent doesn't give unsolicited advice or untrue material, and just always tries to defer to asking the super or somebody with more knowledge on that. And then for the second opinion replacement quote, yeah, add that one in too. It shouldn't state a price or criticize them, and it should offer to have somebody come out to quote them if that's something we're doing. If that's not something we're doing, then figure it out or something.

> Then also add in an eval set for a street name that speech to text mangles, but make it for Houston Street. That's the only one. And then ensure if the agent cannot confirm how to spell or like understand the street, that it can listen to the user read out the letters. That should be a capability.
Changed:
- Spine 5.2: a street the agent can't confirm is spelled by the caller, and the server joins the letters into the name for the read-back.
- Spine 8: times mean what the caller means ("8 to 2" is 8 a.m. to 2 p.m.) for arrival hours and callback windows; an exact arrival inside a window is never promised.
- Journey 6 step 6 (new): a legal question gets "I can't advise on that" and a pointer to the super or 311, no unasked advice.
- Journey 7 step 5: linked by same number, within a few minutes or later the same day after a drop; the greeting confirms the person by name; never two bookings across the calls.
- Journey 7 step 6 (new): another address can be booked; the agent asks whose account it goes on, its own account with the caller as contact being the usual choice. Steps after it renumbered.
- Journey 9 step 3: a second opinion never judges the other quote and offers a comfort advisor to come out and quote, since the knowledge folder says replacements are quoted by a comfort advisor and never on the phone.
Commit: a8108c7 (saved as a WIP commit when the app quit; this round's text is in it)
Status: open

## spec r9 (amendment after lock: eval round 2, second half)
Feedback (verbatim): the same notes as call-journeys r6 above.
Changed:
- S-15b (new): spelled street names, joined by the server.
- S-29: hours the caller is home read as daytime; no exact arrival promised inside a window.
- S-31a (new): booking another address, with the account question asked by the server.
- S-32: callback windows can be tomorrow, bare hours are daytime, the window is kept for the person calling.
- S-38: same number and same day are the gate, the greeting checks the name, never two bookings across the calls.
- S-41a (new): no legal advice, and another company's quote is never judged.
Commit: a8108c7 (saved as a WIP commit when the app quit; this round's text is in it)
Status: open

## call-journeys r7 (amendment after lock: cold handoff after the live calls)
Feedback (verbatim, Jackson):
> Let's just make sure that there's proper handoff, essentially like a cold handoff. Like if they like are angry and demand a person, just say transfer your person now and immediately transfer them, if that's possible. Like, yeah. Properly implement that.
Changed:
- Traits: "Wants a person" is split. Asked once and calmly, it stays a flag and the warm transfer in office hours. Demanded (insistently, again, or angrily), the agent says only "Transferring you to a person now." and the call goes straight to the office, or the on-call technician after hours, with no summary; the server decides from the caller's words. A passing mention or "are you a real person?" isn't a request. The stale "no live transfer in V1" is gone.
- Traits: "Upset or rude" points at the demand line.
- Journey 3, after step 6: a demand is put through only after the safety lines, to on-call after hours. This is a change to decision 051's "never on a safety call", proposed in the PR, because the lines have already told the caller to get outside.
- Journey 13 step 3: a complaint caller who demands a manager is put through at once.
Commit: ff34e7f (amended to add this line)
Status: open

## spec r10 (amendment after lock: cold handoff after the live calls)
Feedback (verbatim): the same note as call-journeys r7 above.
Changed:
- S-38a (new): the cold handoff, who it goes to, when it doesn't happen (a mention, a commercial call, before the safety lines), once per call, and the stored callback line when nothing goes through.
Commit: ff34e7f (amended to add this line)
Status: open

## spec r11 (amendment after lock: address lookup and preferred names)
Feedback (verbatim, Jackson, relayed by the coordinating session):
> ensure no ZIP guessing....but maybe give the system an address lookup tool or at least a ZIP finder tool...of course confirmation is great but if it could just determine the address from a short hand like '248 duffield st'....there's only one of those so you could find the zip from that. also how are we handling the rare error of someone telling us their address but not remembering their zip code....that feature would help with this. also what if the address and zip don't match up....think through that.

> if someone asks to be called by a certain name - as long as it's appropriate - do it....like 'Dr.'
Changed:
- S-13a (new): the ZIP comes from the caller, their record or a lookup of the street they said; one match fills it for the read-back, several ask which, none asks to spell, an outage asks for the ZIP, and a ZIP that disagrees with the lookup is asked about and never booked until the caller settles it.
- S-53 (new): a form of address the caller asks for is used for the call, a linked call and later calls, and a mocking or abusive one is never used or kept.
Commit: 0df6132 (amended to add this line)
Status: open

## spec r12 (amendment after lock: caller changes, issue #128)
Feedback (verbatim, Jackson, relayed by the coordinating session):
> if somebody's calling to say they moved to a different address and to update that accordingly, like that flow should encompass a few things, like the voice agent acknowledging the request, you know, getting the new address, verifying the zip code is still in the service region, and then if it is, properly updating in the CRM and checking that there's no potential resend, like dispatch, to the stale old address.

> make the agent be polite about it, but make it just be like, and just to confirm that, you know, you were the person for the visit. Could you just confirm your name and address?

> Call my daughter at this number instead

On the heads-up to the number on file after a change from another number (relayed by the coordinating session):
> Yes. As far as you can go on that one, that'd be a very good idea.

Changed:
- S-31b (new): a caller who moved; new location, old one inactive, open visits moved, or cancelled out of the area; nothing sent to the old address (DECISIONS.md 075).
- S-31c (new): the name and address check before a change from a number not on the account, in the server's polite words; a mismatch changes nothing and offers a call to the number on file; a change that passes sends a heads-up call to the number on file after the call (DECISIONS.md 076).
- S-13b (new): another person's number is the visit's contact, never the customer's own (DECISIONS.md 077).
- S-31d (new): a number on more than one account asks whose name the visit goes under (#127 item 3, DECISIONS.md 077).
Commit: (this PR)
Status: open

## safety r1 (issue #130: electrical route, card and billing lines, dead air)
Feedback (verbatim, Jackson, relayed by the coordinating session):
> it should check in every 2 minutes. And then if it's been 8 minutes, it should say, I'm gonna hang up, but you know, once you're ready, feel free to call this number back. So we can pick up from where we left off. And then let it hang up. That way it doesn't just burn a ton of minutes. And then make sure the decision for that is like properly logged so that if the person calls back, the agent can be like, hey, we disconnected earlier because you stepped out, but we can pick up where we left off.

> if AC water is coming through the ceiling, that should get urgent priority. A breaker that keeps tripping isn't inherently urgent priority, but it's still an issue that should get some priority. Sparks should... be acknowledged, but it's not like a get everybody out of the house thing. It's just like a you know, step away, try to turn off the machine. If you can turn off the breaker, that would be a good idea. So it should kind of get its own safety route because it doesn't need to be taken as seriously as a gas leak or carbon monoxide leak thing.
Changed (all new lines in tenants/summit-air/agent.yaml, DRAFTED for Jackson's sign-off; the gas and carbon monoxide lines are unchanged):
- `safety.electrical.lines`, with `signed_off: false`: "Please step away from the unit and turn it off, and if you can safely reach the breaker, switch that off too. Once it's off, I can get a technician out to look at it."
- `voice.still_here`: "Take your time, I'm still here."
- `voice.stepped_away`: "It sounds like you've stepped away, so I'm going to hang up for now. Once you're ready, feel free to call this number back, and we can pick up where we left off."
- `voice.greeting_linked_stepped_away` (and `_unknown`): "Hi, this is Summit Air's virtual assistant again. We got disconnected earlier when you stepped away; let's pick up where we left off. Is this {first_name}?"
- `lines.card_stop`: "For your security, please don't read a card number over the phone. Our office will send you a secure payment link, or call you to take the payment."
- `lines.billing_callback`: "I'm sorry about that. Billing goes through our office, so I've asked them to look into the charge, and someone from the office will call you {when}."
Decisions: DECISIONS.md 080 to 082.

Sign-off (verbatim, Jackson, relayed by the coordinating session):
> for the Spark safety line, that's good, but don't say once it's off, I can get a technician out to look at it. Just say, I can get a technician out to look at it. Then turning off the breaker isn't a requirement to dispatch a technician. But the other stuff looks good. That all makes sense. So I approve of those 6 lines. Just make sure to implement my feedback on the Spark safety line.
Changed at sign-off:
- `safety.electrical.lines` now ends "I can get a technician out to look at it." instead of "Once it's off, I can get a technician out to look at it.", and `signed_off` is true.
- The other five lines are signed off as drafted.
- The server adds an `electrical_said` note after the line so the agent offers to book at once; a test pins that a sparks caller books with nothing said about the unit or the breaker being off.
Commit: PR #132
Status: closed

## rules page r1 (issue #140: one simple Rules page on the bench)
Feedback (verbatim, Jackson, relayed by the coordinating session):
> I just thought that the owner guidance and like customization tab was just overcomplicated and kind of makes the system look like a hodgepodge of different rules and stuff. So ideate a way to just fully simplify the owner guidance so that way they can still see what would trigger a fixed response and still be able to add in their own rules, but only keep it in the debug bench, not on the owner view, and simplify it so it's just less all at once. I don't think most of those fields need to be included. And just make sure it doesn't look like a hodgepodge of random rules that are disparately connected. Also make that page show the assembled system prompt sectioned by each sub prompt that's assembled in the order that it's assembled on that page somewhere. ensure proper design, system implementation, and do another check after your first pass to ensure a simplified user interface and user experience.
Changed: three directions mocked in docs/explorations/018-rules-page.md, with every field on the two old pages marked kept, merged or dropped.

Pick (verbatim, Jackson, relayed by the coordinating session):
> let's go with your recommendation of one topic at a time.
Changed at the pick: option A built at `/bench/rules` (DECISIONS.md 091); `/owner/rules` redirects there.
Commit: (this PR)
Status: open

## live call 1 (issue #149: wait time, voice, holding line, callback day, schedule, ending)
Feedback (verbatim, Jackson, relayed by the coordinating session, after call `call-_+16784774024_qSVS59jLcAPP`):
> the wait time was way too long, like very long. And I didn't like the voice as well. So if we can just change the voice from that like weird like girl voice to something else... Just choose any other one... It said got it, but wasn't telling me that it would need a second to do a task. Like it should just be like, okay, I'm gonna do that. Just give me 1 second. You know, something very human, with like the proper swing of the voice... I asked for an appointment Friday. It said, I'll book you tomorrow, anywhere from whatever it said, like 3 to 5 p.m.... Tomorrow would be Thursday. So that's just completely wrong. And that's not what I asked for... there should be no active appointments right now... Actually, no, that's not true, because the eval set needs to test for active appointments. So keep whatever active appointments would be in on the eval set. And then a few extra ones in case I want to do like my own run of the eval set. But most times should be available... Saturday and Sunday should also be available... It also just abruptly ended the call. There should always be a 1 second wait before ending the call to at least ensure in case the person said anything else.
Changed:
- The turn detector's longest wait after the caller stops: left to the issue #150 lane, whose audio rig can put pauses inside a caller's line.
- A reply that promised work but called no tool, or that the caller heard none of, is asked again at once (the 19 s of silence after "Got it.").
- A holding line before a look-up: the model's own in the same reply, or the tenant's ("Just give me one second.") added to it when the caller would hear only "Got it." or nothing. DECISIONS.md 096.
- The voice: Cartesia "Max" instead of Katie, and the clips re-recorded in it. DECISIONS.md 094.
- Callbacks: a weekday the caller names is the day booked ("Friday around 2 PM"), and the agent never explains the calendar.
- The live schedule: weekends open 8 AM to 8 PM, and a script cancels most of the live world's filler visits, keeping the named customers' visits and one filler in six. DECISIONS.md 095.
- Ending: no "That means the callback is off", and a one-second wait after the goodbye that keeps the call open if the caller speaks.
- Cancel and the name check: the reason is optional and asked once at most; one wrong name gets one polite refusal and the callback offer. DECISIONS.md 097.
Commit: (this PR)
Status: open

## live call 2 (issue #149: half replies, refused move, name echo, ZIP, callbacks, scheduling)
Feedback (verbatim, Jackson, relayed by the coordinating session, after call `call-_+16784774024_MXJ5xsvpZQ5H`):
> didnt like how it said my name right back....and just say the zip code is, not just zip 11201....then when i asked it something, it just said sorry about that and stopped. terrible... too fast on the readback of address and phone. then a weird repeat on the call the right number thing. and then it repeat it trying to call again. also i say 2pm, and it says 1-3pm. just let it say it can do it exactly... it just like pauses...like when i asked for a second task....and then asked it if it was going to ask me a question. and then it says 'yes' and then pauses....and doesn't say anything... and why is it saying it doesn't have openings for a tune-up wtf. why didn't it check friday first?... and too many double booking questions....like it asking me two sets of multi-options for the booking stuff.
Changed:
- "Sorry about that.", "Sure, I can set up an AC tune-up." and "Yes." alone are asked again at once (DECISIONS.md 096).
- The move was refused because call 1's failed name check carried over to call 2; a check now stays on its call, and the repeated refusal isn't said twice (DECISIONS.md 098).
- A name the caller just gave isn't said back; "ZIP 11201" is said as "the ZIP code is 1 1 2 0 1" (DECISIONS.md 098).
- The read-back pace is the latency lane's (issue #150).
Commit: (this PR)
Status: open

## schedule edits r1 (issue #152: edit the schedule and customers, live into the agent)
Ask (verbatim, Jackson, relayed by the coordinating session):
> add to both the owner's view and the debug bench is the ability to directly edit the calendar with custom events that could take time slots. The owner's view should also have a way to do things like, adding a job, editing details, and stuff like that. But it should also, along with the debug bench, have a way to just block calendar availability and change the assigned phone numbers of different clients. Because in the demo, I suspect that he might be like, well, what if that time slot was booked? And then I can just book it and give him like the live test that changing the booking on the debug bench or the owner's view creates a live update in the mock Service Titan CRM, which in real time or near real time with only like a small delay can create a change in how the voice agent works because it's calling the database and other tools.
Changed: a Schedule tab on the owner view and the bench (block a slot or a window, add an event or a job), a job page (window, type, notes, address; customer name and phone for admins), and on the bench what the agent would offer per window and a live list of changes in ServiceTitan. Every edit goes through the adapter and clears the slot cache (DECISIONS.md 093).
Commit: (this PR)
Status: open, look to be reviewed from the screenshots in the PR

## service area r1 (issue #149: Jackson's call on the Retell number, call_ca9bfcec0b968db42ac2f428bea)
Feedback (verbatim, Jackson, relayed by the coordinating session):
> you should make this agent capable of any nyc, any of 5 boroughs zip code should be in zone
> "one. nine. nine." should be same as "one hundred ninety nine"
Changed:
- docs/spec.md S-28: the service area is all five boroughs, every residential ZIP; the call never ends before the caller answers an out-of-area line.
- docs/users.md: the service area named as all five boroughs.
- The address check takes "one nine nine", "one ninety nine", "a hundred ninety nine" and "one hundred and ninety nine" alike, with "East" between the number and the street and "fourth", "4th" or "4" for the street (DECISIONS.md 105).
Commit: (PR for joco/call-3-fixes)
Status: open


## safety r2 (issue #149: Jackson's gas test call on the Retell number, call_0246b91f77e01a4b18583e6ca8b)
Feedback (verbatim, Jackson, relayed by the coordinating session):
> it shouldn't ask 'are you outside the house now', that's annoying. same with 'PLEASE get out side now'. like it shouldn't be demanding me to do anything. and it keeps repeating the same statement
> the caller should be able to be like 'wait nvm, im tripping'... it shouldn't be so sensitive to that stuff. and it should be able to get out of its own safety loop.
> you need to overhaul this safety mechanism, it's truly terrible.
> by default, it should give a 30 minute way, not a 2 hour window.
> make sure the safety thing is just also in general less repetitive
> It should do like one full readback, but then if there's ever other readbacks necessary, it just says and the phone number ending in 4024... that way it just doesn't bore the caller with so much text.
> i want to get rid of the cut-off thing because likely in the demo he'll call the same number a few times
Also asked: the assistant is named Brooke, with a warmer greeting that doesn't end a sentence on "Summit Air" (the voice lifts there); "911" written "9-1-1" so the voice doesn't read it as nine hundred and eleven.
Changed:
- Gas and carbon monoxide lines rewritten as advice ("If you're smelling gas, the safest thing is to get everyone outside and call 9-1-1 or the gas company from there."), with no question. Said once per hazard: repeat_limit 0 and no shorter variants; a new hazard still gets its own lines.
- The lines count as said once the agent has spoken after they were handed out, so a line that reaches the server in pieces no longer brings them back on the caller's next line (the call heard them twice).
- A caller who takes it back ("never mind", "I'm tripping", "false alarm", "it's not gas", "I don't think it's leaking", "the gas company said it's fine", "it's been checked") clears the safety state at any point, unless the same line still reports a smell or only says they've gone outside. The job gets a note of what was said, and the call books as usual.
- The sign-off is "Thanks for calling, and please stay safe. We'll follow up with you.", said once, then the call hangs up.
- The booking note advises rather than demands ("Just so you know, the technician can only work on it once the gas company or 9-1-1 has checked it and said it's safe."); booking after the lines still needs a person flagged.
- Greetings name Brooke ("Hi there, thanks so much for calling Summit Air today! I'm Brooke, the virtual assistant. How can I help?", and "Hi Jackson, ..." for a known caller); no "we got cut off" greeting; every tenant line writes 9-1-1.
- Less repetitive: a booking or end_call refused before the lines or the check question are answered says nothing (the caller's line already handed them out); the booking note is "Just so you know, the technician can only work on it once it's been checked and found safe."; safety is per call (DECISIONS.md 115).
- The read-back says "the number you're calling from, ending in four zero two four"; a number read in full once is "the number ending in ..." on any later read-back with changed details.
- Arrival windows are 30 minutes by default (DECISIONS.md 114).
- A booking refused for want of a name asks with the booking_name line.
- docs/spec.md S-17, S-18, S-19 and docs/call-journeys.md journeys 2 and 3 updated to match.
Commit: (joco/stripped)
Status: open
