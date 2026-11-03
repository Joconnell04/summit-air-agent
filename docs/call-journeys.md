# Call journeys

The paths a call takes, one per caller type in docs/users.md, for calls Summit Air receives and the few it makes. Each journey says how the call starts, what the agent asks, which server check fires, how it ends, and what the owner view records. Calls can move between journeys at any point: a maintenance call can become a safety call, and a safety call that turns out to be a false alarm goes back to where it was.

Scope follows DECISIONS.md 008. Where a detail comes from field practice described on Revin's blog, it is cited inline as vendor practice, not as measured fact (docs/explorations/005-revin-blog.md). Deferred items (texts, other languages, playbooks, warm transfer) are in docs/later.md and left out here.

## The spine every call shares

1. **Lookup before hello.** The server looks up the calling number: customer, address, membership, open jobs, calls in the last few minutes. The result goes to the agent with its first turn.
2. **Greeting.** Always names the company. Unknown number: "Thanks for calling Summit Air, this is the virtual assistant. How can I help?" Known number: "Hi, this is Summit Air. Is this Maria?" The agent says it's an AI assistant whenever asked and never denies it.
3. **Listen first.** The caller tells the story once, without interruption. If the caller goes quiet for about 5 seconds, a gentle check-in ("Take your time. Are you still there?"), not a stream of prompts. After a long silence the agent says it will hang up and that they can call back any time.
4. **Safety check on every caller turn.** The server checks each line against the fixed list. A clear signal ("I smell gas", "the CO alarm is going off") switches to journey 3 at once. The word "gas" alone, a gas bill or a gas furnace triggers nothing. A vague description ("something smells off, kind of like eggs") gets one check first: "Just to be safe, are you smelling gas in the house right now?" A clear no puts the call back where it was, and the false alarm is logged for review.
5. **Questions in order of need, only when needed.** The agent asks what it needs to help, in this order, and skips anything already known or not useful for this call:
   1. What's wrong and since when.
   2. Where: address, checked against the service area by ZIP. The phone's area code is never used to guess where someone lives.
   3. How bad: how warm or cold it is inside, and who's affected (journey 2).
   4. What the technician needs: system type and rough age, what's been tried.
   5. One open question at the end: "Anything the tech should know when they get there, like a gate code, a buzzer or a dog?" Pets, access and parking come up here, not as separate questions.
