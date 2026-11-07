# LiveKit voice worker

The first phone stack: Deepgram hears, a Claude model decides, Cartesia speaks. No number rings it now; the phones moved to Retell (DECISIONS.md 112, 121). It stays because the text eval harness runs the agent in process through it (`make eval`, the rules gate in `evals/gate.py`), and the live socket shares its tool-server client and model profiles (`server.py`, `settings.py`). Every fact and rule lives in the tool server (`app/`, docs/tools.md); this folder only turns speech into text, posts every line and tool call to the server, and speaks what comes back.

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
8. **Ending.** `end_call` asks the server. Any safety text in a tool result (the lines, the check question, the safety sign-off) is spoken by the worker word for word, not handed to the model. The worker hangs up only when the result says `hang_up: true`, after the agent's last words have played (or after the tenant's goodbye line if the model said nothing). There is no built-in end-call or transfer tool; a caller who wants a person gets `flag_person`, and the worker then asks the server whether to put them through (below).
9. `POST /calls/{id}/end` runs when the room closes.

## The model

`brain.py` builds it from the profile in `config/models.yaml`. The profile's thinking setting is sent on every Anthropic request (`thinking_request`): Haiku 5.5 thinks before it speaks when the field is left out, which cost 1 to 2 s on the first real call (issue #30, docs/latency.md). Prompt caching is on for the tool list, the system prompt and the last turn pair.

To measure the model and the cache without a phone call, `make measure-delay [PROFILE=fast-prev]` replays a scripted call through the same request building against the Anthropic API (a few cents; no LiveKit connection, nothing registered, no call placed). `--as-deployed` sends what the worker sent before the fix.

## Turn-taking

Values live in `config/models.yaml` under `voice`, all ASSUMED starting points from the earlier practice agent's measurements (docs/history/from-other-projects/bobert/latency-notes.md; Bobert was an earlier personal practice project, not a person) until measured on real phone audio.

- **Interruptions:** adaptive mode, at least 2 recognized words and 0.5 s of speech before the agent stops, and a false interruption resumes the reply after 1.5 s of silence (spec S-50, C-48).
- **End of turn:** the LiveKit turn detector with a 0.3 to 1.5 s wait. Right after the agent asks for a phone number, ZIP or other digits (`turns.asked_for_digits`), the wait becomes 0.9 to 3.0 s, because people pause between groups of digits.
- **Preemptive generation** on: the model starts on the final transcript before the turn is confirmed. Preemptive speech stays off, since a wrong early start is audible. LiveKit keeps the early reply only if the caller line is unchanged when the turn ends, so a turn whose server result adds a `[server]` note (commercial caller, do-not-call, declined safety check) starts the model again; plain turns keep it.

## Bridge line

`bridge.Bridge`, rules from docs/latency.md and spec S-37a:

- A pre-recorded clip (`tenants/<tenant>/clips/bridge_*.wav`, each under 1.6 s, "Okay, just give me one second.") plays when a tool hasn't answered after 0.7 s, only while the agent and the caller are both silent, and never the same clip twice in a row. A reply that calls a look-up with only "Got it." or nothing before it ends with a holding line in the live voice (`voice.holding`, DECISIONS.md 096).
- At most once every 3 caller turns, never the same clip twice in a call.
- It never overlaps the agent: if the tool answers mid-clip, the model's next step starts at once, but the reply's first audio waits for the clip to end (`SummitAgent.tts_node`).

The clips play on a separate audio track (`BackgroundAudioPlayer`), not through the session's speech queue: the earlier practice agent queued its filler line as speech, and the real reply waited 7 to 10 s behind it. Re-record after changing a clip's text: `uv run --env-file .env python -m voice.livekit.clips`.

## Local names

`tenants/<tenant>/names.yaml` lists the street and neighborhood names locals say differently from their spelling (Houston Street is HOW-stun; docs/explorations/011-local-pronunciation.md). `voice/names.py` reads it for every voice stack. Here it feeds two things:

