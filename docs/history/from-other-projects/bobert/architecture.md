# Architecture

How the tool server is put together, why each rule lives where it does, and how it is deployed and tested. For setup and commands see `README.md`. Paths are relative to `agent/`.

The split in one sentence: the voice layer (Retell, or the LiveKit worker in `../voice`) handles speech and conversation; this server owns every fact and every rule, so a model that misbehaves cannot book a taken slot or call a customer who said stop.

## Modules

| File | Owns |
|---|---|
| `app/main.py` | FastAPI app. Routes, signature check, the tool dispatcher (`POST /tools/{name}`), the webhook receiver, `/api/console`, `/debug/*`. Turns every exception into a spoken fallback so the agent never hears a 500. |
| `app/tools.py` | The nine inbound tools (`TOOLS` dict at the bottom). Input cleaning (`normalize_phone`, `normalize_zip`), `Rejected` for business errors, booking, reschedule, cancel. |
| `app/db.py` | Schema, connection settings (WAL, busy timeout), `tx()` which opens `BEGIN IMMEDIATE`, slot seeding, `reset()`. Small migrations run in `init()`. |
| `app/config.py` | Environment, service-area ZIPs, service types, company constants. Fails at startup if `CRM=servicetitan` lacks credentials. |
| `app/retell.py` | `verify()` for `X-Retell-Signature` (uses the Retell SDK) and `record_event()`, which upserts webhook events into `calls`. |
| `app/path.py` | Builds the call-path strip (Intake, ZIP check, Availability, Booking, Outcome) from one call's tool log. Pure function. |
| `app/metrics.py` | `summary()` behind `/api/metrics`: calls, booking rate, tool latency p50 and p95, error rate. Ignores `call_pm_*` ids from Postman. |
| `app/callbacks.py` | `parse_window()`: turns "after 3" or "tomorrow morning" into a start and end inside business hours. Pure, no database. |
| `app/outbound.py` | The `/outbound` router: eligibility rules, preview, guarded dial, `record_outcome`, `request_concession`, office queue. Also owns the `estimates`, `outcomes` and `concessions` tables. |
| `app/negotiation.py` | `decide()`: the discount policy as a pure function; `load_policy()` reads `config/negotiation.json`. |
| `app/crm/` | CRM adapter layer. `base.py` has the protocol and errors, `local.py` uses the SQLite slots, `servicetitan.py` is a ServiceTitan v2 adapter. Selected by `CRM`. |
| `app/console.html` | The single-page trace UI. Polls `/api/console`. |

Tracing is `app/tracing.py`: a no-op in-memory backend by default, Langfuse when keys are set (see below).

## Data model

SQLite file at `DB_PATH` (`data/summit.db` locally, `~/summit/live-data/summit.db` on Vultr). Defined in `app/db.py` unless noted.

- `slots(slot_id PK, kind, start, end, tech)`. `kind` is `service` or `estimate`. Seeded for the next 7 business days, closed Sundays.
- `leads(lead_id PK, call_id UNIQUE, phone, name, address, zip, service_type, urgency, owner_status, decision_makers, preferred_window, notes, outcome, summary, ...)`. One lead per call. `outcome` is the last known result (`booked`, `out_of_area`, `callback`, `transferred`, `emergency_transfer`, `cancelled`).
- `bookings(booking_id PK, slot_id, idempotency_key UNIQUE, lead_id, call_id, status)`. Index `bookings_active_slot` is unique on `slot_id` where `status = 'booked'`, so a cancelled booking frees its slot.
- `tool_calls(id, call_id, tool, args, response, status, latency_ms, error, heard, decisions, created_at)`. Index on `call_id`. `status` is `ok`, `rejected` or `error`. `heard` is the caller's last line when the tool fired.
- `calls(call_id PK, status, started_at, ended_at, ended_reason, transcript, analysis, source, ...)`. `source` is `retell` or `livekit`.
- `estimates(estimate_id PK, phone, zip, service_type, amount_usd, status, consent_type, dnc, attempts, last_attempt_at, outcome, callback_*, ...)` in `app/outbound.py`.
- `outcomes(id, estimate_id NULL for inbound, call_id, outcome, callback_start, callback_end, topic, ...)`: one row per outcome call, so "latest wins" is auditable. Indexed on `estimate_id` and `call_id`.
- `concessions(id, estimate_id, call_id, ask_usd, decision, discount_usd, new_total_usd, accepted)`: every ask, approved or refused.

