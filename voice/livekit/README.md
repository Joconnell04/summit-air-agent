# LiveKit voice worker

The program that answers (646) 624-0418: Deepgram hears, a Claude model decides, Cartesia speaks. It is deliberately thin. Every fact and rule lives in the tool server (`app/`, docs/tools.md); this folder only turns speech into text, posts every line and tool call to the server, and speaks what comes back. Nothing outside `voice/` imports it, so it can be deleted after the bake-off (docs/architecture.md, test `tests/voice/test_voice_layers.py`).

## Run it

```bash
make setup
make mock-st          # the mock ServiceTitan, port 8101
make server           # the tool server, port 8100
uv run --env-file .env python -m voice.livekit.worker dev
```

The phone line is wired by `scripts/phone/setup.py` and the server is deployed with `deploy/summit-air/deploy.sh` (both below).

## How a call goes

1. The LiveKit dispatch rule puts each caller in their own room (`call-...`) and dispatches this worker (`AGENT_NAME`, default `summit-air`). The room name is the call id everywhere, including `make trace CALL=<room>`.
2. **Capacity cap.** `MAX_CALLS` (default 3, the Cartesia Pro stream limit, docs/scaling.md) is shared by every call process through `capacity.Lines`. Past the cap the caller hears the pre-recorded busy message (safety line first) and leaves a voicemail, which is transcribed and flagged for a callback (spec S-47). No voice stream is used.
3. **Lookup before hello.** `POST /calls/start` with the caller ID and the dialed number. Its result becomes the call context in the model's instructions (`prompts/assemble.py`). If the server is unreachable, at the start or on any later caller line, the caller hears the tenant's `server_down` line (which carries the 911 line) and the call ends, since without the server there is no safety check.
4. **Greeting** from the tenant's `voice` lines: by first name when exactly one residential customer matched, otherwise the general greeting. A dropped safety call whose lines weren't said starts with those lines instead.
5. **Every caller line** is posted to `/calls/{id}/turn` before the model answers. If the result carries a safety action, the worker speaks its text word for word with interruptions off and the model doesn't answer that turn. Hints (commercial, do-not-call) are added to the caller's line as `[server]` notes for the model.
6. **Every agent line** is posted too, with its stage timings, so the server can mark the safety lines said and the trace shows where the time went. The timings include the first model request's prompt tokens, `cache_read_tokens` and `cache_write_tokens`, so `make trace` shows whether the prompt cache was hit.
7. **Tools** are the server's model tools, built from `prompts/tools.yaml` and the server's request models. Each posts to `/tools/<name>`; writes carry an `Idempotency-Key` from call, tool and arguments.
8. **Ending.** `end_call` asks the server. Any safety text in a tool result (the lines, the check question, the safety sign-off) is spoken by the worker word for word, not handed to the model. The worker hangs up only when the result says `hang_up: true`, after the agent's last words have played (or after the tenant's goodbye line if the model said nothing). There is no built-in end-call tool and no live transfer in V1 (DECISIONS.md 008); a caller who wants a person gets `flag_person`.
9. `POST /calls/{id}/end` runs when the room closes.

## The model

`brain.py` builds it from the profile in `config/models.yaml`. The profile's thinking setting is sent on every Anthropic request (`thinking_request`): Haiku 5.5 thinks before it speaks when the field is left out, which cost 1 to 2 s on the first real call (issue #30, docs/latency.md). Prompt caching is on for the tool list, the system prompt and the last turn pair.

To measure the model and the cache without a phone call, `make measure-delay [PROFILE=fast-prev]` replays a scripted call through the same request building against the Anthropic API (a few cents; no LiveKit connection, nothing registered, no call placed). `--as-deployed` sends what the worker sent before the fix.

## Turn-taking

Values live in `config/models.yaml` under `voice`, all ASSUMED starting points from the earlier practice agent's measurements (from-other-projects/bobert/latency-notes.md; Bobert was an earlier personal practice project, not a person) until measured on real phone audio.

- **Interruptions:** adaptive mode, at least 2 recognized words and 0.5 s of speech before the agent stops, and a false interruption resumes the reply after 1.5 s of silence (spec S-50, C-48).
- **End of turn:** the LiveKit turn detector with a 0.3 to 1.5 s wait. Right after the agent asks for a phone number, ZIP or other digits (`turns.asked_for_digits`), the wait becomes 0.9 to 3.0 s, because people pause between groups of digits.
- **Preemptive generation** on: the model starts on the final transcript before the turn is confirmed. Preemptive speech stays off, since a wrong early start is audible. LiveKit keeps the early reply only if the caller line is unchanged when the turn ends, so a turn whose server result adds a `[server]` note (commercial caller, do-not-call, declined safety check) starts the model again; plain turns keep it.

## Bridge line

`bridge.Bridge`, rules from docs/latency.md and spec S-37a:

