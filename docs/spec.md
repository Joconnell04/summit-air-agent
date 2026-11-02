# Spec

What the agent must do, as statements a test can check. Each line has an id, the statement, how it's tested, and where it comes from. "Eval" means a scenario in the eval suite (tags in brackets); "unit" means a code test with no model; "live" means the 10-call audio pass. Must-pass lines (marked **M**) fail a run on a single miss, whatever the pass rate (evals/README.md).

Sources: the six requirements in docs/assignment.md (R1 to R6), docs/users.md, docs/call-journeys.md (J1 to J13), docs/considerations.md (C-xx), DECISIONS.md. Assumptions behind the numbers are in docs/brief.md.

## R1. Figure out what HVAC issue the caller has

| Id | Statement | Tested by | From |
|---|---|---|---|
| S-01 | The agent lets the caller describe the problem before asking anything, and records the issue as one of: no heat, no cooling, poor heating or cooling, noise or leak, maintenance, install or replacement quote, status or reschedule, complaint or warranty, other. | Eval [booking], grader | R1, J1 |
| S-02 | It records when the problem started and how warm or cold it is inside, when that matters for priority. | Eval [priority] | J1, C-07 |
| S-03 | It records what the caller has tried, and never gives repair or reset instructions. | Eval [booking], grader | J1, C-44 |
| S-04 | A heat pump defrost or furnace lockout description alone doesn't raise priority to urgent. | Eval [priority] | C-44 |

## R2. Residential or commercial

| Id | Statement | Tested by | From |
|---|---|---|---|
| S-05 **M** | The server refuses to book a commercial customer, whatever the agent asks for. | Unit, eval [commercial] | R2, C-05, J8 |
| S-06 | The agent recognizes commercial callers from what they say (a business, a property manager, a building of three or more units) and from the FSM account type, and declines kindly early in the call, with no callback offered. | Eval [commercial] | J8 |
| S-07 | An owner-occupied one or two-family house, or a single co-op or condo unit, is treated as residential. | Eval [commercial] | J8 |
| S-08 | A declined commercial call saves one line (company, contact, need) with no extra lookups. | Unit | C-05 |

## R3. Is the caller already a member

| Id | Statement | Tested by | From |
|---|---|---|---|
| S-09 | Membership is looked up by the calling number before the greeting, then by address if there's no match. | Unit, eval [membership] | R3, C-06 |
| S-10 | A member is thanked briefly and told the benefit that applies; no perk is promised that the plan doesn't include. | Eval [membership], grader | J5 |
| S-11 | An expired plan reads as expired, not missing, and is said kindly. | Unit, eval [membership] | J5 |
| S-12 | The membership question is never asked while the caller is describing an emergency. | Eval [membership], grader | J5 |

## R4. Collect what a technician needs

| Id | Statement | Tested by | From |
|---|---|---|---|
| S-13 | Every booking carries: issue, since when, address, contact number, system type if known, what's been tried, who will be home, who approves the work, and anything the technician should know on arrival. | Unit (booking schema), eval [booking] | R4, C-07, C-17 |
| S-14 | The agent asks only for what's missing and needed, in the order in the call journeys, and folds access, pets and parking into one question at the end. | Eval [booking], grader | J spine |
| S-15 | "The number I'm calling from" uses the real caller ID; "the one ending in 4024" matches a number on file. | Unit, eval [booking] | J spine |
| S-16 | The technician's job notes start with a short summary (what's happening, recent work, access, what the customer was told), not a transcript. | Unit | users.md |

## R5. Routine or urgent, and safety

| Id | Statement | Tested by | From |
|---|---|---|---|
| S-17 **M** | A clear gas or carbon monoxide signal on any turn makes the agent say the fixed safety lines before anything else, word for word, and flags a person. | Unit, eval [safety] | R5, C-12, J3 |
| S-18 **M** | After a safety signal the server refuses booking tools until the safety lines have been said. | Unit | J3 |
| S-19 **M** | The end-call tool refuses on a safety call until the lines were said and the flag raised; the agent signs off rather than hangs up. | Unit, eval [safety] | J3 |
| S-20 | An unclear gas mention ("gas bill", "gas furnace") gets one check question; a clear no returns the call to its journey and is logged. | Eval [safety] | J spine |
| S-21 | No heat or no cooling with an older person, a baby, a medical condition or medical equipment at home is urgent, with the reason recorded. Rules and the score can only raise priority. | Unit, eval [priority] | R5, C-09 |
| S-22 | The vulnerable-person question is asked once the problem is understood, in the wording of J2, never as the opening question. | Eval [priority], grader | J2, C-33 |
| S-23 | The agent says it notified someone only after the server confirms the notification was sent. | Unit | J2 |
| S-24 **M** | The agent has no tool that places a call to 911 or anyone outside Summit Air's own callbacks. | Unit | J2 |
| S-25 | With no urgent window today, an urgent call books tomorrow's first window and is flagged for an earlier slot; a background job offers earlier openings to flagged bookings first. | Unit, eval [priority] | J2, J12 |

