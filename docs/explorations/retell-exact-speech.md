# Retell: making the agent say an exact sentence

Retell has no setting that speaks a tool result word for word on a retell-llm agent; a result always goes back to the LLM first (SOURCE [1][2]). Verbatim speech exists in only two places: static-sentence nodes in a Conversation Flow, and custom-LLM mode, where our server sends the text and can mix fixed lines with Haiku turns (SOURCE [3][4]). For a retell-llm agent the cheapest fix is a static execution message on a tool, which is verbatim but fixed at configuration time (SOURCE [1]); the full fix is custom-LLM mode, which breaks the dashboard-tuning workflow of DECISIONS 112.

## 1. Question

How can our server make the Retell agent say an exact sentence, and can a call switch models?

## 2. Why it matters to Summit Air

Spec S-17 requires the gas and carbon monoxide lines word for word, and the repo treats the read-back, the DNC confirm and the open-door line the same way. Today Retell's model rewords about half of the `say` strings (FROM Jackson). The gates hold, but the caller hears the paraphrase. The single-prompt agent paraphrased the gas lines on 5 of 6 audio calls (MEASURED, evals/rounds/retell-flow-01.md).

## 3. Options

| Option | Verbatim | Effort | Latency effect | Src |
|---|---|---|---|---|
| A. Tool execution message set to `static_text`, `speak_after_execution` off | Yes, but fixed text per tool; cannot carry a server-chosen sentence | Low: edit the tool config | Speaks as the tool starts, before the HTTP call returns (SOURCE) | [1] |
| B. Custom-LLM WebSocket: Retell keeps telephony, STT, turn-taking, TTS; our server answers each `response_required` | Yes, any turn. `agent_interrupt` can also speak at any moment | High: a WebSocket service, our own Haiku call | Fixed-line turns skip the model (ASSUMED faster). Haiku turns add our hop (ASSUMED tens of ms if hosted in the US) | [3][4][5] |
| C. Conversation Flow static-sentence node saying `{{line}}` | Yes, node speaks "your exact sentence first" | Already built (`voice/retell_flow/`) | The flow's per-turn server hop made a 2.63 s plain-turn median against 1.90 s (MEASURED, retell-flow-01) | [6] |
| D. Update Live Call with `trigger_response` or `additional_context` | No. It nudges the LLM to speak; it has no `say` field | Done today | Interrupts the agent mid-speech | [7] |

Notes on B. The server may answer one turn with fixed text (`content_complete: true`) and the next with streamed Haiku text, so partial use is possible (ASSUMED; each turn is a separate request, no doc says so). A holding line can stay open under one `response_id` while a tool runs (SOURCE [5]). Costs: the LLM playground, simulation and batch tests do not work for custom-LLM agents, agents cannot transfer to one, and we own latency and reconnects (SOURCE [4]). Fast tier and fallback model are unsupported (SOURCE [9]). The repo has no custom-LLM bridge yet (MEASURED, grep). Hosted Haiku 5.5 is $0.0032/min (SOURCE [10]); the custom-LLM rate is not published.

Notes on C. Static text is verbatim in our flow: safety lines were word for word on 79 of 79 standard calls (MEASURED, retell-flow-01). A Retell forum thread reports static nodes ignored on an API-built flow, unanswered by staff (SOURCE [11]). Our flow's `{{line}}` node worked (MEASURED).

### Models per node, and promotion

- Each Conversation Flow node can pick its own LLM (SOURCE [6][12]).
- Agent swap hands the live call to another agent with its own LLM, prompt and tools. The transcript carries over, and A custom-LLM agent cannot be the target (SOURCE [13]).
- Fast fallback runs a backup model beside the primary. It picks per request, never switches the call permanently, and is a latency tool, not promotion (SOURCE [9]).
- Update Live Call changes variables, metadata and context, not the model (SOURCE [7]). In custom-LLM mode we pick the model every turn (ASSUMED, follows from B).

## 4. Recommendation

Do A now for the fixed lines: the safety lines, the goodbye and the DNC confirm each become a tool whose static execution message holds the tenant's exact text. Test first that `{{var}}` works there (ASSUMED, unverified). Test B on a copy of Jackson's agent for the dynamic lines (read-back, confirmations); only B puts the server's sentence straight on the voice. B should be one lane with a gate: verbatim turns 100%, and a median plain turn at or under Retell native's 1.2 to 1.7 s (MEASURED, DECISIONS 112). Skip C, and skip GPT-Live-1, whose voice model speaks backend text "in its own words" (SOURCE [8]).

What would change this: if Retell adds a "speak result" field on custom functions, take that and skip B. If B's Haiku turns run more than about 0.3 s slower than hosted (ASSUMED threshold), keep A and tighten the prompt instead. For a stronger model mid-call, use agent swap.

## 5. Open questions for Jackson

- B moves the prompt out of Retell's dashboard and builder assistant and into our repo. Is that acceptable given DECISIONS 112?
- Is a fixed-text tool (A) enough for the safety lines, or must the read-back also be exact?

## 6. Sources

Read 2026-11-05 unless dated.

1. https://docs.retellai.com/api-references/create-retell-llm.md
2. https://docs.retellai.com/build/conversation-flow/custom-function.md
3. https://docs.retellai.com/api-references/llm-websocket.md
4. https://docs.retellai.com/integrate-llm/overview
5. https://docs.retellai.com/integrate-llm/integrate-function-calling.md
6. https://docs.retellai.com/build/conversation-flow/conversation-node.md
7. https://docs.retellai.com/api-references/update-live-call.md
8. https://docs.retellai.com/build/gpt-live.md
9. https://docs.retellai.com/build/llm-options.md
10. https://www.retellai.com/pricing
11. https://community.retellai.com/t/conversation-flow-agent-ignores-static-text-nodes-llm-generating-responses-instead-of-speaking-hardcoded-text/2078 (2026-03-23)
12. https://www.retellai.com/changelog (2026-03-19, per-node LLM overrides)
13. https://docs.retellai.com/build/single-multi-prompt/transfer-agent.md