## Request shapes

- **Tool call from Retell.** `POST /tools/{name}` with `{"name": ..., "call": {"call_id": ..., "transcript": ...}, "args": {...}}`. `parse_tool_body` in `app/main.py` also accepts bare args (Postman, curl) with an `x-call-id` header.
- **Tool result.** Always HTTP 200 and `{"ok": bool, "message": "..."}` plus tool fields. Rejections add `reason`, often `field` and `alternatives`.
- **Webhook.** `POST /webhooks/retell` with `{"event": "call_started" | "call_ended" | "call_analyzed", "call": {...}}`. Returns 204. Upserts by `call_id`, so Retell's retries are harmless.
- **Signature.** `X-Retell-Signature: v=<ms>,d=<hmac_sha256(key, raw_body + ms)>`, valid five minutes, key is `RETELL_API_KEY`, checked in `app/retell.py` on the raw bytes. `SIGNATURE_MODE` is `enforce`, `log` (default, accept and log) or `off`. The LiveKit worker signs the same way.
- **Outbound tools.** `POST /outbound/tools/record_outcome` and `/outbound/tools/request_concession` use the same body. The estimate id comes from `call.metadata.estimate_id`, falling back to dynamic variables or args.

## Rules that live on the server

Each of these started as a prompt instruction that the model sometimes ignored.

- **Idempotent booking** (`book_appointment`). Voice tools get called twice: Retell keeps running a tool when the caller interrupts. The key defaults to `call_id:slot_id`; a repeat returns the same booking with `duplicate: true`. The check and insert run inside `BEGIN IMMEDIATE`, and the partial unique index is the backstop. An 8-way race test covers it.
- **Slot taken.** If another caller got the slot, the rejection carries two alternatives the agent can offer, so it recovers in one turn.
- **`wrong_kind` rejection.** The adversarial case "changes their mind three times" had the model book an estimate slot as a repair. The server compares the slot's kind with the service type and rejects with alternatives of the right kind. Reschedule enforces the same match.
- **Out-of-area.** `check_service_area` marks the lead `out_of_area`; `check_availability` and `book_appointment` re-check the ZIP and refuse. The prompt cannot be talked past it. `app/path.py` shows this as "stopped", not as a failure.
- **Input cleaning.** Phone must be ten digits and ZIP five. The rejection tells the agent to read the number back in groups. Speech-to-text errors surface here.
- **Two-step callback read-back** (`record_outcome` in `app/outbound.py`). The model skipped the read-back, then set `confirmed: true` without asking. Now a callback time returns `needs_confirmation` with the exact question and saves nothing. The `confirmed` flag only counts if this call already received that question for the same time (`question_was_asked`) and, when Retell sends a transcript, the words "does that work" appear in it.
- **Negotiation** (`app/negotiation.py`, `config/negotiation.json`). Off unless `enabled`. `decide()` checks, in order: enabled, minimum estimate, offers per call, approval threshold, then computes the largest discount the mode allows without crossing the floor. A caller asking for less than the cap gets exactly what they asked for. When no price room is left it offers a non-price concession. Every ask is written to `concessions`.
- **Compliance gate** (`exclusions()` in `app/outbound.py`). An estimate is callable only if it is open, not on the do-not-call list, has written consent, has fewer than 3 attempts, has not been tried in 48 hours, is past any promised callback date, and the customer's local time is inside 8am to 9pm (8pm in FL, OK, MD). Unknown ZIP prefixes are excluded, not guessed. `/outbound/call/{id}` re-checks inside a transaction and counts the attempt before dialing, so a double click cannot place two calls. It is a dry run unless `dry_run=false`. AI disclosure follows `OUTBOUND_DISCLOSURE`, forced upfront in CA and ME (`research/opener-disclosure.md`).
- **Failures return 200.** Retell retries non-2xx and the caller hears dead air. Validation errors, bad JSON and exceptions all return `ok: false` with `FALLBACK` text that tells the agent not to confirm anything.
- **Handoff logging.** Retell's built-in `transfer_call` never reaches this server, so the agent calls `log_handoff` first.

