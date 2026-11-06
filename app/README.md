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

The owner view is three pages for the owner and dispatcher: Today (`/owner`, the dispatch board, what needs a person, the jobs booked), Calls (`/owner/calls`, and each call at `/owner/calls/{id}` in plain words, with a technical details switch and the full record at `/owner/calls/{id}/log`), Results (`/owner/results?days=1|7|30`, or a custom range with `start` and `day`) and search (`/owner/search?q=` and, as you type, `/owner/search/suggest?q=`, over a full-text index of every call, job, flag, lead and line said, `app/search.py`). Everyone signs in with a named account (`app/accounts.py`): an admin changes settings, publishes rules (on the debug bench, `/bench/rules`) and manages users; staff open every page and send reports but change nothing else, and every route that changes something checks the role on the server. The first admin comes from `OWNER_ADMIN_USERNAME`, `OWNER_ADMIN_NAME` and `OWNER_ADMIN_PASSWORD` in the server's `.env`, created once at startup; more users are added on the Settings page (`/owner/settings`) or with `make owner-user ACCOUNT=dana NAME="Dana Ruiz" [ROLE=admin]`. Passwords are scrypt hashes from the standard library, and a session is a random token in an HttpOnly cookie that lasts 400 days, of which only the SHA-256 is stored. The report to Revin from a call page is filed as a GitHub issue by the jobs loop when `REPORTS_GITHUB_TOKEN` is set. The design system behind the pages is `docs/design-system.md`, shown live at `/owner/design`. `make owner-demo` seeds `data/owner-demo/` with a week of earlier bookings and a scripted day of calls (`scripts/seed_owner_day.py`), serves it as if it were 5:20 PM that day, and adds two development accounts: `owner` (admin, `dev-owner-password`) and `staff` (`dev-staff-password`). What it looks like: `docs/images/owner-view.png`.

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
| `GEOCODER` | empty | `geosearch` turns on the address lookup (`geocode.py`): NYC Planning's GeoSearch, then the Census geocoder, set per tenant in `config.yaml` `address_lookup`. Empty, the agent asks for every ZIP |
| `SUMMARIES` | empty | `1` writes the technician's summary after each booked call (`app/summary.py`); it needs `ANTHROPIC_API_KEY` too |
| `FAILURE_GRADER` | empty | `1` turns on the call checks (`app/failure_grader.py`): the daily run and the Check buttons on the owner view and the bench; it needs `ANTHROPIC_API_KEY`, and reads Retell transcripts with `RETELL_API_KEY` (the server's own turns otherwise) |
| `OWNER_ADMIN_USERNAME`, `OWNER_ADMIN_NAME`, `OWNER_ADMIN_PASSWORD` | empty, which creates no one | The owner view's first admin, created at startup if no user has that username; a password changed since is never reset. `OWNER_TOKEN` and `RULES_EDITORS`, from before accounts, are no longer read and are logged as such |
| `OWNER_VIEW_URL` | empty | Where the owner view is served, for the call link in alert emails |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM` | empty, `587` | The mail server for on-call alerts. With no host, alerts are held and shown on the owner view, not sent |
| `REPORTS_GITHUB_TOKEN`, `REPORTS_GITHUB_REPO` | empty | Where owner reports are filed as issues (`owner/repo`). Without them reports are held and shown on the owner view |
| `TRANSFER_OFFICE_NUMBER`, `TRANSFER_ON_CALL_NUMBER` | empty | The numbers a call is put through to, named by role in the tenant's `transfer` section. Empty keeps the flag and named callback (`transfer.py`), for the warm transfer and the cold handoff alike |
| `RECORDING_S3_BUCKET`, `RECORDING_S3_ENDPOINT`, `RECORDING_S3_REGION`, `RECORDING_S3_ACCESS_KEY`, `RECORDING_S3_SECRET` | empty | The S3-compatible bucket LiveKit uploads call audio to; the jobs loop copies each file here and deletes it there. No bucket, no recording (`recordings.py`) |
| `RECORDINGS_DIR` | `recordings` beside `TOOL_DB` | Where call audio is kept on the server |
| `FIXED_NOW` | empty | Development only: serve every request as if it were this moment (ISO with offset). `make owner-demo` sets it |
| `LANGSMITH_URL` | empty | The LangSmith page with the eval experiments, linked from the debug bench's eval pages |

## Modules

| File | Owns |
|---|---|
| `main.py` | Routes, the request wrapper (idempotency replay, refusals into the envelope, one log line per request), the background timer, `/healthz`, `/debug/*` |
| `bench.py` | The debug bench at `/bench`, admin only (`require_admin`): calls, a call as a node graph with its rule proof, the live stream, a call as a draft scenario, settings, evals and rounds |
| `bench_rules.py` | The Rules page on the bench (`/bench/rules`): the owner's rules, the fixed responses and the system prompt by section; save, publish through the eval gate, override with a reason, discard, stop a check, roll back |
| `nodes.py` | A call as a graph of steps in lanes, with the prompt and messages each model request was given rebuilt from the call record, and the rule proof |
| `bench_data.py` | What the bench reads besides calls: the tuning knobs with their documented measurements, model profiles, switches, eval runs, rounds, the scenario draft |
| `redact.py` | Redaction for everything the bench shows: phone numbers to their last four, a service location to its street name, keys and tokens replaced |
| `tools.py` | The model tools and every gate: read-back, safety, commercial, out of area, ownership, per-call limits, end-call |
| `lifecycle.py` | `/calls/start` (lookup before hello, repeat-caller linking, a dropped safety call carried over), `/calls/{id}/turn` (the safety check on every caller line), `/calls/{id}/end` (outcome, a dropped safety call flagged) |
| `safety.py` | The phrase check: clear signals, the vague-smell check question, negation, variants on a repeat |
| `priority.py` | Base priority by job type, rules that only raise it, the off-by-default side-model slot, the after-hours outcome |
| `geocode.py` | The address lookup behind `find_address`: GeoSearch and the Census geocoder, the exact house-and-street rule, started from the caller's line and cached |
| `address_as.py` | What the caller asked to be called, read from their words, refused when mocking or abusive, kept for the call and linked calls, and saved on the customer record as a note |
| `readback.py` | Whether a caller line changes a detail the read-back named (unit, house number, street, ZIP, phone), so a correction after the yes resets it (DECISIONS.md 083) |
| `asks.py` | Work the company doesn't do, declined once and kept as a lead, and a technician asked for by name, kept as a dispatcher note (DECISIONS.md 084, 085) |
| `lookup.py` | The richer lookup (membership, open jobs, last service, recent calls, notes, equipment) and history |
| `booking.py` | Sending a booking to the FSM, shared by the live path and the retry queue |
| `records.py` | The server's own tables: calls, scope, flags, callbacks (named time, owner, outbound rules, overdue), do-not-call |
| `jobs.py` | Retry of queued bookings and leads, earlier openings for flagged urgent bookings, queuing the callbacks the system places, booked-and-kept, on-call email after hours, cache refresh, the search index, collecting call recordings, overdue callbacks, retention, and the technician summary last |
| `summary.py` | The technician's summary after a booked call (spec S-16): the call record the model reads, the checks on its lines, and the pinned job note |
| `failure_grader.py` | The call checks (issue #178): what the grader model reads, the server's rule that a failure needs evidence found in the call, the cache, runs and the daily schedule |
| `checks_view.py` | The Checks pages on the owner view and the bench, Check this call, and the schedule setting |
| `transfer.py` | Warm transfer (C-11): whether a person flag goes to a person now and to whom, the summary they hear, and what happens when nobody takes it. Cold handoff: whether a caller's line demands a person, and who it goes to |
| `recordings.py` | Call audio (story O-6): the file name a call records under, copying finished files from the bucket, deletion with the transcript |
| `outbound.py` | Calls the system places (journey 12): what the voice layer claims, and settling each one from what happened on it |
| `owner.py` | Every number and list on the owner view, each defined in its docstring: the board, the status line, Results |
| `plain.py` | Each tool call on a call page said in plain words (story O-7) |
| `reports.py` | Reports to Revin from the owner view, and filing them as issues (story O-8) |
| `search.py` | The owner view's search: a SQLite full-text index of calls, jobs, flags, leads and lines said, refreshed by the jobs loop |
| `rules.py` | Owner edits (exploration 009): the editable lines and guidance, validation that keeps every gate out of reach, versions with their eval gate result, rollback, the version a call runs under, and the call stage the server decides |
| `rulebook.py` | Every rule the agent follows, by stage, built from the tenant files, the prompt files and the live version, for the guidance check |
| `fixed.py` | The fixed responses on the bench's Rules page: the rulebook's lines and server checks, one row per trigger, by topic |
| `schedule.py`, `schedule_view.py` | The schedule as ServiceTitan holds it, and the edits a person makes to it from the owner view or the bench, each through the adapter, logged and clearing the slot cache (issue #152) |
| `owner_view.py`, `templates/`, `static/owner/` | The owner view's tokens (read, and edit per person), routes, HTML and design system (docs/design-system.md) |
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
| An owner edit changes only the six editable lines and guidance for four stages; anything else in a draft (the read-back, priority, the safety script, any other line) is refused, a stored version is validated again before a call uses it, a new or changed note or line that contradicts a locked rule is refused with the reason (and a save the check can't run on is refused), and a draft goes live only when the eval gate passes on it or an admin overrides the gate with a reason, which the version and the log record | S-51, DECISIONS.md 055 | `rules.validate`, `guidance_check.check`, `rules.applied`, `rules.record_gate`, `rules.publish_now` |
| The call stage comes from the server's records, and booking and closing guidance is sent as a `[server]` note when the stage starts, never on a safety call | S-51 | `rules.stage_now`, `rules.advance_stage`, `lifecycle.turn` |
| A person flag goes to a person now only in office hours when the caller asked for one, or after hours when the server's own priority and cutoffs say the on-call technician is needed tonight (never the flag's priority); never on a safety or commercial call, never with no number set, once per call. The flag and callback are stored first; a transfer nobody takes falls back to them, and a connected one closes them | C-11 | `transfer.role_for`, `transfer.plan`, `transfer.finish`, `tools.needs_on_call_tonight`, `tools.flag_person` |
| A caller who demands a person (insistently, again, or angrily; a mention or a question about the agent isn't one) is handed off on their own turn: the office in hours, on-call after hours, on a safety call only after the fixed lines, never on a commercial call, once per call. The flag and callback are stored first, and an unset number or a failed transfer gives the callback line | C-11 | `transfer.demands_person`, `transfer.handoff_role`, `transfer.start_handoff`, `transfer.finish_handoff`, `tools.demand_handoff` |
| Recordings are kept only under the file name the server gave the call, only inside the recordings folder, and deleted with the transcript | S-45, story O-6 | `recordings.started`, `recordings.purge`, `jobs.purge` |
| Do-not-call marked on the caller's words, cancelling pending callbacks; the tool returns the line to say | S-49 | `lifecycle.turn`, `records.mark_do_not_call` |

## Template and tenant

Nothing about Summit Air is in `app/`; a test checks for the company name, its ZIPs, numbers, job types and rule ids. A tenant folder holds `config.yaml` (shared with the mock: hours, area, job types, plans, refused customer types), `agent.yaml` (dialed numbers, FSM ids, safety lines, temperature brackets, after-hours cutoffs, callback owners and timings, repeat-caller window, retention, every line the server returns as `say`) and `knowledge/*.md`, which `/calls/start` returns for the prompts. `agent.yaml` starts with `extends: hvac`, so it is laid over `trades/hvac/agent.yaml`, which holds what every HVAC company shares: the safety signal phrases and the priority rules (`app/tenant.py`, `merge`; docs/template.md). `python -m app.tenant tenants/<slug>` prints the merged result. The only text in `app/` the agent may say is a handful of neutral fallbacks ("Sorry, give me just a moment.").

## Owner view: redesign in progress

Jackson is redesigning the owner view in a separate session. The first version, Claude's first attempt, read as a cluttered, generic AI dashboard with a back-end debugging-log feel, and didn't give the owner the right level of detail. The numbers behind it (`app/owner.py`) stay and can keep growing; expect the pages and templates (`app/owner_view.py`, `app/templates/`) to change.

## Owner settings

`/owner/settings` holds a fixed list of on/off settings (`app/owner_settings.py`), changed by an admin and seen by staff. Each change is a new row in `setting_changes`, with who and when, shown under Changes on the page with the user changes. `/calls/start` pins the values to the call (`call_settings`), so a change mid-call never flips a call halfway. None loosens a gate; turning one off only drops something extra.

| Setting | Default | Read in |
|---|---|---|
| Two-party consent | off (New York is a one-party state) | `lifecycle.start` sends `recording_notice` (the tenant's `voice.recording_notice`), said before the greeting by both voice stacks; `lifecycle.turn` reads an objection (`recordings.objected`, with the setting on or off), records it in `recording_declines` and returns `recording: {action: stop, text}` with the tenant's `voice.recording_stopped`, and the LiveKit worker stops the recorder and says the line before the agent answers; `recordings.collect` deletes that call's file from the bucket instead of keeping it. A call that opens with the safety lines isn't recorded |
| Call recordings | the tenant's `retention.record_calls` | `recordings.wanted`, which needs both the tenant's `record_calls` and the switch, so the switch can only turn recording off |
| Warm transfer | on where the tenant has a `transfer` section | `transfer.role_for` |
| Technician summaries | on | `summary.run` marks the call skipped |
| On-call alerts | on | `jobs.alert_on_call`; safety flags are emailed either way |

## Owner edits

`/calls/start` pins the live version to the call (`call_stages`) and returns it as `house_rules`: the version number, the guidance by stage and the spoken lines it overrides (greeting, goodbye). `prompts/assemble.py` adds the guidance to the prompt and lays the lines over the tenant's `voice:` lines; the server's own lines (anything else, the callback opening) come from the call's version through `rules.tenant_for_call`. Publishing marks a draft `checking`; `evals/gate.py` (`make rules-gate`, or started by the server when `RULES_GATE=1`) runs the must-pass scenarios on it and writes the result back. The only rules page is on the debug bench (`/bench/rules`, `app/bench_rules.py`); `/owner/rules` redirects there (DECISIONS.md 091). Only an admin can save, publish, override, discard, stop a check or roll back; a version carries the admin's name. An override (`rules.publish_now`) publishes the draft at once without the gate, after a reason; the version's gate record says who, when and why, and the server log gets a `rules.override` line. A result never publishes over a rollback made while it ran, a check can be stopped from the editor, and one that hasn't reported back in an hour (`rules.CHECK_TIMEOUT_MINUTES`) returns to draft. One gate process runs at a time per database (a lock file beside it). What contains an edit: docs/template.md, "Owner edits".

## Schedule edits

The owner view's Schedule tab (`/owner/schedule`) and the bench's (`/bench/schedule`) show a day as ServiceTitan holds it: a row per technician on shift, a column per arrival window, the jobs and blocks in each cell, and how many technicians the agent would offer in each window right now (read with the same `tools.open_windows` find_slots uses). A free cell opens a popover to block it, put an event on it ("Team meeting") or add a job for a customer found by phone; a column's **Block all** blocks every free technician in it; a block opens a popover to take it off. A job opens `/owner/jobs/{id}`, where its window, type, notes and service location address change, and its customer's name and phone number (admins only). The call page's Job booked panel links there. The bench adds the list of what changed in ServiceTitan (edits from either surface and the agent's bookings, read again every 3 seconds) and a customer found by phone.

`app/schedule.py` makes every change through the FSM adapter, never the mock's database, and clears the slot cache after each one, so the next find_slots sees it at once rather than after `SLOT_CACHE_SECONDS`; the call start and find_customer read ServiceTitan live, so a new phone number is recognized on the next call. Every edit, taken or refused, is a row in `schedule_edits` with who made it, their role, the surface and what ServiceTitan said. Staff may block, add and change jobs; a customer's name or number needs an admin, checked in the route and again in `schedule.py`. Nothing deletes or bulk-changes: a block is taken off the schedule (`active: false`), and Block all is one block per technician free in that one window. A block on a technician with a visit in that window is refused by name, and Block all says how many visits stay; only blocks made here (their summary says so) can be taken off from here. Every edit is checked whole before anything is sent, so a refused edit changes nothing. A new phone number already on another customer needs **Use anyway**, and a number on several customers makes the person pick one before a job is added. Only `schedule.py` calls the adapter's edit methods (a test pins it), so no tool can make these changes.

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
- Accounts span every tenant on the server: a user can open any tenant's owner view (`?tenant=<slug>`). Settings are per tenant. A second real tenant needs users scoped to it.
- Under two-party consent the Retell stack says the notice, but Retell keeps its own recording, which an objection doesn't stop; the server's recordings are LiveKit's only.
- No rate limit on sign-in (scrypt makes each guess cost about 22 ms, MEASURED on a laptop by timing ten checks); put one in the proxy before the owner view faces the open internet.
- Flags with no call (a lead the FSM refused) belong to no tenant and don't show on the owner view.
- The technician summary's checks drop a line with nothing in the record behind it and a line with a number the record doesn't hold. A wrong statement made from real facts would pass them, which is why the full intake stays in the job summary underneath the pinned note.
- Against real ServiceTitan the adapter still needs the changes listed in `servicetitan_mock/README.md` (our own idempotency keys, a capacity recheck before creating a job).
