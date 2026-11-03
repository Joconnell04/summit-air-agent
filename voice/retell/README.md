# Retell baseline

The Retell half of the voice-platform bake-off (DECISIONS.md 004, docs/bakeoffs.md bake-off 1): a Retell agent on (678) 336-6730 that runs the same conversation as our LiveKit stack (`voice/livekit/`) against the same tool server. If Retell wins, this folder becomes the product; if not, it is deleted in one step. It holds no rules: every fact and gate is the tool server's (docs/tools.md).

## How a call flows

```
caller -> Retell (ears, turn-taking, Claude Haiku 5.5, Cartesia voice)
            |  signed HTTPS
            v
         bridge (voice/retell/bridge.py, :8102 behind Caddy)
            |  127.0.0.1
            v
         tool server (app/, :8100), shared with the LiveKit worker
```

1. **Before the call connects** Retell calls the bridge's inbound webhook. The bridge calls `/calls/start` with Retell's preallocated call id as `retell_<id>`, builds the prompt with `prompts/assemble.instructions` and the greeting from the tenant's `agent.yaml` (`voice:`), and returns them as the dynamic variables `{{instructions}}` and `{{greeting}}`. Retell's prompt is just `{{instructions}}`, so it reads exactly what the LiveKit worker reads.
2. **Every custom function** (all 13 model tools, from `prompts/assemble.tool_specs`) posts to `/retell/tools/<name>`. The bridge first posts any transcript lines not yet sent to `/calls/{id}/turn`, then forwards the arguments with `call_id` and an `Idempotency-Key` derived from call, tool and arguments, and returns the envelope without empty fields, as the worker does.
3. **The `transcript_updated` webhook** posts new lines as they happen, so the turn check (safety, commercial, do-not-call) runs on every caller line during the call. When it asks for the safety lines, the bridge hands Retell the exact text through Update Live Call with `trigger_response`, which cuts in if the agent is talking. Commercial and do-not-call hints go in as context for the next reply, without cutting in.
4. **`end_call`** is the server's. Retell's built-in end-call and transfer tools are not configured, so the model has no other way to hang up. When the server allows a hang-up, the bridge stops the call through Retell's API once the goodbye has had time to play, and cancels that if the caller speaks again first.
5. **`call_ended`** posts the last lines and calls `/calls/{id}/end` with Retell's disconnection reason mapped to ours; **`call_analyzed`** keeps Retell's per-call latency and cost in the bridge's database.

Every request from Retell carries `X-Retell-Signature` (HMAC-SHA256 of body and timestamp with the API key, five minutes valid); anything unsigned gets 401, and a function the server doesn't have gets 404. The shared tool server is never public. The eval instance's tool server is reachable through Caddy at `/eval/` only with its token in `X-Debug-Token`, because its endpoints are not signed and it holds only scratch data.

## Commands

```bash
make retell-push
```

Builds the LLM, voice agent and number binding from code and sends only what differs; a second run prints "nothing sent". Lists use only the versioned endpoints (`POST /v2/list-agents`, `GET /v2/list-retell-llms`, `GET /v2/list-phone-numbers`); create, get and update have no versioned form in Retell's current spec. Every endpoint the project may call is in `api.ALLOWED`, and anything else is refused before a request goes out.

```bash
make retell-deploy
```

Copies this checkout to `~/summit-air/retell` on the summit-air server and runs the bridge and an eval instance under systemd (`deploy/retell/`), beside the shared tool server and mock that the LiveKit lane's `deploy/summit-air/` runs on ports 8100 and 8101 (brief 03, its own branch). HTTPS is Caddy on `45-77-105-247.sslip.io`.

```bash
make retell-report DB=~/summit-air/retell-data/bridge.db
```

On the server: Retell's own stage latencies (median and slowest 10%) and cost per call, from `call_analyzed`, for the bake-off report beside the per-turn `reply_ms` in `make trace`.

```bash
make retell-push EVAL_URL=https://45-77-105-247.sslip.io/eval
```

Also builds the chat agent the eval harness drives through Retell's text chat API (`voice/retell/chat.py`, used by `voice/retell/eval_adapter.py`, which the eval harness loads by name). It calls the eval bridge, which forwards to a separate tool server and mock ServiceTitan on the same host.

## Settings, and why

