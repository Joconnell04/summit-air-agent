# Brief: moving (646) onto the socket agent, with the live world

Branch: from `joco/stripped` after the server lane's last push. Issue #176 (or its own issue at the coordinator's call). Plan only; nothing here is built yet.

## Why

Jackson picked the socket version for the demo (Retell's ears and voice, our code speaking the server's sentences, Haiku writing the rest). Today it runs on its own instance (`~/summit-air/server-led`, its own tool server with `SERVER_LED=1`, its own mock), so its bookings never reach the owner view, which reads the live world (`~/summit-air/app`, tool server 8100, mock 8101). The demo number (646) has to land in the live world, and switching back must be one step.

## What changes

1. **The flow mode is per call, not per process.** Today `SERVER_LED=1` turns the flow on for every call a tool server sees. The live tool server will serve both Jackson's single-prompt agent and the socket agent at once, so:
   - `calls` gains a `flow` column (`""` or `"server_led"`), set once at `/calls/start` from an optional `flow` field in the request.
   - `app/main.py` `_tool_route` runs `flow.guide` when `ctx.call["flow"] == "server_led"` (or the process setting, kept for the test instance).
   - The bridge's inbound webhook (`voice/retell/bridge.py` `inbound`) sends `flow: server_led` when the answering agent is in a new setting, `RETELL_SERVER_LED_AGENTS` (the `agent_id` in Retell's `call_inbound` event; to be confirmed against a recorded event first, with the dialed number as the fallback key). Every other call starts exactly as today.
   - Tests: a call started without `flow` gets no `next` and no gates on a live-configured server, and one started with it gets both, side by side on the same server.
2. **The socket runs beside the live bridge.** A new process in `deploy/retell/run.sh`, `llm` on port 8114, with `TOOL_SERVER_URL=http://127.0.0.1:8100`, `RETELL_LLM_SERVER_LED=1` and the arm's prompt and tools (as `deploy/server-led/run.sh` `socket`). A Caddy route `/retell-llm/*` → 8114 on the main host. `deploy/retell/deploy.sh` starts it with the other `summit-retell@*` units.
3. **A production socket agent.** "Summit Air (socket)", made by `retell/arms/socket/push.py` with a `--live` switch: the socket URL on the main host, Jackson's voice and listening settings copied from his agent as now. The test agent stays on the server-led instance and 516 for further runs.
4. **No clock offset in the live world.** `app/clock.py` already ignores `SUMMIT_CLOCK_OFFSET_H` outside the eval and server-led worlds; a test pins that.

## Order

1. The server lane lands its last push on `joco/stripped`; I merge it into the socket branch, run `make check` and the socket's text runs, and one call on 516.
2. PR #173 (the server-led flow) and the socket PR merge into `joco/stripped`, with the per-call flow mode above. `make check` passes.
3. Deploy the live services (`deploy/summit-air/deploy.sh`, `deploy/retell/deploy.sh`) at a time the coordinator picks, with no call live on 646 (`retell/arms/socket/line.py`'s busy check, pointed at 646). Nothing changes for callers yet: 646 still answers with Jackson's agent, whose calls start without `flow`.
4. Push the production socket agent. Set `RETELL_SERVER_LED_AGENTS` to its id in `~/summit-air/retell.env` and restart the live bridge.
5. Point 646 at the production socket agent, inbound webhook unchanged (`https://45-77-105-247.sslip.io/retell/inbound`).
6. One check call on 646: the greeting, a booking, the booking on the owner view, the call's label, the socket's log line with its hold-phrase and drop counts.

## Rollback

Point 646 back at `agent_a3e38e6aa3d4e75dfac12aafc8` with the same inbound webhook (one `PATCH /update-phone-number/+16466240418`, the body `retell/arms/socket/line.py` used to restore). Calls that start after that start without `flow`, so the server is as it was for them; nothing is restarted or redeployed. A call in progress on the socket finishes on the socket.

## Risks

- **One tool server, two modes.** The flow's gates are per call, so a single-prompt call can't be refused by a server-led gate. The side-by-side test above pins it.
- **The socket's own timeouts.** A tool slower than 9 s returns the server-down line; the hold phrase covers waits over about 1.3 s. On the live box, find_slots after 461154f measured about 3.5 times faster than the 4.2 s that once dropped a call.
- **Transfers are cold.** Retell's custom-LLM protocol has only `transfer_number`; a warm transfer has to be the server's own (told to the warm-transfer lane).
- **Retell's dashboard tools don't reach it.** The prompt lives in the repo; tuning is a commit, a text run and a deploy.

## Done means

646 answers with the socket agent in the live world, its bookings show on the owner view, the rollback has been run once and undone, and `make check` passes.
