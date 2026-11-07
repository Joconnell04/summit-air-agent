# 009. Rules by call stage that the owner can view and edit

Issue: #56. Consideration: C-56. Status: first draft, for `/discuss 009`. Numbers carry SOURCE (link), MEASURED (how) or ASSUMED.

## 1. Question

Could the owner see every rule the agent follows, sorted by where in the call it applies (before hello, opening, whole call, booking, closing, after the call), and change some of them from the owner view, without being able to loosen anything that must happen? Which rules are safe to edit, how are edits stored, tested and undone, and what does the owner view need from the server? (The owner view's look is issue #53; this file covers data and endpoints only.)

## 2. Why it matters to Summit Air

The brief names the owner's worries as a booked commercial job, a missed gas smell, a quoted price and a promised slot that does not exist (docs/users.md:127). The owner answering "can I change what it does?" is the interview question; the build rule is that "a flag the model can set is not a gate" (CLAUDE.md; lesson from Bobert, an earlier personal practice project, [docs/history/from-other-projects/bobert](../history/from-other-projects/bobert/)). Spec S-46 already puts company facts in `tenants/summit-air/`; this adds who may edit them and how.

### 2a. What the agent follows today

Kinds: **G** hard gate in server code; **W** exact wording line in tenant config; **P** number or list in tenant config; **S** soft guidance in a prompt file; **J** background job. Paths are under `app/`, `tenants/summit-air/` (`agent.yaml`, `config.yaml`) or `prompts/`.

| # | Stage | Rule | Kind | Where |
|---|---|---|---|---|
| 1 | Before hello | Look up caller by caller ID before the model speaks; a dropped safety call resumes in its safety state | G | lifecycle.py:112-142 |
| 2 | Before hello | Dialed number picks the tenant; after-hours flag from hours; repeat-caller window (10 min), callback offer after 2 drops | P, W | agent.yaml:6-8, 296-298, 337; config.yaml:21-28 |
| 3 | Opening | Greeting for known and unknown caller; opening of a system callback or earlier-slot call | W | agent.yaml:347-353, 358-359; assemble.py:66-72 |
| 4 | Opening | Say it is an AI if asked; let the caller tell the story first; one question at a time; banned phrases | S | identity.md:3-18; spine.md:5 |
| 5 | Whole call | Safety phrase check on every caller line; one check question for vague wording | G, P lists | safety.py:151; lifecycle.py:197-227; agent.yaml:49-107 |
| 6 | Whole call | Safety lines, shorter variants, check questions, sign-off: fixed wording signed off by a person | W, locked | agent.yaml:36-112; DECISIONS 009 |
| 7 | Whole call | Detectors on caller lines: a business ("my restaurant"), do-not-call phrases | G, P lists | lifecycle.py:22-47, 230-238; agent.yaml:120-130 |
| 8 | Whole call | Priority: start level from temperature brackets; rules can only raise; two risk factors make Urgent | P (already a condition and action menu) | agent.yaml:138-253; priority.py:52-56, 121-145 |
| 9 | Whole call | Per-call limits (1 booking, 1 reschedule, 1 cancel, 5 lookups); tools that cannot delete or read other customers | G, constants not tenant | settings.py:57-63; tools.py:71-81, 1112-1126 |
| 10 | Whole call | No price or fee unless configured; no repair steps; journey guidance (renter, repeat caller, complaint, member, quote) | S (fee: P) | spine.md:22; journeys.md:5-31; config.yaml:32-34 |
| 11 | Whole call | Customer notes shape manner, never rules | S, G | identity.md:20-22; spec.md:42 |
| 12 | Booking | Safety lines said before any booking tool; a safety-call override is logged | G | tools.py:84-97 |
| 13 | Booking | Read-back of address and phone, asked on this call, confirmed by a later caller line | G, W | tools.py:439-481, 497-502; agent.yaml:325-326 |
| 14 | Booking | Out-of-area ZIP refused and counted; commercial refused | G, P, W | tools.py:108-122; config.yaml:46-70; agent.yaml:327-332 |
| 15 | Booking | Technician hold for urgent jobs; hours and arrival windows | P | config.yaml:21-28, 84-104 |
| 16 | Booking | After-hours no-heat or no-cooling outcome from temperature cutoffs | P | agent.yaml:263-273; priority.py:148-168 |
| 17 | Booking | FSM down or booking refused: callback within 60 min with a named time | P, W | agent.yaml:284, 306-309; tools.py:171-185 |
| 18 | Booking | Every callback has a time; the server writes the words | G, W | tools.py:855-896; agent.yaml:317-322 |
| 19 | Booking | Offer the shape of availability, then exact windows | S | spine.md:15 |
| 20 | Closing | No hang-up in the turn that confirms a booking; "anything else?" first | G, W | tools.py:1098-1107; agent.yaml:344 |
| 21 | Closing | A safety call ends with the sign-off, and hangs up only after goodbye | G, W | tools.py:1075-1096; agent.yaml:109-111 |
| 22 | Closing | Goodbye line when the model gives none | W | agent.yaml:361; voice/livekit/worker.py:179-190 |
| 23 | Closing | Offer membership only at the natural end of a routine call | S | journeys.md:27 |
| 24 | After call | Outcome from records; dropped safety call gets an Urgent flag and a callback in 10 min | G, P | lifecycle.py:274-319; agent.yaml:282 |
| 25 | After call | Outbound callbacks 08:00 to 21:00, one attempt, only to the caller, never to do-not-call | G, P | records.py:256-282; agent.yaml:279-280 |
| 26 | After call | Retry queued bookings, earlier-slot offers, callbacks done from the call log, kept check, overdue flags | J | jobs.py:29, 91, 207, 271; records.py:303 |
| 27 | After call | Email on-call about Urgent flags after hours; retention 30 and 365 days | J, P | jobs.py:304, 387; agent.yaml:289-302 |
| 28 | After call | Technician summary written by a model (spec only; code sets `summary_status` and stops) | J | spec.md:43; lifecycle.py:292; models.yaml:85-90 |

What the table shows (MEASURED, hand count of the rows above):
- 13 of 28 rows include a server gate. Only 4 (rows 4, 10, 19, 23) rest on the model following a prompt, and all four are manner or offers. Those are the natural owner-editable rules.
- Two edits can break a gate without touching code. The read-back sentence names the address as a home, which is how every booking confirms residential (agent.yaml:323-326), so a reworded line can drop the check. Priority brackets set the start level (priority.py:121-127), so "rules only raise" does not stop a bracket edited downward.
- A client has no home for its own soft rules: prompt files carry no company facts (prompts/README.md:16) and `knowledge/*.md` is appended untagged (assemble.py:30-33).
- The eval run hash covers prompts, scenarios and model profiles, not the tenant folder (evals/fingerprint.py:35-42), so a tenant edit would not make a committed run stale.
- The owner view has one shared read token (settings.py:37-38). Edit rights need a separate credential.

### 2b. How others let non-engineers edit behavior

All SOURCE. "Not documented" means I looked and found nothing.

| Product | What an operator edits | Versions, testing, rollback |
|---|---|---|
| Decagon AOPs | Plain-language procedures (free text); guardrails, integrations and critical actions stay in code: "code-based logic ensures that critical actions are executed correctly" | Every edit a versioned commit in workspaces, diffs, rollback, optional approval; unit tests and simulations before publish (2025-11-05) |
| Sierra | Journeys built from typed blocks; SDK for code | Private workspaces, auto-versioned, numbered snapshots QA to staging to production, instant rollback (2025-11-05); guardrails not detailed |
| Intercom Fin | Guidance (free text in categories: tone, clarification, handover) and Procedures (plain language plus conditions and code) | "Default escalation cannot be overridden by a procedure"; Fin Operator drafts a rule, checks conflicts, live only on approval; simulations advised before every publish, not shown to block it; versioning not documented |
| Retell | Global prompt, node prompts, edges, tools | Published versions read-only; revert by a draft from an old version or moving a `prod` tag; no test gate documented |
| Vapi Workflows | Nodes and edge conditions | Its docs say Workflows retires 2026-08-18 for Assistants and Squads, so already gone: a vendor's rule format can vanish |
| Bland Pathways | Nodes, global prompt, global nodes | Edits on a fork until publish, review of proposed changes (2026-02-16), node-level tests (2025-12-01), canary on Enterprise |
| Parloa | Role, instructions, limits in plain language | Simulation before launch; versioning not found |
| Avoca (HVAC) | Free-text Emergency Guidance; typed per-date booking windows (No Booking, Regular, Emergency Only) | Version history to compare with last known good; no approval step; "test one qualifying and one non-qualifying scenario" |
| ServiceTitan voice agent | Opening and closing messages, up to three follow-up questions per issue type (free text, 300 to 1,000 characters); typed per-job-type book, reschedule and cancel toggles | Live preview only; four fixed escalation categories; a customized account stops following global changes |
| Revin | Not documented publicly; press says agents are "configured to" a brand's rules | Unknown |

Patterns: everyone splits free-text instruction from code-held critical actions (Decagon and Fin are closest to our constraint); enterprise tools version and roll back, the two home-services products document less; none documents a publish block on a failed must-pass test, so ours would be stricter; ServiceTitan's copy-on-customize cuts an account off from platform fixes, the trap for layering.

## 3. Options

Each option includes the one before. Effort is ASSUMED engineer days.

**A. Read-only rulebook.** A registry (`app/rulebook.py`) lists each rule with id, stage, kind, spec line, source and a getter for its current value; a test fails if a spec **M** line (9, MEASURED, `**M**` count in docs/spec.md), a top-level `agent.yaml` key or a prompt section is missing. The owner view reads it and marks each row locked or editable with the reason. No storage, no gate. 1.5 days; risk low (drift, caught by the test). Changes still go through `/round`.

**B. Typed edits to existing values and wording.** A SQLite table `rule_versions` (tenant, rule id, value JSON, status, author, reason, parent version, gate run, time), append-only. YAML stays the shipped default; `/calls/start` overlays the live rows for editable ids, and the call records the version it ran under so a bad call traces to an edit. Each field has a schema and a direction: numbers and brackets are tighten-only (priority up, urgent hold up, outbound hours narrower); wording keeps its placeholders and key words (the read-back keeps `{address}`, `{phone}` and "home"). Locked: rows 5 to 7, 9, 12 to 14, 20, 21, 24, 25. Publish gate: smoke suite plus every must-pass scenario on a scratch copy with the draft merged; any must-pass miss blocks, and the pass rate may not fall below the adopted floor (DECISIONS 011). Smoke is about $0.15 (MEASURED, evals/README.md), the 30-scenario standard suite $0.64 (MEASURED, evals/rounds/01-real-failures.md:5). Rollback is a new row pointing at an older version; nothing is deleted. About 4 days; risk medium (a validator per field; the run hash must include the overlay).

**C. B plus stage-tagged soft guidance.** The owner adds short free-text rules tagged opening, whole call, booking, closing or after the call, such as "ask about pets before closing" (at most 5 per stage and 300 characters, ASSUMED). Same table. `assemble.instructions` adds a "House guidance" block after `journeys.md`, grouped by stage, under a fixed preamble that it is soft and never overrides the rules above or a tool's answer. Both stacks get it, since Retell's prompt is one `{{instructions}}` variable filled per call (voice/retell/build.py:107-115). The stage comes from server state (first turn, booking confirmed, `end_call` asked), never the model; if evals show closing rules forgotten, the server re-sends them as a `[server]` note when the stage starts, a path both stacks already run (call.py:75, bridge.py:295). "After the call" guidance feeds the technician summary prompt, the lowest-risk place. The UI says "soft: followed most of the time, cannot override locked rules". A draft is also checked for conflicts by the existing grader profile (Fin Operator's idea; ASSUMED under $0.05 a check). Limit: fixed scenarios cannot exercise a new rule, so the gate proves no regression, not that the rule works; the owner gets a preview call. About 3 days; token cost negligible (about 800 tokens for 600 words, ASSUMED, at $0.10 per million input, SOURCE config/models.yaml:21); risk medium (contradictory or promising rules, the warning in Intercom's guidance advice; a stolen edit token becomes injection into every call).

**D. Condition and action rules from a fixed menu.** Generalize the priority engine (row 8) to triggers (phrase said, job type, after hours, member, ZIP list, stage reached) and actions (raise priority, flag a person, add a technician note, add guidance, say a fixed line at opening or closing). The menu has no action that lowers priority, books, skips a check or suppresses a line, so nothing loosens a gate by construction. Evaluated in `lifecycle.turn` beside the detectors. 8 to 12 days; risk high (a rule engine in a build that asks for no clever abstractions, and rule interactions to test).

**Layering with templates (exploration 010).** Each rule gets `layer` (platform, trade, client) and `locked`. Platform rules (gates, limits, only-raise engine, least privilege) are locked for everyone. Trade rules (HVAC safety wording and phrases, no-heat brackets, journeys) are defaults a client can tighten, not remove. Client rules (hours, ZIPs, hold, wording, guidance) are all the owner edits. Resolve per rule, not per account, so platform fixes still reach customized clients. Safety `signed_off` stays per client (DECISIONS 009).

**By stage, owner may edit:** before hello, nothing (no model); opening and closing, wording lines and guidance; whole call and booking, guidance and tighten-only numbers; after the call, summary guidance and job numbers.

**Owner view needs:** `GET /owner/rules` (stage, kind, layer, lock reason, value, default, version, author, last gate run); `GET /owner/rules/{id}/history`; `PUT /owner/rules/{id}` saves a draft and returns validation; `POST /owner/rules` adds guidance; `POST /owner/rules/{id}/publish` starts the gate, `GET /owner/rules/gate/{job}` reports it; `POST /owner/rules/{id}/rollback`; and an edit token separate from the read token, with each change emailed to a named person.

## 4. Recommendation, and what would change it

Build A now: it is cheap, it is the interview exhibit, and B to D need its registry. Then build C with only the opening and closing wording lines from B (greeting, callback opening, anything-else, goodbye): about 6.5 days in all (ASSUMED). Leave editable numbers and D in `docs/later.md`. The literal idea (rules tagged intro, closer, full call) is C; no screen reaches a gate; the gate reuses the harness; and DECISIONS 008 says the owner view shrinks first, so A is the floor and C the stretch.

Would change it: Revin says owners never self-edit, stop at A. A guidance rule moves the standard pass rate more than the 5% rounding band (DECISIONS 011) in a trial, keep guidance engineer-only. Summit Air asks to change hours, hold or thresholds, do B in full. Three or more asks need "if the caller says X then Y", do D. Retell winning the bake-off changes nothing, because rules stay in our server and assembly; Retell's flow editor would bypass our gates and audit.

**For the Revin interview.** "Yes, in two ways and not a third. The owner sees every rule the agent follows, sorted by where in the call it applies, and each says whether it is locked. They can reword the opening and closing lines and add short guidance for any stage, like 'ask about pets before closing'. Every change is a new version, has to pass our safety tests before it goes live, and rolls back in one click. A screen cannot change anything that protects a caller or the business: the gas and carbon monoxide script, the read-back before booking, residential only, the service area, and priority only going up. Those are enforced in server code, so a badly written rule still cannot make the agent skip them." If asked what is live: the owner view shows them read-only today; editing is designed, not built.

## 5. Open questions for Jackson

1. **How far in V1?** (a) A only; (b) A, then C with four wording lines; (c) all of B and C. Recommend (b), A shipping first.
2. **Who edits and publishes?** (a) the existing owner token; (b) a separate edit login, the owner publishes once the gate passes; (c) the owner drafts and you approve. Recommend (b); in the demo Paul plays the owner and (c) adds a wait.
3. **Can any screen loosen a gate?** (a) never, only engineers through `/round`; (b) a second-person approval path. Recommend (a); a request like small commercial jobs is a business decision, not a setting.
4. **Where do edits live?** (a) YAML through git; (b) SQLite overlay on YAML defaults. Recommend (b); the server cannot commit, and the table is the audit trail and rollback.
5. **Show an edit live in the demo?** (a) live, gate running (minutes of silence, ASSUMED); (b) a pre-run result and a version already in history; (c) skip it. Recommend (b); nobody should wait awkwardly.
6. **Who decides the stage?** (a) server state only; (b) the model reports it. Recommend (a); a flag the model sets is not a gate.

## 6. Sources

Read in full unless marked snippet. No paid fallback (Exa, Firecrawl) was used. docs.avoca.ai/roadmap failed on connection; the Avoca help page was used.

- Decagon, Agent Versioning, 2025-11-05: https://decagon.ai/blog/decagon-agent-versioning
- Decagon, From SOPs to Agent Operating Procedures (undated): https://decagon.ai/resources/from-sops-to-agent-operating-procedures
- Sierra, Agent Studio 2.0, 2025-11-05: https://sierra.ai/blog/agent-studio-2-0
- Intercom Fin, Procedures vs AOPs vs Journeys (competitor-authored, undated): https://fin.ai/learn/ai-agent-procedures-aops-journeys
- Intercom, Guidance vs Procedures; Fin Operator rules; simulations (undated): https://www.intercom.com/help/en/articles/14623785-when-to-use-fin-guidance-vs-procedures ; https://www.intercom.com/help/en/articles/14707474-use-fin-operator-for-guidance-rules ; https://www.intercom.com/help/en/articles/12599517-run-simulations-for-fin-procedures
- Intercom, guidance best practices, contradictory rules (snippet, undated): https://www.intercom.com/help/en/articles/10560969-fin-guidance-best-practices-writing-effective-ai-prompts
- Retell, conversation flow and agent versions (undated): https://docs.retellai.com/build/conversation-flow/overview ; https://docs.retellai.com/agent/version
- Vapi, Workflows overview, retirement notice: https://docs.vapi.ai/workflows/overview
- Bland, changelog 2026-02-16: https://docs.bland.ai/changelog/02_16_2026 ; node-level testing 2025-12-01 (snippet): https://www.bland.ai/changelog/2025-12-01-node-level-testing
- Parloa (marketing and third-party summaries, snippets, unverified): https://www.parloa.com/
- Avoca, defining an emergency (undated): https://help.avoca.ai/How-to-define-what-an-emergency-is-in-Avoca-2f0f2b56d4d580929428fce842c30cb9
- ServiceTitan voice agent settings, account level and global (undated): https://help.servicetitan.com/docs/ccp-configure-voice-agent-account-level-settings-in-contact-center-pro.md ; https://help.servicetitan.com/docs/ccp-configure-voice-agent-global-settings-in-contact-center-pro.md
- Revin press, no editing docs found (snippet): https://techintelpro.com/news/ai/ai-assistants/revin-partners-with-vertex-to-automate-home-services-with-ai-agents
- Repo: docs/spec.md, docs/architecture.md, docs/template.md, DECISIONS.md, evals/README.md, evals/rounds/01-real-failures.md, config/models.yaml.
