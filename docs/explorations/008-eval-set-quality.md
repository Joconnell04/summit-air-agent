# 008. What makes the eval set find real failures, and how a human co-authors it

Issue: none opened yet (consideration C-52). Researched 2026-11-02 with WebSearch, WebFetch, curl and pdftotext; no Exa or Firecrawl credit spent. Provenance: SOURCE is linked in section 6, MEASURED means I counted or read files in this repo (`evals/scenarios`, `evals/results`, `config/models.yaml`) or the public tau2-bench task file, ASSUMED is my estimate. Pages from voice-testing vendors are marked (vendor) and are treated as opinion, not evidence.

## 1. Question

What makes an eval set effective instead of a set the agent already passes? What is specifically risky when an AI writes the cases, plays the caller and grades the call, and how do voice-agent evals handle it? What should Jackson write by hand, in what order, so the eval runs find failures?

## 2. Why it matters to Summit Air

`docs/brief.md` says the demo is judged on whether the agent works, meets the requirements, and "handles the unexpected". The suite is the evidence for all three, and it is also a gate: DECISIONS 011 sets the pass-rate floor from the first standard run on the real prompts. If that run is an easy set, the floor is high and means little, and a regression passes. The spec holds 9 must-pass lines that only evals can show (`docs/spec.md`, marked M).

Money is not the scarce resource. MEASURED: two smoke runs of 8 scenarios cost $0.144 and $0.152, about $0.018 a scenario, far under the $0.12 ASSUMED in `docs/decisions/06-eval-design.md`. What is scarce is Jackson's reading time and the share of cases that can fail.

**What the research says, and what it means here**

| Quality | Finding | Meaning for us |
|---|---|---|
| Source of cases | "20-50 simple tasks drawn from real failures is a great start" (Anthropic, SOURCE). OpenAI warns against sets that do not reproduce real traffic (SOURCE). | We have no real calls yet. The first real source is the 10 live calls. |
| Clear, solvable cases | Two experts should reach the same verdict; a 0% pass rate "is most often a signal of a broken task" (Anthropic, SOURCE). | Each scenario needs a stated purpose and a pass rule a stranger could apply. |
| Headroom and saturation | Capability evals start at a low pass rate; regression evals sit near 100% (Anthropic). Of 60 benchmarks, 29 (48.3%) were saturated; private test sets did not help, expert curation did (SOURCE, Akhtar et al.). | Split the suite into a set that should still fail sometimes and a set that must not fail. |
| Discriminative power | Dynabench keeps only examples a model gets wrong that a person gets right (SOURCE). tau-bench tasks: gpt-4o under 50% and pass^8 under 25% (SOURCE). | Keep cases the current agent fails at least sometimes. |
| Code vs judge | tau-bench scores the database end state (SOURCE). LLM judges agree with humans over 80% of the time but favor their own outputs and, across a model family, its relatives (SOURCE, Zheng; Panickssery; Li). | Our code-first rule is right. The judge needs human labels before it is trusted. |
| Judge calibration | One expert, pass/fail plus a written reason, no 1-5 scale; report catch rates for pass and fail separately; about 100 labels for a firm read, under 60 gives wide intervals (Husain, SOURCE). | Our grader gives a 1-5 score. There are no human labels in the repo. |
| Label quality | MultiWOZ 2.1 changed over 32% of state labels (SOURCE). tau2's simulated user made errors in 47% of airline and 40% of retail conversations (SOURCE). Anthropic's CORE-Bench score went from 42% to 95% after fixing grader and spec bugs. | A failing check is as likely a bad check as a bad agent. Read transcripts. |
| Variance | Report error bars (Miller, SOURCE); most benchmarks do not (BetterBench, SOURCE). | At 46 scenarios and one sample, one standard error is about 6 points (ASSUMED 80% pass rate, independent cases). Decision 011 already waits for repeat runs. |
| Criteria drift | People cannot write the criteria before seeing outputs; grading changes the criteria (Shankar, SOURCE). | Rubrics written from the spec alone will need rewriting after the first read. |
| Staleness | Suites need an owner and change like unit tests (Anthropic); happy-path-only suites "look green" and drift as the agent changes (Cekura, vendor). | Every live failure becomes a case. |

