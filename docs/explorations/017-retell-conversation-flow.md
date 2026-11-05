# 017. Our design as a Retell Conversation Flow

Written by the Retell flow lane (issue #133) before building `voice/retell_flow/`, from the code and Retell's current docs, then checked by a prototype flow over Retell's chat API. The build follows this map; where the build differs, the difference is noted at the end.

## Question

The single-prompt Retell baseline lost the audio bake-off partly because its model paraphrased the gas lines (DECISIONS.md 038). Retell's Conversation Flow has static-sentence nodes, function nodes that run on entry, and edges that branch on values our server returns. Can those carry every rule our stack enforces, with our tool server still deciding every gate, and does the result do better?

## What Retell's flow offers (read 2026-11-04)

| Piece | What it does | Source |
|---|---|---|
| Conversation node | Dialogue with no tools. Prompt mode, or a static sentence said exactly, then dialogue if the call stays in the node. Static sentences take `{{variables}}` | conversation-node, dynamic-variables |
| Subagent node | Dialogue with tools the model chooses to call. Prompt only, no static sentence | subagent-node |
| Function node | Runs one tool on entry, with no model choice; "wait for completion" holds the transition until the result is back | function-node |
| Custom function | Calls our URL, signed with `X-Retell-Signature`; `response_variables` copy fields of the JSON answer into dynamic variables | custom-function |
| Logic split (`branch`) | Routes on entry, silently, by the same conditions as edges; always has an else | logic-split-node |
| Edges | Equation conditions on dynamic variables are checked first, deterministically, then prompt conditions by a model, then else. An always edge fires on every caller turn; a skip-response edge fires when the agent finishes speaking | transition-condition |
| Global node | Reachable from any node on a prompt or equation condition | global-node |
| End node | Ends the call, optionally after a static sentence | end-node |
| Call transfer node | Transfers to a number, which can be a dynamic variable; a "transfer failed" edge comes back | call-transfer-node |
| Node overrides | Per node: model, interruption sensitivity, response wait, voice speed, transcription mode, boosted keywords | conversation-node |
| Flow-level model | `model_config` with Retell's model list, which includes `claude-5.5-haiku`, our live model | create-conversation-flow API |
| API | `POST /create-conversation-flow`, `GET /get-conversation-flow/{id}`, `PATCH /update-conversation-flow/{id}`, `GET /v2/list-conversation-flows` (the unversioned list was removed 2026-06-15) | API reference, deprecation notice |

All pages under https://docs.retellai.com/build/conversation-flow/ and https://docs.retellai.com/api-references/, read 2026-11-04.

## What the prototype showed

A throwaway flow (a stub server behind a tunnel, five chats, about 10 cents of Retell chat, MEASURED from each chat's `chat_cost`) settled four behaviors the docs leave open:

1. **A function node can run on every caller turn before the agent answers.** A dialogue node whose only way out is an else edge into a function node, then a logic split back to the dialogue node, ran the function between every caller line and the reply, and the reply came from the dialogue node. So our server's turn check (safety, commercial, do-not-call, hand-off, notes) can run before every reply, as it does on our stack, instead of racing the reply as the baseline's webhook does.
2. **An equation edge on a subagent fires straight after a tool result.** When a tool's `response_variables` set `route` to `line`, the edge `{{route}} == "line"` moved the call to a static-sentence node at once, and the caller heard the server's sentence exactly. This held with `speak_after_execution` on: the edge beat the model's follow-up, and when the server sent no line the model spoke about the result as usual.
3. **A global node with the same equation did not fire after a tool result** when the subagent's only way out was an always edge; the model paraphrased the read-back instead. Local equation edges on each stage node are used instead of global nodes.
4. **An end node reached only by `{{route}} == "end"`** ended the chat after saying the server's sign-off as written.

The forum reports two related failure modes. A static sentence before a function node isn't heard until the function finishes (https://community.retellai.com/t/conversation-node-with-static-sentence-and-skip-user-response-will-not-play-immediately-if-next-node-is-function-node/947), and a static sentence entered while the caller is still talking can be talked over and lost (https://community.retellai.com/t/conversation-nodes-static-text-instruction-never-spoken-else-edge-fires-3s-after-node-entry/3394, where Retell staff call it a floor race and point at interruption sensitivity). The map avoids the first: no static node leads straight into a function node except the hand-off line before a transfer, and the end node speaks its own line. For the second, the safety node sets interruption sensitivity to 0, as our worker plays the safety lines with interruptions off. The voice-only half of this can only be checked on a phone call (step 5 of the lane).

## The flow

```
greeting (static {{greeting}})          voice only; a chat starts with the caller
   |  else
   v
turn_check (function: our /calls/{id}/turn through the bridge)   <---------------------+
   |  equation edges, top to bottom                                                    |
   +-- {{turn_status}} failure --> server_down (end node, the tenant's server-down line)
   +-- safety   --> safety_line (static {{line}}, can't be interrupted) --else-------->+
   +-- handoff  --> handoff_line (static) --skip--> handoff_call (cold) --failed--> handoff_failed
   +-- line     --> server_line (static {{line}}) --else------------------------------>+
   +-- notice   --> notice_line (static {{line}}) --skip--> stage (the model then answers)
   +-- firm     --> firm_line (static {{line}}, can't be interrupted) --else---------->+
   +-- end_line --> end_with_line (end node, static {{line}})
   +-- else     --> stage (branch on {{stage}}, the server's call stage)
                      +-- opening / booking / closing (subagent nodes, all model tools)
                            |  equation edges after any tool result, on {{route}}:
                            |  line -> server_line, end_line -> end_with_line,
                            |  end -> end_quiet, transfer -> transfer_line --skip--> transfer_call
                            |  (warm) --failed--> transfer_failed
                            +--else (next caller line)----------------------------->+

handoff_failed / transfer_failed (function: tell the server nobody answered)
   +-- {{failed_status}} failure --> fallback_line (static {{fallback}}, the callback line the
   |                                 server sent with the plan)
   +-- line --> server_line (the server's callback line);  else --> fallback_line
```

- **One prompt, three stage nodes.** The global prompt is `{{instructions}}`, the whole prompt `prompts/assemble.py` builds for the call, exactly what our worker reads. Each stage node adds a short stage note and `{{notes}}`, the `[server]` notes from this turn's check. The server, not the model, picks the stage (`app/rules.py` `stage_now`), returned by the turn check. Scoping the prompt itself by stage is exploration 016's separate idea and is not tested here, so this comparison isolates the platform's mechanics: server-chosen stages, server lines said from code, and the per-turn check before the reply.
- **Every stage node has every model tool.** A caller can cancel, ask for a person or end in any stage, and a second job after a booking needs the booking tools in closing (the server's stages only move forward). Retell recommends few tools per node; dropping tools by stage would add failures our stack doesn't have, so it isn't done.
- **Every tool answer carries the route.** The bridge adds `route`, `line` and, for a transfer, `transfer_number` and `fallback` to every envelope, so a variable left from an earlier turn can never fire an edge. When a function node's call itself fails (a timeout, or the bridge unreachable), its variables keep their last values, so each function node first checks Retell's own `execution_status_variable` and routes a failure before any route: the turn check to the server-down end, a transfer report to the fallback line.
- **No new caller line, same stage.** A turn check that posts no new caller line (an event that came twice) keeps the stage and route it had, rather than dropping back to the opening.

## Server rules (layer 1)

| Rule | Place in the flow |
|---|---|
| Booking needs a confirmed read-back (S-26) | Unchanged in `tools.book_appointment`, called from a subagent node; a refusal comes back as the envelope |
| The ZIP comes from the caller (058), address lookup exact match only (069) | Unchanged in `check_service_area`, `find_address`; the lookup's questions come back as `line` (server_line) |
| Commercial callers are declined (S-05) | The turn check marks the call commercial from the caller's words; `book_appointment` refuses. Node: turn_check, then the stage node with the note in `{{notes}}` |
| Priority only rises, on the caller's own words and the outside temperature (019, 072, 073) | Unchanged server code on every `/turn` and `find_slots`; the side model runs on the server beside each `/turn` |
| Safety gate on booking tools and `end_call` (S-18, S-19) | Unchanged in `tools._safety_gate`, `tools.end_call`; `gate_safety` refusals are said as written through server_line |
| Hang-up is the server's | `end_call` is a custom tool in each stage node; only its `hang_up` sets `route` to `end`/`end_line`, the only edges into the end nodes. Retell's built-in end-call tool is not attached anywhere |
| Never end in the turn of the confirmation (S-30) | Unchanged: `end_call` refuses `anything_else_first` |
| Transfers are the server's (051, 061, app/transfer.py) | Two routes, both from server values: a demand on a caller line (`/turn` returns `handoff` with a plan) at turn_check, a cold transfer; a calmer request through `flag_person`, after which the bridge asks `/calls/{id}/transfer` and returns `transfer` only when the server sends a plan with a number, a warm transfer. The transfer node dials `{{transfer_number}}` from that plan. A demand with no number set gets the stored callback line on firm_line, uninterruptible, as our worker says it |
| Warm transfer only to a person who accepts (C-11) | Weaker than our stack. Our worker plays the person a press-1 prompt and gives a voicemail no caller details. Retell's warm transfer connects the caller only after its human detection says a person answered, and whispers the server's summary to whoever it judged a person; there is no press-1 step, so a voicemail Retell mistakes for a person hears the summary and the transfer is reported connected. Unmeasured here |
| Server down mid-call | A caller line the tool server didn't take, or a turn check that failed outright, ends the call on the tenant's server-down line, as our worker does (`voice/livekit/call.py` `server_down`) |
| Reschedule and cancel only the caller's own job, once each (S-31) | Unchanged in the tools |
| Least privilege (S-39) | Unchanged: the flow has only our tools; Retell's built-in transfer, end-call, SMS and code tools are not attached |
| Do-not-call on the caller's words (S-49) | The turn check marks it; the note goes to `{{notes}}`; `mark_do_not_call` returns the line, said through server_line |
| Recording stopped when the caller objects (S-45) | The turn check returns the stop line as `notice`: said from a static node, then the stage node answers the rest of what the caller said, as on our worker. The bridge sets the call's data storage to basic attributes through Update Live Call, so Retell keeps no recording or transcript once its webhooks have gone out. Retell still records until the call ends, so the line ("I've turned the recording off") is true of what is kept, not of the microphone |
| Idempotent booking (S-27) | Unchanged: the bridge sends the same `Idempotency-Key` as the baseline |

## Server-spoken lines (layer 2)

Our worker speaks these word for word; on the single-prompt baseline the model said them. In the flow every one is a static sentence taking the server's text as `{{line}}`:

| Line | From | Node |
|---|---|---|
| Gas and carbon monoxide lines, the repeat variant, the check question (S-17, S-20) | `/turn` `safety.text` | safety_line, interruption sensitivity 0 |
| The safety sign-off (S-19) | `end_call` `say` | end_with_line when it hangs up, server_line otherwise |
| `gate_safety` refusals | the refusal's `say` | server_line |
| The read-back sentence, and its `gate_read_back` refusal | `read_back` `say` | server_line |
| Booking, move and cancel confirmations | `say` on ok | server_line |
| `fsm_unavailable` (S-48) | `book_appointment` `say` | server_line |
| ZIP mismatch, the address lookup's questions | `check_service_area`, `find_address` refusals | server_line |
| The hand-off line, and the callback line when nobody answers | `/turn` handoff `say`, `/calls/{id}/handoff/{id}/done` `say` | handoff_line, then server_line after a failed transfer |
| Recording notice and greeting (two-party consent) | `/calls/start`, `conversation.opening` | greeting, a static sentence (voice; the chat harness shows it to the caller, as for the baseline) |
| Server-down line | `conversation.server_down` at the start; the tenant's line mid-call | the greeting slot, then the bridge's stop-call, as on the baseline; mid-call, end_with_line or the server_down end node |
| The slower read-back in parts (`pace_address`) | `read_back` `parts` | Can't be done in a flow: one static sentence has one voice speed. The whole sentence is said at the node's speed |
| The electrical (sparks) line, the dead-air check-in and goodbye | call-handling lane (#130), not on main yet | The sparks line arrives as a safety action from `/turn`, so it lands in safety_line with no change. The dead-air check-in is a timer in our worker; Retell's reminders are model-written (`reminder_trigger_ms`), so it can't be the server's line in a flow |

## Speech filter (layer 3)

`prompts/assemble.py` `SpokenReply` drops parts of the model's reply before they're spoken. Retell streams a subagent's text to the voice with no hook in between, so none of these rules can run on a subagent's text. Two of them no longer need to, because the sentence is now static:

| Rule | In the flow |
|---|---|
| Drop the model's text when the reply calls a tool the server speaks for (read-back, safety sign-off) | Partly: the static node says the server's line exactly, but text the model wrote before the tool call in the same reply is still spoken (Retell speaks it as it streams; `speak_after_execution` only governs the text after). The stage note tells the model to call those tools without saying anything first, and on a safety call to add no goodbye before the sign-off |
| Drop a sentence announcing the read-back, or restating the caller's ZIP or number | Can't be done in a flow (no hook on model text) |
| Drop tool talk, internal words ("flag"), fees | Can't be done in a flow |
| Carbon monoxide words off a safety call, evacuation advice once, priority claims before Urgent | Can't be done in a flow |
| One question a reply | Can't be done in a flow; left to the prompt, as on the baseline |

## Per-turn `[server]` notes (layer 4)

`/turn` returns hints and known fields; the bridge turns them into text with `prompts/assemble.server_notes`, as the worker does, and the turn check returns them as `{{notes}}`, which every stage node's instruction includes. Because the turn check runs before every reply, each note reaches the reply it is for. On the baseline they went in by Update Live Call, after the reply could start. Two parts reach the model only as text: the pace notes (`pace_slower`, `pace_faster`), whose voice-speed change can only be set per node in a flow, and the shorter safety variant on a repeat, which the server picks and the safety node says, so only its choice of variant is the server's, as on our worker.

## Side model (layer 5)

Runs on the tool server beside each `/turn` and can only raise priority (app/side_model.py). Nothing in the flow touches it; it works as long as every caller line reaches `/turn`, which the turn check guarantees.

## Owner guidance by stage (layer 6)

Whole-call and opening guidance is in `{{instructions}}`; booking and closing guidance comes as `stage_booking`/`stage_closing` notes from `/turn` when the server's stage moves, so in `{{notes}}`. Unchanged server code.

## System prompt (layer 7)

The global prompt is `{{instructions}}`, unchanged. Stage nodes add a few lines each (`voice/retell_flow/build.py`).

## Journeys

| Journey | Path through the flow |
|---|---|
| 1 No heat or cooling, 2 vulnerable household, 4 maintenance, 5 member, 7 repeat caller, 9 quote, 13 complaint, 14 new member | opening, booking and closing stage nodes; read-back and confirmations through server_line |
| 3 Safety caller | turn_check, safety_line, then stage nodes with the safety gates; sign-off through end_with_line |
| 6 Renter or can't approve | `approval_unclear` note in `{{notes}}`; server rules unchanged |
| 8 Commercial | turn_check marks the call; the decline is the model's, the refusal is the server's |
| 10 Not a customer call | `end_call` with its reason; end_quiet |
| 11 After hours | server rules unchanged (cutoffs, on-call); a demand for a person after hours goes to the on-call number through handoff |
| 12 Calls Summit Air makes | Not built, as on the baseline: outbound callbacks run on our worker |
| Hand-off (demand or flag) | handoff_line or transfer_line, the transfer node, and the failed node with the callback line if nobody answers. In the eval chats a demand is reported as handed off, as our harness's default phone company does; the two scenarios that make the transfer fail can't be set on the Retell eval instance. A `flag_person` warm transfer is never offered in a chat, the same as in our harness |

## Can't be done in a flow

1. **The speech filter on model text** (layer 3 above), the same gap as the baseline.
2. **Per-question voice settings**: the longer wait after asking for digits (C-48) and the pace changes are per node in Retell, not per question or per call.
3. **The slower read-back in parts.**
4. **Server-owned timeouts**: Retell's silence timeout and maximum duration still end a call without asking the server, as on the baseline.
5. **Stopping Retell's own recording mid-call** when a caller objects: it can only be kept from being stored.
6. **A chat that opens with the greeting**: Retell chat starts with the caller, so the chat flow starts at the turn check and the greeting isn't in the model's history, the same as the baseline's eval runs.
7. **The press-1 accept step on a warm transfer** (above).
8. **One agent line per reply in the server's record**: the model's own words and the server's static line are two Retell messages, so the server logs them as two agent lines. The caller hears them back to back; a check that wants the confirmation's lead-in in the same line (`booking_lead_in`) can't see it.
9. **Outbound callbacks** (journey 12), as on the baseline.

## Cost and risk

- Every caller turn adds one function call, Retell to our bridge and back, before the model starts. Its cost in delay is measured in the eval run and on the test call, not assumed here.
- Retell's chat cost per message is 0.1 cents for `claude-5.5-haiku`; a chat that ends also bills its post-chat analysis on another model, about 2.1 cents (MEASURED, prototype `chat_cost`), which the build turns off.
- Risk: an edge or node wired wrong strands the call in a node. The build's tests check the graph: every node reachable, every route value has an edge from turn_check and from every stage node, and no end or transfer node is reachable except through a route value.

## What the review changed

The reviewer agent (`.claude/agents/reviewer.md`) read the first build against this map and the spec. Its findings, all fixed before the paid runs: a dead tool server mid-call let the model answer with no safety check; a failed turn check could replay an earlier turn's route; the warm transfer whispered the summary with human detection left implicit; the recording line went out as a plain server line, so the model never answered the rest of the caller's words; a turn check with no new caller line dropped the stage to the opening; the stage note told the model to call `end_call` without a goodbye, against `prompts/spine.md`; a demand with no number set got an interruptible callback line; a failed transfer report could leave the caller with nothing said; and the analysis model was named outside config/models.yaml. A second, targeted review of those fixes found the transfer-report failure could still say an empty fallback, the notice line could be talked off its only edge, and the recording line was said even when Retell refused to drop the recording; those are fixed too. Two of its points stand as limits: a turn check that times out twice (10 s each) ends the call as if the server were down, and the warm transfer rests on Retell's human detection.

## Recommendation

Build it as mapped, run the must-pass set and the standard suite at two samples, and judge it against our stack's 92% reference. It should do better than the baseline on the safety lines word for word and on notes arriving before the reply; it can't match our stack on the speech filter.

## What the run showed

Built as mapped, the flow held every server rule and said every fixed line exactly, and scored 53% on the standard suite against our stack's 92%: the gap is the speech filter in layer 3, which this map listed as "can't be done in a flow" (evals/rounds/retell-flow-01.md, DECISIONS.md 083).

## Sources

- Retell docs as listed above, read 2026-11-04.
- Retell community forum threads 947 and 3394, read 2026-11-04.
- Repo: `docs/architecture.md` ("How rules are layered"), `app/README.md` ("Where each rule lives"), `app/lifecycle.py` `turn`, `app/tools.py`, `prompts/assemble.py` (`SpokenReply`, `spoken_by_server`), `voice/livekit/call.py`, `voice/retell/README.md`, exploration 016.
