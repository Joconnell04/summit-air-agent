# Design trail

How the design was reached, in order, so a reader can follow each choice back to the reasoning behind it. Each step links to the file that holds the detail. Updated whenever a decision lands.

## How a choice gets made

1. An idea or concern goes into `docs/considerations.md` (by voice, with `/consider`).
2. If it needs evidence, a background research agent writes `docs/explorations/NNN-*.md`.
3. If it's Jackson's call, a short readout in `docs/decisions/NN-*.md` lays out the options, trade-offs and a recommendation. Jackson reads it beside the chat and answers.
4. The answer becomes one entry in `DECISIONS.md` (what, why, what else was considered, what would change it), the issue is closed, and the docs it touches are updated.
5. Anything set aside on purpose goes in `docs/later.md`.
6. Documents that define the product (users, call journeys, spec) change only through logged rounds in `coauthoring.md`, with Jackson's feedback verbatim.

## Steps so far

| Step | Outcome | Where |
|---|---|---|
| Users | Ten caller types, staff roles from research, how the agent tells callers apart, safety and repeat-caller handling; locked after three rounds | docs/users.md, coauthoring.md (users r1 to r3), explorations 001 and 002 |
| Working method | Decisions surfaced one at a time as readout files; ideas Jackson raises mid-step are looked into, not written in as settled | CLAUDE.md, docs/decisions/README.md |
| Eval design | Our own harness, code checks decide pass or fail, an LLM grader scores quality | DECISIONS.md 002, docs/decisions/06-eval-design.md |
| Mock ServiceTitan | Built and merged: technician-window slots, idempotent booking, tenant config | DECISIONS.md 003, brief 01, pull request 14 |
| Voice platform | Our own LiveKit stack, with Retell as the baseline to beat | DECISIONS.md 004, docs/decisions/04-voice-platform.md, explorations 003 and 004 |
| Server | A new Vultr server in New Jersey, separate from the shared one | DECISIONS.md 005, docs/decisions/16-server.md |
| Pipeline and models | Three separate steps, a right-sized model per job, no side model at launch, least-privilege harness | DECISIONS.md 006, docs/decisions/05-pipeline-and-models.md, docs/architecture.md |
| Bake-offs | Hypotheses and pass rules set before any run | docs/bakeoffs.md |
| Phone number | (646) 624-0418 bought for our stack; the 678 Retell number is the bake-off baseline; one number after the bake-off | DECISIONS.md 004 |
| Texting | Left out of V1: not in the assignment, and registration takes weeks; terms pages kept for later | DECISIONS.md 007, docs/decisions/15-texting-registration.md |
| Scope | Every consideration triaged: privacy and success measures in, surge mode out (scaling plan instead), languages, decision models and playbooks later | DECISIONS.md 008, docs/decisions/10-considerations-triage.md |
| Call journeys | Shared spine, thirteen journeys including calls Summit Air makes, server checks named in each, Revin field practice cited; locked after three rounds | docs/call-journeys.md, coauthoring.md (call-journeys r1 to r3), exploration 005 |
| Spec | 51 testable statements with must-pass marked; assumptions in the brief; plans renamed Summit Premium and Standard; joining flow, tool-failure queue, do-not-call added; locked after two rounds | docs/spec.md, docs/brief.md, coauthoring.md (spec r1 and r2) |
| Latency budget | Per-stage targets with sources, bridge line only for slow tools, delay changes flagged across eval runs, on-prem estimate; thinking on only off the live path (technician summary, grader) | docs/latency.md, DECISIONS.md 006 |
| Membership | One plan, Summit Standard Membership: a caller is a member or not | coauthoring.md (spec r3) |
| Build briefs | Tool contract plus five lanes (tool server first, then voice, evals, Retell baseline, owner view); design phase closed | docs/tools.md, docs/ai-process/briefs/, docs/ai-process/transcripts/design-session.md |
| Tool server built | Every gate, safety check, priority engine, tool-failure queue, notes and history, logging and trace; 274 tests; safety wording sign-off open in #24 | pull request 25, brief 02 |
| Safety wording signed off | Caller told to hang up and call 911 from outside; no offer to stay on the line, per the gas utility | DECISIONS.md 009, #24 |
| Owner view and jobs built | Dashboard, earlier-slot offers, callbacks, queued-booking retries, retention; callback-done question open in #26 | pull request 28, brief 06 |
| Eval suite built | Harness, tiered scenarios, grader, suites by tag, delay comparison, replay, ratchet; pass-rate floor question open in #27 | pull requests 29 and 34, brief 04 |
| LiveKit voice agent built | Worker, prompts, phone wiring and deploy; a real call to (646) 624-0418 booked a job; reply delay 2.7 s against 1.2 s, being worked in #30; prompt fixes from the eval smoke findings (always check the ZIP, record a commercial lead without asking, no callback time the server didn't give, stop after goodbye) landed in the same pull request | pull request 31, brief 03 |
| Retell baseline built | Same tools, prompts and traces on the 678 number; a real call booked and a gas call held the safety gate; speed-extras question open in #32 | pull requests 33 and 35, brief 05 |
| Callback done read from ServiceTitan | Only an outbound call logged as resolved marks a callback done; missed calls, voicemail or the wrong person leave it open | DECISIONS.md 010, #26 |
| Mock and adapter on ServiceTitan's real shapes | Job and appointment statuses, flat capacity frames with named technicians, assignments through dispatch, contacts as their own call, lead, booking and call field names, the real error body, 200 on creates; Summit Air's own rules moved out of the mock; callback outcome recorded as call tags | exploration 006, servicetitan_mock/README.md, docs/decisions/26-callback-done.md |
| Eval floor waits for the real prompts | Set from the first standard-suite run on the real prompts, rounded down to the nearest 5%, ratchet up only | DECISIONS.md 011, #27 |
| Server fixes from the build review | Read-back names the address as the caller's home; known out-of-area callers counted at start; asked-about ZIPs left out of demand when the call booked; ages however they're said | pull request 36 |
| Reply delay cause found | Thinking was never switched off on the live model (the LiveKit plugin dropped the setting); first token 1,326 ms to 565 ms offline | pull request 38, #30 |
| Eval cleanup and LangSmith | Retell adapter behind the stack boundary, mock reset per Retell scenario, per-check scores and run metadata in LangSmith; smoke 7 of 8 on the real prompts | pull request 37 |
| Priority from temperature brackets | Inside and outside temperature set the starting level; risk read broadly, two risk factors together urgent; outside temperature from the National Weather Service | pull request 39, spec r5, exploration 007 |
| Server monitor live, deploy bug fixed | Healthchecks.io watches the watchdog's pings; every deploy had been installing an empty crontab, now fixed and tested | pull request 41 |
| Retell bake-off setup | Same brain for the 10 calls, about 3 more with fast fallback; Retell has no fast tier for Haiku 5.5 | DECISIONS.md 012, #32 |
| Owner view redesign started | Jackson is redesigning it in a separate session: the first version was cluttered, generic and read like a debugging log, with the wrong level of detail for an owner | app/README.md |
| Mock matched to ServiceTitan's real API; callbacks done by call tags | Field names, statuses, capacity, telecom calls and errors follow ServiceTitan's published specs; a callback is done only when a call back is tagged resolved; company rules moved out of the mock | pull request 46, DECISIONS.md 010, exploration 006 |
| Callback opening asks for the caller by name | A yes to the name confirms the person, so a handled system callback can count as resolved; the plain line stays when no single name is on file | DECISIONS.md 013, #49 |
| Owner view stories | Rebuilt for a non-technical owner: ten stories on three pages (Today, Call, Results), technical detail behind a toggle | DECISIONS.md 014, #40 |
| Owner view design direction | Day timeline mixed with the dispatch desk tables; two versions drawn to choose between | DECISIONS.md 015, #45 |
| Owner view: dispatch board | Version A chosen; decluttered board, no metric cards, filters, Public Sans, a full design system | DECISIONS.md 016, #53 |
| Owner view markup round | Notes on the built pages: figures for the sentence, row links, one tag per call, the call as a graph, per-step details and a log, header search | DECISIONS.md 017, #53 |
| Owner view second markup round | Indexed search with suggestions, technical details as a switch, chart-first Results with a custom range, resizable Jobs columns | DECISIONS.md 018, #53 |
| Ideas round after phase 2 | Four ideas that came from using the built agent were researched and folded back into the design before phase 3 testing, while a design change still cost little and before the eval floor is set | docs/ai-process/ideas-round-questions.md, explorations 009 to 011 |
| Older adult at 60 | Priority's older-adult line moves from 65 (CDC) to 60 to match NYC Health | DECISIONS.md 019 |
| Vapi in the bake-off | A written comparison plus one test call on whether a tool result is spoken word for word | DECISIONS.md 020, #57 |
| Templates by trade and company | A `trades/hvac/` layer each tenant folder extends, kept in the repo rather than in Vapi | DECISIONS.md 021, #57 |
| Rules by call stage | A rulebook the owner reads by stage; guidance and four lines the owner edits behind the must-pass evals; no screen loosens a gate | DECISIONS.md 022, #56 |
| Local names | One tenant list of 15 names feeds Cartesia's pronunciation and Deepgram's keyterms; the read-back keeps the written form | DECISIONS.md 023, #58 |
| Carbon monoxide check for unsafe heating | Asked once when the caller heats with an oven, generator or similar; never suggested as a way to get warm | DECISIONS.md 024 |
| Who tests | Jackson makes the real calls; non-Anthropic callers and graders are allowed in a limited scope | DECISIONS.md 025 |
| More bake-off entrants | ElevenLabs Agents on our brain, OpenAI Realtime and Gemini Live, scored on a simulated audio rig before any real call | DECISIONS.md 026, #62 |
| Trade layer merge rules | Tenant wins key by key, rules merge by id, lists only grow; a tenant that drops safety wording or lowers a trade rule fails to load | DECISIONS.md 027, #63 |
| On-call before after-hours booking | A cold house after hours alerts the on-call technician before the morning fallback is booked | DECISIONS.md 028, #64 |
| Softer safety sign-off | "For your safety, I'd recommend hanging up now, getting outside, and calling 911 from there" | DECISIONS.md 029, #64 |
| Unsafe heat advice | Kept as a prompt rule with evals, since the server sees agent lines only after they are spoken | DECISIONS.md 030, #64 |
| Publish gate rule | Code checks only, one rerun of a miss, both runs kept; never relative to the live version | DECISIONS.md 031, #65 |
| Owner guidance limits | Four stages, locked subjects refused, AI disclosure kept, booking and closing guidance only when the server reaches the stage | DECISIONS.md 032, #65 |
| Safety posture | Escalate politely and get off the line; no on-the-fly coaching or new safety logic | DECISIONS.md 033 |
| V1 scope for phase 3 | Technician summary, model matrix, side model, warm transfer, recordings, eval floor and delay targets in; Spanish, playbooks, capacity, texting and extra owner editing after V1 | DECISIONS.md 034 |
| Co-authoring rounds | Three rounds: failures, then seeds with a hold-out, then judge labels with the floor | DECISIONS.md 035 |
| Model matrix | Haiku stays live: higher pass rate, a third of Sonnet's delay, a fraction of the cost | DECISIONS.md 036, #68 |
| Side model | OpenAI Decisions reads "someone at risk at home" on each line; it can only raise priority | DECISIONS.md 037, #68 |
| Audio bake-off round 1 | Our stack stays; Retell, Gemini Live and OpenAI Realtime out on the safety lines or speed; ElevenLabs needs a read-back fix; our end-of-turn wait gets tuned | DECISIONS.md 038, #70 |
| AI check for owner guidance | A model refuses a note only when it contradicts a locked rule, with the reason; no word list | DECISIONS.md 039, #71 |
| Technician summary | A pinned job note drafted by a model and checked line by line by the server | DECISIONS.md 040, #73 |
| Eval floor | 90%, from a 94% reference run once must-pass scenarios passed 3 of 3 | DECISIONS.md 041, #72 |
| Must-pass and the grader | Must-pass counts code checks; the grader sees what the server set up | DECISIONS.md 042, #72 |
| Eval hash scope | Only what the agent hears on a call | DECISIONS.md 043, #72 |
| Residential by equipment | Apartment buildings and their managers are residential; businesses are commercial | DECISIONS.md 044, #74 |
| Approval callback | Unclear authority gets a callback; a requested booking gets a call before dispatch | DECISIONS.md 045, #74 |
| Booking on a safety call | Lines and flag first, then one offer of a visit for after it's shut off | DECISIONS.md 046, #74 |
| Burning dust | One lighter check question asked the negative way | DECISIONS.md 047, #74 |
