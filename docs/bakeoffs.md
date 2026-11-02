# Bake-offs

Choices settled by measurement rather than opinion. Each is kept small by default (a cheap subset of scenarios and a few real calls), with the full run saved for a release gate. Results go in DECISIONS.md.

| Bake-off | Compares | Measures | Size | Status |
|---|---|---|---|---|
| Voice platform | Our LiveKit stack vs Retell on the 678 number | Eval pass rate, reply delay on real phone audio, interruptions, safety gates holding | Same eval scenarios plus 10 real calls each | Decided to run (DECISIONS.md 004) |
| Speech-to-speech check | OpenAI Realtime and Gemini Live vs our cascaded stack | Reply delay, tool-call success, whether the safety lines come out word for word | About 8 scenarios and 3 calls each (ASSUMED budget: a few dollars) | Planned, in #5 |
| Side models | None vs a second Haiku call vs dedicated decision models (Jev, post-trained Laya, OpenAI Decisions) | Accuracy on labelled lines, added time, cost; only where a bottleneck has been found | evals/decision-models/PLAN.md (quick run about $0.08) | Planned, in #5 |
| Model matrix | Haiku, Sonnet, Opus, and OpenAI and Gemini equivalents, as the brain | Full eval suite, reply delay, cost per call | Full suite, at a release gate | Later; designed once the agent works end to end |

Multilingual (Spanish) runs are left out of every bake-off for now and added later.
