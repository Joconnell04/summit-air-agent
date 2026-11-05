# Retell simulation suite

Jackson's Retell agent ("Summit Air Jackson", DECISIONS.md 112), tested in Retell's own simulation from a laptop, without phone calls. The case files here are the source of truth; `suite.py` creates or updates them in Retell, runs them as one batch, and pulls the results.

## What a run does

1. **Copies the live agent.** Reads the LLM version the live agent runs now (`agent_a3e38e6aa3d4e75dfac12aafc8`, `llm_12eba5f1d4a3b067f0635097de30`) and writes it onto our copy (`state.json`, `copy_llm`), so each run tests the current version. The only change is the function URLs: `/retell/tools/` becomes `/eval/retell/tools/`, the eval bridge. A custom function that would reach anything else stops the copy. The live agent, its LLM and the phone numbers are only read.
2. **Pushes the cases.** Each case file becomes a Retell test case on the copy, with the caller prompt, the success criteria, and the dynamic variable `summit_call_id` set to this run's call id for the case.
3. **Gets the eval world ready.** Resets the eval mock ServiceTitan to its seeded world and ages recorded calls, then starts each case's call on the eval tool server under its own id and caller number. That step is needed because Retell's simulation reaches the bridge as call `playground` every time; the bridge reads `summit_call_id` instead, and the call it names is already started. Starting it here also means a seeded caller (Maria Delgado, Gloria Santos) is matched by caller ID before hello, as on a real call.
4. **Runs one batch.** Retell plays the caller with Claude Haiku 5.5, the agent answers on the copy, and every tool goes to the eval tool server, so the server's gates (read-back before booking, safety lines, commercial refusal, service area) are exercised for real. No tool is mocked.
5. **Pulls the results.** For each case: Retell's verdict and its reason, the transcript, and our own code checks (`evals/checks.py`) run on the tool server's record of the same call. A case passes only if both pass.

## Commands

```bash
uv run python -m evals.retell.suite run                   # everything
uv run python -m evals.retell.suite run --tag must-pass   # one tag
uv run python -m evals.retell.suite run --only gas-smell-in-passing,commercial-bakery
uv run python -m evals.retell.suite pull <run>            # pull a run again
uv run python -m evals.retell.suite port <scenario> ...   # write case files from scenarios
```

They read `RETELL_API_KEY`, `RETELL_EVAL_TOOL_SERVER` and `RETELL_EVAL_DEBUG_TOKEN` from the environment (the laptop `.env`). A run refuses to start when another session has used the eval tool server in the last five minutes, since the reset would spoil its calls; `--force` skips that check.

Results go to `results/<run>/`: `run.json` (batch, copy, case and call ids), one JSON file per case (Retell's test run and our call record), and `summary.json`. Round write-ups are in `evals/rounds/retell-suite-*.md`.

## Case files

One YAML file per case in `cases/`, named by its id.

| Field | What it holds |
|---|---|
| `id`, `title` | The case, shown in Retell as `[summit] <id>: <title>` |
| `tags` | `must-pass`, `safety`, `requirement` (one of the six in docs/assignment.md), `live-call` (a failure from Jackson's calls), and the flow |
| `scenario` | The scenario in `evals/scenarios/` it was ported from, if any |
| `caller_id` | The number the call comes from. Made-up numbers are unique across cases, because the batch runs every case at once in one world |
| `user_prompt` | Retell's caller prompt: Identity, Goal, Behavior |
| `metrics` | Retell's success criteria, judged together into one pass or fail |
| `must` | Our code checks. Empty means the scenario's own `must` list |
| `tool_mocks` | Retell function mocks; none so far |

`port` writes a case from a scenario: the persona and hidden facts become the identity, the opening line the goal, the script the behavior; the criteria come from the scenario's code checks (safety lines quoted word for word from the tenant's config) and its `should` lines. Checks that only a regular expression states well (`agent_says`, `agent_never_says`) stay with the code checks. After porting, a file is edited by hand.

## Limits

- **The clock is real.** The eval tool server runs on today's date and time, so scenarios that set their own time (after hours, a visit window that has passed) are left out, and a run after closing behaves as an after-hours call.
- **No end of call.** Retell's simulation sends no call-ended event, so `ended_by`, `end_reason` and `outcome` are skipped, and the agent's last line after its final tool call isn't on the record. The summary's `ending` says whether the agent called Retell's `hang_up` once `end_call` allowed it (the prompt's closing rule). On the phone the bridge hangs up at that point anyway, so a miss here is invisible on calls.
- **Text, not audio.** These runs test what the agent says and does, not how it sounds or when it speaks. Listening, turn-taking and voice need web or phone calls (docs/retell-knobs.md on the Retell lane's branch).
- **One world per batch.** Cases book against the same mock at the same time, so an early booking can take a window another case would have been offered.
- **Cost.** Retell bills each simulated message at the agent's chat rate (Claude 5.5 Haiku, $0.001 per AI message, SOURCE: https://www.retellai.com/pricing) plus the chat platform rate ($0.002 and up per message, same page) and one analysis unit per case (SOURCE: https://docs.retellai.com/test/testing-pricing.md).
