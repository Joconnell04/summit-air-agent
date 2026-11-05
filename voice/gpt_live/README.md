# GPT-Live

One entry on the live-call slate (docs/explorations/019-forum-stacks.md, section 5): OpenAI's full-duplex phone model, `gpt-live-1`, on its Live Sessions endpoint, with a one-page prompt and no gates beyond the tool server's own. It asks whether a simpler, more natural-sounding stack makes the calls feel better.

GPT-Live is two models. The voice model hears and speaks, full duplex, with its own turn-taking and backchannels. When it needs something done it delegates to a backend Responses model (`gpt-6-luna`), which calls our tools. The backend's function calls come to us and go to the tool server with the call id and an Idempotency-Key on writes, the same tools and arguments the Retell bridge (voice/retell/bridge.py) forwards.

- `session.py`: the session config and the event handling both paths share. Caller and agent lines are posted to `/calls/{id}/turn`; when the turn check returns the safety lines, the voice model is asked to say them word for word (it is the voice, so it may not; the server counts them only when an agent line contains them). `end_call` hangs up only when the server's result says so, after the goodbye has been said.
- `sip.py`: the phone. Twilio sends (646) 624-0418 to `sip:<project>@sip.api.openai.com;transport=tls`; OpenAI posts `live.transport.incoming` to `/gpt-live/webhook`; the service starts the call on the tool server, accepts it with the session, attaches the sideband WebSocket, and ends the call through the hangup endpoint.
- `rig_adapter.py`: the same session on the audio rig, over GPT-Live's primary WebSocket. `make rig STACK=gpt_live SUITE=live`.
- Prompts: `prompts/gpt_live/voice.md` (the voice model) and `prompts/gpt_live/backend.md` (the backend, with the company knowledge and the call context added).
- Model, voice, backend model and prices: `realtime.gpt_live` in config/models.yaml.

Not layered on: the server-spoken lines and the read-back sentences of our own stack, the dead-air filler, the say-exactly turn gate of the old Realtime adapter (voice/openai_realtime). GPT-Live decides when to speak.

Deploy with `sh deploy/gpt_live/deploy.sh`, only when no call is live. The server needs `OPENAI_API_KEY` and `OPENAI_WEBHOOK_SECRET` (the signing secret of the project webhook subscribed to `live.transport.incoming`) in `~/summit-air/.env`.

Cost: $0.05 a voice minute, billed per second, plus the backend's tokens (SOURCE: https://developers.openai.com/api/docs/models/gpt-live-1).