- **Saying them:** a Cartesia pronunciation dictionary, whose id is passed as `pronunciation_dict_id`. Cartesia swaps the names inside its own service, so the server's text, the read-back included, is stored as written. Matching is case-sensitive on `sonic-3`, so `match` lists each spelling the server or Deepgram writes. After editing the list: `uv run --env-file .env python -m voice.names push`, which creates or updates the dictionary and saves its id in the file.
- **Hearing them:** each name's `written` spelling as a Deepgram keyterm (15 of the 100 allowed). Keyterms cost $0.0013 a minute on top of streaming, about $12 a month at 150 two-minute calls a day (SOURCE: deepgram.com/pricing; volume ASSUMED, docs/brief.md).

**Clip test** (`uv run --env-file .env python -m voice.livekit.name_clips OUT_DIR`; 30 clips, each name in a read-back sentence without and with the dictionary, every clip transcribed back with Nova-3 with and without keyterms; about $0.09 of Cartesia and a few cents of Deepgram, ASSUMED from list prices). The clips from the first run are outside git in `data/name-clips/` of the main checkout. MEASURED on that run:

- The dictionary works with the Cartesia-Version the plugin sends on speech (2025-04-16), although the dictionary itself must be created with 2026-08-14. The transcripts moved toward the local reading for Schermerhorn ("Skermerhorn"), Todt Hill ("Tote Hill"), Arrochar ("a brochure", uh-ROH-sher), Van Wyck ("Van Wyke" became "Van Wyck") and Cortelyou. No version change in the worker.
- Without keyterms, Deepgram spelled 7 of 15 plain clips and 8 of 15 dictionary clips right; with keyterms, 15 of 15 and 14 of 15. The miss is Todt Hill said TOHT, heard as "Tote Hill" even with the keyterm, so a caller who says it that way may get a read-back of the wrong spelling and has to correct it.
- Four dictionary clips (Houston Street, Kosciuszko, Schermerhorn, Conselyea) ran 1.0 to 1.5 s longer than the plain ones; listen for a pause around the name.
- A transcript can't tell HOW-stun from HYOO-stun, so whether each name sounds right is a listening check, not this test. The clips are clean synthetic audio, not phone audio.

## Warm transfer (C-11)

The server decides; the model never asks for a transfer. After every `flag_person` that returns ok, the worker posts `/calls/{id}/transfer` with the flag and callback ids (docs/tools.md). The server says go only in office hours when the caller asked for a person in their own words, or after hours when the server's own priority and after-hours cutoffs say the on-call technician is needed tonight (the same judgement that holds a tonight booking until the technician is alerted; the priority the model puts on the flag is never read); never on a safety or commercial call, never when the role's number is unset, once per call (app/transfer.py). The flag and callback are already stored, so whatever happens next, nobody is left without a named callback. When the server says go, `transfer.py`:

1. Lets the agent's turn finish, then says the server's handoff line ("I'm connecting you with someone from our team now").
2. Puts the caller on hold: the session's audio in and out are off and LiveKit's built-in hold music plays on the background track.
3. Dials the person through the outbound trunk (`LIVEKIT_OUTBOUND_TRUNK_ID`) into a room of their own, `<call>-transfer`, ringing for the tenant's `ring_seconds` (20, ASSUMED).
4. When they answer, says one line into that room that names no caller ("Summit Air has a caller for the office. Press 1 to take the call"), since a voicemail greeting may be what answered, and waits `accept_seconds` (10, ASSUMED) for the 1.
5. On a 1, says the summary (the caller's number and the flag's reason, "Urgent" when the server sent it to on-call), moves them into the caller's room (`MoveParticipant`), reports `connected`, and leaves without hanging up; the server closes the flag and callback.
6. Otherwise (no answer, a voicemail that never presses 1, an error) it hangs up the person's leg by deleting their room, takes the caller off hold, reports the outcome, and says the server's line with the callback time stored with the flag ("I couldn't reach anyone just now, so I've let the team know and someone will call you by 9:30 AM").

Nothing leaves the caller on hold: any error in the transfer takes them off hold and says the flag's own callback sentence, and the server is told the transfer failed. A caller who hangs up mid-transfer cancels it, which hangs up the person's leg.

Why a key press and a separate room: the caller never hears ringing, a voicemail greeting or the summary, and a voicemail box is never put through. LiveKit's `WarmTransferTask` (livekit-agents 1.8) joins calls the same way, but a model talking to the person decides when to connect; here the key press does.

