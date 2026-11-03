# Decisions

One entry per real choice, newest first. Each entry: what was decided, why, what else was considered, what would change it, and a link to the discussion or exploration behind it.

<!-- Template
## NNN. Title
Decided: ...
Why: ...
Considered: ...
Would change if: ...
Discussion: docs/ai-process/transcripts/... or docs/explorations/...
Issue: #N
-->

## 016. The owner view is the dispatch board, on a full design system

Decided: version A of the timeline and dispatch desk mix (the day across the top, the tables below), with five changes Jackson asked for: a less cluttered board, no row of metric cards, filters on the board and on Results, a better font than Inter, and a full design system the pages are built from. Public Sans replaces Inter: it comes from Franklin Gothic, one of GT America's two parents, and Google's file has tabular figures (MEASURED: its GSUB table lists `tnum`).
Why: the dispatch board reads like the board a ServiceTitan dispatcher already uses, and version B pushed the jobs table down the page. A design system keeps three pages and any later one consistent, and is what a reviewer reads to see the choices were made on purpose.
Considered: version B (the calendar day); Schibsted Grotesk, Instrument Sans and Archivo as the font (all have tabular figures; Schibsted spaces punctuation oddly when tabular figures are on everywhere).
Would change if: Revin licenses GT America for the product, in which case the type tokens point at it and nothing else changes.
Discussion: docs/decisions/45-owner-view-design-direction.md
Issue: #53

## 015. The owner view is a day timeline with the dispatch desk's tables

Decided: direction 5 (Day timeline) mixed with direction 1 (Dispatch desk). The day is drawn on a timeline like a dispatch board, and the dispatch desk's number tiles, "Needs attention" table and jobs table carry the detail. Two versions of the mix go on the design canvas; the choice between them comes next.
Why: the timeline shows the shape of the day at a glance, which a table of rows doesn't, and the dispatch desk's tables are what an owner who uses ServiceTitan reads fastest. Together they cover both the glance and the follow-up.
Considered: developing directions 1 and 3 (Dispatch desk and Work queue), which the readout recommended; the shift report, big board and work queue on their own.
Would change if: the timeline proves too hard to read on a phone, in which case the phone layout falls back to the dispatch desk tables.
Discussion: docs/decisions/45-owner-view-design-direction.md
Issue: #45

## 014. The owner view is rebuilt from ten stories for a non-technical owner

