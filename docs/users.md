# Users

Who the agent serves, what each of them needs from a call, how the agent can tell which caller it has, and what that means for the build. Callers come first. The people at Summit Air who act on what the agent produces come second.

What the kickoff tells us (SOURCE: docs/assignment.md): residential only, 40 technicians, three counties in NYC, phones overwhelmed on the first heat wave or cold snap, and most calls are an AC that went out, a furnace that won't start, or annual maintenance.

The agent knows the service area from config, not from the prompt: all five boroughs, every residential ZIP in each (Jackson after his call on the Retell number, issue #149), what housing is typical there (two-family houses, co-ops, detached houses), how people give addresses, and building access like a super or a shared basement. It mentions a city or state program only when that changes what the caller should do, such as a tenant's right to heat (C-36).

Background research behind this doc: docs/explorations/001-hvac-shop-operations.md (how a shop this size runs) and docs/explorations/002-nyc-callers.md (who calls in NYC). Numbers stay in those files.

## How the agent tells callers apart

Most signals are not certain, so the agent uses three kinds, in this order:

1. **Facts the server already has.** The phone number matches a customer, a membership, an open job, or a recent call. These are certain and cost nothing on the call.
2. **A short fixed list.** Only the few things that must never depend on a model: a gas smell, a carbon monoxide alarm, smoke or a burning smell, someone on oxygen or other medical equipment, and heating the home with an oven, stove, grill or generator indoors (which gets the carbon monoxide check question once, not the lines). These always raise priority and can never lower it. The list stays short on purpose, because every entry needs eval cases for its false alarms ("gas bill").
3. **A score.** Everything softer, including most vulnerable-person cues ("my mother is 85", "the baby is three weeks old"), is judged by a small classifier or decision model that reads each finished caller turn beside the conversation, not in the reply path, so it adds no delay (C-09, C-10, C-35).

Whether these facts should also pick a playbook for the call (which questions to ask, what to skip) is an idea under review, not a decision (C-32).

## Callers

### 1. No heat or no cooling

The most common call. Usually stressed, often calling during a surge when the wait to reach a person is longest. In NYC the house is often a two-family with a boiler and radiators, or a detached house with a furnace and central air (ASSUMED, see 002).

- Wants: someone out today or tomorrow, a real arrival window, and to know it's handled.
- Worries: hold music, being upsold, a window that slips.
- How the agent knows: the caller's opening words ("the heat's out", "the AC stopped").
- Good call: tells the story once, hears the window and what happens next, said back clearly before the call ends.
- For the build: let the caller talk first and fill gaps after. Offer the earliest real slot. On these calls the agent does not pitch a maintenance plan or other services. During a surge, the call stays short.

### 2. Vulnerable household

The same broken system, plus someone at risk from heat or cold: an older person, a baby, someone with a medical condition. Most heat deaths in NYC are older people with a chronic illness and no working AC at home (SOURCE: NYC Health, in 002). The caller is often not that person. An adult child calling for a parent in another borough is typical.

- Wants: to be taken seriously at once.
- Worries: being handled like a routine booking.
- How the agent knows: callers describe a person, not a diagnosis. The fixed list catches medical equipment like oxygen; the score catches age, babies and illness, and the agent's own question catches what neither does. Callers rarely volunteer it, so the agent asks on every no-heat or no-cooling call.
- How it asks: in plain words, once, in the flow of the call. For example: "Is anyone at home who's older, very young, or has a health condition the heat would affect?" Then, if yes: "Where are they right now?" No checklist of conditions.
- For the build: a priority flag with the reason. The agent says only "I've marked this as a priority" and doesn't repeat the details back. The person on site is the technician's contact; the caller may be the one authorizing the visit. If the caller says someone is struggling to breathe or their oxygen equipment has stopped, the agent tells them to call 911.

### 3. Safety caller

Gas smell, a carbon monoxide alarm, a burning smell or smoke. Often mentioned in passing ("it smells a bit like gas, but anyway the heat's out").

- Needs: the safety instruction before any other question, and to get off the phone and outside.
- How the agent knows: a fixed phrase check on every caller turn, not only the first. No model decision is needed to start the safety script.
- What the agent says: two short scripts in fixed wording that sound like a calm person, not a notice. The content follows Con Edison, National Grid and NYC Health (002); the exact words are signed off by a person before launch (C-12). Roughly:
  - Gas: "Okay, that comes first. Please get everyone out of the house now, and don't flip any switches or use the stove on the way. Once you're outside, call 911 or your gas company. We'll sort out the heat after you're safe."
  - Carbon monoxide: "If your CO alarm is going off, open a window if one's right there, get everyone outside, and call 911 from outside. Once you're out, we can sort out the system."
- After the script the agent ends the call so the caller can leave and use the phone outside; it doesn't keep them talking.
- Checks so the agent never drops a safety caller:
  - The server records that the script was said. Booking questions stay blocked until it has been.
  - The end-call and spam paths refuse to run on a call with a safety signal until the script was said and a person was flagged.
  - A false alarm costs a short delay and a staff alert. A miss could cost a life, so the check is built to over-trigger. "Gas bill" and "gas station" are eval cases so the false alarms stay rare.
  - If the call drops after a safety signal, a person is flagged to call back.

### 4. Maintenance scheduler

Calm. An annual tune-up, often a member whose plan includes it, sometimes prompted by a reminder.

- Wants: a convenient time, quickly, without repeating details Summit Air already has.
- How the agent knows: "tune-up", "annual service", "my maintenance", or a reminder they received.
- For the build: look up the caller by phone first and use what's on file. Offer times that suit them. During a surge, tune-ups are offered a later day so that today's slots stay open for broken systems.

### 5. Replacement or install quote

Wants a new system, a heat pump, an oil-to-gas conversion, or asks about rebates. Heat pumps and rebates come up often in NYC.

- Wants: someone to come and quote it.
- For the build: this is a sales lead for a comfort advisor, not a repair slot. The agent takes the details and books an estimate visit or a callback. It never states a price or promises a rebate (C-21).

### 6. Member

Any of the callers above, on a maintenance plan. Typical perks are priority scheduling, two tune-ups a year, a repair discount, and no after-hours fee (SOURCE: 001). Members expect to be recognized. Some don't know they're members; some plans have lapsed. A plan belongs to the home, not the person, so a relative calling for a parent is matched by address.

- How the agent knows: the phone number, then the address, from the FSM.
- For the build: look them up before asking anything Summit Air already knows. Greet them as a member and name the benefit that applies ("you're a Summit member, so you get priority scheduling"). Every call records whether the caller was a member, so the owner can see it. Never promise a perk the plan doesn't include. A caller who wants to join gets a sign-up lead and a named callback time (call journeys, journey 14).

### 7. Renter, or someone who can't approve the work

A tenant, a house-sitter, a relative staying over. In NYC about half of two-family homes are rented (SOURCE: 002), so this is common, not an edge case. During heat season (October 1 to May 31) a landlord in the five boroughs must provide heat; a tenant's first call is to the landlord, then 311 (SOURCE: HPD, in 002).

- Wants: the system working again; may not be able to approve a paid visit.
- How the agent knows: it asks "Do you own the home or rent it?" on every no-heat or no-cooling call, because callers say "my landlord lives downstairs" only some of the time.
- For the build: record who approves the work separately from the caller. What the agent offers a tenant is decided in call journeys.

### 8. Repeat caller

Has an open job or a recent visit: "where's my tech?", "the same problem came back", reschedule, cancel. Or called a minute ago and the line dropped.

- How the agent knows: the server logs every call by phone number and links calls from the same number. Several calls within a few minutes mean a dropped line, a panicking caller, or a prank.
- For the build: find the open job first and never create a duplicate. Give status from the FSM rather than guessing. If the caller rang a few minutes ago, pick up where that call stopped ("I think we got cut off, you were telling me about the furnace") instead of starting over. A caller who keeps calling with no progress is flagged to a person. Every action is keyed to the call and the caller, so a second or fifth call never books, texts or flags twice (C-34).

### 9. Commercial caller

A restaurant, a store, an office: a business with commercial-grade equipment. Summit Air doesn't serve them. A super or building manager calling about an apartment building's residential systems is not in this group; an apartment building is residential (eval round 2).

- Wants: to know quickly whether Summit Air can help.
- How the agent knows: the signals in C-05, such as a business name or commercial rooftop equipment. "Our building" and "our tenants" alone point to an apartment building, which is residential.
- For the build: tell them early and politely that Summit Air works on homes and apartments, not businesses, and never book them. A caller unsure which they are gets a short explanation. Save one line (company, contact, what they needed) with the normal end-of-call record, with no extra lookups, in case the owner ever wants that list. Where the line falls (a small landlord, a two-family, a co-op unit) is drawn in call journeys.

### 10. Not a customer call

Wrong numbers, robocalls, vendors, job seekers, and people testing the agent with off-topic or adversarial asks.

- For the build: end politely and quickly, log the call type, stay in role. A spam label never applies to a call with a safety signal.

### Traits that apply to any caller

- Upset or rude: stay calm, don't argue, offer a person.
- Hard of hearing, elderly, or slow to answer: slower pace, shorter questions, read-backs.
- Prefers another language: about one in four people in Queens and Brooklyn speak English less than very well, and Spanish is the most common of those languages in every county checked (SOURCE: 002). One agent handles this, not one per language. Speech-to-text detects the language, the model answers in it, and the safety scripts are translated ahead of time. Spanish works on the call. For other languages the agent takes the number and language and a person calls back (C-16).
- Two people on the line, or a caller relaying answers from someone else in the room.
- Asks if it's a robot: says it's an AI assistant for Summit Air.

## Summit Air staff

A shop of 40 technicians usually has separate call-takers and dispatchers, a service manager over the technicians, and comfort advisors who sell replacements (SOURCE: 001). Office headcount is not published; a guess for Summit Air is five or six call-takers, two or three dispatchers, one service manager and three to five comfort advisors (ASSUMED).

### Owner

The person Paul plays in the demo.

- Wants: more calls answered and booked during a surge, no missed emergency, the brand sounding good, and technicians sent only on jobs they can do and bill.
- Worries: the agent books a commercial job, misses a gas smell, quotes a price, promises a slot that doesn't exist, or sounds like a robot. Also cost, liability, and what happens when it breaks.
- Judges success by: booking rate (the share of calls that become a booked job), calls captured after hours, and how many customers are on a maintenance plan.
- Sees: today's bookings, priority flags with reasons, calls that need a person, and the eval scoreboard.

### Call-taker (customer service rep)

Answers the phones today and is swamped in a surge. With the agent, takes the calls the agent hands over and returns callbacks.

- Wants: a handoff that says who's calling and what they need, so the caller isn't asked twice.
- Judges success by: calls booked, callers not left on hold.

### Dispatcher

Assigns booked jobs to technicians and reshuffles the day when urgent work comes in.

- Wants: clean bookings in the FSM, priority with a reason, no double bookings, and no surprise jobs that break the day's schedule.
- Judges success by: a schedule that holds, fewer reschedule calls.
- For the build: records written in the FSM's shape. Each flag carries its reason and a link to the call.

### Service manager

Runs the technicians and decides the hard cases: whether an after-hours call goes out tonight, a dispute, a caller who wants a manager.

- Wants: to hear about the calls that need judgment, and only those.

### Technician

Reads the job before arriving.

- Needs: a short summary in the order technicians want it (SOURCE: ACHR, in 001): what the customer is experiencing, recent work on the system, how to get in, and what the customer was told. Underneath: system type, brand and rough age, when it started, thermostat reading, what's been tried, pets, and who to ask for on site (C-07).
- Judges success by: fixing it on the first visit without calling the office for missing details.
- For the build: structured notes, not a transcript. Separate fields for the on-site contact and the person approving the work.

### On-call technician

At night and on weekends, one technician carries the on-call phone for a week at a time; the service manager backs them up (ASSUMED; this is the most common set-up, SOURCE: 001).

- Needs: a short email alert saying what, where, who, and why it's urgent, so they can decide whether to go out tonight. The agent never promises a tonight visit; the on-call technician decides.

### Comfort advisor

Sells replacement systems and installs.

- Wants: replacement and install leads with the details already taken (system age, what they want, how soon).
- Judges success by: estimates that turn into sales.

## When needs conflict

1. A caller in danger: gas, carbon monoxide, or a vulnerable person without heat or cooling.
2. A caller with a broken system.
3. The owner's business: booked, billable, the right kind of job.
4. Staff time.

For example, during a surge a tune-up is offered a later day so an urgent call can have today's slot.
