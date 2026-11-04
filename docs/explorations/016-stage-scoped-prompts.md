# 016. Stage-scoped prompts: giving the model only the instructions for where the call is

Written by the coordinating session from the code and the public guidance it cites; not a researcher run. An idea for after the live calls, not a decision.

## Question

Today the system prompt is one assembly of six files, loaded whole on every turn. Would the agent slip less if each turn carried only a fixed core plus the instructions for the call's current stage?

## The system prompt today

There is a system prompt; it is assembled, not written as one file. `prompts/assemble.py` (`instructions()`) joins, in order:

1. `prompts/identity.md`: who the agent is and how it sounds (the persona)
2. `prompts/spine.md`: the steps every call shares
3. `prompts/journeys.md`: the 14 kinds of caller, about 1,600 words, the largest part
4. the owner's guidance by stage, when there is any, under `prompts/house-guidance.md`
5. `prompts/tool-use.md`: how to read tool results, refusals and `[server]` notes
6. `prompts/safety.md`: what to do around the safety lines the server speaks

It then adds the call context: the company's facts from `tenants/summit-air/`, the local time, and what the server found before hello. The tool descriptions come from `prompts/tools.yaml`. Splitting it by job keeps each change small and easy to review: a wording fix touches `identity.md`, a new caller type touches `journeys.md`, and the eval hash shows which part changed.

Size: about 5,600 words of prompt files, and 15,586 prompt tokens per turn with the tools on the scripted test call, 15,446 of them read from the prompt cache (MEASURED, tool server log, call `call-_+16466240418_9HpKiYomqUnh`).

## Why it matters

Research on instruction following finds that the more separate rules a model holds at once, the more it drops ("How Many Instructions Can LLMs Follow at Once?", 2025). That matches what we saw: re-asking known details, two questions in one turn and repeated read-backs, each despite a prompt rule, until the fixes moved into server code (DECISIONS.md 060). Voice platforms build the same idea in as conversation flows, a graph of stages that each carry a small prompt and only the tools they need (Retell Conversation Flow, Pipecat Flows, LiveKit's agent handoffs and tasks).

We are halfway there already: the server tracks the call stage from its own records (`app/rules.py` `STAGES`), owner guidance is sorted by stage, and the per-turn `[server]` notes act as stage instructions.

## The design

**Always loaded** (about 1,500 words, ASSUMED): the persona, the safety section (a gas smell can come at any moment), tool use, the owner's whole-call guidance, and the known facts. It never changes during a call, so it stays the cached prefix.

**One stage section, chosen by the server:**

| Stage | What the model is told | Tools | Moves on when (server's records) |
|---|---|---|---|
| Opening | Listen; find out what's wrong; tell which kind of caller this is, from a one-line summary of each of the 14 journeys | `find_address`, `check_service_area` | The problem is stated and the caller type is known |
| Details | That journey's full section; where, how bad, who's home, what the technician needs, who approves | as above, plus the open-job tools for a known caller | Address in area and priority decided |
| Booking | Offering times, the read-back, booking, moving, cancelling | `find_slots`, `read_back`, `book_appointment`, `reschedule_appointment`, `cancel_appointment` | A job booked or a callback set |
| Closing | What happens next, one "anything else?", the goodbye | `end_call`, callbacks | The caller is done |

**Side paths from any stage**, when the server sees the trigger: a demand for a person, a complaint, a commercial or out-of-scope decline, an account change (moved, identity check). The safety lines already work this way.

**Rules:**
- The server picks the stage from its records; the model never does.
- A caller can go back: "and my AC is broken too" during Closing returns the call to Details for a second job. This is the hard part.
- The core goes first and the stage section last, so the cached prefix holds. Each stage change writes a small new cache entry, about four a call, and may add 100 to 200 ms on that one turn (ASSUMED; measure it with `make measure-delay`).

## Effort, cost, risk

- Effort: two to three days, mostly splitting `journeys.md` and `spine.md` by stage and moving the stage logic the server already has into `assemble.py` (ASSUMED).
- Cost: one full two-sample standard run (about $4) plus targeted runs, about $8 in all (ASSUMED from today's runs).
- Risk: a stage the server gets wrong leaves the model without the instructions it needs; a caller who jumps around gets a prompt for the wrong stage. Both show up as new grader misses, which is why the run compares against today's reference.

## Recommendation

Build it after the live calls, behind a switch so both prompts can run side by side. Judge it on the two-sample standard run against the 92% reference, on the must-pass set, and on the grader misses we fought with prompt rules (re-asking, stacked questions, repeated read-backs). Keep it only if it cuts those without new must-pass failures and without a slower first word.

This changes if: the Retell Conversation Flow lane (it maps our design onto Retell's own stage graph) shows that a stage graph does better on the same evals, in which case its stages are a tested starting point.

## Sources

- Retell Conversation Flow, conversation node: https://docs.retellai.com/build/conversation-flow/conversation-node (read 2026-11-04)
- "How Many Instructions Can LLMs Follow at Once?" (2025), cited from memory, not re-read for this note
- Anthropic, "Building effective agents"; OpenAI, "A practical guide to building agents": guardrails around the model, checks at the tool
- Repo: `prompts/assemble.py`, `prompts/README.md`, `app/rules.py`, DECISIONS.md 060, the tool server log for the test call above