Decided: option A. Ten stories on three pages: Today (morning check, emergencies first, the team's to-do list, what the agent put on the schedule, whether it is answering now), Call (read or hear the call, why it did each thing in plain words, tell Revin something was wrong) and Results (is it paying off, what it turned away). Tool names, JSON and ids move behind a technical details toggle.
Why: the brief 06 page was built for a software engineer; the owner knows HVAC, not logs. Each owner worry in docs/users.md gets a place on screen, and the "tell Revin" note shows how the agent improves.
Considered: a lean seven-story cut without the status line, recordings or the note; adding owner settings (service area, on-call person, hours), which would end the read-only page.
Would change if: build time runs short, in which case call recordings are cut first.
Discussion: docs/decisions/40-owner-view-stories.md
Issue: #40

## 013. A callback the system places opens by asking for the caller by name

Decided: option A. The opening is "Hi, this is Summit Air calling back for {first_name}. Is that you?", with the first name of the customer on the number being called, the same match the placed call makes. With no name on that number, two customers with different first names, or ServiceTitan unreachable, it keeps the plain line.
Why: a callback is resolved only when the right person is confirmed on the call (010). The plain opening never checked who answered, so most system callbacks would have been tagged open and handed to a person. A short yes to the name is the confirmation the server already accepts, and it mirrors the inbound greeting for a known caller.
Considered: keep the line and confirm later in the call; leave it and let a person close most callbacks.
Would change if: callers find the name question off-putting on live calls, in which case the check moves after the reason for the call.
Discussion: docs/decisions/49-callback-opening-name.md
Issue: #49

## 012. Retell's bake-off calls run on the same brain, with a few on fast fallback

Decided: option C. The 10 Retell bake-off calls use the same setup as our stack (Haiku 5.5, standard capacity, no backup), and about 3 more run with Retell's fast fallback on, reported separately. Retell offers no faster tier for Claude 5.5 Haiku, so fast fallback is the only extra.
Why: the 10 calls keep the bake-off fair under its own rule (keep our stack unless Retell is more than 0.3 s faster), and the few extra calls answer "did you give Retell a fair shot?". The extras cost little: about $0.20 to $0.24 a two-minute call against $0.19 (readout). They don't apply to chat, so they don't change a suite run.
Considered: the same brain only; Retell's extras on every call.
Would change if: Retell adds a fast tier for Haiku 5.5, in which case the variant calls use it too.
Discussion: docs/decisions/32-retell-speed-extras.md
Issue: #32

## 011. The eval pass-rate floor comes from the first standard run on the real prompts

Decided: option C, with the floor set only after the standard suite has run on the real prompts. That run is adopted as the reference and the floor is its pass rate rounded down to the nearest 5%; only a later adopted run can raise it. Until then the ratchet reports that no reference run is adopted, and must-pass cases, stale results and fewer scenarios still fail the check.
Why: the two smoke runs used the harness's stand-in prompt, so a floor from them would describe a different agent. Rounding down absorbs one or two noisy calls, and safety is held by the must-pass rule whatever the number is.
Considered: no floor (a slow slide on ordinary calls would go unnoticed); a fixed 90% (one flaky call in about 30 blocks a merge).
Would change if: repeat runs of the standard suite turn out noisy, in which case the floor waits until three samples per scenario are affordable.
Discussion: docs/decisions/27-eval-pass-rate-floor.md
Issue: #27

## 010. A callback counts as done only when ServiceTitan logs a call back that resolved it

Decided: option B, with an outcome on every call log entry. A background job marks a promised callback done when ServiceTitan's call log shows an outbound call to that number after the promise whose outcome is resolved. Every call log entry carries an outcome: resolved, still open (the caller needs more, or the wrong person answered) or no contact (no answer, voicemail, failed). When the system places the callback itself, the server writes the outcome at the end of the call: resolved only if the right person answered and the reason for the callback was handled; anything else leaves the callback open. When the office calls back, the person records the outcome in ServiceTitan as they would a call disposition. A failed call, a voicemail or the wrong person answering can never mark a callback done.
Built as: ServiceTitan has no outcome field on a call (its call type is empty for outbound calls, and its phone system creates the call), so the outcome is a call tag: three tag types per tenant ("Callback resolved", "Callback open", "No contact", named in `tenants/<company>/config.yaml` and `agent.yaml`). The office puts one on a call in ServiceTitan as it would dispose any call; the server puts one on the callbacks it places, resolved only when a person answered, the person who asked was confirmed (the read-back or a name check) and the reason was handled (a booking, reschedule or cancel went through, or the agent ended the call as resolved and the server saw nothing against it). The callbacks job marks a callback done from the call log. Detail in docs/decisions/26-callback-done.md.
Why: the owner view stays read-only and counts calls that actually happened and resolved something, which is what an owner means by "do callbacks happen". Without the outcome, any call to the number, including a missed one, would count.
Considered: a Done button on the owner view (counts clicks, and the page stops being read-only); leaving it for later (every person callback reads as overdue).
Would change if: Summit Air's office calls back from cell phones that ServiceTitan doesn't log, in which case the Done button is added as the fallback.
Discussion: docs/decisions/26-callback-done.md
Issue: #26

## 009. Safety lines signed off, with the caller told to hang up

Decided: option B. The gas and carbon monoxide lines, their shorter variants and check questions are signed off as written, with one change: the sign-off tells the caller to hang up and call 911 from outside, and no longer offers to stay on the line. `tenants/summit-air/agent.yaml` has `safety.signed_off: true`.
Why: it was the one place the wording disagreed with the gas utility's guidance for Brooklyn, Queens and Staten Island (National Grid: don't use anything electrical inside, including a phone). Everything else already matched Con Edison, National Grid and NYC Health (exploration 002).
Considered: signing off as written (keeps "I'll stay on if you need me"); rewriting through a round first.
Would change if: a real Summit Air reviews the wording with their own safety officer or utility contact.
Discussion: docs/decisions/24-safety-lines-signoff.md
Issue: #24