**The risk of AI writing its own cases.** Test sets and judges from one model family inflate each other: in one study each model ranked itself first, and diversity controls only partly helped (SOURCE, 2509.26600). Simulated users are too agreeable: with 451 real people on 165 tau-bench tasks, 31 simulators gave an "easy mode" that inflated agent success (SOURCE, Sim2Real). MEASURED here: the harness commit is co-authored by Opus 5.5, the live agent (`fast`) and the simulated caller are both claude-haiku-5-5, and the grader is claude-opus-5-5, so one family holds all four seats. In the two smoke transcripts I read (elderly-no-heat, lapsed-member-stored-active) the Haiku caller volunteered the key fact on the first ask ("I'm 84 and I use a cane"), answered in full sentences and thanked the agent. Two further risks: `docs/ai-process/phases.md` has an "Eval loop until the scoreboard holds" step, so Claude will tune prompts against the visible set (ASSUMED overfitting risk), and vendors that generate scenarios from the agent's own prompt test what the prompt already says.

**Voice agents.** Realistic audio costs points: in tau-Voice (278 tasks), pass@1 fell from 49% to 35% (OpenAI), 51% to 38% (xAI) and 31% to 26% (Google) with accents, 15 dB noise, 8 kHz phone audio and frame loss, yet 79% to 90% of failures were agent behavior, not audio (SOURCE). Its authors note the simulator is more patient than real users. tau2 cut simulator errors to 16% in telecom by giving the user tools that constrain what it can do (SOURCE). Vendors suggest 50 scenarios as a minimum, 5 to 20 blocking personas, every production failure as a permanent test, call-level scoring over turn-level, and text runs on every change with audio nightly (Hamming, Cekura, LiveKit; all vendor or docs). Vendor persona mixes (70% cooperative, 15% frustrated, 10% distracted, 5% accented) have no independent evidence behind them.

**What good public cases share** (MEASURED on the 50 tau2 airline tasks): every task has a `purpose` sentence naming the mistake it tests; 30 of 50 give the caller conditional instructions ("if the agent says X, then say Y") rather than a fixed script; callers hold a false belief the agent must catch (thinks she is Gold, is Silver; says 3 passengers, is wrong); 16 of 50 purposes are about holding a policy line under pressure; 7 of 50 expect no change at all; no task sets a persona, so difficulty comes from the situation, not from voice color.

## 3. Options

**Where the suite stands** (MEASURED unless noted).

