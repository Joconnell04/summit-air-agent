# Build brief: voice agent on LiveKit, prompts, and the phone line

Branch: `build/voice-livekit`. Depends on the tool contract (docs/tools.md); code against a stub until the tool server merges.

## Goal

The LiveKit worker that answers (646) 624-0418 and makes the few outbound calls in journey 12: Deepgram hears, Claude decides, Cartesia speaks, and every rule is the tool server's. Plus the prompts it runs and the phone wiring.

Read first: CLAUDE.md, docs/call-journeys.md, docs/spec.md (Eval, grader and Live lines), docs/tools.md, docs/latency.md, docs/scaling.md, DECISIONS.md 004 to 006, config/models.yaml, docs/history/from-other-projects/bobert/ (the earlier practice project's LiveKit worker, latency notes and prompt; learn from it, don't copy blindly).

## Scope

- `voice/archive/livekit/`: a LiveKit Agents worker (Python). Model and voice chosen by profile from config/models.yaml; Anthropic default, OpenAI and Gemini switchable. Thinking off.
- Calls `/calls/start` before the greeting, `/calls/{id}/turn` on every final transcript and agent line, and the model tools in docs/tools.md. Our own end-call and transfer tools that ask the server first; no built-in end-call tool.
- Safety lines spoken from the text the server returns, never generated.
- Turn handling: adaptive interruptions, a minimum-words threshold so noise doesn't interrupt, resume after false interruptions, a longer end-of-turn wait right after asking for digits (docs/latency.md, C-48).
- Bridge line: a pre-recorded clip played only when a tool takes longer than about 0.7 s, rules in docs/latency.md. It must never cut the agent off or overlap it: it plays only when the agent is silent, each clip is under about 1 second of audio, and if the tool answers mid-clip the reply starts right after the clip ends instead of interrupting it. Tested with a fake slow tool.
- Capacity cap as one setting; at the cap, a pre-recorded safety line and voicemail (S-47).
- Outbound dialing for journey 12 through a LiveKit outbound trunk, only when the tool server schedules a call.
- `prompts/`: split by job (identity and voice, the spine, journeys, tool use, safety hand-off), using tenant config and the knowledge folder; no Summit Air fact hard-coded.
- Phone wiring, scripted: a Twilio Elastic SIP Trunk with the 646 number, origination to LiveKit; LiveKit inbound trunk and dispatch rule; secrets from `.env`.
- Deploy to the summit-air server (docs/architecture.md, hosting): start on boot, a watchdog every minute, `/healthz`, an outside health check that alerts, and the trunk switched to a fallback when the worker is down.
- Per-turn stage timings sent with each turn (docs/latency.md).

## Must be right

Unit tests for everything that doesn't need a model or audio (tool calls go through the server, no built-in end call, bridge-line rules, capacity cap). Then a first end-to-end call to the 646 number, logged with its trace.

## Done when

`make check` passes; a real call to (646) 624-0418 books a job in the mock with the trace readable through `make trace`; the reviewer agent has checked the branch.
