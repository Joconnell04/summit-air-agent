# Latency budget

How long a caller waits for the agent to start answering, split by stage, so every millisecond has an owner and each stage is measured on every call. The targets are spec S-36: a median under 1.2 s on plain turns and under 2.5 s on turns with a lookup or booking, measured from the moment the caller stops speaking to the first sound of the reply, on real phone audio. Targets are revisited whenever Jackson flags a delay (docs/later.md).

## Plain turn (no tool call)

| Stage | Budget | Basis |
|---|---|---|
| Phone network in and out (Twilio, SIP trunk to LiveKit) | 150 ms | ASSUMED; measured on the first real calls |
| Deciding the caller has finished (turn detection) | 400 ms | MEASURED 0.35 to 0.6 s on the earlier practice agent (from-other-projects/bobert/latency-notes.md) |
| Final transcript (Deepgram) | runs alongside turn detection | MEASURED 0.2 to 0.4 s after speech ends, same source |
| Model's first words (Claude Haiku, thinking off) | 500 ms | ASSUMED for Haiku 5.5; Haiku 4.5 measured about 1.0 s to first audio end to end, same source |
| First audio from the voice (Cartesia) | 150 ms | ASSUMED; measured on the first real calls |
| **Total** | **about 1.2 s** | |

## Turn with a lookup or booking

| Stage | Budget | Basis |
|---|---|---|
| Plain turn up to the model deciding to call a tool | 1.0 s | as above, without the voice |
| Tool on our server (cached slots, local database) | 100 ms | ASSUMED; the mock answers in 1 to 16 ms (MEASURED, Postman run) |
| A real FSM call, when one is needed | 300 to 800 ms | ASSUMED; avoided on most turns by the lookup before hello and the slot cache |
| Model's second hop (reads the tool result, starts the reply) | 500 ms | ASSUMED |
| First audio | 150 ms | ASSUMED |
| **Total** | **about 1.8 s, up to 2.5 s with an FSM call** | MEASURED 2.3 s on the earlier practice agent |

## Where the time is saved

- **Lookup before hello** and **cached slots** remove most FSM round trips from the conversation (docs/decisions/05-pipeline-and-models.md, the harness).
- **Short tool results** with a ready-to-say sentence keep the second model hop short.
- **Digits:** when the agent has just asked for a phone number or ZIP, it waits a little longer before deciding the caller has finished, because people pause between groups of digits; the earlier practice agent lowered the cap to 1.5 s with 0.35 s of silence (MEASURED).
- **No filler line by default.** "One moment" at the start of a tool call held the real reply back 7 to 10 s on the earlier practice agent (MEASURED). Not used unless a measurement shows it helps.
- **Thinking off** on every live profile (config/models.yaml).

## How it's measured

Every turn writes timestamps to the call trace: caller stopped, turn end decided, final transcript, model first token, tool start and end, voice first byte, first audio out. `make trace CALL=<id>` prints them per turn, and the live pass reports median and slowest 10% per turn type. The measurement is checked first against a recording with known timings, because the earlier practice agent once blamed 2 to 4 s on speech-to-text that was really a timestamp bug in the harness.