Does well:
- Code decides pass or fail from the tool log and saved records; the grader can fail a call but never rescue one (matches Anthropic's grader order).
- Balanced: no-trigger cases sit beside trigger cases (gas-furnace-no-trigger, vague-smell-cleared, co-alarm-clear), so over- and under-alarming are both tested.
- Traceable: every scenario names spec lines and journeys; 36 of 54 spec lines and 8 of 9 must-pass lines have a scenario.
- Hard share: 12 of 46 are adversarial; 4 are injection.
- Clean state per scenario, errors kept apart from fails, per-run cost recorded.
- Not trivially green: 3 of 8 smoke scenarios passed in both runs on the stand-in prompt. That says the suite can fail an agent; it says nothing about the real prompts yet.

Gaps:
1. No case comes from a real caller. Seeds are the spec, the journeys and Bobert's simulated logs (Bobert is an earlier personal practice project, see `from-other-projects/bobert`).
2. One model family at author, caller, agent and grader (see section 2).
3. The caller is cooperative and has no rule to hold facts back. No scenario's difficulty comes from what the caller withholds or believes wrongly.
4. The heavy speech-to-text roughening never runs: 0 of 46 scenarios carry `transcription-errors`, and the light level is lower-casing, spelled-out digits and a list of 17 sound-alike words (`evals/transcribe.py`).
5. Thin middle of the ladder and little variety: 4 rigorous against 17 standard and 12 adversarial; 44 of 46 run at the default Monday 09:00 (only 2 set `time`); no outside-temperature variation.
6. 18 of 54 spec lines have no scenario; five are tagged "eval" in the spec: S-12, S-15, S-25, S-38, S-48.
7. No `purpose` or `source` field, a 1-5 judge score, and no human labels. Decision 06's "advisory if it disagrees on more than 2 of 20" cannot be checked yet.
8. Noise: 2 of 8 smoke scenarios changed outcome between two runs of the same suite (co-alarm-clear pass to fail, out-of-area-known-caller fail to pass).
9. No held-out set, and the interruption trait is text only (real barge-in waits for the live pass).

**A. Review only.** Jackson reads the 46 and edits YAML. Plugs in at `evals/scenarios/*.yaml`. Effort 2 to 3 hours (ASSUMED); cost none. Risk: editing what is shown anchors the reader, adds no real source and leaves the one-family problem (ASSUMED).

**B. Human seeds, error analysis first, AI expands (recommended).** Jackson reads real-prompt transcripts first, writes seeds in plain words, labels judge output, and holds out a set. Plugs in as new fields on the scenario (`source`, `purpose`, `set: capability | regression | holdout`), new suites in `evals/suites.yaml`, and a labels file next to the results. Effort about 7 to 9 hours of Jackson over three sittings (ASSUMED) plus one small build task. Cost: about 200 runs at $0.018 is $3.60 MEASURED rate, under $10 allowing for longer real prompts (ASSUMED). Risk: his time, and he is not a dispatcher, so realism needs a second person.

**C. Add a second model family as adversary and second judge.** A non-Anthropic model writes attack variants of Jackson's seeds and re-judges the labelled slice. Plugs in at `config/models.yaml` (OpenAI and Gemini profiles exist) but `evals/README.md` says the harness calls Anthropic profiles only today, so it needs an adapter. Effort medium (ASSUMED half a day); cost a few dollars. Risk: another model's caller is differently unrealistic, and it is still not human. Best as an add-on to B.

Hosted voice platforms that generate scenarios stay out of scope (DECISIONS 002); they remain a possible audio layer only.

## 4. Recommendation, and what would change it

Do B, then C for the labelled slice. Order matters: read first, write second, label before trusting the judge, hold out before the first prompt-tuning loop.

| Step | Jackson by hand | Time (ASSUMED) | Where AI is safe |
|---|---|---|---|
| 1. Tag | Skim 10 purposes; cut any that name no mistake | 20 min | Add `source: ai-spec`, `set: regression`, write purposes |
| 2. Read | 25 to 30 transcripts from the first standard run on the real prompts, one line each on the first thing that went wrong, before any grader note | 90 min | Run the suite, print transcripts without grader notes; afterward cluster his notes and propose names he edits |
| 3. Write seeds | 10 to 12 cases in his own words: what the caller says, what a bad agent does, what counts as a pass; aim where he expects failure. Real-sounding sources: 6 to 8 people who have not seen the spec phone the live numbers from a one-line situation card; public HVAC complaint threads (ASSUMED to exist, not checked); the owner persona (Paul) | 60 to 90 min plus about 2 h of calls | Turn seeds into YAML and code checks; he reads each check once |
| 4. Label | 30 graded transcripts pass/fail with a one-line reason, blind to the judge; compare where the judge says pass and he says fail | 60 min | Compute agreement, tune the judge on half, re-run a second-family judge on the same 30 |
| 5. Hold out | 8 to 10 more cases in a folder no build brief mentions; run at release gates only | 45 min | Run the gate; do not read the cases until after it |
| 6. Calibrate | Run each new case 3 times; fails some or all goes to `capability`, passes 3 of 3 goes to `regression`; read any 3-of-3 failure once to rule out a broken case | 30 min | Run, tabulate |
| 7. Keep it live | Each live-call failure becomes a case that day | ongoing | Draft the case from the call |

AI-safe harness changes now: use `transcription-errors` on about 6 scenarios, vary `time` and outside temperature, add scenarios for the 5 spec lines tagged "eval", and give the caller a rule to hold facts back until asked directly. Not safe for AI: deciding pass criteria, judging what is hard, labels for judge calibration, the hold-out contents, calling a set saturated.

What would change it: if the first real-prompt run fails more than half the scenarios, skip step 3 and fix the agent, since the transcripts already supply cases; if it passes over 95%, put the time into steps 3, 5 and 6. With under 3 hours, do only steps 2 and 4 (ASSUMED the best value per hour). If the live calls show failure types the seeds miss, replace seeds with those.

## 5. Open questions for Jackson

1. How many hours can you give this before the first standard run is adopted as the floor: about 3, 6 or 9?
2. Can you get 6 to 8 people who have not seen the spec to phone the two test numbers, and will Paul or anyone who has dispatched HVAC calls look over your seeds for realism?
3. Are you willing to write a hold-out set that Claude does not read until a release gate, even though it slows prompt tuning?
4. Should the judge's verdict stay able to fail a run until you have labelled 30 transcripts, or go advisory until it is calibrated?
5. May the harness spend a few dollars on a non-Anthropic model for the adversary and second judge?
6. Which single failure would embarrass you most in front of the Revin reviewers? Your first hand-written seeds go there.

## 6. Sources

Read in full unless marked snippet. Accessed 2026-11-02.
- Anthropic, Demystifying evals for AI agents (Jan 9, 2026): https://anthropic.com/engineering/demystifying-evals-for-ai-agents
- Anthropic docs, develop test cases: https://platform.claude.com/docs/en/docs/test-and-evaluate/develop-tests
- OpenAI, Evaluation best practices (notes the Evals platform is read-only Oct 31, 2026 and shuts down Nov 30, 2026): https://developers.openai.com/api/docs/guides/evaluation-best-practices
- Husain, error analysis FAQ: https://hamel.dev/blog/posts/evals-faq/why-is-error-analysis-so-important-in-llm-evals-and-how-is-it-performed.html
- Husain, creating an LLM judge: https://hamel.dev/blog/posts/llm-judge/
- Shankar et al., Who Validates the Validators (UIST 2024), abstract and search summary only: https://arxiv.org/abs/2404.12272
- Zheng et al., Judging LLM-as-a-Judge (MT-Bench), abstract: https://arxiv.org/abs/2306.05685
- Panickssery et al., LLM evaluators recognize and favor their own generations (NeurIPS 2024), snippet: https://arxiv.org/abs/2404.13076
- Li et al., Preference Leakage (ICLR 2026), abstract: https://arxiv.org/abs/2502.01534
- When LLMs benchmark themselves, self-bias in automated evaluation, abstract: https://arxiv.org/abs/2509.26600
- Mind the Sim2Real Gap in User Simulation for Agentic Tasks (2026), abstract: https://arxiv.org/abs/2603.11245
- Miller, Adding error bars to evals, snippet: https://arxiv.org/abs/2411.00640
- Reuel et al., BetterBench (NeurIPS 2024): https://arxiv.org/abs/2411.12990
- Akhtar et al., When AI Benchmarks Plateau (ICML 2026): https://arxiv.org/abs/2602.16763
- Kiela et al., Dynabench (2021), abstract: https://arxiv.org/abs/2104.14337
- Liang et al., HELM, abstract: https://arxiv.org/abs/2211.09110
- Yao et al., tau-bench, abstract: https://arxiv.org/abs/2406.12045
- Barres et al., tau2-bench (user simulator errors in Table 2 and Appendix E): https://arxiv.org/abs/2506.07982
- tau2-bench airline tasks (counted by me): https://raw.githubusercontent.com/sierra-research/tau2-bench/main/data/tau2/domains/airline/tasks.json
- tau-Voice, Sierra (ICML 2026), HTML read to 100,000 characters: https://arxiv.org/abs/2603.13686
- MultiWOZ 2.1 and 2.2 corrections, snippets: https://arxiv.org/abs/1907.01669, https://arxiv.org/abs/2007.12720
- Hamming, How to evaluate voice agents (Jan 24, 2026, vendor): https://hamming.ai/blog/how-to-evaluate-voice-agents-2026
- Hamming, voice test personas from support calls (Jun 1, 2026, vendor): https://hamming.ai/resources/voice-agent-test-personas-support-calls-template
- Cekura, how to generate voice agent test cases (Jul 15, 2026, vendor): https://www.cekura.ai/discover/how-to-generate-voice-agent-test-cases
- LiveKit testing and Pipecat Evals docs, search summaries only: https://docs.livekit.io/testing/, https://docs.pipecat.ai/pipecat/fundamentals/evaluations/overview
- Earlier work: DECISIONS.md 002 and 011, `docs/decisions/06-eval-design.md`, `evals/README.md`, `docs/considerations.md` C-52.

Gaps: I did not read the Coval, Bluejay, Vapi or Retell testing pages beyond search summaries; no independent source says what persona mix is right for a phone agent; I read only 2 of the 16 smoke transcripts, so the cooperative-caller observation is a pointer, not a measured rate; the Sim2Real paper's score numbers and the MIMESIS figures were not verified at source.
