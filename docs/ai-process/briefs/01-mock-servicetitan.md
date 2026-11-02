# Build brief: mock ServiceTitan (CRM and FSM)

Issue: see "Closes" in the pull request. Branch: `build/mock-servicetitan`.

## Goal

A standalone mock of the systems a 40-technician residential HVAC company runs on, shaped like ServiceTitan's public API, so the agent's integration layer talks to something that looks and behaves like the real thing. Swapping the mock for real ServiceTitan later changes the adapter's base URL and auth, not the agent.

Read first: CLAUDE.md, docs/assignment.md, docs/users.md, docs/considerations.md (C-02, C-03, C-06, C-07, C-08, C-18, C-34, C-37), docs/explorations/001-hvac-shop-operations.md, docs/explorations/002-nyc-callers.md. Prior art to learn from, not copy blindly: `~/Downloads/Claude/personal/revin-interview/agent/app/crm/` and `agent/postman/servicetitan-mock.postman_collection.json` (an earlier practice agent's smaller mock).

## Scope

Python 3.12, FastAPI, SQLite, in a new top-level package `servicetitan_mock/`, run with `make mock-st` on its own port. No agent logic here; this is the customer's system, not ours.

Model it on ServiceTitan's API groups and field names where they are public (check developer.servicetitan.io; note anything guessed as ASSUMED in the README):

- **CRM:** customers (residential or commercial type), locations (a customer can have several; address, ZIP, borough or county), contacts (phone, email, role such as owner, tenant, adult child), bookings (an intake request that isn't a job yet), leads (for replacement and install interest, and the one-line commercial record).
- **Memberships:** membership types (e.g. Summit Standard Membership with priority scheduling, two tune-ups a year, repair discount, waived after-hours fee), customer memberships attached to a location, status active, expired or cancelled, recurring service visits due.
- **Equipment systems:** per location: system type (boiler with radiators, furnace, central AC, ductless mini-split, heat pump), brand, install year.
- **Jobs and appointments (JPM):** job types (no heat, no cooling, maintenance tune-up, estimate, safety check), business units (residential service, residential install), priority, summary notes, arrival windows, status (scheduled, dispatched, working, done, cancelled), job history.
- **Dispatch:** technicians (40, with skills and home zone), zones by ZIP, capacity by day and arrival window and business unit, held urgent capacity, technician shifts including the weekly on-call technician.
- **Telecom:** a call log by phone number (calls in, linked to customer and job when known), so repeat-caller detection has real data.
- **Settings:** company profile (hours, service-area ZIPs, after-hours rules, configured diagnostic fee or none), loaded from a per-company config file, not hard-coded (C-37).

Behavior that must be right, each with a test:

- Booking is idempotent: the same idempotency key returns the first result; the same slot can't be booked twice, enforced by a database constraint, with a concurrent race test (8 parallel requests, one wins).
- Capacity goes down when booked and back up when cancelled or rescheduled.
- Lookups by phone return all matching customers (shared numbers happen); lookup by address works when the phone doesn't match.
- An expired membership reads as expired, not missing.
- Commercial customers can be stored as leads but appointments for them are rejected with a clear error.
- Out-of-area ZIPs are rejected on booking.
- Errors follow one JSON shape.

## Seed data

A seed script builds a believable Summit Air: three counties (ASSUMED Brooklyn, Queens and Staten Island; keep it in config), real-looking but fake addresses and 555 phone numbers, about 300 customers with a mix of owners and tenants, members (active, expired, two plans at one address), non-members, one commercial account, an out-of-area address, a repeat caller with an open job, a caller with a job done last week, and two weeks of capacity with some days nearly full. Deterministic (fixed random seed) so tests and evals see the same world.

## Logging and review

Every request and response is logged as one JSON line with a request id and, when the caller passes one, a call id. A `GET /debug/logs` endpoint filters by call id, request id or errors, so a person or a dev agent can read what happened on a call. Ship a Postman collection and environment under `postman/` that covers every endpoint with assertions, runnable with Newman (`make postman-st`), so the mock can be checked the same way the integration layer will be.

## Template vs single-tenant

Anything specific to Summit Air (company name, ZIPs, counties, job types, membership plans, fee) lives in `tenants/summit-air/` config and the seed. The mock's code works for any trade. Note in the README which files are template and which are tenant.

## Done when

- `make check` passes with the new tests, ratchet updated.
- `make mock-st` serves the API; `make seed-st` rebuilds the world; Newman passes.
- README section explains the endpoints, what is modeled on real ServiceTitan and what is ASSUMED, and how to point the adapter at real ServiceTitan later.
- Pull request opened against main with `Closes #N`, reviewed by the `reviewer` agent before merge.
