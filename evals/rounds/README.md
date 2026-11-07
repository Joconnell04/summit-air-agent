# Eval rounds

One file per round of testing, with its numbers and the results files behind them. Two kinds:

- **Co-authoring rounds** (`01-` to `04-`): Jackson read real calls and labeled them; his notes set the grader's standards (DECISIONS.md 035).
- **Measurement rounds** (`<topic>-NN`): a bake-off, a check or a comparison, each with its question, method, numbers and verdict. A folder with the same name and `-data` holds results kept beside it.

| File | What it settled |
|---|---|
| [01-real-failures.md](01-real-failures.md) | Five real failures, labeled; the first grader standards |
| [02-seeds-and-holdout.md](02-seeds-and-holdout.md) | Ten seed scenarios and the hold-out set |
| [03-judge-labels.md](03-judge-labels.md) | Ten graded calls; the grader matched to Jackson's labels |
| [04-wording-standards.md](04-wording-standards.md) | The wording standards the grader fails on |
| [reliability-01.md](reliability-01.md) | Whether the must-pass set is steady enough for a floor |
| [model-matrix-01.md](model-matrix-01.md) | Haiku 5.5 against Sonnet 5.5 as the live brain |
| [side-model-01.md](side-model-01.md), [side-model-02.md](side-model-02.md) | The side model's accuracy, then whether it makes the agent oversensitive |
| [bakeoff-audio-01.md](bakeoff-audio-01.md) | Five stacks on the simulated audio rig |
| [bakeoff-audio-02.md](bakeoff-audio-02.md) | Our stack's end-of-turn wait |
| [stt-ink2-01.md](stt-ink2-01.md) | Cartesia Ink-2 against Deepgram Nova-3 for speech-to-text |
| [retell-flow-01.md](retell-flow-01.md) | Retell's Conversation Flow built to our design, against our stack |
| [lookup-prefetch-01.md](lookup-prefetch-01.md) | Handing the model the lookup result in the same turn: no difference worth turning on; code kept on its branch |
| [side-by-side-01.md](side-by-side-01.md) | Four agent arms on the real line (as-is, Sonnet, behavioral prompt, server-led): build on the server-led arm behind the custom-LLM socket; no safety errors on any arm |
| [sonnet-buildout-01.md](sonnet-buildout-01.md) | The Sonnet arm against the done bar with varied seeded callers: 35 of 36 journeys, no safety errors, narration never fixed by prompt; `fast` speech-to-text and dynamic responsiveness not worth turning on |
| [side-model-03.md](side-model-03.md) | A tighter at-risk question and a 0.65 threshold |
| [failure-grader-01.md](failure-grader-01.md) | The failure grader meets the precision and recall bars on 62 labeled calls |
| [simplicity-slate-01.md](simplicity-slate-01.md) | Whether the layering made the agent feel bad: simpler builds, one live call each |
| [retell-live-calls-01.md](retell-live-calls-01.md) | Jackson's live calls to the Retell agent, round 1 |
| [server-led-01.md](server-led-01.md) | The server runs the steps and the model talks: the arm the live agent is built on |
| [socket-buildout-01.md](socket-buildout-01.md) | The server-led flow behind Retell's custom-LLM socket, the live path |
| [knowledge-rag-01.md](knowledge-rag-01.md) | Embedding search for equipment questions; the Sonnet hand-off built and left off |
| [symptom-questions-ab-01.md](symptom-questions-ab-01.md) | Server-written symptom questions against fixed sentences: 6 real re-asks against 13 in 96 runs |

The ten real phone calls are scripted in [../live-calls.md](../live-calls.md).

Raw data from finished rounds whose folders no longer matter here (`server-led-01-data`, `sonnet-buildout-01-data`, `socket-buildout-01-data`) and the `retell-suite-01` notes are on branch `archive/voice-stacks`.