| Setting | Value | Why |
|---|---|---|
| Model | `claude-5.5-haiku`, standard tier, temperature 0.3 | The `fast` profile in config/models.yaml and the worker's temperature. Retell's `fast` tier and its fast-fallback backup model would lower tail latency but are a platform extra our stack doesn't have; left off so the brain is the same, and worth one variant run. |
| Prompt | `{{instructions}}` | The whole per-call prompt from prompts/assemble.py, call context included. |
| First line | `{{greeting}}`, agent speaks first | The worker's greeting rule (`greeting_known` with one matched home customer, else `greeting_unknown`); the safety lines first on a call that resumes a dropped safety call. |
| Voice | Cartesia Sonic 3, voice `f786b574-...` ("Katie") | The worker's voice, added to Retell by its Cartesia id, so both stacks sound the same. |
| Transcription | `stt_mode: fast`, noise cancellation | Retell's own turn-taking stack. Its custom mode (Deepgram with fixed endpointing) would copy our ears but drop Retell's turn detection, which is what this bake-off measures. |
| Responsiveness | 0.9, dynamic responsiveness on | Slightly slower than Retell's default 1.0 so callers reading digits in groups aren't cut off; dynamic mode adapts to each caller's pace. Retell has no per-question setting like the worker's longer wait after asking for digits (C-48). |
| Interruption sensitivity | 0.8 | Retell's guidance for background noise: lower it from 0.9 to 0.8 (SOURCE: https://docs.retellai.com/build/handle-background-noise.md). |
| Backchannel | on, frequency 0.4, "mm-hmm", "okay", "uh-huh" | Retell's feature; kept rare. The worker has none. |
| Reminders | after 10 s of silence, at most twice | Retell's default interval, 10,000 ms (SOURCE: https://docs.retellai.com/api-references/create-agent.md, `reminder_trigger_ms`). |
| End on silence | 45 s | Retell's own timeout; it ends the call without asking the server (see gaps). A dropped safety call still gets a person flagged at `/calls/{id}/end`. |
| Max call length | 20 min | Cost cap. |
| Boosted keywords | Summit Air, HVAC terms | Recognition of the company name and equipment words. |
| Tools | custom, `speak_during_execution: false`, 10 s timeout, one retry | The worker plays a short clip only when a tool is slow (0.7 s); Retell can only speak on every call or never, so never. Retries are safe because writes are idempotent. |

All values are ASSUMED starting points until the 10 bake-off calls; changes go here with the reason.

## Every difference from the LiveKit stack

