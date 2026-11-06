# ElevenLabs Agents, on our brain

An entrant in the simulated audio bake-off (DECISIONS.md 026, docs/bakeoffs.md bake-off 6). ElevenLabs supplies the ears, the turn-taking and the voice; the model is ours, through ElevenLabs' custom-LLM mode. Nothing outside `voice/` imports this folder, so it can be deleted on its own.

## How a reply is made

```
caller audio -> ElevenLabs (speech to text, turn-taking)
                  |  OpenAI-style chat request, streamed
                  v
               brain.py (Claude, profile `realtime.elevenlabs.brain` = fast)
                  |  the same tool contract as the worker
                  v
               tool server (app/)
```

- `brain.py` is an OpenAI-style streaming chat endpoint. For each request it takes the caller's newest words from ElevenLabs' history and runs the conversation the LiveKit worker runs: the same prompt and tools (`prompts/assemble.py`), the turn check on every caller line, `[server]` notes, tool calls forwarded with an Idempotency-Key on writes.
- Safety text from the server (the lines, a gate refusal, the safety sign-off) is streamed as given, with no model step.
- ElevenLabs hangs up only through its built-in end-call tool, which it runs only when the endpoint emits that tool call, and the endpoint emits it only after the tool server's `end_call` returned `hang_up: true`. The agent has no other system tool, so transfers stay off as in V1.
- A request ElevenLabs drops because the caller talked over the agent still finishes its turn, so a tool that ran gets its result into the history and the line reaches the tool server.
- The call id travels in the conversation's extra body, and in the agent's prompt as a fallback.

## On the rig

`rig_adapter.py` serves the brain on a local port, opens a temporary ngrok tunnel, creates a test agent pointed at it (voice and voice model from `realtime.elevenlabs` in config/models.yaml) and deletes the agent when the run ends. Each call starts on the rig's scratch world and then opens ElevenLabs' conversation WebSocket, 16 kHz PCM both ways. Run it with `make rig STACK=elevenlabs` (evals/README.md, "Audio rig"); it needs `ELEVENLABS_API_KEY` and `ngrok`.

## Not built

A real phone number. That would be ElevenLabs' Twilio integration with its conversation-start webhook pointed at the brain's `/calls` route, which starts the call on the tool server and returns the first line; the brain would then run on the server behind Caddy rather than a tunnel. Only worth building if this entrant reaches the final two.