## Deployment

- **Vultr box (primary).** `make live` rsyncs the current commit to `~/summit/app`, runs `uv sync` and restarts the live (:8100) and scratch (:8101) servers. A private Caddy on :8099 routes `/scratch/*` to scratch, and ngrok publishes it on the static domain in `NGROK_URL`. `deploy/vultr/watchdog.sh` runs from cron each minute and restarts any piece that is down, including ngrok after three failed public health checks. All of it runs as jackson in `~/summit`, apart from other services on the same box. `make dev` runs locally with `--reload`.
- **Retell config as code.** `retell/prompt.md`, `llm.json`, `agent.json` (and `retell/outbound/`). `make retell-push` fills in the URLs and updates Retell in place; ids are kept in `retell/.state.json`. The phone number is bound to the `latest` agent version, so a push is live on the next call.
- **In-house voice.** `../voice` replaces Retell as the voice layer and calls the same endpoints.

## Testing layers

- **pytest** (`tests/`, 240-plus tests, offline). Tools, race and idempotency, signature modes, reschedule and cancel, callbacks, metrics, negotiation, outbound rules, CRM adapters against `httpx.MockTransport`, Retell config consistency, and that every case file is well formed. `tests/test_drills.py` guards the drill script.
- **Postman** (`postman/`). The booking path (service area, availability, book, upsert) plus webhook samples in Retell's payload shape, with `pm.test` checks. `make postman` runs it with Newman against the running server; `ENV=scratch` runs it against the scratch server on Vultr. The Postman monitors (daily on, hourly paused) run the same collection against scratch.
- **Retell simulations** (`retell/simulate.py`, `retell/cases*.json`, `retell/outbound/`). Scripted callers against the real prompt and real tools, text only, a few cents per run. Standard cases plus 23 inbound and 17 outbound adversarial personas across two waves. Results append to `../research/progress/triage.md`.
- **Adversarial waves.** Run in small batches, read in full, each failure turned into a fix and a permanent case (`--wave 1|2|all`, `--style plain|real|mumbled`).
- **Drills** (`scripts/drill.py`). Plant a real bug, find it from the console, undo.
- **Replay.** `make replay CALL=<id>`: a recorded call's caller side against the current prompt and tools on a scratch server; exit 1 on divergence.
- **Voice layer** (`../voice/tests`, 23 tests, plus `make audiotest` for the real audio path). Scripted LLM against the real tool server in process.

## Decision models (app/decide.py)

Some server rules are judgments, not lookups: did the caller actually say yes to the read-back, did they just mention a gas smell, is "lost" really lost or "too expensive". Until now each of those was a regex or a flag the LLM set. With `DECISIONS=openai` (or `laya`), a decision model answers a typed question about the caller's last line in about 150 ms and the server acts on the probability:

- `cancel_appointment` and the confirming `request_callback` / `record_outcome` calls refuse when the model is sure the caller did not agree (`needs_clear_yes` / `needs_confirmation` with the caller's words quoted back).
- Every tool result can carry a `notice`: emergency, stop request, or wrong person detected in what the caller just said. The check runs in a thread while the tool runs, so it adds no wall time.
- `record_outcome(lost)` gets a second opinion over the last six lines; a confident `still_deciding` or `price_objection` is stored as `not_now` and the agent is told why.

Every question and answer is logged in `tool_calls.decisions`. Any failure or timeout returns None and the gate behaves as it did before. Thresholds live at the top of `app/decide.py`; `research/decisions/` has the provider comparison and the eval that sets them.
