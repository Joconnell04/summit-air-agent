# ServiceTitan mock

A stand-in for the systems a 40-technician residential HVAC company (Summit Air, the first tenant) runs on, shaped like ServiceTitan's public API. The agent's integration layer talks to this today. Moving to real ServiceTitan later changes the adapter's base URL and auth, not the agent (C-03). This is the customer's system, not ours: it holds no agent logic.

Python 3.12, FastAPI, SQLite. Brief: `docs/ai-process/briefs/01-mock-servicetitan.md`.

## Run

```
make seed-st      # rebuild the world in data/servicetitan_mock.db (start date = today)
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

The seed takes `--start-date YYYY-MM-DD` and a fixed random seed, so one start date always gives one world. Pair it with `ST_MOCK_NOW` for repeatable evals.

## What is in the world

300 customers (owners, tenants, apartment owners), 40 technicians (36 service, 4 comfort advisors), two weeks of shifts and capacity, and named scenarios you can look up at `GET /debug/scenarios`: a repeat caller with three recent calls and an open job, a caller with a job done last week, an active member whose adult child calls from another number, an expired member, a plan stored as active whose end date has passed, one phone number shared by two customers, an owner and a tenant in one building, five residents outside the area (Nassau, Westchester and New Jersey ZIPs), six commercial accounts (a bakery, a property manager, a restaurant, a school, an office and a co-op board), a replacement lead, and a few staff notes on customers (a tone preference, a gate code, a tenant whose landlord approves work, a son who calls for his mother, and a note asking for the read-back to be skipped, which must change nothing). Three days are nearly full (offsets 2, 3 and 9 from the start date). One technician is on call each week from 8 p.m. to 8 a.m.

## Rules the mock enforces

- **Capacity is technician-windows.** A slot is one technician in one arrival window on one day. Open capacity is technicians on shift with the right skills, minus those already booked. A few technicians per window are held for urgent jobs; normal jobs cannot use them.
- **No double booking.** Every write runs inside one `BEGIN IMMEDIATE` transaction (`db.write`). SQLite allows one write transaction at a time, so writes are serialized: a booking checks capacity and inserts while no other write can interleave, and that serialization is what decides a race. `ux_appointments_slot` (one live appointment per technician per window) is the backstop that stops a bug in the capacity check from double booking. On Postgres the write lock goes away, so capacity would have to be claimed with a row lock (`SELECT ... FOR UPDATE` on the technician-window) or a serializable transaction, and the unique index would become the thing that decides a race and turns the loser into a 409. Tests fire 8 parallel requests at one open slot and exactly one wins.
- **Idempotent writes.** Send `Idempotency-Key`; the same key and body returns the first result with `Idempotent-Replayed: true`. The same key with a different body is a 422. Only successful results are stored: a request that fails (409, 422) leaves no record, so retrying the same key runs the rules again and succeeds once the cause is gone, for example when capacity frees up. Intake bookings and calls also dedupe on `externalId`.
- **Notes are append-only.** A note is free text on a customer, optionally about one of its jobs. Notes can be added and read (`customers/{id}/notes` returns job notes too); no endpoint edits or removes one.
- **Cancel and reschedule give capacity back.** A failed reschedule leaves the old booking in place.
- **Phone lookup returns every match,** in any format, at customer or location level. Address lookup normalizes `St` and `Street`; leave `unit` out to get the whole building.
- **Expired reads as expired.** A membership stored as Active whose end date has passed reads as Expired. Lists return every status unless one is asked for.
- **Refused customer types cannot get appointments or intake bookings** (422 `customer_type_not_supported`) but can be stored as leads. Which types are refused, and the message returned, come from `company.rejected_customer_types` in the tenant config; Summit Air refuses Commercial. Lead kinds come from `lead_kinds`. The two customer types themselves (`Residential`, `Commercial`) are ServiceTitan's own.
- **Out-of-area ZIPs are rejected on jobs and intake bookings** (422 `out_of_area`).
- **After-hours windows are urgent-only** and only the on-call technician covers them.
- **One error shape:** `{"status", "code", "title", "detail", "errors", "requestId"}`. Codes: `validation_error`, `invalid_window`, `unauthorized`, `invalid_app_key`, `not_found`, `slot_taken`, `no_capacity`, `invalid_state`, `customer_type_not_supported`, `out_of_area`, `after_hours_urgent_only`, `slot_in_past`, `unknown_reference`, `idempotency_key_reused`.

## Logs

Every request and response is one JSON line in `data/servicetitan_mock.log` (next to the database). Pass `X-Call-Id` to tag a request with a call; every response carries `X-Request-Id` (send your own to keep it). `GET /debug/logs` filters by `callId`, `requestId` or `errors=true`. Client secrets and tokens are masked. These endpoints do not exist in real ServiceTitan.

## Endpoints

All tenant routes live under `/{module}/v2/tenant/{tenantId}/`. Lists use ServiceTitan's envelope (`page`, `pageSize`, `hasMore`, `totalCount`, `data`).

| Module | Endpoints |
|---|---|
| `crm` | `GET/POST customers`, `GET customers/{id}`, `GET/POST customers/{id}/contacts`, `GET/POST customers/{id}/notes`, `GET/POST locations`, `GET locations/{id}`, `GET locations/{id}/contacts`, `GET bookings`, `GET bookings/{id}`, `POST booking-provider/{id}/bookings`, `GET/POST leads`, `GET leads/{id}` |
| `memberships` | `GET membership-types`, `GET membership-types/{id}`, `GET memberships`, `GET memberships/{id}`, `GET recurring-service-events` |
| `equipmentsystems` | `GET installed-equipment` |
| `jpm` | `GET job-types`, `GET job-types/{id}`, `GET job-cancel-reasons`, `GET/POST jobs`, `GET jobs/{id}`, `GET jobs/{id}/history`, `GET/POST jobs/{id}/notes`, `PUT jobs/{id}/cancel`, `GET appointments`, `GET appointments/{id}`, `PATCH appointments/{id}/reschedule` |
| `dispatch` | `POST capacity`, `GET zones`, `GET technician-shifts` |
| `settings` | `GET business-units`, `GET technicians`, `GET technicians/{id}`, `GET company-profile` |
| `marketing` | `GET campaigns` |
| `telecom` | `GET calls`, `GET calls/{id}`, `POST calls` |
| root | `POST /connect/token`, `GET /health`, `GET /debug/logs`, `GET /debug/scenarios` |

Query filters that matter to the agent: `customers?phone=`, `customers?street=&unit=&zip=`, `locations?street=&unit=&zip=`, `memberships?customerId=&locationId=&status=`, `jobs?customerId=&jobStatus=Scheduled,Dispatched,Working`, `calls?phone=&receivedOnOrAfter=`.

## What is modeled on real ServiceTitan, and what is ASSUMED

I could not read developer.servicetitan.io (it is a JavaScript portal; the fetch returned only the page title) and have no sandbox tenant. So the labels below are honest about confidence.

**SOURCE** (ServiceTitan's own documentation, as recorded in the earlier practice agent's notes, `revin-interview/research/servicetitan-mapping.md`):
- OAuth 2.0 client credentials, a `Bearer` token plus an `ST-App-Key` header, tokens that last 15 minutes.

**SECONDARY** (third-party docs and connectors seen in search results, not ServiceTitan's own pages; names likely right, shapes unconfirmed; `revin-interview/research/servicetitan-mapping.md` marks these the same way):
- Production URLs `https://auth.servicetitan.io/connect/token` and `https://api.servicetitan.io`; the sandbox uses `auth-integration.servicetitan.io` and `api-integration.servicetitan.io`.
- The path pattern `/{module}/v2/tenant/{tenantId}/...` and these resources: `crm` customers, locations, location contacts, bookings; `jpm` jobs, job types, appointments (`PATCH .../reschedule`), `PUT jobs/{id}/cancel`; `dispatch` capacity and technician shifts; the booking-provider booking path.

