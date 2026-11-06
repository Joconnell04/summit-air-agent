# Bobert

An earlier personal practice project, not a person: the name is just what the project was called.

An earlier voice agent for a made-up Atlanta HVAC company ("Summit Comfort"). Inbound: qualifies the caller, books, moves or cancels a visit, hands emergencies to a person. Outbound: calls back customers with an unsold estimate. Two voice layers, one tool server.

- **Retell layer.** Agent config as code (prompt, LLM, tools), pushed with a script. Phone number bound to the agent.
- **Tool server.** FastAPI and SQLite. Nine inbound tools, outbound tools, a trace console, Postman and Newman suites, 240-plus offline tests. It owns every fact and every rule.
- **In-house layer.** A LiveKit Agents worker with Deepgram for speech to text and Cartesia for speech, calling the same tool endpoints.

## What worked

- Putting rules on the server. The model asks; the server decides. A model that misbehaves cannot book a taken slot or call someone who said stop.
- Adversarial personas run in small batches through simulated calls: 15 inbound, 10 outbound, then a second wave. Each failure became a prompt line, a tool message or a server rule, plus a permanent case.
- A trace console showing transcript, tool call and database row per call, with a call-path strip. Drills (plant a bug, find it from the console) made the live debugging real.
- A replay harness that re-ran a real stress call against the current prompt. The old agent recorded the call as lost; the new one stated the server's exact offer.

## What failed, and the fix

| Problem | Fix |
|---|---|
| The model skipped the callback read-back, then set `confirmed: true` anyway. | A flag the model can set is not a gate. The tool returns the exact question and saves nothing; `confirmed` counts only if that question was already returned on this call. |
| Booked under a phone number nobody had read back (two callers on one line). | Server gate `read_phone_back`: `book_appointment` refuses once per call until the agent's lines contain the digits. |
| Agent ended the call in the same turn as the booking confirmation, so "actually, cancel that" never happened. | Prompt: ask "Anything else?" and never end the call in that turn. |
| Simulator call ids were stamped to the minute, so a read-back crossing a minute boundary lost its own question. | Ids minted once per simulated call. A harness bug, not an agent bug. |
| First audio target was under 2.0 s; STT looked like 2 to 4 s. | Harness timestamp bug. Real numbers: STT final 0.2 to 0.4 s, end of turn 0.35 to 0.6 s. |
| Digit answers (ZIP, phone) held the turn open for the library's 2.5 s cap. | Lowered the endpoint cap to 1.5 s and the VAD silence to 0.35 s; goodbye line moved worker-side. |
| A filler line at tool start ("one moment") held the real reply back 7 to 10 s. | Shipped off. Tool turns stay near 2.3 s, bounded by two model hops. |
| Booking, one lead with five live bookings, and all simulations sharing one call id on the console. | One active booking per lead; simulated call ids per case. |

Measured latency (SOURCE: one scripted run each, clean TTS audio, not real phone audio): first audio about 1.0 s on plain turns with Haiku against 2.1 s median with GPT-4.1 mini; tool turns about 2.3 s with either. See `latency-notes.md`.

## What carries into Summit Air

The tool-server contract (200 with `ok: false`), idempotent booking, server-side gates for read-back and safety, the persona suite, the replay harness, the console, and the habit of measuring latency per stage before tuning. The inbound prompt is a starting point for style (short turns, banned stock phrases, one question at a time).

## Deliberately not carried over

- **Outbound calling and its consent and calling-hours gate.** The Summit Air brief is an inbound line.
- **Negotiation and concessions.** The brief has the agent never quote prices; a technician prices on site.
- **Decision models (OpenAI Decisions, Laya).** Useful for judging "did the caller say yes", but they add a vendor and a failure mode. `decision-model-eval.md` shows where a regex gate fails; Summit Air starts with server gates and a keyword floor for safety, and adds a model only if evals show a gap.
- **ServiceTitan adapter and the in-house LiveKit stack.** No CRM or telephony choice is fixed by the brief.

## Files here

`architecture.md`, `inbound-prompt.md`, `adversarial-triage-log.md`, `latency-notes.md`, `decision-model-eval.md`. Copied unchanged except phone numbers replaced with +1 555 0100.
