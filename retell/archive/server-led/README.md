# Server-led arm

Arm D of the agent side-by-side (issue #171): Jackson's Retell agent with the call flow in the tool server instead of the prompt. Round write-up: `evals/rounds/server-led-01.md`.

| File | What it is |
|---|---|
| `prompt.md` | The Retell prompt, 828 words: who Brooke is, how she talks, "say each result's `say` as written, then do its `next`", the steps before the first tool, and the few rules the server can't hold |
| `tools.json` | Jackson's custom functions, pointed at the arm's instance, with the flow taken out of four descriptions |
| `push.py` | Creates or updates the arm's LLMs, voice agent and chat agent on Retell |

What the server does with `SERVER_LED=1` is in `app/flow.py`: `next` on every result, the open-door line on the first no-heat or no-cooling offer, and three gates (openings after a confirmed read-back, a booking after the three visit questions, end_call after the outcome sentence).

## Running it

```bash
sh deploy/server-led/deploy.sh
uv run --env-file .env python retell/archive/server-led/push.py
```

The instance is `https://server-led.45-77-105-247.sslip.io` (tool server 8130, mock 8131, bridge 8132 on the summit-air box). Evals go through the Retell chat stack with this arm's chat agent:

```bash
RETELL_CHAT_AGENT_ID=agent_4fc47d4800adc7aa7b720abc46 \
RETELL_EVAL_TOOL_SERVER=https://server-led.45-77-105-247.sslip.io \
RETELL_EVAL_DEBUG_TOKEN=<SUMMIT_EVAL_TOKEN> DEBUG_TOKEN=<SUMMIT_EVAL_TOKEN> \
  make eval STACKS=retell TAGS=id:no-heat-unknown-caller
```

The token is `SUMMIT_EVAL_TOKEN` in `~/summit-air/retell.env` on the box.
