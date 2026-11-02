# Considerations

A running list of things the build should think about. Anyone (Jackson by voice, or an agent) adds to it with `/consider`. Nothing here is a commitment until it is triaged.

Status: `open` (not triaged), `in` (in scope, linked to the spec or an issue), `later` (after go-live, goes in the rollout plan), `out` (won't do, with the reason), `decided` (see DECISIONS.md).

Each item: `- [status] **C-NN Title.** One or two lines. Links.`

## System shape

- [open] **C-01 Four parts.** Caller, voice stack, an integration layer (the tool API the agent calls), and the company's field service system (FSM/CRM, e.g. ServiceTitan). We build the voice stack and the integration layer, and mock the FSM.
- [open] **C-02 Mock FSM that looks real.** ServiceTitan-shaped records: customers, locations, memberships, equipment, job history, technicians, capacity by day. Seeded with members, non-members, a commercial account, an out-of-area address, and a repeat caller with an open job.
- [open] **C-03 One adapter between agent and FSM.** The agent only calls our tools; the adapter talks to the mock today and a real FSM later. Swapping the FSM changes one file.
- [open] **C-04 Owner and dispatcher view.** Today's bookings, priority flags with reasons, calls needing a person, each call's timeline.

## The six requirements

- [open] **C-05 Residential vs commercial.** Signals: business name, "property manager", "our building", rooftop or multi-unit equipment, an FSM account type. What a commercial caller gets: a polite decline with a referral or a message for the office, never a booking.
- [open] **C-06 Membership.** Look up by phone first, then address or name. Handle the caller who doesn't know they're a member, an expired plan, and two plans at one address. Name the benefit back ("you're on the Comfort plan, so you get priority scheduling").
- [open] **C-07 What the technician needs.** System type, brand and rough age, symptoms and when they started, thermostat reading, what's been tried, access (doorman, super, gate, parking), pets, best contact on site.
- [open] **C-08 Booking.** Arrival windows, capacity by priority, confirmation by SMS, reschedule and cancel, no double booking, idempotent tools (retries are normal on voice platforms).

## Priority and safety

- [open] **C-09 How priority is judged.** Hard rules in code (gas smell, CO alarm or symptoms, no heat below a temperature, vulnerable person plus no heat or no AC) plus a classifier for softer signals, plus live outdoor temperature. Rules can only raise priority, never lower it.
- [open] **C-10 Confidence.** The priority decision carries a reason and a confidence. Low confidence on a possibly urgent call rounds up and flags a person.
- [open] **C-11 Flagging a human.** What a flag does: dispatcher board, SMS to on-call, warm transfer during hours. Who gets it after hours.
- [open] **C-12 Safety script.** Gas or CO: tell the caller to leave and call 911 or the utility from outside, before any other question. Fixed wording, signed off by a person.
- [open] **C-13 NYC heat rules.** Renters in NYC are owed heat by the landlord during heat season; a tenant with no heat may need the landlord or 311, not a booking. Check the rules before relying on this.

## The caller

- [open] **C-14 Caller state.** Rude, upset, panicked, adversarial, or confused callers. Adapt tone, stay calm, offer a person.
- [open] **C-15 Special assistance.** Hard of hearing, elderly, slow speech, a caller on behalf of someone else. Slower pace, shorter questions, read-backs.
- [open] **C-16 Language.** NYC callers who prefer Spanish or another language. Detect and switch, or hand off.
- [open] **C-17 Who decides for the household.** Renter vs owner, landlord approval, adult child calling for a parent, spouses who both decide, who will be home. Book with the person who can authorize, and record who the tech should ask for.
- [open] **C-18 Repeat callers.** "Where's my tech?", an open job, a callback about a past visit.

## Staying on purpose

- [open] **C-19 Relevance guard.** It only does Summit Air's job. Off-topic asks (homework, coding, general questions) get one polite line and a return to the call. Prompt injection and role-play stay in role.
- [open] **C-20 Spam and wrong numbers.** Robocalls, sales calls, wrong numbers end quickly and are logged as such.
- [open] **C-21 Prices.** Never quote a repair price. Diagnostic or trip fee only if the company sets one in config.

## Conversation quality

- [open] **C-22 Sounds like a person, not a form.** Ask one thing at a time, let the caller tell the story, fill the gaps after.
- [open] **C-23 Reading back.** Phone, address and time are read back before booking.
- [open] **C-24 Latency and interruptions.** Per-stage budget, barge-in, no dead air on tool calls.
- [open] **C-25 Disclosure.** Says it's an AI if asked, never denies it; recording notice per NY rules.

## Operations

- [open] **C-26 Surge mode.** First heat wave or cold snap: shorter calls, urgent and members first, routine pushed to next available.
- [open] **C-27 Business hours and after hours.** What changes at night and on weekends.
- [open] **C-28 Swappable models and prompts.** Named profiles, prompt files by job, one eval matrix across them.
- [open] **C-29 Hosting.** Always-on tool server and agent worker, a backup number, how a failure is noticed.
- [open] **C-30 Privacy.** What is stored from calls, for how long, and who can see it.
- [open] **C-31 Rollout and success measures.** Shadow mode, then after hours and overflow, then all inbound. Answer rate, booking rate, priority recall, handoff rate.
