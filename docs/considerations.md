# Considerations

A running list of things the build should think about. Anyone (Jackson by voice, or an agent) adds to it with `/consider`. Nothing here is a commitment until it is triaged.

Status: `open` (not triaged), `in` (in scope, linked to the spec or an issue), `later` (after go-live, goes in the rollout plan), `out` (won't do, with the reason), `decided` (see DECISIONS.md).

Each item: `- [status] **C-NN Title.** One or two lines. Links.`

## System shape

- [in] **C-01 Four parts.** Caller, voice stack, an integration layer (the tool API the agent calls), and the company's field service system (FSM/CRM, e.g. ServiceTitan). We build the voice stack and the integration layer, and mock the FSM.
- [in] **C-02 Mock FSM that looks real.** ServiceTitan-shaped records: customers, locations, memberships, equipment, job history, technicians, capacity by day. Seeded with members, non-members, a commercial account, an out-of-area address, and a repeat caller with an open job.
- [in] **C-03 One adapter between agent and FSM.** The agent only calls our tools; the adapter talks to the mock today and a real FSM later. Swapping the FSM changes one file.
- [in] **C-04 Owner and dispatcher view.** Today's bookings, priority flags with reasons, calls needing a person, each call's timeline.

## The six requirements

- [in] **C-05 Residential vs commercial.** Signals: business name, "property manager", "our building", rooftop or multi-unit equipment, an FSM account type. What a commercial caller gets: a polite decline with a referral or a message for the office, never a booking. Still log a one-line lead (company, contact, what they needed) in the same end-of-call write, with no extra tool calls, in case Summit Air ever wants that list.
- [in] **C-06 Membership.** Look up by phone first, then address or name. Handle the caller who doesn't know they're a member and an expired membership. One plan only (Summit Standard Membership). Name the benefit back ("you're a Summit member, so you get priority scheduling").
- [in] **C-07 What the technician needs.** System type, brand and rough age, symptoms and when they started, thermostat reading, what's been tried, access (doorman, super, gate, parking), pets, best contact on site.
- [in] **C-08 Booking.** Arrival windows, capacity by priority, confirmation out loud and on the owner view (no texts in V1, DECISIONS.md 007), reschedule and cancel, no double booking, idempotent tools (retries are normal on voice platforms). See C-34.

## Priority and safety

- [in] **C-09 How priority is judged.** Hard rules in code (gas smell, CO alarm or symptoms, no heat below a temperature, vulnerable person plus no heat or no AC) plus a classifier for softer signals, plus live outdoor temperature. Rules can only raise priority, never lower it. Vulnerability signals: a phrase list as the floor (an age over 70, "my mother", oxygen, dialysis, newborn, pregnant, wheelchair, heart condition), plus a small classifier and a distress or sentiment score run beside the turn, off the reply path, so the latency budget is untouched.
- [in] **C-10 Confidence.** The priority decision carries a reason and a confidence. Low confidence on a possibly urgent call rounds up and flags a person.
- [in] **C-11 Flagging a human.** What a flag does: dispatcher board, SMS to on-call, warm transfer during hours. Who gets it after hours. V1: owner board plus an email alert to the on-call person; warm transfer and texts later.
- [in] **C-12 Safety script.** Gas or CO: tell the caller to leave and call 911 or the utility from outside, before any other question. Fixed wording, signed off by a person. No dead ends: once a safety signal is seen, the end-call and spam paths refuse until the script was said and a person was flagged, and a dropped safety call gets a callback.
- [in] **C-13 NYC heat rules.** Renters in NYC are owed heat by the landlord during heat season; a tenant with no heat may need the landlord or 311, not a booking. Check the rules before relying on this.

## The caller

- [in] **C-14 Caller state.** Rude, upset, panicked, adversarial, or confused callers. Adapt tone, stay calm, offer a person.
- [in] **C-15 Special assistance.** Hard of hearing, elderly, slow speech, a caller on behalf of someone else. Slower pace, shorter questions, read-backs.
- [later] **C-16 Language.** NYC callers who prefer Spanish or another language. Detect and switch, or hand off. One agent, not one per language: speech-to-text with language detection, a multilingual model told to answer in the caller's language, and the safety script pre-translated for the top languages. Test the top two or three in evals. Not a deciding factor in the stack choice; Deepgram Flux Multilingual is the candidate to come back to.
- [in] **C-17 Who decides for the household.** Renter vs owner, landlord approval, adult child calling for a parent, spouses who both decide, who will be home. Book with the person who can authorize, and record who the tech should ask for.
- [in] **C-18 Repeat callers.** "Where's my tech?", an open job, a callback about a past visit. Track calls per number: several calls in a few minutes (dropped line, panic, prank) are linked, and the agent picks up where the last call stopped instead of starting over.

- [later] **C-32 Playbooks.** Idea to look into: the server picks a playbook per call from facts it already has (member, open job, after hours, outdoor temperature, safety or vulnerability flag, possibly a surge) and the prompt loads that section, e.g. no upsell on a surge-day breakdown call. Unverified: whether surges are local enough to detect by ZIP, or are citywide weather events. Rules would choose the playbook, not the model. Builds on C-26.
- [in] **C-33 Asking who's home, naturally.** The vulnerable-person question is asked on every no-heat or no-cooling call in plain words ("Is anyone at home who's older, very young, or has a health condition the heat would affect?"), never as a checklist, and the answer is read back only as "I've marked this as a priority."

- [in] **C-34 Idempotent by default.** Every side effect (booking, flag, note, lead, callback, call record) is keyed to the call and the caller, so a platform retry, a double tool call or the same person calling back five times never does anything twice.

## Staying on purpose

- [in] **C-19 Relevance guard.** It only does Summit Air's job. Off-topic asks (homework, coding, general questions) get one polite line and a return to the call. Prompt injection and role-play stay in role.
- [in] **C-20 Spam and wrong numbers.** Robocalls, sales calls, wrong numbers end quickly and are logged as such.
- [in] **C-21 Prices.** Never quote a repair price. Diagnostic or trip fee only if the company sets one in config.

## Conversation quality

- [in] **C-22 Sounds like a person, not a form.** Ask one thing at a time, let the caller tell the story, fill the gaps after.
- [in] **C-23 Reading back.** Phone, address and time are read back before booking.
- [in] **C-24 Latency and interruptions.** Per-stage budget, barge-in, no dead air on tool calls.
- [in] **C-25 Disclosure.** Says it's an AI if asked, never denies it; recording notice per NY rules.

## Operations

- [out] **C-26 Surge mode.** First heat wave or cold snap: shorter calls, urgent and members first, routine pushed to next available. Out (DECISIONS.md 008): surges are handled by the scaling plan in docs/scaling.md, not a separate mode.
- [in] **C-27 Business hours and after hours.** What changes at night and on weekends.
- [later] **C-35 Decision models.** Could a small typed decision model (Laya, OpenAI Decisions, or "Jev" as Jackson said it; to identify) answer narrow questions like "did the caller agree" or "is this an emergency" beside the turn, off the reply path, so the latency budget holds? The earlier practice agent's eval (from-other-projects/bobert/decision-model-eval.md) found OpenAI Decisions accurate and stock Laya not. Approach: find a bottleneck first, then test whether a side model removes it; a decision model is a solution option, not a goal. Launch with none and a slot for one; the bake-off is evals/decision-models/PLAN.md. Part of #5.
- [in] **C-36 Local context, lightly.** Know the service area (boroughs, neighborhoods, housing types, how people say addresses, building access like a super or a shared basement) from config, without hard-coding NYC. Mention state or city programs only when they change what the caller should do.
- [in] **C-37 A template, not a one-off.** The same build should serve another trade (say a roofing company) by changing config: company facts, service area, job types, plans, safety scripts, prompts with variables, and the FSM adapter. Multi-tenant means one deployment picks the company by the number that was dialed. Weigh it in #4 and document that it was considered.
- [decided] **C-38 One phone number.** One number in the end (DECISIONS.md 004). During the bake-off, 678 rings Retell and (646) 624-0418 rings our stack; the loser's number is retired. No texts in V1 (DECISIONS.md 007). Its SIP trunk can list two destinations in priority order, so if the main voice stack errors or doesn't answer within 4 s, Twilio sends the call to the backup (SOURCE: Twilio failover docs). Switching stacks is a settings change, not a new number.
- [in] **C-40 Calls Summit Air makes.** Outbound calls with the same spine: an earlier slot opened for a flagged urgent booking, a requested callback after drops, a safety follow-up. Only to people who called about this request, 8 a.m. to 9 p.m., one attempt. docs/call-journeys.md journey 12.
- [in] **C-41 Company knowledge folder.** Company-specific facts (slogan, plan wording, equipment brands, common questions) in `tenants/<company>/knowledge/` as markdown the agent can use. docs/template.md.
- [in] **C-42 Kept bookings and pilot kill criteria.** Track booked jobs still on the schedule when the technician arrives, not just bookings made; define a pilot's success and stop criteria up front (Revin's own rollout practice, exploration 005). Extends C-31. In: owner view shows booking rate by call type, callbacks promised vs done, and booked-and-kept; kill criteria go in the rollout plan. Revin blog: hvac-booking-rate-vs-answer-rate, hvac-ai-answering-service-pilot.
- [in] **C-43 After-hours thresholds in config.** Tonight vs first-morning vs callback decided by inside temperature and trend, as tenant config values marked ASSUMED, not hard-coded (exploration 005).
- [in] **C-44 Look-alike no-heat calls.** A heat pump in defrost or a locked-out furnace can look like no heat; eval cases so they don't raise a false urgent flag. The agent doesn't talk callers through resets (exploration 005).
- [in] **C-45 Callbacks with a time, and tracked.** Promise a specific callback time, not "shortly", and show promised vs completed callbacks on the owner view (exploration 005).
- [in] **C-46 More call types.** Second-opinion replacement quotes, refrigerant questions ("isn't R-410A banned?"), complaints and warranty requests, and cancellations (ask why, offer two new windows) (exploration 005).
- [in] **C-47 Keep working when a tool fails.** Every failed tool call is logged; if the FSM is down, the agent says so honestly, the server queues the request and retries it idempotently, and a person is flagged if it still fails. Spec S-48.
- [in] **C-48 Background noise and false interruptions.** A noisy caller shouldn't make the agent stop mid-sentence. Speech-to-text only returns words, but the voice activity detector can react to noise. LiveKit Agents (1.5 and later) has adaptive interruption handling, a minimum-words setting so an interruption needs recognized words, and resuming after a false interruption (SOURCE: docs.livekit.io/agents/logic/turns/tuning, docs.livekit.io/reference/agents/turn-handling-options); a known bug can resume into a caller's pause (github.com/livekit/agents/pull/6714). Noise cancellation on the incoming audio is the other lever (not yet researched). Test with noisy audio cases in the live pass. Spec S-50.
- [in] **C-49 Do not call.** "Don't call me again" marks the number do-not-call at once, cancels pending callbacks, and the agent says so kindly. Spec S-49.
- [in] **C-50 Joining the membership.** A sign-up flow that records a lead and a named callback; no payment taken by the agent. Call journeys journey 14, spec S-12a.
- [in] **C-51 Customer notes and history.** The agent reads the CRM's customer notes, last service date and recent calls in the first lookup, can ask for older history about the matched customer, and can append a note. Notes shape tone and save questions but never override a gate. docs/tools.md.
- [open] **C-52 Owner view for a non-technical owner.** Jackson, on the brief 06 page: "useful for a backend software engineer"; needs to work for "a backend HVAC engineer who is not super familiar with AI coding and software and logs and debugging". On the look: "very AI sloppy, generic, and too dark mode GitHub coder vibe"; "on brand with Revin and somewhat similar to ServiceTitan, but keep it Revin branded", with the real logos. Stories in docs/decisions/40-owner-view-stories.md (#40); brand research in docs/explorations/008.
- [in] **C-39 Least privilege for the agent.** The agent can only do what a phone intake person could, on the caller's own records, with server-checked arguments, per-call limits and no destructive tools, so a caller can't talk it into deleting or exposing data. docs/architecture.md, DECISIONS.md 006.
- [in] **C-28 Swappable models and prompts.** Named profiles, prompt files by job, one eval matrix across them.
- [in] **C-29 Hosting.** Always-on tool server and agent worker, a backup number, how a failure is noticed.
- [in] **C-30 Privacy.** What is stored from calls, for how long, and who can see it. In (DECISIONS.md 008): a retention policy implemented in code (transcripts 30 days, consent and call records 12 months, ASSUMED), with full logs kept in development so debugging isn't hindered.
- [in] **C-31 Rollout and success measures.** Shadow mode, then after hours and overflow, then all inbound. Answer rate, booking rate, priority recall, handoff rate. In (DECISIONS.md 008): the success measures are tracked by the system and shown on the owner view, so they can be presented in the meeting.
