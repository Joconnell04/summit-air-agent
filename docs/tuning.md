# Tuning knobs

The settings that change how a call feels without changing what the agent does. Each one is named here with the problem it solves, where it lives and how it gets tested, so a complaint from a real call ("it cut me off", "it talks too fast") maps to one setting. Values marked ASSUMED come from the earlier practice agent (Bobert, a personal practice project, not a person; from-other-projects/bobert) and are replaced as they're measured.

| Knob | Problem it solves | Where it lives | Now | How it's tested |
|---|---|---|---|---|
| End-of-turn wait | Slow replies, or the agent answering before the caller has finished | `config/models.yaml` `voice.endpointing` (`normal`, and a longer `digits` wait after asking for a phone number or ZIP) and `voice.vad.min_silence_s` | normal 0.3 to 1.5 s, digits 0.9 to 3.0 s, silence 0.35 s (ASSUMED) | Audio rig: plain-turn median and slowest 10%, plus cut-off and talked-over turns, at the current and shorter values; must-pass safety calls still pass |
| Interruption sensitivity | The agent stopping for a cough or a TV, or not stopping for a real "wait, no" | `voice.interruption` (`min_words`, `min_duration`, false-interruption resume) | 2 words, 0.5 s, resume after 1.5 s (ASSUMED) | Live calls 4 and 5 in evals/live-calls.md; audio rig with noise tracks if a change is needed |
| Reply length cap | Long turns when the prompt slips | The model profile's output-token limit in `config/models.yaml` | not set | Text harness: turn length and grader score at two caps |
| Sampling temperature | Run-to-run variance: stray phrasings, flaky scenarios | The model profile in `config/models.yaml` | not set, so the provider default of 1.0 | Standard suite at 1.0, 0.6 and 0.3, two runs each: pass rate, must-pass, grader score |
| Speaking rate | Callers who can't keep up, especially older callers and anyone hearing an address or number | `voice.tts` speed (Cartesia sonic-3 takes 0.6 to 2.0, SOURCE: Cartesia API docs via the LiveKit plugin) | not set, so 1.0 | Live calls and a listen; see "Speaking rate during a call" |

## Speaking rate during a call

Possible, and per call. Each call has its own speech session in the LiveKit worker, and the Cartesia plugin's `update_options(speed=...)` changes the rate for what is spoken next, so one caller slowing down doesn't affect another call.

Proposed shape, consistent with the rest of the build: the server decides, the worker applies. The tool server already reads every caller line, so it can mark a call "slow" when the caller asks ("slow down", "sorry, what?" twice) or when priority finds an older adult, and the worker sets the rate (about 0.85, ASSUMED) for the rest of the call. A model tool that sets the rate is also acceptable here, because pace is manner rather than a gate, but the server trigger is cheaper and testable. The fixed safety lines are spoken at the same slower rate. About half a day with tests (ASSUMED). Retell sets speed per agent, not per call, so this is our pipeline only.
