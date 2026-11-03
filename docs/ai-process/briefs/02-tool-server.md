# Build brief: tool server (the integration layer)

Branch: `build/tool-server`. Pull request says `Closes #N` for this lane's issue. Runs first: the other lanes code against docs/tools.md and can start in parallel with a stub, but merge after this one.

## Goal

The server every call talks to: it owns every fact and every rule. The model asks; the server decides. It talks to the mock ServiceTitan through one adapter, so the real ServiceTitan later changes the adapter's base URL and auth, nothing else.

Read first: CLAUDE.md, docs/spec.md (every S line tagged Unit), docs/tools.md (the contract), docs/architecture.md (least privilege, logging), docs/call-journeys.md, DECISIONS.md, servicetitan_mock/README.md, docs/template.md.

## Scope

Python 3.12, FastAPI, SQLite, package `app/`, run with `make server`.

- The endpoints and envelope in docs/tools.md, exactly.
- `app/fsm/`: the adapter interface and a ServiceTitan adapter pointed at the mock (`make mock-st`). Slot cache refreshed after every booking and on a timer.
- Mock ServiceTitan additions, done first in this lane:
  - Customer notes, modeled on ServiceTitan's customer notes: read with the customer, append-only from the adapter.
  - The seed keeps at least 200 residential customers around the three counties, and grows the edge cases from one each to a few each: about 5 commercial accounts (a property manager, a restaurant, a school, an office, a co-op board) and about 5 residents with out-of-area ZIPs (Nassau, Westchester, New Jersey). Quantity over polish; it should look like a real company's records.
  - A handful of seeded customer notes (a tone preference, a gate code, "tenant, landlord approves work") so the eval scenarios can use them.
- Gates in server code: read-back (the question must have been returned on this call before `confirmed` counts), safety (lines said and person flagged before booking; caller-insisted override logged), commercial, out of area, ownership of bookings, per-call limits, end-call on safety calls.
- Safety check on every caller turn (`/calls/{id}/turn`): the fixed list of clear signals, the "vague smell" check question, "gas" alone and gas bill or furnace ignored; the safety lines and their shorter variants come from tenant config, spoken word for word.
- Priority engine: rules from tenant config that can only raise priority; slot for a side model, off by default (DECISIONS.md 006).
- Repeat callers: calls linked by number; resume context on a recent drop; repeated drops offer a callback.
- Tool-failure queue: a booking that fails because the FSM is down is stored, retried idempotently, and flagged if it still fails (S-48).
- Do-not-call list; leads (commercial, membership, install); callbacks with a named time and an owner.
- The richer lookup (membership, open jobs, last service date, recent calls, notes) in `/calls/start` and `find_customer`; `get_customer_history` and `add_note` scoped to the caller's own records (docs/tools.md, S-15a).
- Logging: one JSON line per request and per turn with call id, request id, tool, arguments, result, gate, stage timings; `GET /debug/calls/{id}`, `GET /debug/logs`, `make trace CALL=<id>`.
- Tenant loading from `tenants/<company>/`, including the knowledge folder; no Summit Air fact in `app/`.
- A Postman collection under `postman/` for every endpoint, with assertions and a Triage folder for the debug endpoints; `make postman`.

## Must be right, each with a test

Every S line in docs/spec.md tagged Unit that belongs to the server, including the 10 must-pass lines: S-05, S-17, S-18, S-19, S-24, S-26, S-27 (8-way race test), S-28, S-39 (injection and "delete everything" change nothing). Plus S-15 (caller ID and "ending in 4024"), S-15a (lookup, history, append-only notes, notes never bypass a gate), S-31, S-32, S-38, S-42, S-43, S-45, S-46, S-48, S-49.

## Done when

`make check` passes with the new tests and the ratchet updated; `make server` and `make postman` pass against the mock; the reviewer agent has checked the branch against this brief and docs/spec.md.
