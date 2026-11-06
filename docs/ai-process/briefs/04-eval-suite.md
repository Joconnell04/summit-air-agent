# Build brief: eval suite and grader

Branch: `build/eval-suite`. Depends on the tool contract; runs against the tool server once it merges.

## Goal

The harness that tests the agent before anyone calls it, the same way for every stack and model profile, cheap by default. Design: evals/README.md and DECISIONS.md 002. Results go to LangSmith through the existing `evals/langsmith_sync.py`, using the existing schema in `evals/results.py`.

Read first: CLAUDE.md, evals/README.md, evals/results.py, docs/spec.md, docs/call-journeys.md, docs/bakeoffs.md, .claude/agents/red-team-caller.md, docs/history/from-other-projects/bobert/adversarial-triage-log.md.

## Scope

- Runner: a simulated caller (profile `offline.caller`) talks turn by turn to the agent under test: our stack's brain in process, or the Retell baseline through its text chat interface (an adapter, so both are scored alike). Fresh scratch copy of the mock ServiceTitan and a unique call id per scenario.
- A transcription layer that roughens caller lines the way speech-to-text does.
- Scenario files with flow, difficulty (easy, standard, rigorous, adversarial) and traits (stutters, self-corrections, late corrections after the read-back, split digits, interruptions, two speakers, injection). About 40 to start, covering every journey and every must-pass line; use the red-team-caller agent to draft adversarial ones.
- Code checks on the tool-call log and saved records decide pass or fail; the grader (profile `offline.grader`, thinking on) scores how the call felt and can fail a pass, never rescue a fail.
- Suites as tag filters (`evals/suites.yaml`): smoke, standard, rigorous, full. `make eval SUITE=... TAGS=... STACKS=... PROFILES=...`.
- Per-turn timings and cost per scenario; each run compared with the previous run of the same suite and stack, with delay changes over 100 ms or 10% flagged.
- Replay: `make replay CALL=<id>` re-runs a recorded call's caller turns and diffs the tool-call sequence.
- Ratchet: results committed with a hash of prompts, scenarios and profile; `scripts/ratchet.py` fails on a stale hash, fewer scenarios, a failed must-pass case, or a pass rate under the floor in `evals/baseline.json`. `make check` stays offline.
- The noisy-audio and interruption cases for the live pass are written here as scripts for the 10-call audio pass.

## Must be right, each with a test

Offline tests for the runner with a fake model: scoring rules, must-pass handling, the error outcome, suite filtering, the comparison report and the ratchet changes.

## Done when

`make check` passes; `make eval SUITE=smoke` runs end to end against the merged tool server for about $1 (ASSUMED) and uploads to LangSmith; the reviewer agent has checked the branch.
