# Architecture

Status: version 1, written before the build. The voice platform (#4) is still open, so the voice layer is described for both candidates.

## Parts

1. **Caller and phone number.** One Twilio number for calls and texts (C-38).
2. **Voice layer.** Hears the caller, runs the conversation, speaks. Thin: it turns speech into text, calls our tools, and turns replies into speech.
3. **Tool server (the integration layer).** Every fact and rule lives here: lookups, priority, the safety and read-back gates, booking, flags, texts, call records. The model asks; the server decides.
4. **FSM adapter.** The only code that talks to the company's field service system. Today it points at the mock ServiceTitan; later at the real one (C-03).
5. **Mock ServiceTitan.** The customer's system, emulated: CRM, memberships, equipment, jobs, dispatch, call log (brief: docs/ai-process/briefs/01-mock-servicetitan.md).

## Repo layout

```
app/                 tool server (template code)
  fsm/               FSM adapter interface and the ServiceTitan adapter
servicetitan_mock/   the mock customer system
voice/
  livekit/           LiveKit worker (only if LiveKit is built)
  retell/            Retell agent config as code and push script (only if Retell is built)
prompts/             prompt files by job, shared by both voice layers
tenants/summit-air/  everything specific to Summit Air (docs/template.md)
config/models.yaml   model profiles
evals/               harness, scenarios, results
postman/             collections and environments for the tool server and the mock
```

## Bake-off without a messy repo

If both voice layers are built, the rule is that everything except the two folders under `voice/` is shared, and neither folder holds a rule, a prompt or a fact.

- Both layers call the same tool server endpoints with the same payloads and load the same prompt files.
- Each layer is one folder plus one Makefile target (`make voice-livekit`, `make voice-retell`) and one entry in the deploy config.
- The evals run against the tool server and prompts, so the same scenarios score both layers; only the 10 audio calls differ.
- Picking the winner: delete the losing folder and its Makefile target, set the Twilio trunk's destination, record the decision. Nothing else changes. A test checks that no file outside `voice/` imports from either layer, so the delete stays clean.

## Logging and traces

Built in from the first commit, because QA and live debugging depend on it.

- Every tool call carries a call id. Every request and response is written as one JSON line with the call id, request id, tool, arguments, result, the gate that fired if any, and the time taken.
- `GET /debug/calls/{call_id}` returns a call's full timeline: what the caller said, what the agent said, each tool call and result, flags raised. `GET /debug/logs` filters by call, request, tool or errors. Both need a debug token.
- **Postman.** A collection under `postman/` covers every tool endpoint with assertions, plus a Triage folder that calls the debug endpoints for a given call id. Newman runs it locally and in CI (`make postman`). The mock ServiceTitan has its own collection.
- **For dev agents.** `make trace CALL=<id>` prints a call's timeline in plain text, so a reviewing agent can read what happened without a browser. The `reviewer` agent and eval failures link to it.
- **Owner view.** The same data feeds the owner and dispatcher view (C-04).

## Hosting

The tool server and the LiveKit voice worker run on an always-on Vultr server. Whether that is the existing shared server or a new one for this project is open (see the readout surfaced after decision 004). Capacity limits and cost are in docs/explorations/003-capacity-and-cost.md and docs/cost.md.

### Planned reboots

The existing server installs security updates on its own and reboots when an update needs it, at 09:30 UTC (4:30 a.m. New York time in winter, 5:30 a.m. in summer). The setting is `Unattended-Upgrade::Automatic-Reboot-Time "09:30"` in `/etc/apt/apt.conf.d/52epic-reboot`, owned by another project on the same server, so we leave it alone.

- How often: 5 reboots between Sep 5 and Oct 14 (MEASURED, `last -x`), only on days an update required one.
- How long the server is down: 10 to 12 seconds from shutdown to boot (MEASURED, `last -x -F`, all five reboots). Our services start on boot and are taking calls about 30 to 60 seconds after that (ASSUMED until measured on the first reboot; Python start-up plus loading the voice models).
- Impact: a call in progress at that moment drops, and a new call in that minute isn't answered. At 4:30 or 5:30 a.m. that is rare, and a caller can call straight back. Low impact, so we accept it rather than turn updates off.

### Knowing it's back

- Services start on boot (`@reboot` in the user crontab, no root needed) and a watchdog run every minute restarts anything that has stopped.
- A health endpoint (`GET /healthz`) checks the tool server, the database and that the voice worker is registered with LiveKit.
- An outside monitor (Healthchecks.io free plan) expects a ping from the watchdog every minute and alerts by email and text if pings stop for 5 minutes. Every reboot therefore shows up as a short gap, and a server that doesn't come back raises an alert.
- If the worker is down while LiveKit is up, Twilio won't fail over on its own (docs/explorations/004-call-control-and-numbers.md), so the watchdog also switches the Twilio number to its backup destination when the health check fails twice in a row, and back when it passes.