## 008. What V1 builds: the triage of every consideration

Decided: option A of the triage, with Jackson's changes. In: everything that touches the six requirements, safety, call quality, the template, least privilege, privacy (a real retention policy, with full logs kept in development) and success measures (tracked by the system and shown on the owner view for the meeting). Later: languages, decision models, playbooks, warm transfer and texts (docs/later.md). Out: a separate surge mode, because high call volume is handled by the scaling plan (docs/scaling.md) rather than a special mode. The aim is to scaffold the whole system first and come back to the later items once it works end to end.
Why: the demo is judged on the requirements, how the agent handles the unexpected, and how the cuts are explained; the later items become the rollout story.
Considered: a leaner cut that also moved the owner view and repeat callers to later.
Would change if: the build runs short, in which case the owner view is the first to shrink.
Discussion: docs/decisions/10-considerations-triage.md
Issue: #10

## 007. No texting in V1

Decided: the agent confirms bookings out loud on the call and the owner view shows them; no confirmation texts in V1, and no business texting registration.
Why: the assignment asks the agent to "book the appointment or confirm next steps with the caller", not to text. US business texting needs carrier registration that takes 1 to 3 weeks, and the only sole proprietor brand Jackson can hold is already used for unrelated client work. Calls need no registration, so the phone number is unaffected.
Considered: a sole proprietor registration for this demo (blocked by the one-brand limit), a second Twilio account, a texts-only toll-free number.
Would change if: a real customer goes live, in which case they register their own business brand with their tax ID and texting is switched on. The public terms and privacy pages stay up for that (docs/decisions/15-texting-registration.md).
Discussion: docs/decisions/15-texting-registration.md
Issue: #15

## 006. Three separate steps, a right-sized model per job, no side model at launch

Decided: calls run as three separate steps (Deepgram hears, a Claude model decides, Cartesia speaks), with the safety lines spoken from code as fixed text. Each job gets its own model size: a small fast model (Haiku) for live turns, a mid-size model (Sonnet) for after-call summaries, a large model (Opus) only offline as the eval grader; nothing large in the live path. Thinking stays off on every live profile and is on where nobody is waiting: the after-call technician summary and the eval grader (added after the latency review). Anthropic is the default provider and OpenAI and Gemini profiles can be switched in by config. The server is the brain's harness: lookups before hello, about six small tools, cached slots, gates, time limits, idempotent bookings and least-privilege checks on every write. No side model at launch, with a slot built for one; a decision model is added only where a measured bottleneck appears and a bake-off shows it fixes it.
Why: separate steps let the server read every caller line before the agent answers, keep the safety wording exact, and swap or right-size each part. Speech-to-speech is not faster: published first-reply times for OpenAI Realtime and Gemini Live are 0.96 to 1.14 s (SOURCE, in the readout) against about 1.0 s measured for this design on the earlier practice agent, and it costs more and can't guarantee fixed wording.
Considered: one speech-to-speech model (OpenAI Realtime, Gemini Live); a hybrid where a speech-to-speech model talks and a text pipeline checks (two of everything, more ways to fail, and the check still trails the reply); a second Haiku call beside each turn; dedicated decision models (Jev, a post-trained Laya, OpenAI Decisions).
Would change if: the small speech-to-speech bake-off matches this design on tool success and the safety gates; or the side-model bake-off finds a bottleneck a decision model fixes (docs/later.md tracks adding one in at least one place).
Discussion: docs/decisions/05-pipeline-and-models.md, docs/bakeoffs.md, evals/decision-models/PLAN.md
Issue: #5

## 005. A new Vultr server in New Jersey, just for Summit Air

Decided: run the tool server and the voice program on a new Vultr server (2 vCPU, 4 GB, New Jersey, Ubuntu 24.04, $20 a month billed hourly), not on the existing shared server.
Why: the shared server runs Epic's client services, is too small for 5 calls at once, gives us no admin rights (so it needed an ngrok tunnel) and sits in Atlanta. The new one is the cheapest option in the New York area, uses the existing Vultr account, isolates Summit Air from client work, and can be deleted after the interview.
Considered: keeping or resizing the shared server; DigitalOcean (New York) and Akamai Linode (Newark) at about $24 a month; Hetzner (prices unclear after its 2026 increases, not near New York); usage-billed platforms such as Fly.io (no saving for an always-on program).
Would change if: call volume needs more than about 5 calls at once (resize, or add a second server as a hot spare).
Discussion: docs/decisions/16-server.md
Issue: #16