Off unless both are set: the server's `TRANSFER_OFFICE_NUMBER` or `TRANSFER_ON_CALL_NUMBER`, and the worker's outbound trunk. The person's leg is an outbound call on the Twilio trunk, $0.014 a minute while it lasts (SOURCE: docs/explorations/003-capacity-and-cost.md), plus LiveKit SIP minutes for that leg.

For the demo, both roles point at Jackson's personal phone: the two numbers are set in the server's `.env` and nowhere in the repo, so a transferred demo call rings that phone.

## Cold handoff to a person

A caller who demands a person (insistently, again after asking once, or while angry) is sent straight through, with no summary and no key press. The server decides on the caller's own turn, after the safety check (app/transfer.py): `/calls/{id}/turn` returns `handoff` with the flag and callback it has already stored and, when the role's number is set, the number to send the call to. The office takes it in office hours and the on-call technician after hours. On a gas or carbon monoxide call the server waits until the fixed lines have been said; a demand on the same line as the gas smell gets the lines and nothing else, and the next demand is handed off. Never on a commercial call, once per call (counting a warm transfer).

`call.py` says the server's line word for word ("Transferring you to a person now") instead of letting the model answer. Then `transfer.py`:

1. Mutes the conversation, and marks the call's end reason as a transfer, since the caller leaves the room as the transfer goes through.
2. Sends the caller's own call leg to the number with `TransferSIPParticipant`: a SIP REFER through the inbound trunk, ringing for the tenant's `ring_seconds` (20, ASSUMED). LiveKit waits for the number to answer; unanswered, the request fails and the caller stays in the room (SOURCE: https://docs.livekit.io/telephony/features/transfers/cold.md).
3. Answered, it reports `handed_off` and the agent leaves. The flag and callback stay open, because a voicemail can answer as well as a person.
4. Failed, refused, or any error: it takes the caller off hold, reports `failed`, and says the server's line with the callback time stored with the flag. With no number set, the server's line is that callback line to begin with and nothing is sent.

Needs on the Twilio trunk: "Call Transfer (SIP REFER)" enabled, with "Enable PSTN Transfer" (SOURCE: the LiveKit page above). The number the person sees comes from the trunk's "Caller ID for Transfer Target" setting, not from the worker. Unlike the warm transfer, it doesn't use the outbound trunk.

## Recordings (story O-6)

When `/calls/start` returns a `recording.object_key` and the worker has a bucket (`RECORDING_S3_*`), `recording.py` starts one LiveKit room-composite egress, audio only, as an MP4 into that bucket under that name, and reports the egress id to the server. It starts in the background after the session, so the greeting doesn't wait; the first second or so of the greeting may be missed while the recorder joins (ASSUMED until a recorded call is checked). It stops when the agent leaves, so after a transfer the conversation with the person isn't recorded. LiveKit Cloud can only upload to cloud storage (SOURCE: https://docs.livekit.io/transport/media/ingress-egress/egress/outputs.md, "local storage isn't available"), so the tool server's jobs loop copies each finished file into `data/recordings/` and deletes it from the bucket (app/recordings.py); the owner view plays the copy and deletes it with the transcript after 30 days.

Cost and limits (SOURCE: https://livekit.com/pricing and https://docs.livekit.io/deploy/admin/quotas-and-limits.md, read for this change): audio-only composite recording is billed as transcode minutes. The Build plan the project is on includes 60 a month as a hard cap, with 2 recordings at once, so the third simultaneous call and every call after about 60 recorded minutes a month goes unrecorded (the call itself is unaffected). Ship ($50 a month) includes 600 minutes, then $0.005 a minute audio-only, with 100 at once: about $0.01 for a two-minute call (call length ASSUMED, docs/brief.md). The bucket holds a file only until the next jobs run.

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
| `LIVEKIT_URL` | empty | The project URL, for joining a transfer's own room; also read by the LiveKit SDK |
| `RECORDING_S3_BUCKET`, `RECORDING_S3_ENDPOINT`, `RECORDING_S3_REGION`, `RECORDING_S3_ACCESS_KEY`, `RECORDING_S3_SECRET` | empty (no recording) | The bucket LiveKit uploads each call's audio to; the tool server reads the same variables to collect it |

Plus the provider keys: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `DEEPGRAM_API_KEY`, `ANTHROPIC_API_KEY`, `CARTESIA_API_KEY`.

## Phone wiring

`uv run --env-file .env python -m scripts.phone.setup` creates, or finds by name: the Twilio Elastic SIP Trunk `summit-air` with origination to the LiveKit project's SIP host (`LIVEKIT_SIP_HOST`, from the LiveKit dashboard's project settings) and a termination credential list; the LiveKit inbound trunk and dispatch rule; the LiveKit outbound trunk; and attaches the number to the Twilio trunk.

## Hosting

`deploy/summit-air/` (docs/architecture.md, hosting):

- `deploy.sh` copies the checkout and `.env` to the server, installs, restarts, and installs the crontab.
- `run.sh` starts the mock, the tool server, the worker and the outbound poller if they aren't running; cron runs it at boot.
- The owner view signs people in with accounts (app/accounts.py). Before the first deploy of accounts, set `OWNER_ADMIN_USERNAME`, `OWNER_ADMIN_NAME` and `OWNER_ADMIN_PASSWORD` in the server's `.env`: the tool server creates that admin at startup. `OWNER_TOKEN` and `RULES_EDITORS` are no longer read and can be removed. A server started with no user logs an error saying so, and `make owner-user ACCOUNT=<name> NAME="<name>" ROLE=admin DB=<the server's TOOL_DB>` adds one from a shell on the server.
- `watchdog.sh` runs every minute: restarts anything stopped, checks the worker's own health page (`:8081/`, 200 only once registered with LiveKit) and the tool server's `/healthz`, posts the worker heartbeat, pings `HEALTHCHECK_URL` when set, and after two failed checks in a row moves the number to the recorded fallback (`scripts/phone/fallback.py`: Twilio plays the tenant's fallback line and records a voicemail), and back when the worker passes again. Fallback voicemails stay in Twilio's call log; they don't reach the owner view.

## Not done here

- The outside monitor is a Healthchecks.io check on the free plan, owned by the project's Gmail account (the project owner's Gmail, the same contact as the public terms page). Its ping URL is `HEALTHCHECK_URL` in the server's `.env` and in the Notion vault, never in the repo, because anyone holding it could ping the check and hide an outage. The check expects a ping every minute with a 5-minute grace period, so a stopped worker, a stopped tool server or a server that doesn't come back from its nightly reboot sends an email. Chosen because the watchdog already knows whether the stack is healthy, so the outside monitor only has to notice silence; an uptime probe from outside (UptimeRobot) would have to reach a public health page, and UptimeRobot's free plan is non-commercial (docs/explorations/003-capacity-and-cost.md).
- Warm transfer has not run end to end: the decision, the outcomes and the fallback are tested against the real server with a fake LiveKit (`tests/voice/test_voice_transfer.py`), but `ConsultRoom` (joining the second room, the summary audio, the key press) and the real dial and move have not. Running it needs a number to put through in the server's `.env`, the Twilio trunk allowed to call it, and someone to answer and press 1.
- The cold handoff has not run end to end: the server's decision and the worker's `TransferSIPParticipant` request, its failures and the fallback are tested with a fake LiveKit (`tests/tool_server/test_ts_handoff.py`, `tests/voice/test_voice_handoff.py`), but no real REFER has been sent. Running it needs SIP REFER enabled on the Twilio trunk and a number to send the call to in the server's `.env`.
- Recording has not run end to end on a call. The server's `.env` now points at a Supabase Storage bucket (S3-compatible) whose upload was tested by hand, and the egress request was checked against LiveKit Cloud (PR description); a recorded call, the copy into `data/recordings/` and playback on the call page have not been checked.
- Outbound calls: the claim, dispatch and report are tested against the real server, but the dial and answering-machine branches of `place_outbound` have no test with a fake LiveKit API and have not run end to end.
- OpenAI and Gemini profiles are wired but untested on a live call; Gemini's thinking setting is left at the plugin default.
- The bridge clip waits 0.4 s after its last frame (`CLIP_TAIL_S`, the audio buffer LiveKit keeps) before the reply's audio may start; measure it once a tool is slow enough to trigger a clip.
