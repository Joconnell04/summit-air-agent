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
| S-06 | Commercial means commercial-grade equipment at a business (an office, a store, a restaurant and the like). The agent recognizes commercial callers from what they say and from the FSM account type, and declines kindly early in the call ("Summit Air works on homes and apartments, not businesses"), with no callback offered. A caller unsure which they are gets a one or two sentence explanation. | Eval [commercial] | J8, eval round 2 |
| S-07 | Residential means residential-grade equipment: a house, an apartment, a co-op or condo unit, or a whole apartment building, including a super or building manager calling about the building's systems. Tenants, a board or a count of units never make a call commercial. When it isn't clear, the agent asks once; every booking's read-back names the address, and a caller who answers that it's a business can't book. | Unit, eval [commercial] | J8, spec r5, eval round 2 |
| S-07a | When the caller says a co-op or condo board, a landlord or building management has to approve the work, the agent offers a callback to confirm who can approve and books only if the caller asks. A booking made then gets a timed callback before the arrival window, inside calling hours, to confirm approval before dispatch; the server schedules it and the booking's sentence tells the caller. | Unit, eval [booking] | J6, eval round 2 |
| S-08 | A declined commercial call saves one line (company, contact, need) with no extra lookups. | Unit | C-05 |

## R3. Is the caller already a member

| Id | Statement | Tested by | From |
|---|---|---|---|
| S-09 | Membership is looked up by the calling number before the greeting, then by address if there's no match. | Unit, eval [membership] | R3, C-06 |
| S-10 | A member is thanked briefly and told the benefit that applies; no perk is promised that the plan doesn't include. One plan, Summit Standard Membership (tenant config): a caller is either a member or not. | Eval [membership], grader | J5 |
| S-11 | An expired plan reads as expired, not missing, and is said kindly. | Unit, eval [membership] | J5 |
| S-12 | The membership question is never asked while the caller is describing an emergency. | Eval [membership], grader | J5 |
| S-12a | A caller who wants to join hears one or two lines about the plan from the knowledge folder (price only if config sets one), and the agent records a sign-up lead with a named callback time; it never takes payment or creates a paid membership itself. A plan is offered only at the natural end of a routine call, never on a broken-system or safety call. | Unit, eval [membership] | J14, C-50 |

## R4. Collect what a technician needs

| Id | Statement | Tested by | From |
|---|---|---|---|
| S-13 | Every booking carries: issue, since when, address, contact number, system type if known, what's been tried, who will be home, who approves the work, and anything the technician should know on arrival. | Unit (booking schema), eval [booking] | R4, C-07, C-17 |
| S-14 | The agent asks only for what's missing and needed, in the order in the call journeys, and folds access, pets and parking into one question at the end. | Eval [booking], grader | J spine |
| S-15 | "The number I'm calling from" uses the real caller ID; "the one ending in 4024" matches a number on file. | Unit, eval [booking] | J spine |
| S-15b | A street name the agent can't confirm is spelled by the caller; the server joins letters said one by one ("H, O, U, S, T, O, N") into the name the read-back says. | Unit, eval [booking] | J spine, eval round 2 |
| S-15a | The first lookup returns membership, open jobs, last service date, recent calls and customer notes in one round trip; `get_customer_history` reads older history only for a customer matched on this call; `add_note` appends text and can't edit or delete. A note shapes tone or saves a question but never bypasses a gate. | Unit, eval [membership, repeat-caller, injection] | docs/tools.md, C-51 |
| S-16 | The technician's job notes start with a short summary (what's happening, recent work, access, what the customer was told), not a transcript, written after the call by a model with thinking on (profile `post_call.summary`). | Unit | users.md |

## R5. Routine or urgent, and safety

