# 010. Vapi: a third bake-off entrant, and a template layer?

Researched 2026-11-03 with built-in search, fetch and curl; nothing sent to Vapi, no paid calls. Issue #57 (C-54, C-37). Jackson's ideas, investigated, not decided. Numbers are SOURCE, MEASURED or ASSUMED.

## 1. Question

(A) Should Vapi join Retell and our LiveKit stack in bake-off 1? (B) Should Vapi assistants and squads be how we stamp out setups by trade (HVAC, plumbing) and client (Summit Air and its heuristics)?

## 2. Why it matters to Summit Air

Bake-off 1 (docs/bakeoffs.md, DECISIONS 004, 012) picks who answers the phone. Its main finding so far: Retell's model speaks every line, so it paraphrased the signed-off gas script (MEASURED, voice/retell/README.md); our stack says server text verbatim. Decision 004 set Vapi aside as "the same trade as Retell"; this checks that. brief.md and spec.md (R5 safety, R6 booking) need exact lines and server-owned hang-ups. Revin sells to HVAC, plumbing, electrical and roofing (SOURCE: exploration 005), so a trade-then-client template (C-37) is close to their product.

## 3. Options

### Part A: bake-off

**A1. Vapi in plain mode (Vapi runs ears, brain and mouth; our server answers tools).**
- *Tools.* A function tool points at a bridge, `voice/vapi/`, copied from voice/retell/bridge.py. Vapi needs `result` as one flat string and HTTP 200 always (SOURCE: [function tools](https://docs.vapi.ai/tools/custom-tools)), which fits our always-200 rule; our `data` goes in `result`.
- *Exact words.* Better than Retell on paper. A tool result can carry a `request-complete` message; if present, "only this message will be spoken and the model will not be requested", up to 1,000 characters, with `endCallAfterSpokenEnabled` (SOURCE: [OpenAPI spec](https://api.vapi.ai/api-json)). So our `say` plays verbatim and a server-approved hang-up can follow. With no tool call (gas said in passing), the bridge uses live control `say` with `interruptAssistantEnabled` and interruptions off (SOURCE: [live call control](https://docs.vapi.ai/calls/call-features)). Voice formatting must be switched off; it rewrites numbers (SOURCE: [formatting](https://docs.vapi.ai/assistants/voice-formatting-plan)). Transcript events are not blocking, so the model may start a reply first, the same gap as Retell (ASSUMED; one gas call tests it).
- *Hang-up.* Leave out the built-in end-call; our `end_call` tool decides. Silence timeout, max duration and idle hooks still end calls, and control-URL end-call is a kill switch, not a veto (SOURCE: [hang up](https://docs.vapi.ai/tools/hang-up)). Same as Retell.
- *Model.* `claude-haiku-5-5` is listed; `reasoningEffort` "none" is thinking off, and unset means none (SOURCE: OpenAPI spec). Deepgram and Cartesia Sonic 3 are listed too, so ears and voice can match ours: a cleaner turn-taking test than Retell's `stt_mode: fast`.
- *Phone.* A free Vapi number (US area code, inbound only, can't be ported), or a new Twilio number imported into Vapi, or a third origination URI on our trunk (one stack at a time) (SOURCE: [free number](https://docs.vapi.ai/free-telephony), [Twilio import](https://docs.vapi.ai/phone-numbers/import-twilio), [Twilio trunk](https://docs.vapi.ai/advanced/sip/twilio)). Never +16787376865. The number goes in `phone_numbers` in agent.yaml. `assistant-request` picks the assistant per call but must answer in 7.5 s (SOURCE: [events](https://docs.vapi.ai/server-url/events)); our lookups took 2 to 55 ms (MEASURED, Retell README).
- *Latency.* No consistent independent result. Real phone calls, default stacks, endpointing pinned to 0.1 s: Vapi 1,558 ms median, 2,008 p95; Retell 1,740 and 2,259 (SOURCE: [OpenBenchmarks](https://github.com/openbenchmarks-labs/voice-agent-latency), 2026-08-01). A blog test with GPT-4.1: Vapi 539 ms, Retell 714 (SOURCE: [GrowwStacks](https://growwstacks.com/blog/voice-ai-showdown-vapi-synthflow-retell-latency-test), 2026-01-21). Cekura means of 3.08 s and 2.21 s, with 41 of 246 Vapi calls never connecting (SOURCE: secondhand, [Telnyx page](https://telnyx.com/resources/voice-ai-latency-benchmark), a competitor). None used Haiku 5.5 or our prompt.
- *Price.* Per minute: hosting $0.05, Deepgram about $0.0097, Twilio inbound $0.008 (SOURCE: [pricing](https://vapi.ai/pricing), 2026-11-03); Haiku about $0.0032 (Retell's price, ASSUMED equal; Vapi lists no Claude price); Cartesia about $0.03 (ASSUMED from LiveKit's rate). Total about $0.10, so about $0.20 for two minutes (ASSUMED) against $0.19 on Retell (MEASURED). Batch like 012's: 10 calls about $2.00 plus a 30-scenario chat suite about $0.70, about $3 (ASSUMED). Usage-only plan: 4 concurrent calls, 14-day log retention, no support SLA (SOURCE: [packages](https://docs.vapi.ai/billing/pricing-and-success-packages)).
- *Effort and risk.* 2 to 3 days (ASSUMED): bridge 1.5, push script and chat adapter 1, deploy and calls 0.5. Risks: build time before a 45-minute interview; one more set of platform gaps to write up; 10 calls cannot separate platforms closer than about 0.3 s (ASSUMED; OpenBenchmarks calls 38 ms inside its error at about 400 turns).

**A2. Vapi with a custom LLM.** `model.provider: custom-llm` points at an OpenAI-style `/chat/completions` stream we serve (SOURCE: [custom LLM](https://docs.vapi.ai/customization/tool-calling-integration)). We run Haiku, run the turn check before each reply and stream exact lines, which closes the gap above; the same idea as the unbuilt Retell custom-LLM mode in 004. About 1.5 to 2 extra days (ASSUMED); only worth it if A1 paraphrases.

**A3. Documented comparison only.** This file is the entry, plus one line in 004. Optional: one Vapi test call (about $0.20) after the main bake-off. Under half a day.

### Part B: template layer

Vapi has (SOURCE: [quickstart](https://docs.vapi.ai/assistants/quickstart), [squads](https://docs.vapi.ai/squads), [variables](https://docs.vapi.ai/assistants/dynamic-variables), [GitOps](https://github.com/VapiAI/gitops)): saved, versioned assistants; squads (assistants that hand off); call-time `assistantOverrides` and `variableValues`; four dashboard starters (support, lead, scheduler, info collector; none for HVAC); an Apache-2.0 GitOps repo syncing files to orgs (2 orgs on Usage only, 5 on Core). Workflows were retired 2026-08-18 (SOURCE: [migration](https://docs.vapi.ai/workflows/legacy-migration)). There is no inheritance, so platform, trade and client layers would be merged by us. Whether an override replaces a whole field such as `model` is undocumented in what I read (ASSUMED; test once).

**B1. Vapi assistants and squads as the layers.** Base assistant, one per trade, overrides per client. Each client's prompt would then live in Vapi and drift from `tenants/`. Vapi's native homes for rules are the prompt, tool descriptions and rejection plans (regex or Liquid over the last message; SOURCE: [rejection plan](https://docs.vapi.ai/tools/tool-rejection-plan)), so building there pushes rules toward the prompt, which our build rule forbids for must-happen rules. Squad handoffs are the model's call, so "which stage are we in" would move from server state into the prompt. It is safe only if the server stays the authority and Vapi holds wiring.

**B2. Keep tenant folders; add a trade layer in the repo.** `tenants/<company>/` with `prompts/assemble.py` already stamps out a company, and `tests/tool_server/test_ts_platform.py` serves a second tenant from a copied folder (docs/template.md). C-37's open angle adds `trades/hvac/` (intake questions, signal lists, default safety kinds, persona scenarios) that a tenant extends. About 1 day (ASSUMED), no cost, works on every voice stack.

**B3. Vapi only renders.** `assistant-request` returns an assistant built at call time from the merged files, as the Retell bridge does with `{{instructions}}`; nothing client-specific is stored in Vapi. That is B2 plus A1, no new design.

Meeting point with 009: if it puts rule tags by call stage in tenant files, a squad would be one way to render them; who may edit which rule stays in 009.

## 4. Recommendation, and what would change it

Do not add Vapi as a full entrant now, and do not use it as the template layer. Take A3 and B2. Retell already stands for "managed platform"; with about 10 calls a side, a third entrant adds breadth, not a result that could beat the 0.3 s rule; and our tenant folders already do what B1 would, with the rules where our build standard puts them. Keep the one cheap check: Vapi's exact-say path is the one fact that could weaken the "managed platforms can't say fixed lines" argument.

This changes if: the test call speaks the gas lines exactly on the first reply and ends only on the server's say (then run A1, because our own stack must stand on speed, cost and fit alone); Retell loses to LiveKit on turn-taking (a second managed option matters); a Revin engineer says they use Vapi (postings name LiveKit, Daily, Twilio Voice and OpenAI Realtime, not Vapi); or more than 2 days are spare after the Retell and LiveKit results.

## 5. Open questions for Jackson

1. **Vapi in bake-off 1.** (a) Documented comparison plus one test call after the main run. (b) Full entrant in plain mode, about $3 and 2 to 3 days. (c) Entrant in custom-LLM mode. Recommend (a); move to (b) if the test call passes or days are spare.
2. **If Vapi runs, which number.** (a) The free Vapi number. (b) A new Twilio number imported, about $1.15 a month (SOURCE: exploration 004). (c) A third origination URI on the trunk. Recommend (a): no Twilio change, and all stacks stay callable at once.
3. **Template layering (C-37).** (a) Leave `tenants/` as is. (b) Add `trades/hvac/` that tenants extend. (c) Build the layers as Vapi assistants. Recommend (b), sized first as a prompt, not a decision; (a) if time is short; not (c).

## 6. Sources

Vapi docs, read 2026-11-03: [function tools](https://docs.vapi.ai/tools/custom-tools), [rejection plan](https://docs.vapi.ai/tools/tool-rejection-plan), [hang up](https://docs.vapi.ai/tools/hang-up), [live call control](https://docs.vapi.ai/calls/call-features), [server events](https://docs.vapi.ai/server-url/events), [custom LLM](https://docs.vapi.ai/customization/tool-calling-integration), [formatting](https://docs.vapi.ai/assistants/voice-formatting-plan), [variables](https://docs.vapi.ai/assistants/dynamic-variables), [squads](https://docs.vapi.ai/squads), [assistant quickstart](https://docs.vapi.ai/assistants/quickstart), [workflows migration](https://docs.vapi.ai/workflows/legacy-migration), [free number](https://docs.vapi.ai/free-telephony), [Twilio import](https://docs.vapi.ai/phone-numbers/import-twilio), [Twilio trunk](https://docs.vapi.ai/advanced/sip/twilio), [packages](https://docs.vapi.ai/billing/pricing-and-success-packages), [OpenAPI spec](https://api.vapi.ai/api-json), [pricing](https://vapi.ai/pricing), [GitOps](https://github.com/VapiAI/gitops) (last push 2026-10-31).

Latency: [OpenBenchmarks](https://github.com/openbenchmarks-labs/voice-agent-latency) (2026-08-01); [GrowwStacks](https://growwstacks.com/blog/voice-ai-showdown-vapi-synthflow-retell-latency-test) (2026-01-21); [Telnyx's summary of Cekura](https://telnyx.com/resources/voice-ai-latency-benchmark) (undated; Cekura's page not read). Revin: [Built In NYC listing](https://www.builtinnyc.com/job/ai-systems-engineer/9252017) (removed 2026-10-29). Repo: docs/decisions/04 and 32, voice/retell/README.md, docs/template.md, docs/explorations/004 and 005.

Unverified without a test call: Vapi's Claude and Cartesia prices, chat price, override merging, whether a tool-result message plays unedited, tool behavior on interruption. Tools: built-in search and fetch, curl; no paid fallback.
