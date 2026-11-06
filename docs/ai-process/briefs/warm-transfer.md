# Build brief: warm transfer on Retell

Branch: `joco/warm-transfer-retell`, from `origin/joco/stripped`. Issue #184 (label `agent`). The pull request says `Closes #184`.

## Goal

When a caller asks for a real person, or has a complaint or a billing question, during office hours, the Retell agent hands the call to the office properly instead of offering a callback. Summit Air has no office line yet, so the other end is a placeholder that says "Thanks for holding. Please wait for the next available team member." and then holds. Paul, who plays the owner in the interview demo, can call and hear the whole path work.

Read first: CLAUDE.md, docs/assignment.md, app/transfer.py (the LiveKit-era warm transfer and the cold handoff, whose rules this reuses), voice/retell/bridge.py, docs/retell-knobs.md, and Retell's Transfer Call page (https://docs.retellai.com/build/single-multi-prompt/transfer-call.md).

## Scope

1. **Server tool `request_transfer`** in app/tools.py, with tests. The server decides whether the call may go to the office now:
   - inside the company's hours (tenant `company.hours`, 8 AM to 8 PM), the owner's transfer switch on, and an office number set in the server's environment;
   - the reason is a request for a person, a complaint, or billing; a request for a person must be in the caller's own words on this call (`transfer.asks_for_person`), not only the model's say-so;
   - never on an open gas or carbon monoxide call or a commercial one, and at most one transfer per call.
   Refused, it returns a `say` and a `need`; after hours the `need` points at the existing `flag_person` callback. Allowed, it stores a flag of kind `transfer`, a fallback callback and a `transfers` row, so the call shows on the owner view, and returns the number and the line the agent says before handing off.
2. **Retell side.** Retell's `transfer_call` tool, warm transfer, dialing `{{transfer_number}}`. The number is a dynamic variable that only the bridge sets, through Update Live Call, after `request_transfer` allows the transfer: a `transfer_call` the model makes without the server's yes has no number to dial and fails. A script adds the tool to an agent by API (versioned endpoints only), for the coordinator to apply to Jackson's agent. The bridge reports Retell's `transfer_bridged` and `transfer_cancelled` webhooks back to the server.
3. **Placeholder destination.** The simplest reliable answer: a static TwiML answer on a Twilio number (hold line, hold music, a short "Thanks for holding" about every 40 seconds), if the coordinator approves the number. Never +16787376865.
4. **Prompt guidance** for the coordinator to apply, in the pull request, not in the live prompt.
5. **Test.** Unit tests for the rules and the bridge; one real phone call to a copy of the agent, not Jackson's, proving the handoff reaches the placeholder and the hold line plays. Retell spend under $3.

## Out of scope

Changing or deploying Jackson's live agent, prompt or services (the coordinator does), a real office line, and the cold handoff on Retell (`demand_handoff`, which the Retell bridge still ignores).