## 004. Build our own LiveKit stack, with Retell as the baseline it has to beat

Decided: option D. The product is our own stack: Deepgram, a Claude model and Cartesia, run by our program on our own server, with LiveKit's free plan connecting the phone line. A quick Retell setup on the existing 678 number is the baseline. Two numbers exist only so both can be called during the bake-off: 678 rings Retell, (646) 624-0418 rings our stack through a Twilio Elastic SIP Trunk. Both are scored on the same eval scenarios plus 10 real phone calls each; afterwards there is one number, the winner keeps it, and the other voice folder is deleted.
Why: our own stack lets server code decide every hang-up and transfer (Retell's tools can't end a call or veto its built-in transfer), lets each part be swapped or right-sized, and appears to match how Revin's voice team builds. Retell is the strongest managed option, so beating it on real calls is the evidence; it costs a few hours because the tool server, prompts and evals are shared.
Considered: LiveKit only (no evidence it beats a managed platform), Retell only (polished, but our code can't stop a hang-up and we can only turn its dials), Retell with our own model through its custom LLM mode (keeps Retell's turn-taking but still tied to Retell), Vapi (the same trade as Retell with no head start) and Pipecat (the same trade as LiveKit, with less sign Revin uses it).
Would change if: the Retell baseline beats our stack on turn-taking or reply speed on real phone calls, in which case Retell, possibly in custom LLM mode, becomes the product.
Discussion: docs/decisions/04-voice-platform.md, docs/explorations/003-capacity-and-cost.md, docs/explorations/004-call-control-and-numbers.md
Issue: #4

## 003. The mock sells technician-windows, not counters
Decided: In the mock ServiceTitan, a slot is one technician in one arrival window on one day. Open capacity is technicians on shift with the right skills, minus those booked, minus a few held for urgent jobs. A database constraint allows one live appointment per technician per window.
Why: A counter can go wrong under parallel requests; a unique row cannot. It also gives skill matching, an on-call technician and a real technician to put in the job.
Considered: A capacity table with a count per window and business unit (simpler, but needs extra locking to be safe).
Would change if: Real ServiceTitan capacity turns out to be count-based and the adapter needs the same shape from the mock.
Discussion: docs/ai-process/briefs/01-mock-servicetitan.md
Issue: #13

## 002. Test with our own harness, scored by code first

Decided: our own pytest harness. A simulated caller talks in text to the real prompts and tool server on a scratch database. Code checks on the tool-call log and saved records decide pass or fail; an LLM grader scores conversation quality and can fail a run the code passed, never rescue one it failed. About 10 real audio calls are added once the voice platform is chosen. A quick run is about 8 scenarios (about $1, ASSUMED); the full suite runs only at a release gate (about $15, ASSUMED).
Why: the rules that matter (safety lines before anything else, read-back before booking, no commercial bookings) live in server code, so only a harness that reads server state can prove them. The earlier practice agent's worst bugs were ordering bugs that a transcript judge misses and a tool-log check catches exactly.
Considered: the voice platform's own simulator (Retell's can't check tool arguments or the database; LiveKit's only works if LiveKit wins), a general eval framework such as promptfoo or Braintrust (adds a dependency without removing the harness), hosted voice testing such as Cekura, Coval or Hamming (judges from outside the call, unpublished pricing).
Would change if: LiveKit wins the platform decision and our harness grows past about 500 lines, in which case LiveKit's test helpers take over the per-turn checks.
Updated after Jackson's review: the same harness scores both voice stacks (Retell through its text chat interface, not its own simulator), scenarios are realistic (stutters, corrections, transcription errors) and labelled by flow and difficulty so suites are chosen by tag, and LangGraph and Langflow were considered and not used (docs/decisions/06-eval-design.md).
Discussion: docs/decisions/06-eval-design.md
Issue: #6


