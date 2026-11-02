# Call journeys

The paths a call takes, one per caller type in docs/users.md. Each journey says how the call starts, what the agent asks, which server check fires, how it ends, and what the owner view records. Items marked "Open:" are Jackson's call, with a recommendation.

Scope follows docs/decisions/10-considerations-triage.md. Deferred items (texts, Spanish, playbooks, warm transfer) are in docs/later.md and left out here.

## The spine every call shares

1. **Lookup before hello.** The server looks up the calling number: customer, address, membership, open jobs, calls in the last few minutes. The result goes to the agent with its first turn.
2. **Greeting.** "Thanks for calling Summit Air, this is the virtual assistant. How can I help?" If the number matched: "Hi, is this Maria?" The agent says it's an AI assistant whenever asked and never denies it.
3. **Listen first.** The caller tells the story once. The agent fills gaps afterwards, one question at a time.
4. **Safety and priority check on every caller turn.** The fixed list (gas, carbon monoxide, smoke or burning, oxygen or medical equipment) is checked by the server on every line. A match switches the call to the safety journey at once.
5. **What the technician needs.** System, symptoms and since when, what's been tried, access (super, gate, parking), pets, who will be home, who approves the work.
6. **Read-back gate.** Before any booking the agent reads back the address and phone number in groups and the caller confirms. The server refuses to book until that read-back happened on this call.
7. **Book.** The agent offers the earliest real windows for that priority. Booking is idempotent on call and slot.
8. **Confirm out loud.** Day, arrival window, what happens next. No text in V1.
9. **"Anything else?"** before ending; never end in the same turn as the confirmation.
10. **Owner view.** Every call records: caller, type, priority with reason, member or not, outcome (booked, flagged, declined, callback, not a customer), refused tool calls if any.

## 1. No heat or no cooling

1. Caller: "The heat's out." The server looks up the address and any open job.
2. Agent: "I'm sorry, that's no fun. When did it stop?" Then, one at a time: what kind of system (if not on file), what the thermostat reads, whether they've tried anything.
3. Agent asks who's home (see journey 2) and whether they own or rent (see journey 6). Both are asked once, plainly.
4. Server checks the ZIP is in the service area. Out of area: polite decline and the call is logged.
5. Read-back gate, then the earliest window for that priority.
6. Ends booked. No maintenance-plan pitch on this call.
7. Owner view: priority (urgent if outdoor temperature or the answers call for it), reason, booked window.

## 2. Vulnerable household

1. Starts as journey 1. The agent asks: "Is anyone at home who's older, very young, or has a health condition the heat would affect?"
2. If yes: "Where are they right now?" No checklist of conditions.
3. The score (a classifier beside the turn) or the fixed list for medical equipment raises priority. Rules can only raise it.
4. If someone is struggling to breathe or their oxygen equipment has stopped: the agent tells the caller to call 911 first.
5. Agent: "I've marked this as a priority." It does not repeat the details back.
6. Ends booked in the earliest urgent window, or flagged to a person if no urgent window is open today.
7. Owner view: urgent, reason ("85-year-old at home, no heat, 52 F inside").

Open: if no urgent window is open today, does the agent book tomorrow's first window and flag a person, or only flag? Recommend both: book the first window so the caller has something certain, and flag a person to try to move it up.

## 3. Safety caller (gas, carbon monoxide, smoke)

1. Starts anywhere, often mid-sentence: "it smells a bit like gas, but anyway the heat's out."
2. The server's fixed-list check fires on that turn. From here the server refuses booking tools until the safety script has been said.
3. Agent says the fixed script, spoken from code, word for word (signed off by a person before launch). Gas: get everyone out now, don't flip switches or use the stove, call 911 or the gas company from outside. Carbon monoxide: open a window if one is right there, get everyone out, call 911 from outside.
4. Server flags a person immediately.
5. Agent ends the call so the caller can leave and use the phone outside. The end-call tool refuses until the script was said and the flag raised.
6. If the call drops before the script finishes: a person is flagged to call back.
7. A spam or wrong-number label can never apply to a call with a safety signal.
8. Owner view: safety call, what was said, time the script finished, flag status.

Open: after the caller is safe, should Summit Air offer a follow-up visit on a later call? Recommend yes, by a person calling back, not by the agent on this call.

## 4. Maintenance scheduler