| Id | Statement | Tested by | From |
|---|---|---|---|
| S-17 **M** | A clear gas or carbon monoxide signal on any turn makes the agent say the fixed safety lines before anything else, word for word, and flags a person. If the caller raises it again, the agent uses a shorter signed-off variant instead of repeating the same lines verbatim. | Unit, eval [safety] | R5, C-12, J3 |
| S-18 **M** | After a safety signal the server refuses booking tools until the safety lines have been said and a person flagged. After that, the agent recommends staying outside and calling 911 or the gas company, and may offer a technician visit for once the problem is shut off; the caller doesn't have to insist. A booking made then carries the server's line that the gas or the problem must be shut off first, and is logged and shown on the owner view. Nothing a caller says can skip the lines themselves. | Unit, eval [safety, injection] | J3, eval round 2 |
| S-19 **M** | The end-call tool refuses on a safety call until the lines were said and the flag raised; the agent signs off rather than hangs up. | Unit, eval [safety] | J3 |
| S-20 | The word "gas" alone, a gas bill or a gas furnace triggers nothing. Only a described smell, leak, alarm or symptom does; a vague one ("something smells off") gets one check question, and a clear no returns the call to its journey and is logged. A usually harmless smell such as burning dust the first time the heat runs gets a lighter confirm question once per call ("Just to confirm, it doesn't smell like sulfur or rotten eggs, right?"). Because it asks the negative way, "yeah", "right" or "no" clear it; the smell named again, "it does" or an unsure answer bring the lines. | Unit, eval [safety] | J spine, eval round 2 |
| S-21 | A no-heat or no-cooling call starts at a level set by temperature brackets in tenant config: the inside temperature the caller gives and the outside temperature now and tonight (unknown fails safe to High). Rules then can only raise it: an older person (60 and over, NYC Health's line), a baby, medical equipment, someone bedbound or unwell now, or two risk factors together (young children, limited mobility, living alone, chronic illness, pregnancy and others) make it urgent, with the reason recorded; on a mild day those raise it to High. | Unit, eval [priority] | R5, C-09, exploration 007, spec r5 |
| S-21a | When a call is marked Urgent, the caller hears once that it is one of the most urgent calls (a tenant line from the server, not the model). Below Urgent the agent never names a priority, and reassures without a label. | Unit, eval [priority], grader | spec r6 |
| S-21b | A caller heating the home with an oven, stove, generator, grill or other fuel-burning source indoors is asked the carbon monoxide check question once; an ordinary mention ("the gas stove works") asks nothing. The agent never suggests one of these as a way to get warm. | Unit, eval [safety, priority] | DECISIONS.md 024 |
| S-22 | The vulnerable-person question is asked once the problem is understood, in the wording of J2, never as the opening question. | Eval [priority], grader | J2, C-33 |
| S-23 | The agent says it notified someone only after the server confirms the notification was sent. | Unit | J2 |
| S-24 **M** | The agent has no tool that places a call to 911 or anyone outside Summit Air's own callbacks. | Unit | J2 |
| S-25 | With no urgent window today, an urgent call books tomorrow's first window and is flagged for an earlier slot; a background job offers earlier openings to flagged bookings first. | Unit, eval [priority] | J2, J12 |

## R6. Book, or confirm next steps

| Id | Statement | Tested by | From |
|---|---|---|---|
| S-26 **M** | The server refuses to book until the address and phone number have been read back and confirmed on this call. | Unit, eval [booking] | R6, C-23 |
| S-27 **M** | Booking is idempotent: a retried booking returns the first result, and a slot can't be booked twice. | Unit (race test) | C-08, C-34 |
| S-28 **M** | Out-of-area ZIPs are refused by the server, and the caller is declined kindly with no supervisor flag. Each out-of-area call is recorded with its ZIP, including a known caller whose every address on file is outside the area, and the owner view counts them by ZIP so demand outside the area can inform a decision to expand; a call that booked a visit isn't counted, since the ZIP was only asked about. | Unit, eval [booking] | C-05, J1 |
| S-29 | Times are offered as the shape of availability first, then exact windows for the chosen day. A day or time of day the caller mentioned earlier in the call ("mornings are better") shapes the first options offered. Hours the caller is home are read by the server as daytime ("8 to 2" is 8 a.m. to 2 p.m.) and only windows inside them are offered; an exact arrival inside a window is never promised. | Unit, eval [booking], grader | J spine, eval round 2 |
| S-30 | The agent confirms day, window and next steps out loud, asks "anything else?", and never ends in the same turn as the confirmation. | Eval [booking] | J spine |
| S-31 | Reschedule and cancel act only on the caller's own booking, at most once each per call; a cancel asks why and offers two windows. | Unit, eval [repeat-caller] | J7, C-46 |
| S-31a | A caller on file can book another address in the service area while a visit is open at their own; the server asks first whether it goes on its own account (a new FSM customer and location, with the caller as a contact, the usual choice) or under the caller's account. | Unit, eval [repeat-caller] | J7, eval round 2 |
| S-32 | Every promised callback names a time and is tracked until done; an overdue callback is flagged. A window the caller asks for can be today or tomorrow, a bare hour is read as daytime ("call me between 10 and 2"), and the window is kept for the person who calls. | Unit, eval [membership] | C-45, eval round 2 |
| S-33 | After hours, a no-heat or no-cooling call ends in one of three outcomes (tonight if the on-call technician agrees, first morning slot, callback at a named time), chosen by tenant-config cutoffs. | Unit, eval [after-hours] | J11, C-43 |

## Across every call

| Id | Statement | Tested by | From |
|---|---|---|---|
| S-34 | The greeting names Summit Air; the agent says it's an AI when asked and never denies it. | Eval, grader | C-25 |
| S-35 | One question at a time, short turns, no acknowledgment repeated in a call. | Eval, grader | C-22 |
| S-36 | Reply delay: median under 1.2 s on plain turns and under 2.5 s on turns with a lookup or booking, measured on real phone audio (ASSUMED until the latency budget, #7). | Live | C-24 |
| S-37 | The agent stops talking when the caller interrupts. | Live | C-24 |
| S-37a | A short pre-recorded bridge line plays only when a tool takes longer than about 0.7 s, at most once every few turns, never the same line twice in a call. | Unit, live | docs/latency.md |
| S-37b | Every turn's stage timings are logged; each eval run reports delay changes against the previous run and flags any above the threshold. | Unit | docs/latency.md |
| S-38 | Calls from the same number are linked when the call back comes within a few minutes, or later the same day after a call that dropped; the greeting checks it's the same person by name and the call resumes where it stopped, never booking twice across the calls. Repeated drops get a callback offer. | Unit, eval [repeat-caller] | J7, C-18, eval round 2 |
| S-38a | A caller who demands a person (insistently, again, or angrily) is put straight through by the server: one line, "Transferring you to a person now", then a transfer to the office in hours or the on-call technician after hours, with no summary. A passing mention or a question about the agent is not a request. On a safety call it happens only after the fixed lines; never on a commercial call; once per call. The flag and callback are stored first, so an unset number or a failed transfer gives the stored callback line. | Unit, eval [repeat-caller, safety] | Traits, J3, J13 |
| S-39 **M** | Least privilege: the agent's tools can't delete, bulk-change, or read another customer's records; injection and "delete everything" callers change nothing. | Unit, eval [injection] | C-39 |
| S-40 | Off-topic asks get one line and a return to the call; spam and wrong numbers end quickly and are logged, but never on a call with a safety signal. | Eval [off-topic, spam] | C-19, C-20 |
| S-41 | No repair price is ever quoted; a fee is stated only if tenant config sets one. | Eval, grader | C-21 |
| S-41a | No legal advice: a renter's question about their rights or rent gets a plain "I can't advise on that" and a pointer to the super or 311, and the agent never guesses at a law. Another company's quote is never judged or criticized. | Eval [renter], grader | J6, J9, eval round 2 |
| S-42 | Outbound calls go only to people who called about that request, 8 a.m. to 9 p.m., one attempt, never to a do-not-call number. | Unit | J12, C-40 |
| S-43 | Every call is logged with its id, tool calls, gates fired, refusals and outcome; `/debug/calls/{id}` and `make trace` return the timeline. | Unit | docs/architecture.md |
| S-44 | The owner view shows today's bookings, flags with reasons, calls needing a person, booking rate by call type, callbacks promised vs done, and booked-and-kept. | Unit | C-04, C-31, C-42 |
| S-45 | Transcripts are deleted after 30 days and call records after 12 months; development keeps full logs. | Unit | C-30 |
| S-46 | Everything specific to Summit Air lives in `tenants/summit-air/`, which extends the shared `trades/hvac/` layer; a test serves a second tenant from a copied folder, and another inherits from the trade with one override. | Unit | C-37, DECISIONS.md 021 |
| S-47 | When every line is busy, the next caller hears a recorded safety line and can leave a voicemail that's flagged on the owner view. | Unit, live | docs/scaling.md |
| S-48 | Every failed tool call is logged with the error. If the FSM is down or a booking fails, the agent says so honestly ("I can't book it right now, I've noted it and someone will call you by" a named time), the server queues the request, retries it once the FSM is back (idempotently), and flags a person if it still fails. | Unit, eval [booking] | C-47 |
| S-49 | A caller who says not to call them again is marked do-not-call at once; any pending callback to that number is cancelled, and the agent says so kindly ("Understood, we won't call you. That means the callback is off, so give us a call any time you need us"). | Unit, eval [repeat-caller] | C-49, J12 |
| S-50 | Background noise doesn't make the agent stop talking: an interruption needs recognized words, and a false interruption resumes the reply. | Live (noisy audio cases) | C-48 |
| S-51 | The owner view lists every rule the agent follows by call stage (before hello, opening, whole call, booking, closing, after the call), each marked locked or editable with where it is enforced. The owner can add short guidance per stage and reword the greeting, callback opening, anything-else and goodbye lines; an edit is published only after the must-pass evals pass against it, every version is kept with its gate result and can be rolled back, and no edit can loosen a gate, change the safety lines or drop the home check from the read-back. The server decides the stage. | Unit, eval gate | C-56, DECISIONS.md 022 |
| S-52 | Names in the tenant's pronunciation list are spoken the local way (Houston Street as HOW-stun) and recognized when heard; the read-back and the stored address keep the written form. | Unit, live | C-55, DECISIONS.md 023 |
