# Tool server

The integration layer every call talks to: it owns every fact and every rule. The model asks; the server decides. The voice layer (our LiveKit worker or the Retell baseline) calls the lifecycle endpoints and passes the model's tool calls through; the contract is `docs/tools.md`. The server reaches the company's field service system only through `app/fsm/`, pointed at the mock ServiceTitan today.

Python 3.12, FastAPI, SQLite. Brief: `docs/ai-process/briefs/02-tool-server.md`.

## Run

```
make mock-st   # the mock ServiceTitan on :8101
make server    # the tool server on :8100
make postman   # seed a throwaway world, start both on spare ports, run postman/tool-server.postman_collection.json
make trace CALL=<call id>   # a call's timeline in plain text, read straight from the database
make owner-demo             # seed a day of calls and serve the owner view on :8100
```

The owner view is three pages for the owner and dispatcher: Today (`/owner`, the dispatch board, what needs a person, the jobs booked), Calls (`/owner/calls`, and each call at `/owner/calls/{id}` in plain words, with each step's detail behind "Details" and the full record at `/owner/calls/{id}/log`), Results (`/owner/results?days=1|7|30`) and search (`/owner/search?q=`, calls from the last 30 days by name, number, address, ZIP or job). Open `/owner?token=<OWNER_TOKEN>` once; a cookie keeps it. The one write is a report to Revin from a call page, filed as a GitHub issue by the jobs loop when `REPORTS_GITHUB_TOKEN` is set. The design system behind the pages is `docs/design-system.md`, shown live at `/owner/design`. `make owner-demo` seeds `data/owner-demo/` with a week of earlier bookings and a scripted day of calls (`scripts/seed_owner_day.py`), serves it as if it were 5:20 PM that day, and uses the token `dev-owner-token`. What it looks like: `docs/images/owner-view.png`.

`GET /debug/calls/{id}` and `GET /debug/logs` (filter by `call_id`, `request_id`, `tool`, `errors=true`) need the `X-Debug-Token` header; `make server` sets `dev-debug-token` unless `DEBUG_TOKEN` is set.

| Variable | Default | Meaning |
|---|---|---|
| `TOOL_DB` | `data/tool_server.db` | The server's own SQLite file; the JSON log sits next to it (`.log`) |
| `TENANTS_DIR` | `tenants` | One folder per company |
| `DEFAULT_TENANT` | `summit-air` | Used when the dialed number matches no tenant |
| `FSM_BASE_URL` | the tenant's `fsm.base_url` | Where the FSM is |
| `FSM_CLIENT_ID`, `FSM_CLIENT_SECRET`, `FSM_APP_KEY`, `FSM_TOKEN_URL` | the mock's values in `config.yaml` | FSM credentials; real ones come from the secret store |
| `DEBUG_TOKEN` | empty, which refuses every debug request | Token for `/debug/*` |
| `APP_ENV` | `development` | Anything else turns on the retention policy |
| `JOBS_INTERVAL_SECONDS` | `60` | Background jobs (`app/jobs.py`); `0` turns them off |
| `OWNER_TOKEN` | empty, which refuses every owner view request | Token for `/owner` |
| `OWNER_VIEW_URL` | empty | Where the owner view is served, for the call link in alert emails |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM` | empty, `587` | The mail server for on-call alerts. With no host, alerts are held and shown on the owner view, not sent |
| `REPORTS_GITHUB_TOKEN`, `REPORTS_GITHUB_REPO` | empty | Where owner reports are filed as issues (`owner/repo`). Without them reports are held and shown on the owner view |
| `FIXED_NOW` | empty | Development only: serve every request as if it were this moment (ISO with offset). `make owner-demo` sets it |

## Modules

| File | Owns |
|---|---|
| `main.py` | Routes, the request wrapper (idempotency replay, refusals into the envelope, one log line per request), the background timer, `/healthz`, `/debug/*` |
| `tools.py` | The model tools and every gate: read-back, safety, commercial, out of area, ownership, per-call limits, end-call |
| `lifecycle.py` | `/calls/start` (lookup before hello, repeat-caller linking, a dropped safety call carried over), `/calls/{id}/turn` (the safety check on every caller line), `/calls/{id}/end` (outcome, a dropped safety call flagged) |
| `safety.py` | The phrase check: clear signals, the vague-smell check question, negation, variants on a repeat |
| `priority.py` | Base priority by job type, rules that only raise it, the off-by-default side-model slot, the after-hours outcome |
| `lookup.py` | The richer lookup (membership, open jobs, last service, recent calls, notes, equipment) and history |
| `booking.py` | Sending a booking to the FSM, shared by the live path and the retry queue |
| `records.py` | The server's own tables: calls, scope, flags, callbacks (named time, owner, outbound rules, overdue), do-not-call |
| `jobs.py` | Retry of queued bookings and leads, earlier openings for flagged urgent bookings, queuing the callbacks the system places, booked-and-kept, on-call email after hours, cache refresh, overdue callbacks, retention |
| `outbound.py` | Calls the system places (journey 12): what the voice layer claims, and settling each one from what happened on it |
| `owner.py` | Every number and list on the owner view, each defined in its docstring: the board, the status line, Results |
| `plain.py` | Each tool call on a call page said in plain words (story O-7) |
| `reports.py` | Reports to Revin from the owner view, and filing them as issues (story O-8) |
| `owner_view.py`, `templates/`, `static/owner/` | The owner view's token, routes, HTML and design system (docs/design-system.md) |
| `mail.py` | Sending alert email over SMTP, or holding it when no server is set |
| `tenant.py` | Loading `tenants/<company>/` and picking one by dialed number |
| `fsm/` | The adapter interface, the ServiceTitan adapter and the slot cache |
| `trace.py` | The call timeline behind `/debug/calls/{id}` and `make trace` |

## Where each rule lives

| Rule | Spec | Code |
|---|---|---|
| No booking for a commercial customer, whatever the agent asks: a caller line that describes a business by the patterns in `agent.yaml` (`lifecycle.sounds_commercial`, best effort, kept narrow because a false positive costs a booking), a commercial lead on the call, a caller ID that matches only commercial accounts, or the customer the address resolves to | S-05 | `lifecycle.turn`, `tools._commercial`, `booking.send` |
| Safety lines word for word before anything else; a person flagged at once; a shorter variant on a repeat, never the same one twice in a row | S-17 | `safety.check_caller_turn`, `lifecycle.turn` |
| Booking tools refused until the lines were said (an agent turn containing them) and the flag raised; after that a booking is an override, logged as `gate=safety_override` | S-18 | `tools._safety_gate` |
| End call refused on a safety call until the lines were said; then the sign-off, and no spam or wrong-number label | S-19 | `tools.end_call` |
| "Gas" alone, a gas bill or a gas furnace triggers nothing; a vague smell gets one check question | S-20 | `safety`, phrases in `agent.yaml` |
| Priority only raised, by rules over facts and every caller line; the agent's priority is logged, not used | S-21 | `priority.decide` |
| Urgent with no window today: book tomorrow's first and flag for an earlier slot | S-25 | `tools.find_slots`, `tools.book_appointment` |
| Read-back: confirmed only after the server returned the sentence on this call and a caller line followed | S-26 | `tools.read_back` |
| Idempotent booking; the FSM's one-appointment-per-technician-window rule decides a race | S-27 | `main.run`, `booking.send`, the mock's `ux_appointments_slot` |
| Out-of-area refused and recorded with its ZIP, no flag | S-28 | `tools._out_of_area` |
| Reschedule and cancel only the caller's own booking, once each per call: booked on this thread, matched by the number the call came from, or at the address confirmed in the read-back; a number the caller only states is not enough | S-31 | `tools._owned_job`, `tools._limit` |
| Every callback names a time and an owner; overdue ones are flagged | S-32 | `records.parse_callback_time`, `records.mark_overdue` |
| Linked calls within minutes; repeated drops get a callback offer | S-38 | `lifecycle.start` |
| Reads and writes scoped to customers matched on the call; no delete, bulk or free-query call in the adapter | S-39 | `records.scoped_customers`, `fsm/base.py` |
| Outbound only to the caller, 8 a.m. to 9 p.m., one attempt, never do-not-call | S-42 | `records.outbound_allowed` |
| A booking the FSM couldn't take is queued, retried with the same keys, and flagged if it still fails; a booking still being sent (`sending`) is left to its request | S-48 | `tools.book_appointment`, `jobs.retry_queue` |
| An urgent booking flagged for an earlier slot is offered the first opening before its window, one booking per opening, one offer call each, never an after-hours window | S-25, journey 12 | `jobs.offer_earlier_slots`, `outbound` |
| A callback the caller asked for is dialed by the system at its time if allowed, otherwise handed to a person; a failed dial, a voicemail or an answer that did not confirm the person and handle the reason hands it to a person | S-32, S-42, journey 12 | `jobs.schedule_callbacks`, `outbound.claim`, `outbound.finish`, `outbound.callback_outcome` |
| A callback is done only when ServiceTitan's call log shows a call to that number after the promise, tagged resolved. The server tags the callbacks it places (resolved only if a person answered, the right person was confirmed, and the reason was handled); the office tags the ones it makes | S-32, C-45, decision 010 | `jobs.confirm_callbacks`, `outbound.settle_callback`, `outbound.apply_tag`, `outbound.retry_tags` |
| Urgent holds and the urgent-only night window are the company's rules, applied to what ServiceTitan's capacity returns, again right before a job is created or moved | S-25, decision 003 | `capacity.usable_technicians`, `capacity.pick_technician`, `booking.send` |
| A booking counts as kept if its job was still on the schedule when its current window came | C-42 | `jobs.check_kept` |
| An urgent flag raised while the company is closed is emailed to the on-call person, once, with what, where, who and why | Journey 11 | `jobs.alert_on_call` |
| Do-not-call marked on the caller's words, cancelling pending callbacks; the tool returns the line to say | S-49 | `lifecycle.turn`, `records.mark_do_not_call` |

## Template and tenant

Nothing about Summit Air is in `app/`; a test checks for the company name, its ZIPs, numbers, job types and rule ids. A tenant folder holds `config.yaml` (shared with the mock: hours, area, job types, plans, refused customer types), `agent.yaml` (dialed numbers, FSM ids, safety lines and phrases, priority rules, after-hours cutoffs, callback owners and timings, repeat-caller window, retention, every line the server returns as `say`) and `knowledge/*.md`, which `/calls/start` returns for the prompts. The only text in `app/` the agent may say is a handful of neutral fallbacks ("Sorry, give me just a moment.").

## Owner view: redesign in progress

Jackson is redesigning the owner view in a separate session. The first version, Claude's first attempt, read as a cluttered, generic AI dashboard with a back-end debugging-log feel, and didn't give the owner the right level of detail. The numbers behind it (`app/owner.py`) stay and can keep growing; expect the pages and templates (`app/owner_view.py`, `app/templates/`) to change.

## Known limits

- The outside temperature is one point, NYC's Central Park station and its forecast grid, which can differ from Staten Island or Nassau by a few degrees; the 55 F and 80 F brackets absorb that (exploration 007). It is read only on a server with `WEATHER=1`, hourly at most, and is unknown (so priority fails safe to High) after three hours without a good read.

- One server process: a per-call lock serializes a call's requests so a retried booking can't race its first attempt. A second process would need that claim in the database; out of scope at Summit Air's size (docs/scaling.md).
- The JSON log file is never rotated or purged here; retention covers the database. Production log rotation belongs to the deploy.
- FSM credentials from the environment apply to every tenant; a second real tenant needs per-tenant secrets.
- `find_customer` by a number or address the caller states returns that customer's lookup, notes included, as the contract says. Changing their visit needs the read-back of its address, but reading the notes doesn't; worth an eval case.
- `check_service_area` records whatever ZIP the agent passes as out of area, so a ZIP the caller only asked about counts in the owner view's demand by ZIP.
- `end_call(reason="caller_goodbye")` on a safety call hangs up on the agent's word; it is only reachable after the lines were said and a person flagged.
- The owner view is read-only apart from reports to Revin. A callback becomes done from ServiceTitan's call log (decision #26, `docs/decisions/26-callback-done.md`): the office tags its calls there, and the callbacks job reads them. If the office calls back from a phone ServiceTitan does not log, the callback stays open. Flags have no close action, except those the jobs settle themselves; a callback marked done closes the flags it left on its call. A callback opens by asking for the customer on that number by first name (decision 013), so a yes confirms the person; with no single name on the number, it is confirmed only by the read-back or a later name check.
- The job that offers earlier openings checks for an existing offer outside its write, which is safe with one server process (the timer runs one job at a time) but not with two.
- The booked-and-kept check reads at most 50 bookings per run, oldest window first; bookings the FSM keeps refusing to read, or moved later, are read again each run and could crowd out newer ones in a large backlog.
- One `OWNER_TOKEN` opens every tenant's owner view (`?tenant=<slug>`); a second real tenant needs its own token.
- Flags with no call (a lead the FSM refused) belong to no tenant and don't show on the owner view.
- `/calls/end` marks the technician summary as pending (spec S-16); the summary job itself is not built yet.
- Against real ServiceTitan the adapter still needs the changes listed in `servicetitan_mock/README.md` (our own idempotency keys, a capacity recheck before creating a job).
