# 27. What pass-rate floor should the eval ratchet enforce?

Issue: #27. Status: decided

## Question

`make check` now fails on a stale eval run, fewer scenarios, or any failed must-pass case. The last piece is the pass-rate floor (`min_eval_pass_rate` in `evals/baseline.json`, null today) and which run becomes the first reference. Nothing in the harness waits on this.

## Decision

C, set once the standard suite has run on the real prompts; that run becomes the reference. DECISIONS.md 011.

## Options

### A. No pass-rate floor yet
Must-pass failures, stale hashes and fewer scenarios still fail the check. Nothing to tune, but a slow slide on ordinary calls (tone, extra questions, wrong slot) would go unnoticed.

### B. A fixed 90% floor
The bake-off bar for "books correctly" (docs/bakeoffs.md). Easy to explain, but with about 30 judged scenarios one call moves the rate about 3 points, so a single flaky call can block a merge.

### C. Measured floor, ratcheted
Adopt the first standard-suite run on the real prompts and set the floor at its pass rate rounded down to the nearest 5%; only a later adopted run can raise it.

## Context

| | A | B | C |
|---|---|---|---|
| Catches safety regressions | yes (must-pass rule) | yes | yes |
| Catches a slide on ordinary calls | no | yes, if above 90% to start | yes |
| Blocks merges on noise | never | often, near the bar | rarely (5-point cushion) |
| Work | none | none | one adopt after prompts land |

The smoke run on this branch used the harness's stand-in prompt, so it shows the harness works; it says little about the real agent and shouldn't be the reference.

## Recommendation

C, adopted once the voice lane's prompts are in `prompts/`: the floor starts from what the agent actually does, the rounding absorbs one or two noisy calls, and safety is held by the must-pass rule whatever the number is. If the standard suite proves noisy across repeat runs, fall back to A until the full suite's three samples per scenario are affordable.

## Research notes

- One call is 1/30 of a 30-scenario suite, about 3.3 points (ASSUMED suite size: the standard suite has 30 easy and standard scenarios today, MEASURED by `select(load_all(), "standard")`).
- Error runs (simulated caller loops, harness breaks) have no verdict and are left out of the rate (decision 06), so they can't push it under the floor.
- Adopting: `uv run python scripts/ratchet.py --adopt evals/results/<run>.json`; it refuses a run with a must-pass failure and never lowers `min_eval_scenarios`. The floor itself is set by hand in `evals/baseline.json`.
