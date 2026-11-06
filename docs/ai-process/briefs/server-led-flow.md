# Build brief: server-led booking flow and a short prompt

Branch: `joco/server-led-flow-979e54`, from `joco/stripped` (PR #164). Issue #171. Arm D of the agent side-by-side.

## Goal

Haiku on Jackson's Retell agent keeps breaking the prompt's "never" lines: it says the caller's name, narrates its steps, asks two questions at once, rewords required lines and runs steps out of order. The prompt (`retell/summit-air-jackson/prompt.md` on `joco/retell-agent`, 2,254 words, MEASURED with `wc -w`) holds most of the call flow. This lane moves the flow into the tool server, so the model mostly talks: each tool result names the next step, required lines come from the server as `say`, and the order of the booking steps is enforced in code. Rules that must happen stay in server code (CLAUDE.md); a flag the model can set is not a gate.

Read first: CLAUDE.md, docs/assignment.md, DECISIONS.md, `app/envelope.py`, `app/tools.py` (`find_slots`, `read_back`, `book_appointment`, `end_call`), `voice/retell/bridge.py` (`Bridge.tool`), and `evals/rounds/retell-live-calls-01.md` on `joco/retell-agent` (Jackson's feedback, the gross errors).

## Scope

- **Switch.** One setting, `SERVER_LED=1`, turns everything below on for an instance. Off, the server answers exactly as today, so the live services and the other arms are unchanged.
- **`next` on every result.** The envelope gains an optional `next`: the next step in plain words, with the exact question to ask where there is one. Refusals get one too. It is left out of the body when empty.
- **Order gates**, each from the server's own records, never from an argument the model sets:
  - `find_slots` refuses before a confirmed `read_back` (`calls.readback_confirmed_at`), except when moving a visit already booked.
  - `book_appointment` refuses until the three visit questions (approval, someone home, gate code or pets) were each said in an agent turn after the opening was offered and a caller line followed. The refusal's `say` is the first missing question, word for word.
  - `end_call` refuses while the latest `say` of another tool on the call was never said in an agent turn after that result. The refusal's `say` is that sentence. Once per sentence, so a transcript that never matches can't trap the call.
- **The open-door line** ("If anything makes this more urgent for you, just let me know.") comes in `find_slots`' first offer on a `no_heat` or `no_cooling` call, once a call.
- **The arm's prompt and tools** in `retell/arms/server-led/`: `prompt.md` at about a third of Jackson's (who Brooke is, how she talks, "say each result's `say` as written, then do its `next`", the few real rules), `tools.json` with the URLs of the test instance.
- **A test instance** on the summit-air box, `deploy/server-led/`: its own folder (`~/summit-air/server-led`), systemd units, ports 8130 (tools), 8131 (mock), 8132 (bridge), and a Caddy site file for `/server-led/*`. Its own Retell LLM and agent, "Summit Air server-led (test)".

## Out of scope

- The live services (`deploy/summit-air`, `deploy/retell`, the `summit-retell@*` units), the live world reset, and Jackson's agent and its LLM. Never touched.
- Calls to (646) 624-0418. A phone number of its own only under $5 and with the coordinator's OK.
- The safety path: the bridge already says the gas and carbon monoxide lines from the transcript and through `speak_first`. The prompt only says never to add safety advice.

## Done means

- The arm's agent passes the gross-error checks below on the test instance, measured on Retell simulation chats and at least one web call.
- `make check` passes; every behavior change has a test; a PR that says `Closes #171`.
- A short note on what would still raise the arm's ceiling, and the coordinator has the agent id, the instance URL and the prompt word count.

## Gross-error checks (from Jackson's calls, retell-live-calls-01.md)

1. Never says the caller's name.
2. Never narrates its steps or the call ("let me book that", "I'll move on", "the caller").
3. Never echoes back or asks "is that right?" outside the read-back.
4. One question per turn.
5. Required lines word for word: the read-back, the open-door line, the priority lines, the booking confirmation, the safety lines (once).
6. Steps in order: read-back confirmed before an opening, the three visit questions before booking, nothing left unsaid before `end_call`.
7. Never mentions notes, tools, reference text or what a lookup found.
8. Ends the call after the caller's last no, once.

## Spend

Retell under $10 for the lane, logged in the PR and the hand-over note. Simulation chats cost about $0.105 a case (MEASURED on PR #167).
