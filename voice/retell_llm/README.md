# Retell in custom-LLM mode

Experiment 2 of issue #154: Retell does the ears, the turn-taking, interruptions and the voice, and our brain writes every reply. Retell opens a WebSocket to our server for each call and asks for a response whenever it thinks it is the agent's turn; the socket answers with the same model profile, prompt, tool server, speech filter and server-spoken lines as the LiveKit worker (`voice/archive/livekit/`). It runs on the eval instance only, behind its own agent; the (678) number stays on the single-prompt agent (`voice/retell/`). Results: evals/rounds/retell-02.md.

## How a call flows

```
caller -> Retell (ears, turn-taking, Cartesia voice)
            |  wss, one socket per call (Retell's LLM WebSocket)
            v
         socket (voice/retell_llm/llm_socket.py, :8113 behind Caddy at /eval/retell-llm/)
            |  127.0.0.1                    |  HTTPS
            v                               v
         eval tool server (:8110)        Anthropic (config/models.yaml `fast`)
```

1. The rig (or, for a phone call, the bridge's inbound webhook) starts the call on the tool server and gives Retell the call id, the prompt and the first line as dynamic variables (`voice/retell/conversation.py`). The socket asks for the call's details, then sends the first line as response 0.
2. On every `response_required` the socket posts the agent lines Retell reports as spoken, then the caller's line, to the tool server, as the worker does before the model answers. Safety lines, the card stop, the billing callback and a handoff line go back as the server wrote them, with `no_interruption_allowed`, and the model is not asked.
3. Otherwise the model streams its reply. Each sentence passes the reply rule (`prompts/assemble.py` `SpokenReply`) before it is sent, and the whole reply is held while the rule says so (from the address check until the read-back is confirmed, and on a safety call). Tool calls go to the tool server; the read-back, the sign-off, a booking's confirmation and the other server lines are sent word for word and end the turn.
4. A newer request from Retell cancels the turn still being written. A reply the caller never heard (Retell's transcript has no agent line after theirs) comes out of the model's history, so the next reply answers the caller's whole line. Writes are idempotent, so a tool a cancelled turn already called returns the same result.
5. `end_call` is the server's: the socket sets Retell's `end_call` on the response only when the server's `end_call` said to hang up. A transfer is Retell's cold transfer to the number in the server's handoff or transfer plan, on phone calls only; a web call has no line to transfer, so the server is told the handoff failed and the stored callback line is said, as the worker does with no trunk.
6. Dead air: Retell's reminders are set to the worker's timings (`dead_air` in config/models.yaml); the socket says the tenant's check-in line until the last one, then its goodbye, and ends the call as `stepped_away`.
7. When the socket closes (normally, or after six seconds without Retell reconnecting), the last agent lines go to the tool server and the call is closed there.

Retell sends no auth header on the socket, so its URL carries a key derived from the Retell API key, and a socket without it is closed (SOURCE: https://docs.retellai.com/integrate-llm/setup-websocket-server.md). The agent has no webhook: the socket posts every line itself, so nothing is posted twice.

## Run it

```bash
make retell-deploy
```

Adds `summit-retell@eval-llm` (port 8113) and the Caddy route beside the rest of the Retell lane (`deploy/retell/`).

```bash
make rig STACK=retell_llm SUITE=live
```

The rig creates or updates the agent "Summit Air rig test (Retell custom LLM, audio bake-off only)" with the production voice agent's voice and dials, on the socket.

## What differs from the LiveKit worker

| | LiveKit worker | Retell custom LLM |
|---|---|---|
| Ears, end of turn, interruptions | Deepgram Nova-3, our endpointing, a longer wait after asking for digits | Retell's (responsiveness 0.9, dynamic), no per-question wait |
| Backchannel | None | Retell's, as on the single-prompt agent ("mm-hmm", "okay", "uh-huh") |
| When the reply starts | One request per end of turn | Retell may ask several times per caller turn; each newer request cancels the last |
| Speaking rate | The server's pace per call, and per part of a slower read-back | One speed for the call; a slower read-back's parts are said at it |
| Bridge clip for a slow tool | Yes | None |
| Recording | LiveKit egress, stopped when the caller objects | Retell's own recording; the objection line is said but nothing stops Retell's recording |
| Warm transfer | Server-decided, the worker dials and consults | Server-decided, Retell's cold transfer, phone calls only |
| Turn timings in the trace | Every stage | `server_ms`, `llm_first_token_ms`, `tool_ms`, and `reply_ms` from Retell's word timings |
