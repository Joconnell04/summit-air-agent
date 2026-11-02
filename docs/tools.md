# Tool contract

The interface between the voice layer (LiveKit worker or the Retell baseline) and the tool server. Every build lane codes against this page, so the lanes can run in parallel. The model asks; the server decides (docs/architecture.md).

## Envelope

Every endpoint returns HTTP 200 with:

```json
{"ok": true, "say": "One sentence the agent can say", "data": {}, "refused": null}
```

- `ok: false` with `refused` set (`gate_read_back`, `gate_safety`, `out_of_area`, `customer_type`, `limit`, `not_owner`, `fsm_unavailable`, ...) when the server says no. `say` then tells the agent what to say instead. A non-2xx would make voice platforms retry and leave dead air.
- Every request carries `call_id`. Writes also carry an `Idempotency-Key` (the voice layer derives it from call id, tool and arguments), so a retried call returns the first result.
- Every request and result is logged with call id, request id, tool, arguments, result, the gate that fired, and timings (docs/architecture.md, logging).

## Call lifecycle (called by the voice layer, not by the model)

| Endpoint | When | Returns |
|---|---|---|
| `POST /calls/start` | Call connects; `caller_id`, `dialed_number`, `direction` | Tenant, greeting context: matched customers, locations, membership, open jobs, calls in the last few minutes, after-hours flag |
| `POST /calls/{id}/turn` | Every final caller transcript and every agent line | Safety action if any (`speak_safety_lines` with the exact text or a variant), journey switch hints, and the turn is logged with timings |
| `POST /calls/{id}/end` | Call ends | Ok; triggers the after-call summary and callbacks |
| `GET /healthz` | Watchdog | Tool server, database, FSM reachability, worker heartbeat |

## Tools the model can call

| Tool | Arguments | Server checks |
|---|---|---|
| `find_customer` | phone or address | Returns only records matching what the caller gave; never a list of other customers |
| `check_service_area` | zip | Out-of-area recorded with its ZIP |
| `find_slots` | job type, priority, preferred days or time of day | Returns the shape of availability and exact windows for one day at a time; urgent windows only for urgent jobs |
| `read_back` | address, phone | First call returns the exact read-back sentence and records that it was asked; `confirmed: true` is accepted only after that sentence was returned on this call and a caller turn followed |
| `book_appointment` | slot, job details (S-13 fields) | Read-back done, safety lines said if a signal fired, residential, in area, one booking per call; idempotent; queued and retried if the FSM is down |
| `reschedule_appointment` / `cancel_appointment` | booking id, reason | Booking belongs to this caller; one of each per call |
| `flag_person` | reason, priority, callback time | Returns the sentence the agent may say only after the flag is stored |
| `record_lead` | kind (commercial, membership, install), details | No extra lookups; commercial never becomes a booking |
| `mark_do_not_call` | none (uses the caller ID) | Cancels pending callbacks to that number |
| `end_call` | reason | Refused on a safety call until the lines were said and a person flagged |

There is no tool that deletes, bulk-changes, queries freely, or places a call to an outside number.