- A pre-recorded clip (`tenants/<tenant>/clips/bridge_*.wav`, each under 1.1 s) plays only when a tool hasn't answered after 0.7 s, and only while the agent and the caller are both silent.
- At most once every 3 caller turns, never the same clip twice in a call.
- It never overlaps the agent: if the tool answers mid-clip, the model's next step starts at once, but the reply's first audio waits for the clip to end (`SummitAgent.tts_node`).

The clips play on a separate audio track (`BackgroundAudioPlayer`), not through the session's speech queue: the earlier practice agent queued its filler line as speech, and the real reply waited 7 to 10 s behind it. Re-record after changing a clip's text: `uv run --env-file .env python -m voice.livekit.clips`.

## Calls Summit Air makes (journey 12)

The worker never decides to dial. The tool server's jobs queue the calls (earlier openings, requested callbacks) and check the rules when a call is claimed (spec S-42; docs/tools.md, `/outbound/claim`). `outbound.py` runs beside the worker: every `CLAIM_EVERY_S` it claims the next call, only while a line is free under the capacity cap, and dispatches the worker into a room named by the claim's `call_id` with the claim as metadata. The worker then starts the call with `direction: outbound`, dials through the LiveKit outbound trunk (`LIVEKIT_OUTBOUND_TRUNK_ID`) with answering-machine detection, and reports `/outbound/{id}/done`: a person hears the server's `opening_line` and the normal conversation follows; a voicemail greeting gets the tenant's one voicemail line.

## Settings

| Variable | Default | What |
|---|---|---|
| `TOOL_SERVER_URL` | `http://127.0.0.1:8100` | The tool server |
| `AGENT_NAME` | `summit-air` | Must match the dispatch rule |
| `MAX_CALLS` | 3 | Capacity cap |
| `LINES_DIR` | `/tmp/summit-air-lines` | Where the cap's count lives |
| `MODEL_PROFILE` | `default` in config/models.yaml | The brain; `openai-fast` or `gemini-fast` switch provider |
| `DEFAULT_TENANT` | the only tenant folder | Whose recorded lines play when the server is unreachable |
| `LIVEKIT_OUTBOUND_TRUNK_ID` | empty (outbound off) | From `scripts/phone/setup.py` |
| `CLAIM_EVERY_S` | 15 | How often the outbound poller asks for a call to place |

Plus the provider keys: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `DEEPGRAM_API_KEY`, `ANTHROPIC_API_KEY`, `CARTESIA_API_KEY`.

## Phone wiring

`uv run --env-file .env python -m scripts.phone.setup` creates, or finds by name: the Twilio Elastic SIP Trunk `summit-air` with origination to the LiveKit project's SIP host (`LIVEKIT_SIP_HOST`, from the LiveKit dashboard's project settings) and a termination credential list; the LiveKit inbound trunk and dispatch rule; the LiveKit outbound trunk; and attaches the number to the Twilio trunk.

## Hosting

`deploy/summit-air/` (docs/architecture.md, hosting):

- `deploy.sh` copies the checkout and `.env` to the server, installs, restarts, and installs the crontab.
- `run.sh` starts the mock, the tool server, the worker and the outbound poller if they aren't running; cron runs it at boot.
- `watchdog.sh` runs every minute: restarts anything stopped, checks the worker's own health page (`:8081/`, 200 only once registered with LiveKit) and the tool server's `/healthz`, posts the worker heartbeat, pings `HEALTHCHECK_URL` when set, and after two failed checks in a row moves the number to the recorded fallback (`scripts/phone/fallback.py`: Twilio plays the tenant's fallback line and records a voicemail), and back when the worker passes again. Fallback voicemails stay in Twilio's call log; they don't reach the owner view.

## Not done here

- The outside monitor is a Healthchecks.io check on the free plan, owned by the project's Gmail account (jt272004@gmail.com, the same contact as the public terms page). Its ping URL is `HEALTHCHECK_URL` in the server's `.env` and in the Notion vault, never in the repo, because anyone holding it could ping the check and hide an outage. The check expects a ping every minute with a 5-minute grace period, so a stopped worker, a stopped tool server or a server that doesn't come back from its nightly reboot sends an email. Chosen because the watchdog already knows whether the stack is healthy, so the outside monitor only has to notice silence; an uptime probe from outside (UptimeRobot) would have to reach a public health page, and UptimeRobot's free plan is non-commercial (docs/explorations/003-capacity-and-cost.md).
- Outbound calls: the claim, dispatch and report are tested against the real server, but the dial and answering-machine branches of `place_outbound` have no test with a fake LiveKit API and have not run end to end.
- OpenAI and Gemini profiles are wired but untested on a live call; Gemini's thinking setting is left at the plugin default.
- The bridge clip waits 0.4 s after its last frame (`CLIP_TAIL_S`, the audio buffer LiveKit keeps) before the reply's audio may start; measure it once a tool is slow enough to trigger a clip.