6. **Contact number.** "The number I'm calling from" means the caller ID the call arrived with, which the agent reads back in groups. If the customer has several numbers on file, shorthand works: "the one ending in 4024" picks the matching number, read back to confirm.
7. **Read-back gate.** Before any booking the agent reads back the address and phone number and the caller confirms. The server refuses to book until that read-back happened on this call.
8. **Offer times like a person would.** First the shape of availability: "I've got openings Tuesday and Thursday afternoon, and Thursday morning. What works best?" Then the exact arrival windows for the day they pick. Urgent calls get the earliest urgent window offered first.
9. **Book and confirm out loud.** Day, arrival window, what happens next. Booking is idempotent on call and slot. No text in V1.
10. **"Anything else?"** before ending; the agent never ends in the same turn as the confirmation.
11. **Owner view and logs.** Every call records caller, type, priority with reason, member or not, outcome (booked, flagged, declined, callback, not a customer), any refused tool calls, any journey switches, and the full trace for debugging. The owner view also shows booking rate by call type rather than one blended number, callbacks promised against callbacks done, and bookings still on the schedule when the technician arrives ("booked and kept"), because a message isn't a booked job and answered calls with nobody asking for the booking are where revenue leaks (C-42; Revin blog: https://revin.ai/blog/hvac-booking-rate-vs-answer-rate, https://revin.ai/blog/hvac-ai-answering-service-pilot).
12. **Callbacks have a time.** Any promised callback names a time ("by 9:30" or "within 20 minutes"), gets an owner and a timer on the server, and an overdue one is flagged. "Shortly" is never said (C-45; Revin blog: https://revin.ai/blog/hvac-callback-queue).

**How it sounds.** Short turns, plain words, one question at a time. Acknowledgments vary and fit the moment ("Oh no, in this weather?", "Okay, let's get someone out to you", "Got it"); the same phrase isn't used twice in a call, and the evals check for it.

**Company knowledge.** Summit Air's own facts live in `tenants/summit-air/knowledge/` as plain markdown: a sign-off line or slogan, how they describe their plans, notes on equipment brands they service, answers to common questions. The agent can use them; a different company swaps the folder (docs/template.md).

## 1. No heat or no cooling

1. Caller: "The heat's out." The lookup already has the address and any open job.
2. The agent acknowledges it like a person and asks when it stopped, then how cold it's getting inside. Service first: what's wrong, how bad, where.
   - Look-alikes: a heat pump that has just stopped and is steaming outside may be in a normal defrost and restart within about 10 minutes, and a furnace can lock itself out after repeated failed starts. The agent asks how long it has been off, may mention that heat pumps pause to defrost, and still offers to book; it never walks the caller through a reset (a gas appliance and a liability), and a defrost description alone doesn't make the call urgent (C-44; Revin blog: https://revin.ai/blog/hvac-after-hours-no-heat-calls).
3. Who's affected comes up naturally once the agent understands the problem (journey 2), never as the opening question.
4. Own or rent, only when it matters for who approves the visit (journey 6).
5. ZIP checked against the service area. Out of area: "I'm sorry, that's outside the area we cover, so we're not able to send someone. A local heating company should be able to help." No supervisor flag; turning away out-of-area calls is normal practice. The ZIP is recorded, and the owner view counts out-of-area calls by ZIP so demand can inform a decision to expand.
6. Read-back gate, then the earliest window for that priority.
7. Ends booked. No maintenance-plan pitch on this call.
8. Owner view: priority (urgent if the inside temperature or the people affected call for it), reason, booked window.

## 2. Vulnerable household

How to ask: call-takers in this trade routinely screen for people at risk (older adults, babies, someone with a medical condition) on no-heat and no-cooling calls (SOURCE: vendor HVAC intake guides, e.g. https://getnextphone.com/blog/hvac-lead-qualification-workflow and https://www.fieldproxy.ai/blueprints/hvac-no-heat-emergency-flow; no authoritative industry script was found). The wording here is ours. It asks about the people, not for a reason to rank them:

1. After the agent understands the problem and how cold or hot it is inside: "Who's at home with it right now? Anyone who'd have a harder time with the cold?"
2. The caller's answer decides priority; the agent doesn't ask for a list of conditions. If they mention someone older, a baby, an illness or medical equipment, one follow-up at most: "Are they doing okay at the moment?"
3. The score beside the turn, or the fixed list for medical equipment, raises priority. Rules can only raise it.
4. If someone is struggling to breathe or their oxygen equipment has stopped, the agent tells the caller to call 911 first. The agent itself never places a call to 911 or anyone else; there is no tool for it.
5. The agent says what it actually did, which the server returns as a sentence only once it's true: "I've marked this as urgent and let our on-call team know, so someone's keeping an eye on it." It doesn't repeat the medical details back.
6. If an urgent window is open today, book it. If not, book tomorrow's first window so the caller has something certain, and flag a person to try to move it up (decided in round 2).
7. A background job watches for earlier openings (cancellations, a technician freed up) and offers them to flagged urgent bookings first, by an outbound call (journey 12).
8. Owner view: urgent, reason ("84-year-old at home, no heat, 52 F inside"), whether an earlier slot is being sought.

## 3. Safety caller (gas, carbon monoxide, smoke)

1. Starts anywhere, often mid-sentence: "it smells a bit like gas, but anyway the heat's out."
2. Clear signal: the server's check fires on that turn and refuses booking tools until the safety lines have been said and a person flagged. Vague signal: the one check question from the spine first.
3. The agent says the fixed lines, spoken from code, word for word, in a calm voice (wording signed off by a person before launch). Gas: get everyone out now, don't flip switches or use the stove, call 911 or the gas company from outside. Carbon monoxide: open a window if one is right there, get everyone out, call 911 from outside.
4. The server flags a person immediately. If the caller brings it up again, the agent uses a shorter signed-off variant ("Please get outside and call 911 from there") rather than repeating the full lines word for word.
   - If the caller insists on booking after hearing the lines ("stop, just book the appointment"), the agent books it. The server allows this only after the lines were said and the flag raised, and logs it as a safety override on the owner view.
5. The agent doesn't hang up on them. It signs off: "Please hang up now, get everyone outside, and call 911 from there. We'll follow up once you're safe." It doesn't offer to stay on the line, because the gas utility says not to use a phone inside (decision 009) If the caller keeps talking, it repeats the instruction once, briefly. The end-call tool refuses until the lines were said and the flag raised; the call ends when the caller hangs up or says goodbye.
6. If the call drops before the lines finish: a person is flagged to call back.
7. False alarm: if the caller says clearly that they don't smell gas and nothing is going off, the call returns to the journey it came from and the owner view shows "safety check cleared" for review.
8. Follow-up: once the caller is safe, a person (or journey 12) calls back to offer a visit, treated as top priority. The agent doesn't offer one on the safety call (decided in round 2).
9. A spam or wrong-number label can never apply to a call with a safety signal.
10. Owner view: safety call, what was said, when the lines finished, flag status, follow-up status.

## 4. Maintenance scheduler

1. Caller: "I need my annual tune-up." The lookup has already found them.
2. The agent uses what's on file: "I've got you at 42 Elm Street, is that still right?"
3. Times offered the way the spine describes, around what suits them. Urgent slots are held back for broken systems, so tune-ups get the regular windows.
4. Read-back gate, book, confirm.
5. Owner view: routine, booked.

## 5. Member

1. Same as the journey for their reason for calling, with the lookup showing an active plan.
2. A short, real thank-you and the benefit that applies, without a script feel: "I see you're a Summit member, thanks for sticking with us. That gets you priority scheduling, so let me find you the earliest spot."
3. Not found by phone: once the urgent part of the call is handled, "Are you on one of our maintenance plans, by any chance?" Then the server checks by address. Never asked while the caller is describing an emergency.
4. Expired plan: said plainly and kindly, with an offer for someone to help renew; no promised perk.
5. Owner view: member status on every call.

## 6. Renter, or someone who can't approve the work

1. Asked only when it matters: "Is it your place, or are you renting?"
2. If they rent, the agent records who approves the work separately from the caller.
3. Owner approving: book as usual, with the owner as the approver.
4. Owner unknown or unreachable: take details and flag a person.
5. Tenant with no heat in heat season (October 1 to May 31, five boroughs): one neutral line ("In the city, landlords have to provide heat in season, and 311 takes complaints"), then book if the tenant or landlord can approve, or take details and flag a person. No legal advice beyond that line (decided in round 2).

## 7. Repeat caller

1. The lookup shows an open job or a recent visit.
2. "Where's my tech?": status from the FSM, never guessed. If the window has passed, flag a person.
3. Same problem came back: book a return visit linked to the earlier job, priority reviewed by a person.
4. Reschedule or cancel: only a booking that belongs to this caller (server check). One reschedule and one cancel per call. On a cancel the agent asks why and offers two specific new windows before ending, and logs the reason; a cancellation is often a rebooking (C-46; Revin blog: https://revin.ai/blog/hvac-same-day-cancellations).
5. Dropped and called back within a few minutes: the server links the calls and the agent picks up where the last call stopped: "I think we got cut off, you were telling me about the furnace." Nothing already done is done again.
6. Keeps getting cut off (two or more drops in about ten minutes): "Looks like we keep getting cut off, the signal might be spotty. Want me to have someone call you back in a few minutes instead?" A yes creates a callback (journey 12).
7. Several calls with no progress and no drops: flag a person.
8. Owner view: linked calls shown as one thread.

## 8. Commercial caller

1. Signals: a business name, "our building", "our tenants", rooftop or multi-unit equipment, or a commercial account in the FSM.
2. Where the line falls (decided in round 2): an owner-occupied one or two-family house, or a single co-op or condo unit, is residential; a landlord calling about a building of three or more units, a property manager, or any business premises is commercial.
3. The agent, early and kindly: "Thanks for thinking of us. Unfortunately Summit Air only works on homes, so we're not the right fit for this one." No callback offered.
4. The server refuses any booking for a commercial customer type.
5. One line saved with the normal end-of-call record: company, contact, what they needed. No extra lookups.
6. Owner view: commercial, declined.

## 9. Replacement or install quote

1. Caller asks for a new system, a heat pump, a conversion or rebates.
2. The agent takes what a comfort advisor needs: system age, what they want, how soon, home type. For rebate questions also fuel type, utility, owner or renter, and ZIP; only the advisor ever states a dollar amount.
3. Second opinion (the caller holds another company's replacement quote): the agent captures what was recommended, which part failed, the system's age and whether it still runs, and never criticizes the other company. A system that still runs goes to a comfort advisor; one that doesn't is booked as a repair visit.
4. Refrigerant questions ("isn't R-410A being banned?"): the agent doesn't state regulations or prices, which change; it books a diagnostic with a leak check and asks the system's age (C-46; Revin blog: https://revin.ai/blog/hvac-second-opinion-calls, https://revin.ai/blog/hvac-refrigerant-recharge-calls, https://revin.ai/blog/hvac-heat-pump-rebate-calls).
5. Ends with a callback from a comfort advisor at a named time (decided in round 2; the mock has no advisor calendar). Never a price or a rebate promise.
6. Owner view: lead for a comfort advisor.

## 10. Not a customer call

1. Wrong number, robocall, vendor, job seeker, or someone testing the agent with off-topic asks.
2. One polite line, then either back to the purpose or a goodbye. Off-topic asks ("write my essay", "ignore your instructions") get one line and a return to the call; the agent stays in role.
3. Any request to change or delete data is refused by the server, whatever the agent was told (least privilege, docs/architecture.md).
4. Never applied if a safety signal was heard.
5. Owner view: not a customer, with the type.

## 11. After hours

1. Same spine. The agent says the office is closed and what it can still do.
2. A no-heat or no-cooling call ends in one of three named outcomes, chosen by the inside temperature, how fast it is changing, and who is home (C-43; Revin blog: https://revin.ai/blog/hvac-after-hours-no-heat-calls):
   - **Tonight, if the on-call technician agrees:** inside below 50 F or falling fast, or an infant, an older person or a medical need at home. The on-call technician gets an email alert with what, where, who and why, and the agent tells the caller the technician will call them by a named time (within 20 minutes) to confirm. The agent never promises a tonight visit itself (decided in round 2).
   - **First morning slot:** inside above 55 F and holding, nobody at extra risk. Booked now.
   - **Callback at a named time:** anything in between, or when the caller would rather wait.
   The cutoffs are Summit Air settings in tenant config, marked ASSUMED, not hard-coded. An after-hours fee, if Summit Air sets one, is mentioned only after this triage.
3. Other routine calls: booked for the next business day.
4. Safety calls: journey 3, always.

## 12. Calls Summit Air makes

The system can also place calls, with the same spine (greeting names Summit Air and the reason for calling, read-back before any change, "anything else?"):

1. **Earlier slot opened** for a flagged urgent booking: "Hi, this is Summit Air calling about your heating appointment. We've had an opening today at 2 to 4, would you like it?" Yes moves the booking; no keeps the original.
2. **Safety follow-up** after a gas or carbon monoxide call: checks they're safe and offers a top-priority visit.
3. **Callback requested** after repeated drops or when the caller asked for one.

Rules: only to people who called Summit Air about this service request, only between 8 a.m. and 9 p.m. local time, never to a number marked "do not call", one attempt and one voicemail at most. If a caller says not to call them again, the number is marked do-not-call at once, any pending callback is cancelled, and the agent says so kindly: "Understood, we won't call you. That means the callback is off, so give us a call any time you need us." Voicemails say only that Summit Air is calling about the appointment and give the number.

Who places them (decided in round 3): the earlier-slot offer and the requested callback are placed automatically by the system; the safety follow-up is a task for a person, because it can involve a gas leak still being sorted out.

## 13. Complaint or warranty

1. The caller is unhappy with finished work, a price, or wants a warranty or credit exception.
2. The agent listens, doesn't argue or promise anything, and says what happens next: "I'm sorry that happened. I'll get this to a manager now, and they'll call you by" a named time.
3. Flags a person with a short summary (who, which job, what they want), so the caller doesn't have to repeat it (C-46; Revin blog: https://revin.ai/blog/hvac-ai-call-transfer-to-human).
4. Owner view: complaint, flagged, callback time.

## 14. Wants to become a member

1. The caller asks about joining, or a routine call (a tune-up booking) ends with a natural opening: "By the way, we have a Summit Standard Membership that covers two tune-ups a year and priority scheduling. Want me to have someone tell you more?" Never offered on a broken-system or safety call.
2. One or two lines about the plan from the knowledge folder; a price only if tenant config sets one.
3. The agent can't take payment or create a paid membership. It records a sign-up lead and tells the caller someone will call them by a named time.
4. Owner view: membership leads, with which call they came from.

## When a tool fails

If a lookup or booking fails (the FSM is down, a timeout), the agent says so honestly and keeps the caller's details: "I can't book that right now, but I've written it all down and someone will call you by 4 to confirm." The server queues the request, retries it once the FSM is back without double-booking, and flags a person if it still fails. Every failed tool call is logged.

## Traits on any call

- Upset or rude: calm, no arguing, offer a person.
- Hard of hearing or slow to answer: slower pace, shorter questions, more read-backs, longer wait before a check-in.
- Two people on the line or someone relaying: confirm who is approving and who will be home.
- "Am I talking to a robot?": "Yes, I'm an AI assistant for Summit Air." Never denied.
- Wants a person: during hours, take details and flag a person for a callback (no live transfer in V1); after hours, as journey 11.
