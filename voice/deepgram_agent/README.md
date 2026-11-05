# Deepgram Voice Agent entry

One of the single live calls per stack on (646) 624-0418. Deepgram's Voice Agent API does the ears and turn-taking (Flux, `flux-general-en`) and the voice (Aura-2 `aura-2-arcas-en`, the nearest to Cartesia's Max); our `fast` profile, Claude Haiku 5.5, writes the replies through Deepgram's custom Anthropic endpoint on our key; our tool server answers every function. The prompt is the one-page `prompts/stripped.md`, and the only gates are the server's own: refused tools, the safety lines from the turn check, and `end_call`'s hang-up. Settings are `realtime.deepgram_agent` in config/models.yaml.

## How a call flows

```
caller -> Twilio -> POST /deepgram/twiml (signature checked) -> <Connect><Stream>
       -> wss /deepgram/stream (server.py, :8120 behind Caddy at deepgram.45-77-105-247.sslip.io)
       -> Deepgram agent session (agent.py), 8 kHz mu-law both ways
                         |  every function, every line
                         v
                  tool server (:8100, shared)
```

1. The stream's start message carries the call's sid and a key derived from it; a stream without the key is closed. The call starts on the tool server as `dg-<CallSid>`.
2. The Settings message holds the stripped prompt, the tool list, the greeting (or the safety lines on a resumed safety call) and the tenant's local names as Flux keyterms.
3. Every caller line goes to the turn check; safety lines come back and are injected as the agent's next words, word for word, interrupting whatever it was saying.
4. Functions run on the tool server with an Idempotency-Key for writes; the envelope goes back as the result.
5. `end_call` drops the line only when the server says so: after the goodbye the model said with it, or, if it said none, after the tenant's goodbye.

## Run it

```bash
make deepgram-deploy
```

Then point the number at it with `uv run --env-file .env python -m scripts.route_number deepgram`, and back with `livekit`.

```bash
make rig STACK=deepgram_agent SUITE=live
```

## What it leaves out

No speech filter or held replies, no dead-air check-ins, no bridge clip during a slow tool, no warm transfer, no call recording on the owner view. Deepgram has no "say exactly" mode for model text, so the read-back and booking confirmations are the model's rendering of the server's `say`.