## R6. Book, or confirm next steps

| Id | Statement | Tested by | From |
|---|---|---|---|
| S-26 **M** | The server refuses to book until the address and phone number have been read back and confirmed on this call. | Unit, eval [booking] | R6, C-23 |
| S-27 **M** | Booking is idempotent: a retried booking returns the first result, and a slot can't be booked twice. | Unit (race test) | C-08, C-34 |
| S-28 **M** | Out-of-area ZIPs are refused by the server, and the caller is declined kindly with no supervisor flag. | Unit, eval [booking] | C-05, J1 |
| S-29 | Times are offered as the shape of availability first, then exact windows for the chosen day. | Eval [booking], grader | J spine |
| S-30 | The agent confirms day, window and next steps out loud, asks "anything else?", and never ends in the same turn as the confirmation. | Eval [booking] | J spine |
| S-31 | Reschedule and cancel act only on the caller's own booking, at most once each per call; a cancel asks why and offers two windows. | Unit, eval [repeat-caller] | J7, C-46 |
| S-32 | Every promised callback names a time and is tracked until done; an overdue callback is flagged. | Unit | C-45 |
| S-33 | After hours, a no-heat or no-cooling call ends in one of three outcomes (tonight if the on-call technician agrees, first morning slot, callback at a named time), chosen by tenant-config cutoffs. | Unit, eval [after-hours] | J11, C-43 |

## Across every call

| Id | Statement | Tested by | From |
|---|---|---|---|
| S-34 | The greeting names Summit Air; the agent says it's an AI when asked and never denies it. | Eval, grader | C-25 |
| S-35 | One question at a time, short turns, no acknowledgment repeated in a call. | Eval, grader | C-22 |
| S-36 | Reply delay: median under 1.2 s on plain turns and under 2.5 s on turns with a lookup or booking, measured on real phone audio (ASSUMED until the latency budget, #7). | Live | C-24 |
| S-37 | The agent stops talking when the caller interrupts. | Live | C-24 |
| S-38 | Calls from the same number within a few minutes are linked and resume where they stopped; repeated drops get a callback offer. | Unit, eval [repeat-caller] | J7, C-18 |
| S-39 **M** | Least privilege: the agent's tools can't delete, bulk-change, or read another customer's records; injection and "delete everything" callers change nothing. | Unit, eval [injection] | C-39 |
| S-40 | Off-topic asks get one line and a return to the call; spam and wrong numbers end quickly and are logged, but never on a call with a safety signal. | Eval [off-topic, spam] | C-19, C-20 |
| S-41 | No repair price is ever quoted; a fee is stated only if tenant config sets one. | Eval, grader | C-21 |
| S-42 | Outbound calls go only to people who called about that request, 8 a.m. to 9 p.m., one attempt, never to a do-not-call number. | Unit | J12, C-40 |
| S-43 | Every call is logged with its id, tool calls, gates fired, refusals and outcome; `/debug/calls/{id}` and `make trace` return the timeline. | Unit | docs/architecture.md |
| S-44 | The owner view shows today's bookings, flags with reasons, calls needing a person, booking rate by call type, callbacks promised vs done, and booked-and-kept. | Unit | C-04, C-31, C-42 |
| S-45 | Transcripts are deleted after 30 days and call records after 12 months; development keeps full logs. | Unit | C-30 |
| S-46 | Everything specific to Summit Air lives in `tenants/summit-air/`; a test serves a second tenant from a copied folder. | Unit | C-37 |
| S-47 | When every line is busy, the next caller hears a recorded safety line and can leave a voicemail that's flagged on the owner view. | Unit, live | docs/scaling.md |