**ASSUMED** (guessed from memory or invented for the mock; each needs one sandbox call to confirm):
- Every response and request field name beyond the ones above, including the camelCase names, the `Scheduled/Dispatched/Working/Done/Canceled` status words, and `Active/Expired/Canceled` membership status.
- The capacity response: `availabilities[].timeslots[]` with `windowId`, `openAvailability`, `heldForUrgent`, `technicianIds`. It follows the practice agent's adapter, not a verified response. The `priority` and `zip` fields on the request are inventions.
- `Idempotency-Key` on writes. Real ServiceTitan has no known idempotency support (practice agent notes: assumed none), so production needs the key stored on our side.
- Membership perks on the membership type (`prioritySched`, `visitsPerYear`, `repairDiscountPercent`, `afterHoursFeeWaived`). Real perks live in discounts and recurring service templates.
- The notes endpoints and their fields (`text`, `createdBy`, `jobId`). ServiceTitan has customer and job notes; the paths and shapes here are guesses.
- Contact `role` (owner, tenant, adult_child), address `county`, location `zoneId`, technician `skills` and `homeZoneId`, `systemType` and `installYear` on installed equipment, `kind` on leads, and leads that carry a bare name and phone.
- `GET settings/company-profile`, `POST telecom/calls`, `GET jobs?jobStatus=` with a comma-separated list, `GET customers?street=`, the `/debug/*` endpoints, and `X-Call-Id`.
- 201 on creates (real ServiceTitan may return 200), and the single error body shape.
- Arrival windows (8 to 12, 12 to 4, 4 to 8, plus an after-hours window), hours, the 36/4 split of service technicians and comfort advisors, urgent holds of two per window, the three counties, perk values, and every fee. Fees are null on purpose: Summit Air's real fees are unknown, and the agent never states a fee that is not configured.
- Time zone and the weekly on-call rotation.

