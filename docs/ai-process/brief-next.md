# Brief: where things stand

The one page to read before the live calls and the interview. Older briefs are in [past-briefs/](past-briefs/); the last one from the custom-stack period is [brief-custom-stack-final.md](past-briefs/brief-custom-stack-final.md).

## Where it is

The agent runs on Retell, starting from Jackson's own agent "Summit Air Jackson" (agent_a3e38e6aa3d4e75dfac12aafc8), which (646) 624-0418 rings through our inbound webhook. Retell runs the phone line, listening, turn-taking and voice; our tool server owns every fact and rule. How we got here: DECISIONS.md 111 and 112, and the [simplicity slate](../../evals/rounds/simplicity-slate-01.md).

## Needs Jackson

1. **The next call to his agent,** once the Retell lane's change sets are published and snapshotted, the server fixes are deployed, and a scripted phone call passes.
2. **Retell prototype agents.** The flow, native and copy agents from the slate are still on the Retell account. Say whether to delete them; nothing deletes without his OK.

## Being built

- **Retell lane:** snapshots of his agent into `retell/summit-air-jackson/` after every change; the greeting and call context; safety lines said once; the priority question on no-heat and no-AC calls; accurate speech-to-text; our end_call in place of the built-in hang-up; a general HVAC knowledge base with strict retrieval; eleven simulation tests; a reference of every setting in [docs/retell-knobs.md](../retell-knobs.md).
- **Server lane:** a real live-world reset, a distinct "can't see your number" refusal, no open-visit details before the identity check, and the reschedule asking for a name and address first.

## Decisions to know cold

| Topic | What we decided | Entry |
|---|---|---|
| Platform | Retell for the voice, our tool server for every rule; the custom stack is the reference | 111, 112 |
| The core rule | The model asks; the server decides. Booking, refusals, hang-ups, priority and safety lines are server code | 022 |
| Live model | Haiku 5.5 | 036 |
| Safety | Fixed lines, said word for word on the server's instruction; the call can't end until they're said | 009, 033, 060 |
| Priority | Set by the server from what the caller says and the outside temperature; only goes up; 60 and over is an older adult | 019, 072 |
| Windows | Any run of 30-minute blocks, two hours unless the caller asks | 108 |
| Service area | All five boroughs | 105 |
| Dropped stacks | GPT-Live-1 dropped after one call; Deepgram Voice Agent paused | 107, 111 |
