# 06. How we test the agent

Issue: #6. Status: decided, our own harness (DECISIONS.md 002), revised after Jackson's review; LangSmith as a results viewer is the one open sub-choice

## Question

Where test calls run, who scores them, what tool runs it all, and how both voice stacks are scored the same way. Blocks the eval build brief (#8) and the eval floor in `make check`.

## Options

### A. Our own harness: a simulated caller against the real tool server (chosen)
A cheap model plays a caller persona against our real prompts and tools on a scratch database. Code checks on tool calls and saved records decide pass or fail; an LLM grader scores only how the call felt. Reuses the simulator and replay ideas from Bobert, an earlier personal practice project (a voice agent for a made-up HVAC company, built before this assignment).

### B. The voice platform's own simulator
Retell or LiveKit plays the caller and an LLM judges the transcript. Little to build, but Retell can't check tool arguments or the database, and scoring each stack with its own simulator wouldn't be a fair comparison.

### C. A general eval framework (promptfoo, Braintrust, LangSmith)
Good dashboards and experiment comparison, but a multi-turn caller against our server still needs our own driver, so on its own it adds a dependency without removing the harness.

### D. Hosted voice testing (Cekura, Coval, Hamming)
Places real synthetic phone calls and scores speech and interruptions. Sees only the outside of the call, no database checks, and pricing is mostly unpublished.

## How it works

- **One harness, both stacks.** The same scenarios run against our LiveKit stack (in process) and against the Retell baseline (through Retell's text chat interface, same tools on the same server), so the voice-platform bake-off compares like with like. Retell's own simulator isn't used for scoring.
- **Audio pass.** The same 10 scripted calls are placed to both phone numbers, by an automated caller that speaks the script (ASSUMED feasible with an outbound call from our own caller agent) or by hand from the script if that isn't ready.
- **Realistic callers.** Scenarios aren't clean text. The simulated caller stutters, trails off, gives a wrong ZIP and corrects it straight away, gives a wrong address and only corrects it after the agent reads it back, splits a phone number across turns, interrupts, answers vaguely, or has someone else in the room. A transcription layer adds the kind of errors speech-to-text makes ("for one one two oh one").
- **Labelled and tiered.** Every scenario carries a flow tag (booking, safety, membership, commercial, renter, repeat caller, after hours, off topic, injection) and a difficulty: easy, standard, rigorous, adversarial. Suites are just tag filters, so a run can be picked on the fly: `make eval SUITE=smoke`, `make eval TAGS=flow:safety,difficulty:rigorous`, `make eval SUITE=standard STACKS=livekit,retell` for a bake-off table.
- **Cheap by default.** The smoke suite is about 8 scenarios for about $1 (ASSUMED); the full suite runs only at a release gate (about $15, ASSUMED).

## LangChain tools (named in the job description)

- **LangSmith:** fits as a results viewer. The harness stays the source of truth, but each run can be sent to LangSmith as an experiment, which gives side-by-side comparison of bake-off setups and traces of each simulated call. Free Developer plan: 5,000 traces a month, 14-day retention, 1 seat (SOURCE: langchain.com/pricing). Off the live call path, so no effect on reply speed.
- **LangGraph:** not used. LiveKit Agents already runs the conversation loop, and the rules live in server gates; a graph framework in the live path adds a layer and delay without adding a step the call needs.
- **Langflow:** not used. A visual builder for prototypes, not for a tested, code-reviewed agent.

## Recommendation

Our own harness (A), the same harness for both voice stacks, realistic tiered scenarios selectable by tag, and a 10-call audio pass. Add LangSmith as an optional results viewer for the bake-offs, since it costs nothing on the free plan and shows the comparison clearly.

## Research notes

Detail moved from the summary:

- Scoring rule: code checks decide pass or fail for every requirement; the grader can fail a run the code passed, never rescue one the code failed. LiveKit's simulator uses the same rule.
- Persona suite: about 40 scenarios (ASSUMED), seeded from `.claude/agents/red-team-caller.md` and Bobert's 23 inbound cases; each is a persona, hidden facts, an opening line and checks. Every real failure becomes a permanent scenario.
- Replay (Bobert): re-run a recorded call's caller turns against current prompts and tools; exit non-zero if the tool-call sequence differs. Unique call ids and a scratch database per scenario; Bobert's shared id let an earlier case pass a later gate.
- Why code checks first: in Bobert's log, 5 of 8 first-wave outbound failures were one ordering bug, and gas smell failed three times on speech-versus-tool order. Simulator loops are marked error, not fail.
- Ratchet floor: `make check` stays offline. A paid run commits `evals/results.json` with a hash of prompts, scenarios and profile. `ratchet.py` fails on a stale hash, fewer scenarios, any failed "must" scenario (safety, commercial, out of area, booking gates), or a pass rate under `min_eval_pass_rate`.
- Cost discipline: default about 8 scenarios, 1 sample, about $1 (ASSUMED); full 3-sample run only at a release gate; about 10 audio clips (about $3, ASSUMED) after #4. Audio pass: `lk agent simulate audio` if LiveKit wins, a scripted call to the test number if Retell wins.
- Grader check: drop it to advisory if it disagrees with Jackson's labels on more than 2 of 20 transcripts (ASSUMED threshold).
- promptfoo is reported to be owned by OpenAI since March 2026 (secondary source). Cekura meters 5 credits per voice minute (SOURCE). Hamming's page describes LiveKit-to-LiveKit testing.


All dollar figures marked ASSUMED are mine; nothing here was MEASURED, and no paid search tool was used (built-in search and fetch only).

**Cost model (ASSUMED inputs, SOURCE prices).** Per scenario run: agent 16 model calls averaging 5k input tokens (80k in, 2k out); simulated caller 18k in, 0.6k out; grader 4k in, 0.4k out. Prices (SOURCE, [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)): Haiku 4.5 $1 in / $5 out per million; Sonnet 5.5 $2 / $10. Haiku agent and caller, Sonnet 5.5 grader: about $0.09 + $0.02 + $0.01 = $0.12 per run. 40 scenarios x 3 samples = 120 runs, about $15. With Sonnet 5.5 as the agent, about $0.21 per run and $25 per full run. Cross-check (SOURCE, [Retell pricing](https://www.retellai.com/pricing)): chat agents on Claude 4.5 Haiku cost $0.007 per message, so 12 agent messages are about $0.08 before the simulated caller. Caching would cut the agent cost; the Batch API (50% off) does not fit an interactive loop.

**Audio.** SOURCE: Retell voice is $0.07 to $0.31 per minute, with a $0.11 example plus $0.015 per minute Twilio telephony ([Retell pricing](https://www.retellai.com/pricing)); Deepgram Nova-3 streaming STT is $0.0077 per minute ([Deepgram](https://deepgram.com/pricing)). Ten 2-minute clips at about $0.125 per minute is $2.50 (ASSUMED call length), plus the clip generation.

**Platform facts (SOURCE).**
- LiveKit unit tests: `session.run(user_input=...)`, `is_function_call(name, arguments)`, `.judge(llm, intent=...)`, `mock_tools`; text mode, run on every pull request ([test framework](https://docs.livekit.io/agents/start/testing/test-framework/), [testing overview](https://docs.livekit.io/agents/build/testing/)).
- LiveKit simulations: `scenarios.yaml` with `instructions` and `agent_expectations`; LLM judge; `on_simulation_end` can fail a judged pass but not rescue a fail; `--samples` with `--pass-rate` default 0.75; runs on LiveKit Cloud; text by default, audio via `lk agent simulate audio`; no dollar figures given ([simulations](https://docs.livekit.io/agents/start/testing/simulations/)). A search result noted the docs disagree about audio mode and beta status, so confirm before relying on it.
- Retell simulations: test case = caller prompt, success criteria, dynamic variables, function mocks; all criteria judged together in one pass; text only; can end in Error (10-minute timeout); billed per message for both the agent and the simulated user, batch cases add one analysis unit each, chat rates $0.001 to $0.05 per message ([simulation testing](https://docs.retellai.com/test/llm-simulation-testing.md), [testing pricing](https://docs.retellai.com/test/testing-pricing.md)). Retell has a chat completion endpoint for text conversations ([API](https://docs.retellai.com/api-references/create-chat-completion.md)); request parameters not verified.
- promptfoo: `llm-rubric`, `javascript`/`python` assertions, `trajectory:tool-used`, `trajectory:tool-args-match`, `trajectory:tool-sequence`, `threshold`; grading varies between runs unless the grader is set to temperature 0 ([assertions](https://www.promptfoo.dev/docs/configuration/expected-outputs/), [llm-rubric](https://www.promptfoo.dev/docs/configuration/expected-outputs/model-graded/llm-rubric/)).
- Cekura: first seat free, $30 per extra seat per month, 5 credits per voice minute, 0.5 per chat message, overage at twice the rate, 12-month minimum; the dollar value of a credit is not on the page ([Cekura, 2026-07-25](https://www.cekura.ai/discover/voice-ai-testing-usage-based-pricing)). A search snippet said $0.25 per minute; unverified.
- Hamming: LiveKit-to-LiveKit tests, REST trigger from CI, advises temperature 0 and pass-rate thresholds such as 95% over 100% ([Hamming guide](https://hamming.ai/blog/testing-livekit-voice-agents-complete-guide)). Prices not found.
- Secondary only (competitor blogs, treat as biased): Coval $100 or $500 per month, Roark $0.15 per simulated minute, Braintrust Pro $249 per month, LangSmith Plus $39 per seat, promptfoo acquired by OpenAI in March 2026 ([comparison](https://andrew.ooo/answers/voice-agent-regression-testing-vendors-coval-cekura-bluejay-roark-hamming-2026/), [Braintrust vs LangSmith](https://costbench.com/compare/braintrust-vs-langsmith/)).

**Repo evidence (SOURCE: files in this repo).**
- `from-other-projects/bobert/adversarial-triage-log.md`: inbound batch 1 passed 10 of 15; outbound batch 1 passed 2 of 10; rerun after a flag fix passed 3 of 8 because the model set `confirmed: true` without asking, so the gate moved into the server. Three entries are "Ending the conversation early as there might be a loop".
- `from-other-projects/bobert/architecture.md`: Retell simulations were text only, "a few cents per run", 23 inbound and 17 outbound personas; `make replay CALL=<id>` exits 1 on divergence; call ids were once stamped to the minute and once shared as "playground".
- `scripts/ratchet.py` and `evals/baseline.json`: today only `min_tests` (4) is enforced; `min_eval_pass_rate` is null, and the script says the floor is wired in once the suite exists.

**Gaps.** No measured grader-versus-human agreement exists for this agent; the 2-in-20 threshold is a placeholder. Hosted-vendor prices and Retell's analysis-unit price were not found. If the per-run token estimate is wrong by 2x, a full run is $30 not $15 and nothing in the recommendation changes.
