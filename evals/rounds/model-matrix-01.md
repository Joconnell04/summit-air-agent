# Model matrix 1: Haiku 5.5 against Sonnet 5.5 as the live brain

Bake-off 4 in `docs/bakeoffs.md`, the cheap version: two Claude models, the standard suite, two repeats each.

**Verdict: Haiku 5.5 stays the live model.** Sonnet 5.5 passed fewer calls (84% and 87% against 91% and 91%), failed the same must-pass cases, and took 1.6 s to its first word on a plain turn against 0.57 s, which misses the 1.2 s bar. It costs about 26 times as much per call for the agent's own tokens. Sonnet paces questions better, one at a time with fewer re-asks, but it narrates its tools and the server to the caller and probes the residence check until it turns renters away. Every number below is MEASURED from the results files named in the method.

## Method

- **What ran.** Our stack in process (`livekit`, text only, no audio) on the `standard` suite: 32 scenarios, every easy and standard one. Haiku is profile `fast` (`claude-haiku-5-5`, thinking off). Sonnet is profile `quality` (`claude-sonnet-5-5`, thinking off through its `between_tools` setting; a spot check of six requests returned text blocks only, no thinking). Everything else was the same for both: the agent's prompt files, the tool server and the mock ServiceTitan, the simulated caller (Haiku 5.5) and the grader (Opus 5.5).
- **Repeats.** Two of each scenario per model, so run-to-run noise shows. Haiku ran both repeats in one file; Sonnet ran one repeat at a time so its cost could be checked after the first.
- **Results files.** Haiku: `evals/results/20261103-220532-livekit-fast-standard.json`. Sonnet: `evals/results/20261103-220404-livekit-quality-standard.json` and `evals/results/20261103-221111-livekit-quality-standard.json`. The first Sonnet file carries a different `prompt_hash` only because a side-model question file sat in `prompts/` for a few minutes while it finished; the agent's prompt files were identical in all three runs (checked by recomputing the hash with and without that file).
- **Reply delay** is the text harness's time from the caller's line reaching the server to the agent's first word: the model's first token on a plain turn, and the first spoken word on a turn with a tool call (a lookup turn). It leaves out speech-to-text and text-to-speech, and was measured from a laptop, so read it as the model's share of the delay, not a phone call's. Turns spoken from code (the greeting, the safety lines) are left out.
- **Cost per call** is new in this round: each scenario now records `agent_cost_usd`, the agent's own tokens without the simulated caller and the grader, at the prices in `config/models.yaml`.
- **Why not through a Claude subscription.** Driving fresh Claude Code sessions as the rig was ruled out: Claude Code wraps the model in its own system prompt, tools and caching, so neither the tool calls nor the reply delay would match how our agent calls the model, and the comparison would not be fair.
- **Cost before running.** Estimated from one probe call per model on `tune-up-known-caller` ($0.021 a call for Haiku, $0.109 for Sonnet, MEASURED): about $1.30 for Haiku and $7.00 for Sonnet, under the $10 cap. Spent: $1.33 Haiku, $5.59 Sonnet, $0.13 for the probes.

## Results

| | Haiku 5.5 | Sonnet 5.5 |
|---|---|---|
| Pass rate, repeat 1 | 29 of 32 (91%) | 26 of 31 (84%), 1 error |
| Pass rate, repeat 2 | 29 of 32 (91%) | 27 of 31 (87%), 1 error |
| Must-pass failures | `co-alarm-clear` twice, `gas-smell-in-passing` once | `co-alarm-clear` twice, `gas-smell-in-passing` twice |
| Grader score, mean (median) | 3.05 (3), 64 calls | 3.19 (3), 62 calls |
| Plain turn, first word p50 / p90 | 568 / 722 ms (332 turns) | 1591 / 2324 ms (313 turns) |
| Lookup turn, first word p50 / p90 | 1288 / 2671 ms (228 turns) | 1987 / 5243 ms (214 turns) |
| Lookup turn, model first token p50 / second hop p50 | 569 / 537 ms | 1254 / 1550 ms |
| Agent cost per call, mean | $0.0027 | $0.0705 |
| Whole scenario cost, mean (with caller and grader) | $0.021 | $0.087 |

Against the bake-off scorecard: neither model reaches the grader bar of 4; Haiku meets the plain-turn delay bar (under 1.2 s median) and the lookup bar (under 2.5 s median); Sonnet meets the lookup bar only.

