# Decisions

One entry per real choice, newest first. Each entry: what was decided, why, what else was considered, what would change it, and a link to the discussion or exploration behind it.

<!-- Template
## NNN. Title
Decided: ...
Why: ...
Considered: ...
Would change if: ...
Discussion: docs/ai-process/transcripts/... or docs/explorations/...
Issue: #N
-->

## 002. Test with our own harness, scored by code first

Decided: our own pytest harness. A simulated caller talks in text to the real prompts and tool server on a scratch database. Code checks on the tool-call log and saved records decide pass or fail; an LLM grader scores conversation quality and can fail a run the code passed, never rescue one it failed. About 10 real audio calls are added once the voice platform is chosen. A quick run is about 8 scenarios (about $1, ASSUMED); the full suite runs only at a release gate (about $15, ASSUMED).
Why: the rules that matter (safety lines before anything else, read-back before booking, no commercial bookings) live in server code, so only a harness that reads server state can prove them. The earlier practice agent's worst bugs were ordering bugs that a transcript judge misses and a tool-log check catches exactly.
Considered: the voice platform's own simulator (Retell's can't check tool arguments or the database; LiveKit's only works if LiveKit wins), a general eval framework such as promptfoo or Braintrust (adds a dependency without removing the harness), hosted voice testing such as Cekura, Coval or Hamming (judges from outside the call, unpublished pricing).
Would change if: LiveKit wins the platform decision and our harness grows past about 500 lines, in which case LiveKit's test helpers take over the per-turn checks.
Discussion: docs/decisions/06-eval-design.md
Issue: #6

## 001. Separate speech-to-text, model and voice stages, not one speech-to-speech model

Decided: a cascaded pipeline (Deepgram to hear, a Claude Haiku-class model to decide, Cartesia to speak). The gas and carbon monoxide safety lines are spoken from code as fixed text, with no model in between. Side models (an urgency or vulnerability classifier, a typed decision model) run beside each turn, off the reply path; we launch with the fixed list and server gates and test OpenAI Decisions and Jev in the evals before adding either.
Why: speech-to-speech is not faster. Published first-audio times for OpenAI Realtime and Gemini Live are 0.96 to 1.14 s (SOURCE, in the readout) against about 1.0 s MEASURED for the cascaded stack on the earlier practice agent. It also costs more (about $0.025 to $0.18 per minute against about $0.02 to $0.04, SOURCE and ASSUMED), can't guarantee the safety wording word for word, and its caller transcript can arrive after the reply has started, too late for the server's checks.
Considered: speech-to-speech (OpenAI Realtime, Gemini Live); a hybrid where speech-to-speech talks and a cascade runs the checks (two stacks to pay for and test); Laya as a side model (missed 6 of 10 emergencies in the earlier practice agent's eval, English only).
Would change if: a speech-to-speech profile matches the cascade on tool success and the persona evals, or Haiku 5.5 with thinking off measures over 1.5 s to first audio (fall back to Haiku 4.5, the measured control).
Discussion: docs/decisions/05-pipeline-and-models.md
Issue: #5