**Where the mock is stricter than the real thing.** Real ServiceTitan capacity is advisory: two callers can both see the last slot and the second job still saves (practice agent notes). The mock refuses the second booking. An agent that passes against the mock still needs the recheck-before-booking step against real ServiceTitan, and a dispatcher process for overbooking.

## Pointing the adapter at real ServiceTitan

The adapter owns every difference, so the agent does not change.

1. Base URL `https://api.servicetitan.io` (sandbox `https://api-integration.servicetitan.io`) and token URL `https://auth.servicetitan.io/connect/token`. Keep the `/{module}/v2/tenant/{tenantId}` paths.
2. Credentials: the customer's admin creates the client ID and secret; the app key belongs to the registered app. All three come from the secret store, never from the repo.
3. Read the company profile from the per-company config file instead of `company-profile`. Read membership perks from config instead of the membership type.
4. Drop `Idempotency-Key` and keep the key in our own table; put it in the job summary so a person can find it.
5. Map the capacity response, and recheck capacity right before creating a job.
6. Write calls to our own store (no write endpoint on `telecom` is assumed), and drop the `/debug/*` endpoints and `X-Call-Id` from production traffic.
7. Look up each customer's business unit, job type, campaign and cancel reason IDs during onboarding; the mock uses the IDs in `tenants/summit-air/config.yaml`.

## Template files and tenant files

Template (works for any trade; do not edit per company):
`servicetitan_mock/` (code, `schema.sql`, `worldkit.py`), `scripts/build_postman_st.py`, `scripts/run_postman_st.sh`, `postman/`, and everything under `tests/servicetitan_mock/` except the fixtures that name Summit Air.

Tenant (Summit Air only): `tenants/summit-air/config.yaml` (company name, phone, email domain, hours and refused customer types; fees; counties and ZIPs; business units; job types; arrival windows; urgent holds by day and business unit; lead kinds; equipment names; membership plans and the names of their visits; mock credentials) and `tenants/summit-air/seed.py` (names, streets, scenarios). A second company is a new folder under `tenants/` with the same two files; point `ST_MOCK_TENANT` at it. `tests/servicetitan_mock/test_st_platform.py` proves company facts come from the config by serving a different company from a copied folder.

The Postman collection and the scenario names in `test_st_world.py` read `/debug/scenarios`, which the tenant seed fills. A new tenant's seed must plant the same scenario keys or those two need editing.
