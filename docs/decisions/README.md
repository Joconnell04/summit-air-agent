# Decision readouts

One file per issue that needs Jackson's call, named `NN-<slug>.md` after the issue number. Research happens first, in the background; the readout is what Jackson reads in a few minutes beside the chat, then answers in chat. The outcome goes in DECISIONS.md and the issue is closed.

## Format

```
# NN. <Question as a title>

Issue: #NN. Status: researching | ready | decided

## Question
One or two sentences: what is being decided and what it blocks.

## Options
### A. <name>
Purpose, reasoning and trade-offs in one to three sentences.
### B. <name>
...

## Context
What picking one over another trades away, in plain words: cost, latency, risk, effort, what it shows Revin. A small table is fine.

## Recommendation
The pick and why, in about two sentences, and what would change it.

## Research notes
Evidence behind the readout, numbers labelled SOURCE, MEASURED or ASSUMED, and sources with links. Jackson doesn't need to read this part to decide.
```

Everything above "Research notes" fits on one screen.
