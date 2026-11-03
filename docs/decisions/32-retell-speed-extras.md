# 32. Run the Retell baseline with Retell's paid speed extras, or with the same brain as our stack?

Issue: #32. Status: ready

## Question

Retell can run Claude Haiku 5.5 on a faster capacity tier and start a backup model alongside it that takes over when the first one is slow. Our LiveKit stack has neither. Should the Retell side of the bake-off use them? This only changes how the 10 Retell bake-off calls are set up; nothing in the build waits on it.

## Options

### A. Same brain (how it is set up now)
Haiku 5.5, Retell's standard capacity, temperature 0.3, no backup: the same model setup as our stack. The bake-off then measures the platform (ears, turn-taking, voice, call control) and nothing else, which is what its decision rule assumes.

### B. Retell's extras on
The faster tier and a fast-fallback backup model. Retell as well as it can be set up, so a Retell win can't be blamed on its setup; but a faster reply could come from paid capacity our stack could also buy, not from the platform.

### C. Both
The 10 calls on A, plus about 3 on B as a variant, reported separately.

## Context

| | A | B | C |
|---|---|---|---|
| What the result means | Platform only | Platform plus Retell's paid extras | Both, kept apart |
| Cost | About $0.19 for a two-minute call (MEASURED) | More per minute for the faster tier (ASSUMED; Retell's pricing page lists faster tiers as costing more without a figure for Haiku 5.5) | A plus about 3 calls on B |
| Effort | None | One line in build.py and a push | A push before and after the variant calls |

## Recommendation

C. A keeps the bake-off fair under its own rule (keep our stack unless Retell is more than 0.3 s faster), and a few calls on B show what Retell's best buys and costs, which is a useful answer if Revin asks "did you give Retell a fair shot?". This changes to A only if the extra calls aren't worth the time.

## Research notes

- Tiers: `model_config.primary.tier` takes `standard`, `fast` or `ultrafast`; "fast draws from dedicated capacity for lower, steadier latency ... faster tiers cost more" (SOURCE: https://docs.retellai.com/api-references/create-retell-llm.md, ServiceTier).
- Fast fallback: a backup model starts with the primary and takes over if the primary hasn't started answering after 0.3 to 60 s (default 1.5 s); voice agents only, not chat (SOURCE: https://docs.retellai.com/build/llm-options.md).
- Current numbers on A, booked scripted call `call_d570d98c180ab7ac1587b999d12`: Retell's median LLM latency 0.55 s, end-to-end 1.88 s, $0.19 for 112 s (MEASURED, Retell call data). The scripted caller talked over the agent, so the end-to-end figure is high.
