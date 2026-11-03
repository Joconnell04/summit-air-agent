# 62. Which extra entrants join the bake-off, and how they're tested without phone calls

Issue: #62. Status: ready

## Question
Besides our LiveKit stack, Retell and Vapi (a written comparison plus one test call, DECISIONS.md 020), which other platforms are worth testing, and can they be scored without Jackson calling each one?

## Options
### A. ElevenLabs Agents plus OpenAI Realtime, on a simulated audio rig (recommended)
ElevenLabs Agents in custom-LLM mode runs our same Haiku brain and tool server behind ElevenLabs' turn-taking and voice, so our server still decides hang-ups. OpenAI Realtime, already planned as a side check, is promoted to a full simulated entrant. Gemini Live is dropped. A small rig streams synthesized caller speech to each platform and times the first sound back, so no one dials. Real calls go only to the final two.
### B. ElevenLabs only
The same rig, one new entrant. OpenAI Realtime keeps its planned 3 real calls.
### C. Add nothing
Retell and our stack, with Vapi as a note. Cheapest, but "better than managed platforms" would rest on one platform.

## Context

| | A | B | C |
|---|---|---|---|
| New entrants | 2 | 1 | 0 |
| Build effort (ASSUMED) | about 2 days ElevenLabs, 1.5 days Realtime, plus the rig | about 2 days plus the rig | none |
| Spend for the simulated round (ASSUMED) | about $15 across four stacks | about $10 | none |
| Calls Jackson makes | 10 each for the final two only | same | 10 each for two stacks |

- Nothing published beats our design's reply delay: about 1.0 s on the earlier practice agent (DECISIONS.md 006). The best independent median for a managed stack is Telnyx at 1.30 s, then ElevenLabs at 1.42 s (SOURCE: OpenBenchmarks, exploration 013). A newcomer wins only by tying us on speed and also keeping the safety lines exact and hang-ups on our server.
- ElevenLabs may still reword our text; the first gas run settles it, and a paraphrase drops it.
- Gemini Live averaged 3.05 s in one independent test and runs on preview models (SOURCE: exploration 013).
- Reserves if A leaves a gap: Deepgram's Voice Agent API, Pipecat, Telnyx. Out: Bland, Synthflow, Hume, Cartesia's managed agents, because our server can't control the call.
- What it shows Revin: the choice of our stack measured against the strongest managed options on the same brain and scenarios, not argued.

## Recommendation
A. It tests the two strongest alternatives on the terms that matter (exact safety words, server-owned hang-ups, reply delay) for about $15 and no extra calls from Jackson. A newcomer is chosen only if it is at least 0.3 s faster on plain turns and passes every must-pass scenario word for word three times; anything less is a tie, and a tie keeps our stack. This changes if the rig's numbers disagree with real calls by more than the gap between entrants, in which case the phone loop is used for everything.

## Research notes
All figures, sources and the rig design are in docs/explorations/013-more-bakeoff-entrants.md (sections 3 and 4). Amazon Nova Sonic, Azure Voice Live and Google's agent products were not researched.
