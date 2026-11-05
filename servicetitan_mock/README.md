# ServiceTitan mock

A stand-in for the systems a 40-technician residential HVAC company (Summit Air, the first tenant) runs on, shaped like ServiceTitan's public API: the paths, field names, enums and error body come from ServiceTitan's own OpenAPI documents (`docs/explorations/006-servicetitan-field-names.md`). The agent's integration layer talks to this today. Moving to real ServiceTitan later changes the adapter's base URL, token URL and credentials, not the agent (C-03). This is the customer's system, not ours: it holds no agent logic and none of Summit Air's own rules (urgent holds, the night window, which customers it books and which ZIPs it serves). Those live in the tool server and the tenant config.

What the mock does that the real API does not is listed in one place, [Not in the real API](#not-in-the-real-api), with what the adapter does about each.

Python 3.12, FastAPI, SQLite. Brief: `docs/ai-process/briefs/01-mock-servicetitan.md`.

## Run

```
make seed-st      # rebuild the world in data/servicetitan_mock.db (start date = today)
                  # (python -m servicetitan_mock.seed --if-stale rebuilds only when the file is missing or older than schema.sql, which the deploy script uses)
make mock-st      # serve it on http://127.0.0.1:8101 (ST_PORT=... to change)
make postman-st   # seed a throwaway world, start the mock on :8102, run the Postman collection with Newman
```

Sign in the way ServiceTitan does, then call any endpoint with both headers:

```
curl -s -d grant_type=client_credentials -d client_id=cid.summit-air-mock \
  -d client_secret=cs.summit-air-mock http://127.0.0.1:8101/connect/token
curl -s -H "Authorization: Bearer <token>" -H "ST-App-Key: ak1.summit-air-mock" \
  "http://127.0.0.1:8101/crm/v2/tenant/1000/customers?phone=(718)%20555-0142"
```

The credentials above are mock values from the tenant config, not secrets. Interactive docs are at `/docs`.

Settings, all optional:

| Variable | Default | Meaning |
|---|---|---|
| `ST_MOCK_DB` | `data/servicetitan_mock.db` | SQLite file |
| `ST_MOCK_TENANT` | `tenants/summit-air` | Folder holding `config.yaml` and `seed.py` |
| `ST_MOCK_NOW` | real time | Freeze the clock (ISO time with offset) so evals see the same "now" |
| `ST_MOCK_ALLOW_RESET` | off | Set to `1` to enable `POST /debug/reset`, which rebuilds the seeded world in place (start date = today on the mock's clock). Only the Retell eval instance sets it (`deploy/retell/run.sh`); without it the endpoint answers 403 `reset_disabled` |
| `ST_MOCK_RESET_TOKEN` | none | If set, `POST /debug/reset` needs the same value in `X-Debug-Token` |

The seed takes `--start-date YYYY-MM-DD` and a fixed random seed, so one start date always gives one world. Pair it with `ST_MOCK_NOW` for repeatable evals.

## What is in the world

300 customers (owners, tenants, apartment owners), 40 technicians (36 service, 4 comfort advisors), two weeks of shifts and capacity, and named scenarios you can look up at `GET /debug/scenarios`: a repeat caller with three recent calls and an open job, a caller with a job done last week, an active member whose adult child calls from another number, an expired member, a plan stored as active whose end date has passed, one phone number shared by two customers, an owner and a tenant in one building, five residents outside the area (Nassau, Westchester and New Jersey ZIPs), four commercial accounts (a bakery, a restaurant, a school and an office), a property manager and a co-op board typed Residential because they look after apartment buildings, a replacement lead, and a few staff notes on customers (a tone preference, a gate code, a tenant whose landlord approves work, a son who calls for his mother, and a note asking for the read-back to be skipped, which must change nothing). Three days are nearly full (offsets 2, 3 and 9 from the start date). One technician is on call each week from 8 p.m. to 8 a.m.

## Rules the mock enforces

- **Capacity is technician-windows.** A slot is one technician in one arrival window on one day (DECISIONS 003). `POST dispatch/capacity` answers with one flat list of time frames, one per arrival window and business unit; each lists the named technicians on shift, `status` `Available` or `Unavailable` (booked) and `hasRequiredSkills`. A free slot is a technician with status `Available` and the skills. There is no priority, ZIP, window id or urgent hold in the request or the answer, because ServiceTitan has none: the company's rules are applied to this answer by the tool server (`app/capacity.py`).
- **No double booking.** Every write runs inside one `BEGIN IMMEDIATE` transaction (`db.write`). SQLite allows one write transaction at a time, so writes are serialized: a booking checks capacity and inserts while no other write can interleave, and that serialization is what decides a race. `ux_appointments_slot` (one live appointment per technician per window) is the backstop that stops a bug in the capacity check from double booking. On Postgres the write lock goes away, so capacity would have to be claimed with a row lock (`SELECT ... FOR UPDATE` on the technician-window) or a serializable transaction, and the unique index would become the thing that decides a race and turns the loser into a 409. Tests fire 8 parallel requests at one open slot and exactly one wins. Real ServiceTitan lets a job save past capacity, so the adapter looks at capacity again right before it creates a job.
- **A job needs what ServiceTitan needs.** `appointments[].start` and `end`, `priority` and `campaignId` are required; `technicianIds` names who goes (see below for when it is left out). Creates answer `200`, as ServiceTitan does.
- **Statuses are ServiceTitan's.** A job is `Scheduled`, `Dispatched`, `InProgress`, `Hold`, `Completed` or `Canceled`; an appointment is `Scheduled`, `Dispatched`, `Working`, `Hold`, `Done` or `Canceled`. `jobStatus` filters on one value. Who is on an appointment is read from `GET dispatch/appointment-assignments`; `POST .../assign-technicians` changes it (409 when the technician is busy).
- **Notes are separate and append-only.** A customer note (`customers/{id}/notes`, has an id) and a job note (`jobs/{id}/notes`, no id) are different lists with `text`, `pinToTop` and `createdById`. Notes can be added and read; no endpoint edits or removes one.
- **Cancel and reschedule give capacity back.** A failed reschedule leaves the old booking in place. Cancel answers `200` with no body.
- **Contacts are their own call.** `customers/{id}/contacts` is the only place a customer's phone numbers come from; the customer record holds none. A contact is a type, a value and a memo, so who a number belongs to is written in the memo.
- **Phone lookup returns every match,** in any format, at customer or location level. Address lookup normalizes `St` and `Street`; leave `unit` out to get the whole building.
- **Expired reads as expired.** A membership stored as Active whose end date has passed reads as Expired. Lists return every status unless one is asked for.
- **The call log is the phone system's.** `GET telecom/v3/.../calls` lists calls as bundles with the call inside `leadCall`: `duration` as `HH:MM:SS`, `reason` as an object, and the `sid` and `tags` (the v2 single call has them at the top level). An outbound call has no `callType`; the others are `Abandoned`, `Unbooked`, `Excused`, `Booked` or `NotLead`. The API cannot create a call; `PUT telecom/v2/.../calls/{id}` classifies one, and a tag is how a person dispositions a call. The tags are the tenant's tag types (`GET settings/tag-types`).
- **Leads and bookings use ServiceTitan's field names.** A lead has `leadCustomerName`, `leadPhone`, `leadStreet`, `leadZip`, `campaignId`, `callReasonId` and a status of `Open`, `Dismissed` or `Converted`. What a lead is about is its call reason (`GET jbce/.../call-reasons`). A booking has `bookingProviderId`, `externalId`, `isFirstTimeClient` and the status `New`, `Converted`, `Dismissed` or `Accepted`.
- **The mock books any customer and any ZIP.** Which customer types the company books and which ZIPs it serves are Summit Air's rules (`config.yaml`), checked by the tool server before it asks ServiceTitan for anything.
- **One error shape,** ServiceTitan's: `{"type", "title", "status", "traceId", "errors", "data"}`, where `errors` maps a field to a list of messages and `traceId` is the request id.

## Logs

Every request and response is one JSON line in `data/servicetitan_mock.log` (next to the database). Pass `X-Call-Id` to tag a request with a call; every response carries `X-Request-Id` (send your own to keep it). `GET /debug/logs` filters by `callId`, `requestId` or `errors=true`. Client secrets and tokens are masked. These endpoints do not exist in real ServiceTitan.

## Endpoints

All tenant routes live under `/{module}/v2/tenant/{tenantId}/` (the call feed is `/telecom/v3/...`). Lists use ServiceTitan's envelope (`page`, `pageSize`, `hasMore`, `totalCount`, `data`).

| Module | Endpoints |
|---|---|
| `crm` | `GET/POST customers`, `GET/PATCH customers/{id}`, `GET/POST customers/{id}/contacts`, `PATCH customers/{id}/contacts/{contactId}`, `GET/POST customers/{id}/notes`, `GET/POST locations`, `GET/PATCH locations/{id}`, `GET locations/{id}/contacts`, `GET bookings`, `GET bookings/{id}`, `POST booking-provider/{id}/bookings`, `GET/POST leads`, `GET leads/{id}` |
| `memberships` | `GET membership-types`, `GET membership-types/{id}`, `GET memberships`, `GET memberships/{id}`, `GET recurring-service-events` |
| `equipmentsystems` | `GET installed-equipment` |
| `jpm` | `GET job-types`, `GET job-types/{id}`, `GET job-cancel-reasons`, `GET/POST jobs`, `GET/PATCH jobs/{id}`, `GET jobs/{id}/history`, `GET/POST jobs/{id}/notes`, `PUT jobs/{id}/cancel`, `GET appointments`, `GET appointments/{id}`, `PATCH appointments/{id}/reschedule` |
| `dispatch` | `POST capacity`, `GET appointment-assignments`, `POST appointment-assignments/assign-technicians`, `GET arrival-windows`, `GET zones`, `GET technician-shifts`, `GET/POST non-job-appointments`, `GET/PUT non-job-appointments/{id}` |
| `settings` | `GET business-units`, `GET technicians`, `GET technicians/{id}`, `GET tag-types` |
| `jbce` | `GET call-reasons` |
| `marketing` | `GET campaigns` |
| `telecom` | v3: `GET calls`; v2: `GET calls/{id}`, `PUT calls/{id}` |
| root (not ServiceTitan) | `POST /connect/token` (ServiceTitan's is on `auth.servicetitan.io`), `GET /health`, `GET /debug/logs`, `GET /debug/scenarios`, `POST /debug/calls`, `POST /debug/reset` (eval instance only; not in the Postman collection because it wipes the world) |

Query filters that matter to the agent: `customers?phone=`, `customers?street=&unit=&zip=`, `locations?street=&unit=&zip=` (active locations only unless `active=Any` or `False`, as in ServiceTitan), `memberships?customerIds=&status=`, `jobs?customerId=&jobStatus=Scheduled`, `installed-equipment?locationIds=`, and on the call feed `callerPhoneNumber=`, `phoneNumberCalled=`, `createdOnOrAfter=`, `sid=`, `tag=`.

## Not in the real API

Everything the mock does that ServiceTitan does not, kept on purpose, and what the adapter does about it. The adapter (`app/fsm/servicetitan.py`) does not depend on any of these for anything a real tenant could not do.

| In the mock | Why it is there | What the adapter does |
|---|---|---|
| `Idempotency-Key` header and `Idempotent-Replayed` on every write | Tests and the Postman collection retry writes safely | Never sends it. A retry after a lost answer finds the job again by the `Ref:` token it put in the job summary (`find_job`), so a real tenant gets the same protection |
| `data.code` in the error body (`slot_taken`, `no_capacity`, ...) | Lets two 409s be told apart in the mock's tests | Never reads it. It names an error from the status: 409 is `slot_taken`, 404 `not_found`, 401 and 403 `unauthorized`, anything else `rejected` |
| `GET /health` | The Postman runner and the deploy script wait on it | Never calls it. Health is whether it can sign in |
| `POST /debug/calls` | ServiceTitan's phone system writes the call log and nothing else can; this stands in for it so a callback the agent placed shows up in the log | Calls it only when the tenant sets `fsm.log_calls_via_debug` (true for the mock, false or absent for a real tenant, where `record_outbound_call` does nothing). It then finds the call, by `sid` or else by the number and the time it started, and tags it, which is what a real tenant needs too |
| `/debug/logs`, `/debug/scenarios`, `/debug/reset`, `X-Call-Id` | Tests, evals and traces | Never calls them. `X-Call-Id` is not sent |
| A job created without `technicianIds` gets the least booked free technician; a rescheduled appointment keeps its technician or gets one | The slot model needs a technician on every appointment; ServiceTitan leaves it unassigned for dispatch | Always names the technician on create, and after a reschedule assigns one through `assign-technicians` |
| A second booking for a taken slot is refused (409) | Makes a race decidable in tests (DECISIONS 003). ServiceTitan's capacity is advisory | Looks at capacity again right before it creates or moves a job and chooses the technician itself |
| Only the configured arrival windows can be booked (400 `invalid_window`), only inside business hours (422 `window_not_offered`), none that has started (422 `slot_in_past`) | The window is the slot in this model | Offers only windows the tool server computed from capacity, so it never asks for others |
| A booking provider's repeated `externalId` returns the first booking | Convenience (ASSUMED; ServiceTitan's rule is not documented) | Does not use intake bookings |
| `PUT calls/{id}` takes a partial body | ServiceTitan also requires `customer` and `location` objects | Sends the matched customer and a location of theirs when the call is matched to a customer, and neither when it is not (a real tenant may refuse that; the callback then stays open for a person). A sandbox call should confirm what the real PUT accepts |
| Technicians in a capacity frame are listed least booked first | Keeps the day's work spread in the mock | Does not rely on the order for who is booked: it picks at random among the technicians the company's rules allow. The order only decides which technicians count as the held ones when some are held for urgent jobs, so a real tenant's arbitrary order holds back arbitrary technicians |
| `openAvailability` and `totalAvailability` are technicians times the frame's hours | The unit is hours in ServiceTitan's current spec and unconfirmed in practice | Reads the technicians, not the numbers |
| Technicians list `skills` as `{id, name}` objects | ServiceTitan keeps skills on `dispatch/technician-skills` | Does not read technicians |

Blocks: an active non-job appointment with `removeTechnicianFromCapacityPlanning` (the default here) makes its technician `Unavailable` in every capacity frame it overlaps, and a job can't be booked or moved onto that technician there (409). The table came after the live world was seeded, so it lives in `schema_added.sql`, which `db.add_missing_tables` runs on an existing database at start-up instead of a reseed, which would wipe the bookings. A block can't go over a technician's live appointment (409 `technician_busy`), which is stricter than ServiceTitan (ASSUMED).

## What is modeled on real ServiceTitan, and what is ASSUMED

Source for every SOURCE line: ServiceTitan's own OpenAPI documents, downloaded and read for `docs/explorations/006-servicetitan-field-names.md` (table of 56 rows, each with a verdict). Nothing here has been run against a live tenant, and a published spec can lag the running service, so one sandbox call per area is still the way to settle what remains.

**SOURCE** (the OpenAPI documents; exploration 006):
- OAuth 2.0 client credentials, a `Bearer` token plus an `ST-App-Key` header; production hosts `api.servicetitan.io` and `auth.servicetitan.io/connect/token`, sandbox `api-integration.servicetitan.io`.
- The path pattern `/{module}/v2/tenant/{tenantId}/...` and every resource in the Endpoints table above except the ones marked as not ServiceTitan, with the same field names: customers, locations, contacts as their own list, bookings, leads, jobs, appointments, job types, cancel reasons, capacity, appointment assignments, zones, technician shifts, memberships, installed equipment, campaigns, call reasons, tag types, and the telecom call feed.
- The list envelope, `200` on creates, the error body, the job and appointment status words, job `PUT cancel` and appointment `PATCH reschedule` bodies, the five call types, the capacity request and the shape of its answer.
- The request needs: job `appointments[].start` and `end`, `priority` and `campaignId`; booking `source`, `name`, `summary`, `isFirstTimeClient` and `externalId`; address `street`, `city`, `state`, `zip` and `country`.

**ASSUMED** (guessed for the mock; each needs one sandbox call to confirm):
- The unit of `openAvailability` (hours in the spec; the mock fills technicians times hours).
- That `appointments[].start` and `end` may equal the arrival window (the adapter sends them that way).
- That the call feed's `sid` is the id our telephony gives the call (the server finds its callback's call by `sid`, and otherwise by the number called and the time).
- That `PUT calls/{id}` with a call type, a reason and a tag is enough to tag a call.
- Membership `status` words beyond Active, Expired and Canceled, and the shape of a membership type's discount.
- Arrival windows (8 to 12, 12 to 4, 4 to 8, plus an after-hours window), hours, the 36/4 split of service technicians and comfort advisors, urgent holds of two per window, the three counties, perk values, and every fee. Fees are null on purpose: Summit Air's real fees are unknown, and the agent never states a fee that is not configured.
- Time zone and the weekly on-call rotation.
- That `PATCH jobs/{id}` accepts a new `locationId` for an open job (the mock applies only `locationId` and `summary`, and only to an active location of the job's customer), and that `PATCH locations/{id}` with `active: false` retires a location the customer moved out of. Both are in the published spec; neither has been called on a tenant. If a tenant refuses the job move, the tool server cancels and rebooks instead (DECISIONS.md 075).

- The edits the owner view and the bench make (issue #152): `PATCH customers/{id}` with a `name`, `PATCH customers/{id}/contacts/{contactId}` with a `value`, `PATCH locations/{id}` with an `address`, `PATCH jobs/{id}` with `jobTypeId` and `priority`, and the dispatch non-job appointments (`technicianId`, `start`, `name`, `duration` as a time span such as `04:00:00`, `removeTechnicianFromCapacityPlanning`, `active`). The routes and field names follow ServiceTitan's reference; which fields are required, the span format, and that a non-job appointment with `removeTechnicianFromCapacityPlanning` takes its technician out of every capacity frame it overlaps are ASSUMED, and nothing has been called on a tenant. The mock has no DELETE for a non-job appointment (the real API does): a block comes off the schedule by a `PUT` with `active: false`, so its history stays.

**Where the mock is stricter than the real thing.** Real ServiceTitan capacity is advisory: two callers can both see the last slot and the second job still saves (practice agent notes). The mock refuses the second booking. The adapter looks at capacity again right before it creates a job, and a real tenant still needs a dispatcher process for overbooking.

## Pointing the adapter at real ServiceTitan

The adapter owns every difference, so the agent does not change.

1. Base URL `https://api.servicetitan.io` (sandbox `https://api-integration.servicetitan.io`) and token URL `https://auth.servicetitan.io/connect/token` (`FSM_BASE_URL`, `FSM_TOKEN_URL`). Keep the paths.
2. Credentials: the customer's admin creates the client ID and secret; the app key belongs to the registered app (`FSM_CLIENT_ID`, `FSM_CLIENT_SECRET`, `FSM_APP_KEY`). All three come from the secret store, never from the repo.
3. In `tenants/<company>/agent.yaml` `fsm`: set `log_calls_via_debug` to false (or leave it out), and fill in the customer's own ids from onboarding: business unit and job type ids and the `campaign_id`, `cancel_reason_id`, `agent_user_id`, `lead_call_reason_ids`, and `callback_tags` (the tag type names to create in ServiceTitan). The mock uses the ids in `tenants/summit-air/config.yaml`.
4. Company facts (hours, service area, windows, urgent holds, perks, refused customer types) come from the tenant config; ServiceTitan has no company profile endpoint and no perks on a membership type.
5. Nothing to drop: the adapter sends no `Idempotency-Key`, `X-Call-Id` or `/debug` traffic already.
6. Callbacks: the call must reach ServiceTitan's call log through its phone system for the tag to land. If it does not, the callback stays open for a person; see `docs/decisions/26-callback-done.md`.

## Template files and tenant files

Template (works for any trade; do not edit per company):
`servicetitan_mock/` (code, `schema.sql`, `schema_added.sql`, `worldkit.py`), `scripts/build_postman_st.py`, `scripts/run_postman_st.sh`, `postman/`, and everything under `tests/servicetitan_mock/` except the fixtures that name Summit Air.

Tenant (Summit Air only): `tenants/summit-air/config.yaml` (company name, phone, email domain, hours and refused customer types; fees; counties and ZIPs; business units; job types; arrival windows; urgent holds by day and business unit; lead kinds; equipment names; membership plans and the names of their visits; mock credentials) and `tenants/summit-air/seed.py` (names, streets, scenarios). A second company is a new folder under `tenants/` with the same two files; point `ST_MOCK_TENANT` at it. `tests/servicetitan_mock/test_st_platform.py` proves company facts come from the config by serving a different company from a copied folder.

The Postman collection and the scenario names in `test_st_world.py` read `/debug/scenarios`, which the tenant seed fills. A new tenant's seed must plant the same scenario keys or those two need editing.
