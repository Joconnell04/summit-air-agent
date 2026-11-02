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

- [open] **C-05 Residential vs commercial.** Signals: business name, "property manager", "our building", rooftop or multi-unit equipment, an FSM account type. What a commercial caller gets: a polite decline with a referral or a message for the office, never a booking. Still log a one-line lead (company, contact, what they needed) in the same end-of-call write, with no extra tool calls, in case Summit Air ever wants that list.
- [open] **C-06 Membership.** Look up by phone first, then address or name. Handle the caller who doesn't know they're a member, an expired plan, and two plans at one address. Name the benefit back ("you're on the Comfort plan, so you get priority scheduling").
- [open] **C-07 What the technician needs.** System type, brand and rough age, symptoms and when they started, thermostat reading, what's been tried, access (doorman, super, gate, parking), pets, best contact on site.
- [open] **C-08 Booking.** Arrival windows, capacity by priority, confirmation out loud and on the owner view (no texts in V1, DECISIONS.md 007), reschedule and cancel, no double booking, idempotent tools (retries are normal on voice platforms). See C-34.

## Priority and safety

- [open] **C-09 How priority is judged.** Hard rules in code (gas smell, CO alarm or symptoms, no heat below a temperature, vulnerable person plus no heat or no AC) plus a classifier for softer signals, plus live outdoor temperature. Rules can only raise priority, never lower it. Vulnerability signals: a phrase list as the floor (an age over 70, "my mother", oxygen, dialysis, newborn, pregnant, wheelchair, heart condition), plus a small classifier and a distress or sentiment score run beside the turn, off the reply path, so the latency budget is untouched.
- [open] **C-10 Confidence.** The priority decision carries a reason and a confidence. Low confidence on a possibly urgent call rounds up and flags a person.
- [open] **C-11 Flagging a human.** What a flag does: dispatcher board, SMS to on-call, warm transfer during hours. Who gets it after hours.
- [open] **C-12 Safety script.** Gas or CO: tell the caller to leave and call 911 or the utility from outside, before any other question. Fixed wording, signed off by a person. No dead ends: once a safety signal is seen, the end-call and spam paths refuse until the script was said and a person was flagged, and a dropped safety call gets a callback.
- [open] **C-13 NYC heat rules.** Renters in NYC are owed heat by the landlord during heat season; a tenant with no heat may need the landlord or 311, not a booking. Check the rules before relying on this.

## The caller

- [open] **C-14 Caller state.** Rude, upset, panicked, adversarial, or confused callers. Adapt tone, stay calm, offer a person.
- [open] **C-15 Special assistance.** Hard of hearing, elderly, slow speech, a caller on behalf of someone else. Slower pace, shorter questions, read-backs.
- [open] **C-16 Language.** NYC callers who prefer Spanish or another language. Detect and switch, or hand off. One agent, not one per language: speech-to-text with language detection, a multilingual model told to answer in the caller's language, and the safety script pre-translated for the top languages. Test the top two or three in evals. Not a deciding factor in the stack choice; Deepgram Flux Multilingual is the candidate to come back to.
- [open] **C-17 Who decides for the household.** Renter vs owner, landlord approval, adult child calling for a parent, spouses who both decide, who will be home. Book with the person who can authorize, and record who the tech should ask for.
- [open] **C-18 Repeat callers.** "Where's my tech?", an open job, a callback about a past visit. Track calls per number: several calls in a few minutes (dropped line, panic, prank) are linked, and the agent picks up where the last call stopped instead of starting over.

- [open] **C-32 Playbooks.** Idea to look into: the server picks a playbook per call from facts it already has (member, open job, after hours, outdoor temperature, safety or vulnerability flag, possibly a surge) and the prompt loads that section, e.g. no upsell on a surge-day breakdown call. Unverified: whether surges are local enough to detect by ZIP, or are citywide weather events. Rules would choose the playbook, not the model. Builds on C-26.
- [open] **C-33 Asking who's home, naturally.** The vulnerable-person question is asked on every no-heat or no-cooling call in plain words ("Is anyone at home who's older, very young, or has a health condition the heat would affect?"), never as a checklist, and the answer is read back only as "I've marked this as a priority."

- [open] **C-34 Idempotent by default.** Every side effect (booking, flag, text, commercial lead, call record) is keyed to the call and the caller, so a platform retry, a double tool call or the same person calling back five times never does anything twice.

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
- [open] **C-35 Decision models.** Could a small typed decision model (Laya, OpenAI Decisions, or "Jev" as Jackson said it; to identify) answer narrow questions like "did the caller agree" or "is this an emergency" beside the turn, off the reply path, so the latency budget holds? The earlier practice agent's eval (from-other-projects/bobert/decision-model-eval.md) found OpenAI Decisions accurate and stock Laya not. Approach: find a bottleneck first, then test whether a side model removes it; a decision model is a solution option, not a goal. Launch with none and a slot for one; the bake-off is evals/decision-models/PLAN.md. Part of #5.
- [open] **C-36 Local context, lightly.** Know the service area (boroughs, neighborhoods, housing types, how people say addresses, building access like a super or a shared basement) from config, without hard-coding NYC. Mention state or city programs only when they change what the caller should do.
- [open] **C-37 A template, not a one-off.** The same build should serve another trade (say a roofing company) by changing config: company facts, service area, job types, plans, safety scripts, prompts with variables, and the FSM adapter. Multi-tenant means one deployment picks the company by the number that was dialed. Weigh it in #4 and document that it was considered.
- [decided] **C-38 One phone number.** One number in the end (DECISIONS.md 004). During the bake-off, 678 rings Retell and (646) 624-0418 rings our stack; the loser's number is retired. No texts in V1 (DECISIONS.md 007). Its SIP trunk can list two destinations in priority order, so if the main voice stack errors or doesn't answer within 4 s, Twilio sends the call to the backup (SOURCE: Twilio failover docs). Switching stacks is a settings change, not a new number.
- [in] **C-39 Least privilege for the agent.** The agent can only do what a phone intake person could, on the caller's own records, with server-checked arguments, per-call limits and no destructive tools, so a caller can't talk it into deleting or exposing data. docs/architecture.md, DECISIONS.md 006.
- [open] **C-28 Swappable models and prompts.** Named profiles, prompt files by job, one eval matrix across them.
- [open] **C-29 Hosting.** Always-on tool server and agent worker, a backup number, how a failure is noticed.
- [open] **C-30 Privacy.** What is stored from calls, for how long, and who can see it.
- [open] **C-31 Rollout and success measures.** Shadow mode, then after hours and overflow, then all inbound. Answer rate, booking rate, priority recall, handoff rate.