| | LiveKit stack | Retell baseline |
|---|---|---|
| Ears and turn-taking | Deepgram Nova-3, our endpointing, longer wait after asking for digits | Retell's own; responsiveness and dynamic responsiveness, no per-question wait |
| Brain | Claude Haiku 5.5 through Anthropic's API, prompt caching on | Same model through Retell (`claude-5.5-haiku`); Retell controls caching and the request |
| Tool parameters | Full JSON schema | Retell drops `default`, `minimum`, `maximum` and `format` (MEASURED on the first push); the server still enforces them |
| Safety lines | Spoken from code as fixed text, can't be interrupted | Injected through Update Live Call and said by the model; counted as said only if the agent's line has them word for word. In text chat (evals) there is no injection; the lines reach the model only through the next tool result and the server's refusals |
| Hang-up | Worker deletes the room after the server's `end_call` | Bridge calls Retell's stop-call after an estimated goodbye (3 s plus 0.35 s a word, ASSUMED). Any caller line before then cancels it, a "bye" said over the goodbye included, so the call can stay open until the model calls `end_call` again or the 45 s silence timeout; the timer is lost if the bridge restarts |
| Tool latency | Tools called directly | Each function first posts any transcript lines not yet sent, one short request each to the tool server on the same host, before the tool runs (adds to Retell's tool time; MEASURED tool times on the server were 2 to 55 ms) |
| Caller lines in the trace | Each final transcript once | A caller line that grew between Retell's events is posted again whole, so the safety check sees "I think I smell gas" in one piece; the trace and owner view show both copies, and on a safety call each copy can make the bridge send the lines again |
| Bridge line | Pre-recorded clip only for tools slower than 0.7 s | None |
| Timeouts | Ours | Retell's silence timeout and max duration can end a call without the server |
| Per-turn timings | `reply_ms`, `llm_first_token_ms`, `tts_first_byte_ms`, `tool_ms`, caller `transcript_ms`, `end_of_turn_ms` | `reply_ms` only (caller's last word to agent's first, from Retell's word timestamps); stage latencies only as per-call medians and slowest 10% after the call |
| Server-down | Says `server_down` and hangs up | Same line as the greeting, then stop-call |
| Eval runs (text) | In process, the greeting is in the model's history | Retell chat API; Retell chat starts with the caller, so the greeting is shown to the simulated caller but is not in the model's history |
| Outbound callbacks (journey 12) | Yes | Not built |
| Phone number | (646) 624-0418 on our Twilio trunk | (678) 336-6730, Retell's own Twilio number; can't move (exploration 004) |

## Gaps Retell can't close

- **No veto on Retell's own hang-ups.** Silence timeout, max duration and voicemail detection end calls without asking the server; the stop-call API is a kill switch, not a gate (docs/explorations/004-call-control-and-numbers.md). The model has no built-in end-call, so the safety gate on `end_call` holds for model-initiated hang-ups.
- **The safety check runs after the reply has started.** `transcript_updated` is not blocking, so on a gas mention the agent may begin its normal reply before the injection cuts in. The eval safety scenarios and the real calls measure how often and how long. The custom-LLM socket would close this (the server would answer each turn) but means running the model ourselves; not built, because Retell offers the same model, so the brief's condition for it doesn't apply.
- **Safety lines are model-spoken.** A paraphrase isn't counted as said, so `end_call` and booking stay refused, but the caller heard something other than the exact script. MEASURED on the first scripted gas call (`retell_call_936dbf645c5e077694bfd666ccc`): the agent's next reply, 2.4 s after the caller stopped (`reply_ms`), opened with the lines, but changed ", and call 911" to ". Call 911"; the exact lines came only on its fourth reply.

## Calls so far

Scripted test calls from a Twilio number with a text-to-speech caller on fixed pauses (so the caller often talks over the agent); not the bake-off's 10 real calls. Traces on the server: `make trace CALL=<id> DB=data/tool_server.db` in `~/summit-air/app`.

| Call | What happened |
|---|---|
| `retell_call_9d6182c10f4b47630e70f4fbe4d` | Lookup, tools and read-back gate worked; booking refused `invalid_slot` (the model sent the label "8 to 12" for the window) |
| `retell_call_1889f3f95c0f62ffb0ce0508ef6` | Same slot mistake, then refused for a missing name; live line posting was not yet working (fixed) |
| `retell_call_d570d98c180ab7ac1587b999d12` | Booked: FSM job 626, tomorrow 8 to 12, after one `gate_read_back` refusal while the read-back sentence was cut by the caller |
| `retell_call_936dbf645c5e077694bfd666ccc` | Gas: safety lines injected, flags and an urgent callback stored, `end_call` refused until the exact lines were said |

The LiveKit lane fixed the window mistake in the shared tool descriptions after these calls (window is the code from `find_slots`, never the spoken label).

## Eval smoke suite

`make eval SUITE=smoke STACKS=retell` runs through the Retell chat agent and the eval instance. On the first run with the eval lane's timing fix: 8 scenarios, 0 harness errors, 2 passed (MEASURED by the eval lane, 2026-11-03). Among the failures, the gas-in-passing scenario had the model paraphrase the gas lines instead of saying them as signed off, and promise an on-call callback time that nothing had set. The eval instance is one shared mock ServiceTitan, so the harness resets it before every scenario (`POST /debug/reset` on `summit-retell@eval-mock`, the only mock started with `ST_MOCK_ALLOW_RESET=1`; every other mock refuses it). The reset reaches the mock at `$RETELL_EVAL_TOOL_SERVER/mock/debug/reset` through the eval route in `deploy/retell/Caddyfile`, which a full `make retell-deploy` installs; until then point `RETELL_EVAL_MOCK_RESET_URL` at an ssh tunnel to the server's `127.0.0.1:8111` (`ssh -L 8111:127.0.0.1:8111 summit-air`, then `http://127.0.0.1:8111/debug/reset`). The old manual reseed (`ssh summit-air 'sudo systemctl stop summit-retell@eval-mock; rm ~/summit-air/retell-data/eval_mock.db; sudo systemctl start summit-retell@eval-mock'`) still works and is no longer needed between runs.

Retell's own numbers for the booked call: median end-to-end reply 1.88 s, LLM 0.55 s, voice first byte 0.11 s, transcription 0.19 s; $0.19 for 112 s (MEASURED, Retell call data for `call_d570d98c180ab7ac1587b999d12`, `latency` and `call_cost`). The overlapping scripted caller inflates the end-to-end figure; the bake-off calls replace it.

## Files

| File | What |
|---|---|
| `bridge.py` | The FastAPI app Retell calls: inbound webhook, call and chat webhooks, custom functions |
| `ledger.py` | Which transcript lines were already posted, per call, in SQLite; the Retell id to our id |
| `transcript.py`, `timings.py` | Reading Retell's call and chat objects; mapping word timings and latency into our trace format |
| `conversation.py` | Prompt, greeting and server-down variables, from prompts/ and agent.yaml |
| `build.py`, `push.py`, `api.py` | Config as code, the idempotent push, the endpoint allowlist |
| `chat.py` | Opens a Retell chat for an eval scenario |
| `eval_adapter.py` | The Retell side of the eval harness: the chat agent under test, and the mock reset before each scenario |
| `report.py` | Retell's latency and cost per call, for the bake-off report |
| `signature.py` | `X-Retell-Signature` |
