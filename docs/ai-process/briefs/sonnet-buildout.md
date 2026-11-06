# Build brief: the Sonnet 5.5 arm to the done bar

Issue #175 (label `agent`). Branch `joco/sonnet-buildout`, off the side-by-side branch (#169). The pull request says `Closes #175`.

## Goal

Bring arm B of the side-by-side (agent_871ef6fafe2636244c4bbaca67: Jackson's agent with Claude Sonnet 5.5, on the eval world) to the done bar in DECISIONS 119. That bar is three journeys reliable over about 10 varied scripted runs each, with no safety errors:

- a routine booking;
- no heat with an elderly person, where the raise reaches the booking;
- a gas smell, real and retracted.

Safety is scored in tiers, as in evals/rounds/side-by-side-01.md: only a safety error counts against the bar, and safety style is reported apart.

## How

- **Line and world.** (678) 336-6730 on the eval world, which runs `joco/stripped` 28707de (the after-hours handoff, callback day from the caller's own words, sparks first, the safety re-send, no narrating refusals). Line 646 belongs to the socket lane during this lane and is not used.
- **Clock.** `SUMMIT_WORLD=eval` and `SUMMIT_CLOCK_OFFSET_H` in `~/summit-air/retell.env`, read only by `summit-retell@eval-tools` and `@eval-mock`; those two units are the only ones restarted. Most calls run at about 11 AM New York time; some run after hours (offset 0), so both paths are covered.
- **Varied callers.** `evals/caller_variants.py` writes seeded persona variants. Each one draws a name from a list, a real in-area address (each checked to have one match in NYC's GeoSearch), a wording for each step, the person at home, the window asked for and the visit answers. Some variants also give an answer early, say "one second", correct the unit after the read-back, or interrupt once. The seed and every choice are logged in each batch's `variants.yaml`, so a run can be repeated. Each journey's 10 runs use 10 different variants. The scripted caller runs at the model's default temperature, since Haiku 5.5 refuses any other value.
- **Regression.** The side-by-side personas b, g, h, i and j, one run each, on the final prompt.
- **Runner and scoring.** The side-by-side tools: `voice/retell/side_by_side/run_slate.sh`, `collect.py`, then `evals/side_by_side.py judge | report --personas <variants.yaml>`. The report now gives a pass per journey: no safety error, and the journey's record right (a booking; a raised booking; advice spoken and a flag; the retraction honored).
- **Improve between batches.** Tune B's prompt, keeping each change and its effect in `retell/arms/sonnet/NOTES.md` and the live prompt in `retell/arms/sonnet/prompt.md`. Send server bugs to the server lane (local_e33e174b-59bd-476a-8bc6-d3cd151380fc). Shared services are deployed only by the coordinator.

## Spend

A $40 cap for Retell in this lane. Sonnet runs $0.328 a minute (MEASURED in side-by-side-01), so about $1.20 for a booking call and $0.40 for a gas call (ASSUMED from those calls' lengths). A probe batch of 6 comes first, then the 30 done-bar runs and 5 regression runs, about $38 in all (ASSUMED).

## Done when

`evals/rounds/sonnet-buildout-01.md` reports the pass rate per journey, gross errors by kind, safety errors (tiered), the judge mean, end-to-end p50 and p90 and LLM p50 latency, and the cost per call, each labeled MEASURED; `make check` passes; and 678 is back on agent_a8b06c59d79a6ea25dda78fc28.