**Noise.** Haiku's two repeats both passed 29 of 32 but failed different scenarios (four scenarios split between repeats), and one scenario is 3 points of pass rate. The difference in pass rate (91% against 84 to 87%) is two to three scenarios, real but small; the difference in reply delay is not noise (every percentile, hundreds of turns).

**The must-pass failures are not a model difference.** All seven are the grader failing the safety sign-off, which the server speaks word for word from `tenants/summit-air/agent.yaml` ("We'll follow up once you're safe" read as a callback the call never set up). Both models fail it the same way because neither model says it.

### Where they split

| Scenario | Haiku | Sonnet | Why, from the transcripts |
|---|---|---|---|
| `baby-no-heat-standard` | pass, pass | error, error | Sonnet asked about system age, what was tried, the landlord's name and the buzzer, and ran out of the 16 caller turns both times. |
| `wants-membership` | pass, pass | fail, fail | Sonnet told the caller about its tools both times: "The tool only takes a callback window for today, let me try that". |
| `oven-heat-co-check` | pass, pass | fail, pass | Sonnet asked how many apartments the renter's building had, heard six, and declined the call as commercial. |
| `renter-landlord-unknown` | pass, pass | pass, fail | Sonnet asked whether it was a bigger building; "an apartment building" matched the server's commercial phrase list, and Sonnet then said "The server has told me to decline" to the caller. |
| `expired-member-hard-of-hearing` | pass, pass | fail, pass | Sonnet never told the caller the membership had lapsed. |
| `reschedule-own-booking` | pass, fail | pass, pass | Haiku booked a second visit instead of moving the first (round 1 failure 3, still open for Haiku). |
| `no-heat-gas-stove` | fail, pass | pass, pass | Haiku said "carbon monoxide" when telling the caller not to heat with the oven. |
| `after-hours-no-heat-cold` | fail, pass | pass, pass | The gas safety lines fired on a call that should not have them, then no on-call flag; the simulated caller's wording differed between repeats. |
| `gas-smell-in-passing` | pass, fail | fail, fail | The grader on the sign-off line (above). |

**Where Sonnet is better.** Pacing: fewer stacked questions and re-asks. The grader's notes mention two questions in one turn on 29 of 64 Haiku calls and 21 of 64 Sonnet calls (MEASURED, a search of `grader_notes` for "stack"). The grader's largest gains were on `after-hours-tune-up` (2 and 2 for Haiku, 4 and 3 for Sonnet), `two-family-owner-residential` and `vague-smell-cleared` (2 and 2 against 3 and 3), where Haiku asked two things in one turn or re-read a confirmed address. It also chose `reschedule_appointment` correctly every time.

**Where Sonnet is worse.** It narrates its own reasoning to the caller ("The yes covers the read-back", "The tool only takes...", "The server has told me to decline"), which the grader's notes mention on 20 of 64 Sonnet calls and 2 of 64 Haiku calls (MEASURED, a search of `grader_notes` for internal, narration or reasoning). It probes the residence check harder and talks itself into declining renters in apartment buildings. It collects more than the technician needs, so calls run long. And it is a second slower to the first word on every turn.

## Decision rule (bake-off 4)

The hypothesis was that a larger model improves "feels like a person" a little and costs too much reply delay to use live, so Haiku stays. The rule, from the shared scorecard: Sonnet replaces Haiku only if it holds every must-pass case Haiku holds, meets the delay bars (plain under 1.2 s median, lookup under 2.5 s median), and beats Haiku on pass rate or grader score by more than the run-to-run noise.

**Result.** The hypothesis holds. Sonnet's grader score is 0.14 higher, within noise; its plain-turn delay misses the bar by 0.4 s; its pass rate is lower. Haiku stays the live model. Sonnet stays the after-call summary model (DECISIONS.md 006), where nobody waits.

## What this says to fix next

- The question pacing Sonnet does better is a prompt matter for Haiku ("one question at a time" and no re-reading a confirmed read-back), not a reason to change models.
- The sign-off line fails the grader on every safety call for both models. Either the signed-off wording or the grader's instruction about it should change; this is for the eval reliability lane and Jackson.
- The residence check: the server's commercial phrase list matches "apartment building", which a renter in their own apartment says naturally. Haiku avoided it in these runs by not asking; any model that asks will trip it. A narrow fix (the phrase only from a caller who manages or owns the building) or a side model for the `commercial` question are the candidates (evals/decision-models/PLAN.md).
- Not run: Opus, and the OpenAI and Gemini profiles (the harness speaks to Anthropic only). Opus would be slower than Sonnet, so it cannot pass the delay bar.
