# Retell Conversation Flow agent

Our design mapped onto Retell's own stage graph (issue #133), to test whether a managed platform's flow does what we built better. The map, rule by rule, is [exploration 017](../../docs/explorations/017-retell-conversation-flow.md). It shares the single-prompt baseline's bridge, tool server, voice and eval instance (`voice/retell/README.md`) and holds no rules of its own.

## How a call flows

```
caller -> Retell (ears, turn-taking, Cartesia voice)
            flow: greeting -> turn_check -> stage node -> ...      (voice/retell_flow/build.py)
            |  signed HTTPS, one function call per caller line, one per tool
            v
         bridge (voice/retell_flow/bridge.py, :8102, also serves the baseline)
            v
         tool server (app/)
```

1. **Before hello** the inbound webhook is the baseline's: `/calls/start`, then the prompt and greeting as `{{instructions}}` and `{{greeting}}`. The greeting node says `{{greeting}}` as a static sentence.
2. **Every caller line** goes through `turn_check`, a function node that posts the transcript to `/calls/{id}/turn` before the agent answers, and gets back `route`, `line`, `stage` and `notes`. Equation edges on `route` send the call to the safety lines, a hand-off, or a line the server wants said; otherwise a logic split on `stage` (the server's, from `rules.stage_now`) picks the opening, booking or closing node.
3. **The stage nodes** are subagent nodes running Claude Haiku 5.5 on the shared prompt plus a short stage note and this turn's `[server]` notes. They have every model tool.
4. **Every tool answer** carries the routing fields. When the server's answer has a sentence our worker would speak itself (`prompts/assemble.spoken_by_server`: the read-back, confirmations, safety refusals and sign-off), `route` is `line` and an equation edge moves the call to a static-sentence node saying it word for word, before the model can.
5. **Hang-up and transfers** are reachable only through `route` values the bridge set from the server's answer: `end_call` allowed (`end_line` with the sign-off, or `end` after the agent's own goodbye), a demand for a person with a plan (`handoff`, a cold transfer), or a person flag the server puts through (`transfer`, a warm transfer with the server's summary for the person). A transfer nobody answers comes back through a function node that tells the server, and the caller hears the server's callback line.

## Commands

```bash
make retell-flow-push
```

Builds the voice flow and its agent from code and sends only what differs. The 678 number stays where it is.

```bash
make retell-flow-push EVAL_URL=https://45-77-105-247.sslip.io/eval BIND=1
```

Also the chat flow and chat agent for evals (it prints the chat agent's id for `RETELL_FLOW_CHAT_AGENT_ID`), and puts the flow agent on (678) 336-6730. `make retell-push` puts the single-prompt agent back.

```bash
make eval SUITE=standard STACKS=retell_flow SAMPLES=2
```

Through `voice/retell_flow/eval_adapter.py`: the baseline's adapter on the flow's chat agent, with the same eval instance and mock reset per scenario.

## Settings, and why

The voice, ears, turn-taking and timeouts are the baseline's (`voice/retell/build.py` `SPEECH`), so the comparison with the baseline is about the flow. What the flow sets:

| Setting | Value | Why |
|---|---|---|
| Model | `claude-5.5-haiku`, standard tier, temperature 0.3, for the whole flow | Our live profile (`fast` in config/models.yaml) through the baseline's model map |
| Global prompt | `{{instructions}}` | The prompt our worker reads, so the test isolates the flow's mechanics; scoping the prompt by stage is exploration 016, not this |
| Stage nodes | opening, booking, closing, chosen by the server | The server's stages (app/rules.py); the model never picks one |
| Tools per stage | All model tools | A caller can cancel, ask for a person or start a second job in any stage, and the server's stages only move forward |
| Safety lines node | Static sentence, interruption sensitivity 0 | Our worker plays the safety lines with interruptions off; Retell staff tie lost static lines to a caller still talking (exploration 017) |
| `speak_after_execution` | On for every tool | The edge on `route` beats the model's follow-up when the server sent a line (MEASURED in the prototype); otherwise the model speaks about the result |
| Webhook events | No `transcript_updated` | Every caller line reaches the server through `turn_check`, in order, before the reply, so the webhook would only post lines twice |
| Post-chat analysis model | `gpt-5.4-nano` (chat agent) | Nothing reads it, and the default model costs 2.08 cents a chat (MEASURED) |
| Transfer ring | 20 s | The tenant's `transfer.ring_seconds` |

## Differences from our stack

Everything the baseline lists in `voice/retell/README.md` ("Every difference") still holds except:

- **The safety lines, read-back, confirmations and sign-off are said from the server's text** in static-sentence nodes, not by the model.
- **The safety check and the `[server]` notes come before the reply**, through `turn_check`, not by Update Live Call after the reply has started.
- **Hang-up is Retell's end node**, reached on the server's answer, so no timer estimates the goodbye.
- **Warm transfer is built**: the server decides, Retell dials. The person hears the server's summary as Retell's private whisper, not a press-1 prompt, so a voicemail can pick up where our worker's would not.

What a flow can't do is in exploration 017, "Can't be done in a flow": the speech filter on model text, per-question voice settings, the slower read-back in parts, server-owned timeouts, stopping Retell's recording, a chat that opens with the greeting, and outbound callbacks.

## Files

| File | What |
|---|---|
| `build.py` | The flows and agents as code; tests read the graph from it |
| `bridge.py` | The flow's routes on the baseline's bridge: turn check, tools, transfer outcomes |
| `push.py` | The idempotent push, finding each flow through the agent that holds it |
| `eval_adapter.py` | The flow's chat agent as an eval stack |
