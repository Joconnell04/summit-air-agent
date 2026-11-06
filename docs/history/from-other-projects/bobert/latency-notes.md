# Headless audio test notes

Checkpoint log for bobert/audiotest.py.

## Interfaces found (livekit-agents 1.8.8, read from .venv)
- `livekit.agents.voice.io.AudioInput`: async iterator of `rtc.AudioFrame`; override `__anext__`, `on_attached`, `on_detached`. Constructor `AudioInput(label=..., source=None)`.
- `io.AudioOutput(label, capabilities=AudioOutputCapabilities(pause=...), next_in_chain=None, sample_rate=...)`: abstract `capture_frame`, `flush`, `clear_buffer`. Subclass must call `super().capture_frame()` / `super().flush()` (they count segments) and call `on_playback_finished(playback_position=, interrupted=)` once per flushed segment, else `wait_for_playout()` hangs. `on_playback_started(created_at=)` is optional but the console sinks emit it.
- Wiring: `session.input.audio = X`, `session.output.audio = Y` before `session.start(agent=...)`; with no `room` argument no RoomIO is created (the repo's conversation test already relies on this). `ChatCLI`/console uses the same two assignments (`cli/_legacy.py`, `cli/tcp_console.py`).
- Console sink reference: pushes bytes into a buffer, `flush()` starts a task that waits for real-time playout, then fires `on_playback_finished`. `clear_buffer()` sets an interrupt event.
- Events used: `agent_state_changed`, `user_input_transcribed` (has `is_final`, `created_at`), `conversation_item_added`, `close`.
- Turn detector: `inference.TurnDetector(version="v1-mini")` is a local ONNX model (no cloud call for v1-mini). Weights must be in the HF cache.

## What broke
- First smoke run: Cartesia TTS raised "Attempted to use an http session outside of a job context". Plugins need `livekit.agents.utils.http_context.open()` outside a worker. Fixed inside audiotest.py (no change to bobert/*.py).
- Smoke run 2 (2 turns) worked end to end: greeting, STT heard both lines, Bobert answered, check_service_area hit the server, console shows `selected.meta.source == "livekit"`. Console source lives at `/api/console?call_id=X` -> `selected.meta.source`, not in the `calls` list.
- Bobert asked "Is this for AC repair?" after the ZIP instead of offering times, so a "Yes, it's the AC." line was added to the script before the time pick.
- `ended_by_agent` initially read the session close event, which fires on our own aclose too; now it reads `rec.disconnection_reason == "agent_hangup"` (set by end_call).
- Full run 1 completed the pipeline (9 caller turns, agent hung up via end_call, lead saved) but no booking: Bobert asked address, then name, then number, read it back, and only then offered times, so the first script put "the first one works" too early. Script reordered to match.
- Full run 2: call completed. Booking bk_b2b977 landed (slot ser_1027_0800), lead saved with name, phone, address, ZIP; console source livekit. Turn detector v1-mini loaded locally without a download problem.
- Findings, not fixed (outside the task): (1) On the last line Bobert called end_call with no goodbye; the turn committed on "That's all." before "Thanks." finished. (2) STT final lands 2.2 to 4.1 s after the end of caller audio and is the same with `--vad-only`, so it is not the turn detector; cause not isolated. (3) Bobert's question order varies run to run, so the fixed script can mismatch.
- No changes to bobert/*.py were needed. Added: bobert/audiotest.py, Makefile, tests/fixtures/audio/, README section.

## Latency investigation (second pass)

Numbers are SOURCE (measured by bobert/audiotest.py, laptop to Deepgram, OpenAI, Cartesia) unless marked ASSUMED.
Targets from the main session were STT final under 1.0 s and first agent audio under 2.0 s from the end of caller speech (ASSUMED targets).

### Where the time went
1. **Harness bug, not the pipeline, explained all of the STT delay.** `SimInput.say()` stamped the start/end marks with a
   counter (`_queued`) that did not include the idle silence already emitted, so both marks fired at the next 20 ms tick, i.e. when the
   line was queued. "End of caller audio" was therefore the start of the pad, and every timing included 0.6 s pad + the whole line
   (2 to 3 s). That is the 2.2 to 4.1 s STT figure, and why `--vad-only` looked identical. Fix: marks are now stream positions
   (`_emitted + pending`). Pacing itself was fine (frames queued every 20 ms, wait in the input queue max 0.04 s, mean 0.000 s; 16 kHz, 20 ms
   frames; Deepgram audio position advances 1:1 with wall time).
2. **After the fix, STT final is 0.2 to 0.4 s** after the real end of speech (median 0.24 s at the library defaults), so the STT target was already met. Deepgram
   `endpointing_ms` defaults to 25 ms and fires `speech_final` about 0.2 s after the last word.
3. **End-of-turn wait.** `eou_metrics.end_of_utterance_delay` was 0.58 s on normal turns (Silero `min_silence_duration` 0.55 s) and **2.5 s
   (the library max_delay) on digit answers** (ZIP, phone number, and once "Yes. It's the AC."): the audio turn detector judged the caller
   not finished and held the full cap. This is a pipeline cost, not a harness one.
4. **LLM time to first token** is now the main cost: 0.45 to 1.8 s typical in-pipeline, with spikes of 2.2, 3.5, 4.5 and one 12.8 s (run 5, turn 2). A bare
   gpt-4.1-mini call with the same system prompt and no tools measured 0.49 to 0.73 s from this laptop (6 calls, SOURCE), so about 0.55 s is the floor and the tool schemas
   (llm.json is 11 KB) plus OpenAI variance add the rest. Turns that call a tool pay two LLM hops (tool call, then the spoken reply).
5. Cartesia TTS first byte 0.14 to 0.21 s. Agent-state `thinking -> speaking` lands about that long after the first LLM token.
6. Preemptive generation is already on by default in 1.8.8 (`TurnHandlingOptions.preemptive_generation`, `preemptive_tts` off): the LLM request starts when the
   final transcript arrives, 0.1 to 0.3 s before the turn commits. It is now an explicit setting.

Typical good turn, vad-only run: speech ends -> final +0.21 s -> turn committed +0.36 s -> first LLM token +0.8 s -> first audio +0.8 to 1.4 s.

### What changed
- bobert/audiotest.py: mark fix above; per-line report windows (a final that lands mid-line, such as a pause inside a phone number, stays with its own line);
  `early_audio` flag; `--trace` writes `trace.log` (raw Deepgram messages, VAD user state, STT events, EOU/LLM/TTS metrics, all relative to the end of the latest caller line).
- bobert/config.py (env override each): `STT_ENDPOINTING_MS` 100, `VAD_MIN_SILENCE_S` 0.35, `VAD_ACTIVATION` 0.5, `ENDPOINT_MIN_DELAY_S` 0.3,
  `ENDPOINT_MAX_DELAY_S` 1.5, `PREEMPTIVE_GENERATION` on, `PREEMPTIVE_TTS` off, `GOODBYE_LINE`.
- bobert/agent.py: reads those settings; `load_vad()` shared by prewarm and `build_session`.
- Not changed: activation threshold stays at 0.5 and Deepgram endpointing is raised from 25 to 100 ms, both on purpose for noisy phone audio (clean TTS clips would allow lower).

### Goodbye
The model skipped it. In the full runs the turn committed on "No. That's all." (Deepgram split the line at the pause; "Thanks." arrived as a second turn 0.7 s later),
and the model answered with `end_call` and no words. `end_call` already waited for the step's playout, but that step had no speech. Fix in bobert/tools.py: if the model spoke nothing
in the turn that called `end_call`, the tool returns at once and a background task says `GOODBYE_LINE` (`allow_interruptions=False`, so the late "Thanks." cannot cut it), waits for playout, then hangs up.
`say()` cannot be awaited inside the tool because it queues behind the running turn. Confirmed in the vad-only run: "Thanks for calling Summit Comfort. Take care." played, then hangup.
`../agent/retell/prompt.md` untouched (line 80 already says to thank them; the model does not always).

### Before and after, per turn (ms from the real end of the caller line)
Question order varies from run to run (the model, not the harness), so rows are the same script line, not always the same question.

| Turn | Caller line | A: run 1 old harness, STT | A: first audio | B: fixed harness, old settings, STT | B: first audio | C: new, turn detector (max 1.5 s) STT | C: first audio | D: new, vad-only STT | D: first audio |
|---|---|---|---|---|---|---|---|---|---|
| 1 | air conditioner warm air | 3798 | 5388 | 295 | 1315 | 92 | 2341 | 214 | 2435 |
| 2 | zip code | 3172 | 6523 | 228 | 3865 | 327 | 14359 (LLM ttft 12.8 s) | 373 | 2094 |
| 3 | yes, the AC | 2413 | 3235 | 238 | 3542 | 328 | 1722 | 318 | 1792 |
| 4 | street address | 3290 | 4003 | 209 | 4564 | 221 | 3354 | 77 | 769 |
| 5 | name | 2782 | 3648 | 207 | 1876 | 271 | 1488 | 360 | 2074 |
| 6 | phone number | 4167 | 6630 | 166 | 2651 | 279 | 4390 | 379 | 2196 |
| 7 | yes, that's right | 2452 | 5071 | 251 | 2129 | 349 | 1478 | 433 | 2573 |
| 8 | first one works | 2467 | 4359 | 261 | 1109 | 362 | 9123 (tool turn, LLM ttft 4.5 and 3.7 s) | 370 | 2464 |
| 9 | no, that's all, thanks | 2194 | none | 246 | none | 385 | 15 (answered before the line ended) | 353 | 1396 (goodbye) |
| median | | 2782 | 4715 | 238 | 2390 | 327 | 2341 | 360 | 2094 |

A and B are the same code and settings; the difference is the harness fix alone. A used the first final after the line end and B the same, C and D use the last final of the line.
An intermediate run with max_delay 2.0 (turn detector) gave STT median 311 ms, first audio median 2427 ms, with digit turns at 3.3 to 3.9 s; the 1.5 s cap is what C uses.

### Verdict against the targets
- STT final under 1.0 s: met in every run after the harness fix (max 433 ms). Was never a pipeline problem.
- First audio under 2.0 s: **not reliably met.** Median 2.1 to 2.4 s; about half of turns are under 2.0 s. What is left is LLM first-token time (0.5 to 1.8 s, plus
  spikes) and a second LLM hop on tool turns. Next levers, UNVERIFIED because not run: a faster LLM (`LLM=anthropic` with Haiku; no ANTHROPIC_API_KEY in .env so it could not be tried), a smaller tool schema,
  `PREEMPTIVE_TTS=1` (saves about 0.15 s, risk of a wrong early start), a filler line on tool turns.
- UNVERIFIED: real phone audio (8 kHz, noise). The test clips are clean Cartesia speech, so `VAD_MIN_SILENCE_S` 0.35 and `ENDPOINT_MAX_DELAY_S` 1.5 are untested against callers who pause mid-number.
  With 0.35 s VAD silence the line "No, that's all. Thanks." was split into two turns in the full run; the goodbye fix covers that case.
- UNVERIFIED: the old settings were never re-run in `--vad-only` on the fixed harness, so there is no clean vad-only before.
- Run count: 7 audiotest runs (one 2-turn trace, one 9-turn bug-confirming) plus one failed start; each a few cents. The goal of 4 to 5 was exceeded by 2 because the harness bug had to be found first.

## Haiku instead of GPT-4.1 mini (SOURCE: one vad-only run, 2026-10-23 13:10)

`LLM=anthropic` (claude-haiku-4-5), new settings, same script. STT final 200 to 331 ms. First agent audio per turn: 1021, 1896, 902, 927, 1206, 1004, 2365, 2265, goodbye spoken. Median about 1.0 s on plain turns against 2.1 s with GPT-4.1 mini; the two tool turns (availability, booking) are 2.3 s because they pay two model hops. Booking bk_ad6628 landed. The demo .env now runs LLM=anthropic; the code default stays openai so the Retell comparison holds. Next lever for tool turns: a filler line when a tool call starts, or preemptive TTS.

## Filler line at tool start (SOURCE: two vad-only runs with Haiku, 2026-10-26)

Tried: `context.session.say(filler, add_to_chat_ctx=False)` fired when the tool is invoked, not awaited, so the second model hop would hide behind "One moment while I check the schedule." It played (13 agent segments for 10 turns), but the real reply came 9 s after the preamble on the availability turn and 12 s after the caller on the booking turn, against 2.3 s without it. The extra speech handle appears to take the floor and the tool turn's own reply waits behind it and its playout. `with_filler()` is not an option either: it waits for idle and closes when the tool returns, and the tool returns in milliseconds. `TOOL_FILLER` stays off by default; the code is there for a second attempt with a handle the turn can pre-empt. The honest number for tool turns is still 2.3 s, bounded by two model hops; the next lever is a smaller tool schema or preemptive TTS.