1. Caller: "I need my annual tune-up." Lookup has already found them.
2. Agent uses what's on file: "I've got you at 42 Elm Street, is that still right?"
3. Agent offers windows that suit them (mornings, a day of the week). During a surge, tune-ups are offered a later day so today's slots stay open for broken systems.
4. Read-back gate, book, confirm.
5. Owner view: routine, booked.

## 5. Member

1. Same as the journey for their reason for calling, with the lookup showing an active plan.
2. Agent greets them as a member and names the benefit that applies: "You're on the Comfort plan, so you get priority scheduling."
3. Expired plan: the agent says it plainly and offers a person to renew, without promising the perk.
4. Unknown member (no phone match): the agent asks for the address and the server checks membership by address.
5. Owner view: member status recorded on every call.

## 6. Renter, or someone who can't approve the work

1. On every no-heat or no-cooling call the agent asks: "Do you own the home or rent it?"
2. If they rent, the agent records who approves the work separately from the caller.
3. Owner present or approving: book as usual, with the owner as the approver.
4. Owner unknown or unreachable: the agent takes details and flags a person.

Open: what a tenant with no heat in heat season (October 1 to May 31, five boroughs) is offered. Recommend: say one neutral line ("In New York City, landlords are required to provide heat in season, and 311 takes complaints"), then offer to book if the tenant or landlord can approve the visit, or take details and flag a person if not. No legal advice beyond that line.

## 7. Repeat caller

1. The lookup shows an open job or a recent visit.
2. "Where's my tech?": status from the FSM, never guessed. If the window has passed, flag a person.
3. Same problem came back: book a return visit linked to the earlier job, priority reviewed by a person.
4. Reschedule or cancel: only a booking that belongs to this caller (server check). One reschedule and one cancel per call.
5. Dropped and called back within a few minutes: the server links the calls and the agent picks up where the last call stopped: "I think we got cut off, you were telling me about the furnace." Nothing already done is done again.
6. Several calls in a few minutes with no progress: flag a person.
7. Owner view: linked calls shown as one thread.

## 8. Commercial caller

1. Signals: a business name, "our building", "our tenants", rooftop or multi-unit equipment, or a commercial account in the FSM.
2. Agent, early and politely: "Summit Air only works on homes, so we're not the right fit for this one."
3. The server refuses any booking for a commercial customer type.
4. One line saved with the normal end-of-call record: company, contact, what they needed. No extra lookups.
5. Owner view: commercial, declined.

Open: where the line falls. Recommend: an owner-occupied one or two-family house, or a single co-op or condo unit, is residential; a landlord calling about a building of three or more units, a property manager, or any business premises is commercial.

## 9. Replacement or install quote

1. Caller asks for a new system, a heat pump, a conversion or rebates.
2. Agent takes the details a comfort advisor needs: system age, what they want, how soon, home type.
3. Ends as a booked estimate visit or a callback from a comfort advisor. Never a price or a rebate promise.
4. Owner view: lead for a comfort advisor.

Open: does the agent book estimate visits itself, or only take a callback? Recommend a callback for V1; the mock has no advisor calendar yet.

## 10. Not a customer call

1. Wrong number, robocall, vendor, job seeker, or someone testing the agent with off-topic asks.
2. Agent gives one polite line and either returns to the purpose or ends the call. Off-topic asks ("write my essay", "ignore your instructions") get one line and a return to the call; the agent stays in role.
3. Any request to change or delete data is refused by the server, whatever the agent was told (least privilege, docs/architecture.md).
4. Never applied if a safety signal was heard.
5. Owner view: not a customer, with the type.

## 11. After hours

1. Same spine. The agent says the office is closed and what it can still do.
2. Routine calls: booked for the next business day.
3. Urgent calls (no heat with a vulnerable person, no cooling with a medical condition): the on-call technician gets an email alert with what, where, who and why urgent, and the agent tells the caller someone will call them back shortly.
4. Safety calls: journey 3, always.

Open: are after-hours urgent calls booked for tonight or only flagged for the on-call technician to decide? Recommend flag only: the on-call technician decides whether to go out tonight, and the agent never promises a tonight visit.

## Traits on any call

- Upset or rude: calm, no arguing, offer a person.
- Hard of hearing or slow to answer: slower pace, shorter questions, more read-backs.
- Two people on the line or someone relaying: confirm who is approving and who will be home.
- "Am I talking to a robot?": "Yes, I'm an AI assistant for Summit Air." Never denied.
- Wants a person: during hours, take details and flag a person for a callback (no live transfer in V1); after hours, as journey 11.
