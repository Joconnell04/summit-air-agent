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

The ten real phone calls are scripted in [../live-calls.md](../live-calls.md).
