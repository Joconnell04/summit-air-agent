# Server-led arm, round 1: the server runs the steps, the model talks

Issue #171, brief `docs/ai-process/briefs/server-led-flow.md`. Arm D of the agent side-by-side: Jackson's Retell agent with the call flow moved out of the prompt and into the tool server. Every number here is MEASURED from the files in `server-led-01-data/` unless it says otherwise.

**In short.** The order now holds in code on every call: no opening before a confirmed read-back, no booking before the three visit questions, no goodbye while a booking sentence is unsaid. The prompt is 828 words, 37% of Jackson's 2,254 (MEASURED with `wc -w`). But on simulated chats Haiku still breaks the voice-level checks the arm was meant to fix: it reworded about half of the server's sentences, narrated its steps on 8 of 12 calls and said a caller's name on 3 of 12. Prompt wording stopped moving those numbers after the second round. The ceiling here is the model writing every word, not where the flow lives.

## 1. What ran

- **Agents.** Voice `agent_e758054f5009c5106edd882d81` (LLM `llm_dfd71458b4bbb517fb13ea5463ef`), chat `agent_4fc47d4800adc7aa7b720abc46` (LLM `llm_756e438fff63e41dda13ca5bac46`), both "Summit Air server-led (test)". Claude Haiku 5.5 on Retell. Voice and listening settings are read from Jackson's agent (`retell/arms/server-led/push.py`).
- **Instance.** `https://server-led.45-77-105-247.sslip.io`: its own tool server with `SERVER_LED=1`, mock ServiceTitan and bridge (`deploy/server-led/`). The live services were not touched.
- **Harness.** The text harness through Retell chat (`make eval STACKS=retell` with this arm's chat agent and instance, `voice/retell/eval_adapter.py`): our caller simulator, our code checks, our grader. Six scenarios a round: `no-heat-unknown-caller`, `tune-up-known-caller`, `elderly-no-heat`, `readback-yes-but-unit`, `gas-smell-in-passing`, `commercial-bakery` (four in round 2, one in round 1).
- **Gross-error checks.** `server-led-01-data/gross.py`, patterns over the transcripts and the tool server's record of each call (`traces/`). Rough: it prints every line it flags so the counts can be checked by eye.

## 2. Rounds

| Round | Results file | What changed before it |
|---|---|---|
| 1 | `20261106-010112` | First build |
| 2 | `20261106-011527` | end_call waits only on outcome sentences (it re-offered a booked window after goodbye); `next` right after `say`; the cost line |
| 3 | `20261106-012105` | Open-door line before the offer's closing question (dropped after it every time); known-caller and echo lines in the prompt |
| 4 | `20261106-012601` | Temperature 0 instead of 0.3; the commercial line that contradicted the server's taken out of the prompt |
| 5 | `20261106-013056` | Jackson's sharper wording for names, the address on file, narration and gas advice |

Rounds 4 and 5 together, 12 calls (`gross.py` over both files):

| Gross error (brief) | Calls with it |
|---|---|
| 1 Says the caller's name | 3 of 12 |
| 2 Narrates its steps | 8 of 12 |
| 3 Echoes outside the read-back | 3 of 12 |
| 4 Two questions in one turn | 3 of 12 |
| 5 Server sentences not said as written | 21 of 40 sentences |
| 6 Steps out of order | 0 of 12: held by the server |
| 7 Mentions tools or notes | 2 of 12 |
| Own safety advice (gas call) | 2 of 2 gas calls |

The server's own gates fired 5 times over the 23 calls: one opening asked for before the read-back, one booking before the visit questions, three goodbyes before the booking sentence (one a bug fixed in round 2, two on reworded confirmations, fixed after round 5 by matching at half the sentence's words, `app/flow.py` `SAID_SHARE`). 10 calls booked; the rest were the on-call path after 8 PM, a commercial caller, a gas caller who declined, or a harness turn limit.

## 3. What the rounds showed

- **Order is solved in code.** Every booking had a confirmed read-back and all three visit questions first, the open-door line reached the caller word for word from round 3 on, and no call ended before its outcome was said. None of this depends on the prompt.
- **Haiku rewrites what it is handed.** It added the date to "tomorrow" on every offer and booking, turned "Okay, I've booked it" into "You're booked", added promises ("they'll ring the downstairs bell"), and turned the read-back's words into digits. Temperature 0 did not change that.
- **Some narration is the server's own wording.** "Let me get this to our on-call technician first." and "Let me check that again." are server lines (`on_call_first`, the retry line), shared with the live agent.
- **Gas in a chat can't be judged fairly.** A simulated chat has no live webhook, so the safety lines arrive only with the next tool result; Haiku answered the hazard in its own words before any tool. On a phone call the bridge says the lines as soon as the word is heard (`say_now`), which this round did not test.
- **The clock moves the scenarios.** After 8 PM New York time a no-heat call goes to the on-call path, so rounds 4 and 5 booked fewer visits than rounds 2 and 3 for reasons that have nothing to do with the arm.

## 4. What would raise the ceiling

1. **Let code speak the server's sentences.** Retell's custom-LLM socket (`voice/retell_llm/`, experiment 2 on #154) puts our code between the model and the voice: `say` is spoken as written, and the speech filter drops narration, names and echoes before anything is heard. The server-led gates in this branch work unchanged behind it. That removes errors 1 to 5 by construction rather than by asking.
2. **A stronger model on the same server.** The Sonnet 5.5 arm will show how much of the rewording is Haiku. The gates and `next` carry over as they are.
3. **Server lines that don't narrate.** Reword `on_call_first` and the retry line so the server never hands the model "let me".
4. **Dates in the server's sentences.** The grader asked for the day and date in the confirmation, and Haiku added them on its own every time; saying them in the server's sentence would remove the most common rewording.
5. **Phone-only checks.** One web or phone call to the voice agent for `say_now` on gas and the hang-up after goodbye. Not run here: the audio rig's Retell entrant patches a shared test agent another lane may be using.

## 5. Spend

Retell: 263 chat completions over 23 scenarios (MEASURED, harness logs), about $0.80 to $1.60 at Retell's per-message rates for Haiku plus the chat platform (ASSUMED from https://www.retellai.com/pricing; Retell's usage page was not read). No phone or web calls, no phone number. Caller simulator and grader: $0.73 on our Anthropic key (MEASURED, the results files' `cost_usd`).
