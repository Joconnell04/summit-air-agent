-- The tool server's own records. The FSM holds customers and jobs; this database holds what the
-- agent did on each call and the state the gates read. Timestamps are UTC ISO strings.

CREATE TABLE IF NOT EXISTS calls (
    id TEXT PRIMARY KEY,
    tenant TEXT NOT NULL,
    caller_id TEXT NOT NULL,
    caller_key TEXT NOT NULL,
    dialed_number TEXT NOT NULL,
    direction TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    end_reason TEXT,
    end_requested INTEGER NOT NULL DEFAULT 0,
    after_hours INTEGER NOT NULL DEFAULT 0,
    linked_call_id TEXT,
    outcome TEXT,
    commercial INTEGER NOT NULL DEFAULT 0,
    -- The technician's summary (app/summary.py): none, pending (the call ended), written (not in
    -- the FSM yet), done, failed, or skipped (nothing was booked).
    summary_status TEXT NOT NULL DEFAULT 'none',
    turn_seq INTEGER NOT NULL DEFAULT 0,
    -- Safety: none, checking (the check question was asked), signal (lines not said yet),
    -- lines_said, cleared (a vague mention the caller said no to).
    safety_state TEXT NOT NULL DEFAULT 'none',
    safety_kind TEXT,
    safety_signal_at TEXT,
    safety_lines_at TEXT,
    safety_flag_id INTEGER,
    safety_variant INTEGER NOT NULL DEFAULT 0,
    -- Read-back: what was read back, the turn it was asked on, and when the caller confirmed.
    readback_json TEXT,
    readback_asked_seq INTEGER,
    readback_confirmed_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_calls_caller ON calls (caller_key, started_at);
-- The summary job reads waiting calls oldest first; this keeps that read to the waiting rows.
CREATE INDEX IF NOT EXISTS ix_calls_summary ON calls (tenant, summary_status, ended_at, id);

-- Customers matched on a call. Reads and writes are scoped to these.
CREATE TABLE IF NOT EXISTS call_customers (
    call_id TEXT NOT NULL REFERENCES calls (id) ON DELETE CASCADE,
    customer_id INTEGER NOT NULL,
    matched_by TEXT NOT NULL,
    customer_type TEXT NOT NULL,
    PRIMARY KEY (call_id, customer_id)
);

CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY,
    call_id TEXT NOT NULL REFERENCES calls (id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    speaker TEXT NOT NULL CHECK (speaker IN ('caller', 'agent')),
    text TEXT NOT NULL,
    at TEXT NOT NULL,
    timings_json TEXT NOT NULL DEFAULT '{}',
    safety_action TEXT
);
CREATE INDEX IF NOT EXISTS ix_turns_call ON turns (call_id, seq);

-- Bookings, reschedules and cancels asked for on a call. A booking the FSM could not take is kept
-- as queued and retried with the same key, so a retry never books twice.
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY,
    call_id TEXT NOT NULL REFERENCES calls (id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('book', 'reschedule', 'cancel')),
    -- sending: the live request is talking to the FSM; the retry job leaves it alone.
    status TEXT NOT NULL CHECK (status IN ('sending', 'booked', 'queued', 'failed', 'done')),
    fsm_key TEXT NOT NULL UNIQUE,
    request_json TEXT NOT NULL,
    fsm_job_id INTEGER,
    fsm_appointment_id INTEGER,
    customer_id INTEGER,
    window_start TEXT,
    window_end TEXT,
    job_type TEXT,
    priority TEXT,
    priority_reasons TEXT NOT NULL DEFAULT '',
    safety_override INTEGER NOT NULL DEFAULT 0,
    seek_earlier INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    turn_seq INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_bookings_call ON bookings (call_id);
CREATE INDEX IF NOT EXISTS ix_bookings_status ON bookings (status);

-- The turn when the agent first offered a window or named the wait on a call (find_slots answered).
-- A priority raised by a fact the caller first gave after it is flagged for the dispatcher
-- (raised_after_wait in the booking's request, DECISIONS.md 072); nothing is lowered.
CREATE TABLE IF NOT EXISTS waits (
    call_id TEXT PRIMARY KEY REFERENCES calls (id) ON DELETE CASCADE,
    turn_seq INTEGER NOT NULL,
    at TEXT NOT NULL
);

-- A line the server hands out to be said once a call (the top-priority line), recorded when it is
-- handed out, so two tool calls in one caller turn can't both carry it (PR #139 review).
CREATE TABLE IF NOT EXISTS lines_handed (
    call_id TEXT NOT NULL REFERENCES calls (id) ON DELETE CASCADE,
    line TEXT NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (call_id, line)
);

CREATE TABLE IF NOT EXISTS flags (
    id INTEGER PRIMARY KEY,
    call_id TEXT REFERENCES calls (id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    reason TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'Normal',
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL
);

-- Every promised callback has a time and an owner, and is tracked until done (spec S-32).
CREATE TABLE IF NOT EXISTS callbacks (
    id INTEGER PRIMARY KEY,
    call_id TEXT REFERENCES calls (id) ON DELETE CASCADE,
    phone TEXT NOT NULL,
    phone_key TEXT NOT NULL,
    due_at TEXT NOT NULL,
    owner TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'done', 'cancelled', 'overdue')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_callbacks_phone ON callbacks (phone_key, status);

CREATE TABLE IF NOT EXISTS do_not_call (
    phone_key TEXT PRIMARY KEY,
    phone TEXT NOT NULL,
    call_id TEXT,
    added_at TEXT NOT NULL
);

-- Out-of-area calls by ZIP, so demand outside the area can inform a decision to expand (S-28).
CREATE TABLE IF NOT EXISTS out_of_area (
    id INTEGER PRIMARY KEY,
    call_id TEXT REFERENCES calls (id) ON DELETE CASCADE,
    zip TEXT NOT NULL,
    at TEXT NOT NULL,
    UNIQUE (call_id, zip)
);

CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY,
    call_id TEXT REFERENCES calls (id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('stored', 'queued', 'failed')),
    fsm_key TEXT NOT NULL UNIQUE,
    request_json TEXT NOT NULL,
    fsm_lead_id INTEGER,
    callback_id INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL
);

-- Notes the agent added. Job notes made before the booking go into the job summary.
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    call_id TEXT REFERENCES calls (id) ON DELETE CASCADE,
    attach_to TEXT NOT NULL CHECK (attach_to IN ('customer', 'job')),
    customer_id INTEGER,
    fsm_note_id INTEGER,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- The technician's summary for a booked call, written after the call (spec S-16). lines_json holds
-- [label, text] pairs as pinned to the job; dropped_json the lines the server refused, and why.
CREATE TABLE IF NOT EXISTS summaries (
    call_id TEXT PRIMARY KEY REFERENCES calls (id) ON DELETE CASCADE,
    booking_id INTEGER REFERENCES bookings (id) ON DELETE CASCADE,
    lines_json TEXT,
    dropped_json TEXT NOT NULL DEFAULT '[]',
    model_attempts INTEGER NOT NULL DEFAULT 0,
    post_attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    cost_usd REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- One row per request, beside the JSON log line, so the owner view and traces can query them.
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY,
    call_id TEXT,
    request_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    args_json TEXT NOT NULL,
    ok INTEGER NOT NULL,
    refused TEXT,
    gate TEXT,
    say TEXT NOT NULL DEFAULT '',
    ms REAL NOT NULL,
    error TEXT,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tool_calls_call ON tool_calls (call_id);

-- A retried write with the same Idempotency-Key gets the first result back.
CREATE TABLE IF NOT EXISTS idempotency (
    key TEXT NOT NULL,
    tool TEXT NOT NULL,
    call_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (key, tool)
);

CREATE TABLE IF NOT EXISTS heartbeats (
    worker TEXT PRIMARY KEY,
    at TEXT NOT NULL
);

-- Calls the system places (journey 12): an earlier opening offered to a flagged urgent booking, or
-- a callback the caller asked for. A callback waits until its time; the voice layer claims a
-- queued one, dials it and reports back. One attempt each (spec S-42).
CREATE TABLE IF NOT EXISTS outbound (
    id INTEGER PRIMARY KEY,
    tenant TEXT NOT NULL,
    call_id TEXT REFERENCES calls (id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('earlier_slot', 'callback')),
    phone TEXT NOT NULL,
    booking_id INTEGER REFERENCES bookings (id) ON DELETE CASCADE,
    callback_id INTEGER REFERENCES callbacks (id) ON DELETE CASCADE,
    offer_json TEXT,
    due_at TEXT NOT NULL,
    -- waiting (a callback before its time), queued, dialing, done, skipped (not allowed to dial).
    status TEXT NOT NULL CHECK (status IN ('waiting', 'queued', 'dialing', 'done', 'skipped')),
    -- accepted, declined, answered, no_answer, voicemail, no_report, or why it was skipped.
    result TEXT,
    placed_call_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_outbound_status ON outbound (status, due_at);

-- Whether a booking was still on the schedule when its window came (booked-and-kept, C-42).
CREATE TABLE IF NOT EXISTS kept (
    booking_id INTEGER PRIMARY KEY REFERENCES bookings (id) ON DELETE CASCADE,
    kept INTEGER NOT NULL,
    fsm_status TEXT NOT NULL,
    checked_at TEXT NOT NULL
);

-- Email alerts to the on-call person for urgent flags raised after hours. Held when no mail server
-- is set, so development never sends mail and the owner view still shows what would have gone.
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    flag_id INTEGER NOT NULL UNIQUE REFERENCES flags (id) ON DELETE CASCADE,
    to_address TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('sent', 'held', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- What the owner told Revin was wrong on a call (story O-8), the one thing the owner view writes.
-- Filed as a GitHub issue when REPORTS_GITHUB_TOKEN and REPORTS_GITHUB_REPO are set; held
-- otherwise, the way alert email is held without a mail server.
CREATE TABLE IF NOT EXISTS owner_reports (
    id INTEGER PRIMARY KEY,
    call_id TEXT NOT NULL REFERENCES calls (id) ON DELETE CASCADE,
    turn_seq INTEGER,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'held' CHECK (status IN ('held', 'filed', 'failed')),
    issue_number INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_owner_reports_call ON owner_reports (call_id);
-- The latest outside temperature per tenant, for priority (app/weather.py).
CREATE TABLE IF NOT EXISTS weather (
    tenant TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    now_f REAL,
    low_f REAL
);

-- How a callback call the system placed went, and whether that reached ServiceTitan as a tag on
-- the call (docs/decisions/26-callback-done.md). outcome is resolved, open (answered, but the
-- person or the reason was not confirmed) or no_contact (no answer, voicemail, failed). The tag
-- is retried until it lands.
CREATE TABLE IF NOT EXISTS call_outcomes (
    outbound_id INTEGER PRIMARY KEY REFERENCES outbound (id) ON DELETE CASCADE,
    sid TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('resolved', 'open', 'no_contact')),
    why TEXT NOT NULL,
    tagged INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- When the call log was last read for a callback, so a backlog of unreachable callbacks cannot keep
-- the newer ones from being checked (jobs.confirm_callbacks takes the least recently checked).
CREATE TABLE IF NOT EXISTS callback_checks (
    callback_id INTEGER PRIMARY KEY REFERENCES callbacks (id) ON DELETE CASCADE,
    checked_at TEXT NOT NULL
);

-- The owner view's search index (app/search.py): one row per thing a person might look for on a
-- call (the call, its job, its flags, its leads and callbacks, each line said); extra holds words
-- that should match but are not shown, such as a phone number's bare digits. search_indexed holds
-- a fingerprint of each call when it was indexed, so only calls that changed are indexed again.
CREATE VIRTUAL TABLE IF NOT EXISTS search_docs USING fts5(
    tenant UNINDEXED,
    call_id UNINDEXED,
    kind UNINDEXED,
    anchor UNINDEXED,
    at UNINDEXED,
    label UNINDEXED,
    title,
    body,
    extra,
    tokenize = 'unicode61 remove_diacritics 2',
    prefix = '2 3'
);
CREATE INDEX IF NOT EXISTS ix_flags_call ON flags (call_id);
CREATE INDEX IF NOT EXISTS ix_leads_call ON leads (call_id);
CREATE INDEX IF NOT EXISTS ix_callbacks_call ON callbacks (call_id);
CREATE TABLE IF NOT EXISTS search_indexed (
    call_id TEXT PRIMARY KEY,
    tenant TEXT NOT NULL,
    signature TEXT NOT NULL
);

-- Owner edits to how the agent works (app/rules.py, exploration 009): the editable wording lines
-- and guidance by call stage, as whole versions over the tenant's YAML defaults. Rows are never
-- deleted: a rollback is a new published row that copies an older one (rollback_of is its
-- number). A draft goes live only through the eval gate, whose result is kept in gate_json.
CREATE TABLE IF NOT EXISTS rule_versions (
    id INTEGER PRIMARY KEY,
    tenant TEXT NOT NULL,
    number INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'checking', 'published', 'blocked', 'discarded')),
    content_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    parent_id INTEGER REFERENCES rule_versions (id),
    rollback_of INTEGER,
    author TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    published_by TEXT,
    -- The live version's number when the draft was sent to the gate (0 for the defaults).
    base_live INTEGER,
    gate_json TEXT,
    gate_at TEXT,
    published_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (tenant, number)
);
CREATE INDEX IF NOT EXISTS ix_rule_versions_live ON rule_versions (tenant, status, published_at);

-- The stages a call reached, as the server saw them (app/rules.py stage_now), and the rule version
-- it started under, so a bad call traces to an edit.
CREATE TABLE IF NOT EXISTS call_stages (
    call_id TEXT NOT NULL REFERENCES calls (id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    version_id INTEGER REFERENCES rule_versions (id),
    at TEXT NOT NULL,
    -- 1 once the owner's guidance for this stage went to the model as a [server] note.
    noted INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (call_id, stage)
);

-- Warm transfers to a person (C-11), at most one per call. The flag and callback the transfer
-- stands in for are stored before anyone is dialed, so a transfer nobody answers still ends in a
-- named callback; a connected one marks them done.
CREATE TABLE IF NOT EXISTS transfers (
    id INTEGER PRIMARY KEY,
    call_id TEXT NOT NULL UNIQUE REFERENCES calls (id) ON DELETE CASCADE,
    flag_id INTEGER NOT NULL REFERENCES flags (id) ON DELETE CASCADE,
    callback_id INTEGER REFERENCES callbacks (id) ON DELETE SET NULL,
    role TEXT NOT NULL,
    -- What the person answering hears before they take the call, kept for the record.
    summary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'dialing'
        CHECK (status IN ('dialing', 'connected', 'no_answer', 'not_accepted', 'failed')),
    started_at TEXT NOT NULL,
    finished_at TEXT
);

-- Cold handoffs to a person (app/transfer.py): a caller who demands a person is sent straight to the
-- role's number by a SIP transfer, with no summary and no key press. A call has at most one
-- transfer of either kind. handed_off means the call went to the number, not that a person
-- answered, so the flag and callback stay open; not_dialed is a demand heard with no number set.
CREATE TABLE IF NOT EXISTS handoffs (
    id INTEGER PRIMARY KEY,
    call_id TEXT NOT NULL UNIQUE REFERENCES calls (id) ON DELETE CASCADE,
    flag_id INTEGER NOT NULL REFERENCES flags (id) ON DELETE CASCADE,
    callback_id INTEGER REFERENCES callbacks (id) ON DELETE SET NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'dialing'
        CHECK (status IN ('dialing', 'handed_off', 'failed', 'not_dialed')),
    started_at TEXT NOT NULL,
    finished_at TEXT
);

-- Call audio (story O-6). The voice layer records the call to a bucket; the jobs loop copies the
-- file to the server's data folder and removes it from the bucket. Deleted with the transcript.
CREATE TABLE IF NOT EXISTS recordings (
    call_id TEXT PRIMARY KEY REFERENCES calls (id) ON DELETE CASCADE,
    object_key TEXT NOT NULL,
    egress_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'recording'
        CHECK (status IN ('recording', 'stored', 'missing', 'deleted')),
    path TEXT,
    bytes INTEGER,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Owner view accounts (app/accounts.py). A removed user keeps its row, so the name on a rule
-- version or a settings change still reads; only one live user can hold a username.
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'staff')),
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    removed_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_live ON users (username) WHERE removed_at IS NULL;

-- A signed-in browser. The cookie holds a random token; only its SHA-256 is kept here, so a copy
-- of the database can't be used to sign in.
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions (user_id);

-- Who added, removed or changed the role of a user, and when.
CREATE TABLE IF NOT EXISTS user_changes (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id),
    action TEXT NOT NULL CHECK (action IN ('added', 'removed', 'role', 'password')),
    detail TEXT NOT NULL DEFAULT '',
    by_name TEXT NOT NULL,
    at TEXT NOT NULL
);

-- Owner settings (app/owner_settings.py), one row per change, never edited or deleted: the
-- current value of a setting is its newest row, or the default when it has none.
CREATE TABLE IF NOT EXISTS setting_changes (
    id INTEGER PRIMARY KEY,
    tenant TEXT NOT NULL,
    key TEXT NOT NULL,
    value INTEGER NOT NULL CHECK (value IN (0, 1)),
    author TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_setting_changes ON setting_changes (tenant, key, id);

-- The settings a call started under, so a change mid-call never flips it halfway and every
-- later step (recording, transfer, summary, alert) reads the same values.
CREATE TABLE IF NOT EXISTS call_settings (
    call_id TEXT PRIMARY KEY REFERENCES calls (id) ON DELETE CASCADE,
    settings_json TEXT NOT NULL
);

-- A caller who said not to be recorded. The jobs loop deletes the file from the bucket instead of
-- keeping it, whatever the voice layer did.
CREATE TABLE IF NOT EXISTS recording_declines (
    call_id TEXT PRIMARY KEY REFERENCES calls (id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    at TEXT NOT NULL
);

-- Speaking rate (app/pace.py, DECISIONS.md 053). The server decides the pace from the caller's
-- lines and the worker applies it. One row per call: the speed in effect and what set it (default,
-- record, linked, request, repeats, older_adult). `requested` is 1 once the caller asked for a pace,
-- so their age no longer changes it; `address_speed` is a slower pace asked for the address alone,
-- used by the next read-back and then cleared; `repeats` counts "sorry, what?" since the last step.
CREATE TABLE IF NOT EXISTS call_pace (
    call_id TEXT PRIMARY KEY REFERENCES calls (id) ON DELETE CASCADE,
    speed REAL NOT NULL,
    source TEXT NOT NULL,
    requested INTEGER NOT NULL DEFAULT 0,
    older_adult INTEGER NOT NULL DEFAULT 0,
    address_speed REAL,
    repeats INTEGER NOT NULL DEFAULT 0
);

-- Every change of pace on a call, after the turn numbered `seq` (0 for the pace a call starts at).
-- `scope` is call (the rest of the call) or address (the next read-back's address only).
CREATE TABLE IF NOT EXISTS pace_changes (
    id INTEGER PRIMARY KEY,
    call_id TEXT NOT NULL REFERENCES calls (id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    speed REAL NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('call', 'address')),
    source TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_pace_changes_call ON pace_changes (call_id, seq);

-- The pace a caller asked for, kept for their number so their next call starts at it. Asking for
-- the normal pace removes it.
CREATE TABLE IF NOT EXISTS caller_pace (
    tenant TEXT NOT NULL,
    phone_key TEXT NOT NULL,
    speed REAL NOT NULL,
    call_id TEXT,
    set_at TEXT NOT NULL,
    PRIMARY KEY (tenant, phone_key)
);

-- Addresses looked up with find_address (app/geocode.py). `status` is one, several, none or
-- unavailable; `zips_json` the ZIPs found. A ZIP from a `one` lookup counts as known for the ZIP
-- gate (tools._zip_known), since the house and street came from the caller's own lines.
CREATE TABLE IF NOT EXISTS address_lookups (
    id INTEGER PRIMARY KEY,
    call_id TEXT NOT NULL REFERENCES calls (id) ON DELETE CASCADE,
    house TEXT NOT NULL,
    street TEXT NOT NULL,
    hint TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    zips_json TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_address_lookups_call ON address_lookups (call_id);

-- Buildings find_address found on a call with more than one home in them (MapPLUTO's residential
-- units), so read_back asks once for the unit when none was given.
CREATE TABLE IF NOT EXISTS buildings (
    call_id TEXT NOT NULL REFERENCES calls (id) ON DELETE CASCADE,
    house TEXT NOT NULL,
    street TEXT NOT NULL,
    units INTEGER NOT NULL,
    PRIMARY KEY (call_id, house, street)
);

-- A ZIP the caller gave that the lookup of their street disagrees with. The agent asked which is
-- right after the turn numbered `asked_seq`; the caller's ZIP counts again only once they say it
-- after that question (tools._zip_mismatch).
CREATE TABLE IF NOT EXISTS address_checks (
    call_id TEXT NOT NULL REFERENCES calls (id) ON DELETE CASCADE,
    house TEXT NOT NULL,
    street TEXT NOT NULL,
    said_zip TEXT NOT NULL,
    found_zip TEXT NOT NULL,
    asked_seq INTEGER NOT NULL,
    asks INTEGER NOT NULL DEFAULT 1,
    settled_at TEXT,
    at TEXT NOT NULL,
    PRIMARY KEY (call_id, house, street, said_zip)
);

-- What the caller asked to be called ("call me Dr. Patel"), from their own words
-- (app/address_as.py). The latest line wins. `saved_at` is when it went onto the customer record
-- as a note, after the call.
CREATE TABLE IF NOT EXISTS call_names (
    call_id TEXT PRIMARY KEY REFERENCES calls (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    seq INTEGER NOT NULL,
    source TEXT NOT NULL,
    at TEXT NOT NULL,
    saved_at TEXT
);

-- The name and address check before a change for a caller whose number isn't on the customer's
-- account (tools._identity_gate). `status` is asked, verified or mismatch; `asked_seq` the turn the
-- question was last asked on. After a mismatch the callback the caller accepts goes to
-- `on_file_phone`, the account's own number, and `callback_id` is that callback.
CREATE TABLE IF NOT EXISTS identity_checks (
    call_id TEXT NOT NULL REFERENCES calls (id) ON DELETE CASCADE,
    customer_id INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('asked', 'verified', 'mismatch')),
    asked_seq INTEGER NOT NULL,
    asks INTEGER NOT NULL DEFAULT 1,
    on_file_phone TEXT NOT NULL DEFAULT '',
    callback_id INTEGER,
    at TEXT NOT NULL,
    PRIMARY KEY (call_id, customer_id)
);

-- A customer who moved (tools.record_move). In the area: the new location, the old ones made
-- inactive and the open jobs moved to the new one. Out of the area: nothing changes but a note,
-- and the open jobs at the old address are cancelled. Job ids are JSON lists.
CREATE TABLE IF NOT EXISTS address_moves (
    id INTEGER PRIMARY KEY,
    call_id TEXT NOT NULL REFERENCES calls (id) ON DELETE CASCADE,
    customer_id INTEGER NOT NULL,
    old_location_ids TEXT NOT NULL,
    new_location_id INTEGER,
    new_location_made TEXT NOT NULL DEFAULT '',
    street TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    zip TEXT NOT NULL,
    in_area INTEGER NOT NULL,
    jobs_moved TEXT NOT NULL DEFAULT '[]',
    jobs_cancelled TEXT NOT NULL DEFAULT '[]',
    jobs_for_person TEXT NOT NULL DEFAULT '[]',
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_address_moves_call ON address_moves (call_id);

-- A heads-up call to the customer's number on file after a change made from another number
-- (app/heads_up.py). queued waits for the call to end; dialing, then sent with `result` answered,
-- voicemail or no_answer; would_call when the number isn't on HEADSUP_ALLOWLIST (never dialed);
-- skipped for a do-not-call number. `line` is the sentence said once.
CREATE TABLE IF NOT EXISTS heads_ups (
    id INTEGER PRIMARY KEY,
    tenant TEXT NOT NULL,
    call_id TEXT NOT NULL REFERENCES calls (id) ON DELETE CASCADE,
    customer_id INTEGER NOT NULL,
    job_id INTEGER,
    phone TEXT NOT NULL,
    line TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'dialing', 'sent', 'would_call', 'skipped')),
    result TEXT,
    placed_call_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_heads_ups_status ON heads_ups (status);

-- A caller who asked the agent to wait (app/dead_air.py, DECISIONS.md 081): the turn they asked on,
-- and the turn they came back on (NULL while the hold lasts).
CREATE TABLE IF NOT EXISTS holds (
    call_id TEXT NOT NULL REFERENCES calls (id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    at TEXT NOT NULL,
    back_seq INTEGER,
    PRIMARY KEY (call_id, seq)
);

-- The electrical route (app/safety.py electrical_line, DECISIONS.md 082): the caller's line that
-- reported sparks or an electrical smell, and when the tenant's electrical lines were said. Kept
-- apart from the calls table's safety columns, which this route never sets.
CREATE TABLE IF NOT EXISTS electrical (
    call_id TEXT PRIMARY KEY REFERENCES calls (id) ON DELETE CASCADE,
    heard_seq INTEGER NOT NULL,
    heard_line TEXT NOT NULL,
    said_at TEXT
);

-- Every change a person made to ServiceTitan from the owner view or the bench (app/schedule.py),
-- with who made it, whether ServiceTitan took it, and the time it says the record changed. Never
-- edited or deleted. A refused edit is kept too, with the reason.
CREATE TABLE IF NOT EXISTS schedule_edits (
    id INTEGER PRIMARY KEY,
    tenant TEXT NOT NULL,
    at TEXT NOT NULL,
    user_id INTEGER,
    author TEXT NOT NULL,
    role TEXT NOT NULL,
    surface TEXT NOT NULL CHECK (surface IN ('owner', 'bench')),
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    detail TEXT NOT NULL,
    ok INTEGER NOT NULL CHECK (ok IN (0, 1)),
    error TEXT,
    -- What kind of refusal, when the page offers a next step for it (phone_taken, pick_customer).
    code TEXT,
    -- For pick_customer: the add-job request and the customers who share the number, so the page
    -- can ask which one without the number in the URL.
    pending_json TEXT,
    fsm_modified_on TEXT
);
CREATE INDEX IF NOT EXISTS ix_schedule_edits ON schedule_edits (tenant, id);

-- A reset of the live world (scripts/reset_live_world.py): the mock ServiceTitan was reseeded, so
-- the customer, location and job ids recorded before it point at nothing. A call never picks up
-- one that started before the latest reset (lifecycle._linked).
CREATE TABLE IF NOT EXISTS world_resets (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL
);

-- The name a visit goes under, when the agent asked for it before the read-back and passed it to
-- read_back (Jackson's Retell agent asks at the start of the call): the read-back then doesn't
-- ask again, and a booking with no details.name uses it.
CREATE TABLE IF NOT EXISTS booking_names (
    call_id TEXT PRIMARY KEY REFERENCES calls (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    at TEXT NOT NULL
);

-- A kept check that found no job in ServiceTitan (a reseeded or reset world): tried again later,
-- further apart each time, and given up after a few (jobs.check_kept). The eval server asked for
-- one missing job about 30 times a minute without it.
CREATE TABLE IF NOT EXISTS kept_misses (
    booking_id INTEGER PRIMARY KEY REFERENCES bookings (id) ON DELETE CASCADE,
    attempts INTEGER NOT NULL,
    next_at TEXT NOT NULL
);

-- Every result on a server-led instance (app/flow.py), with the caller turn it answered, so the
-- gates can tell what was said after it.
CREATE TABLE IF NOT EXISTS flow_results (
    id INTEGER PRIMARY KEY,
    call_id TEXT NOT NULL REFERENCES calls (id) ON DELETE CASCADE,
    tool TEXT NOT NULL,
    ok INTEGER NOT NULL,
    say TEXT NOT NULL,
    turn_seq INTEGER NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_flow_results_call ON flow_results (call_id);

-- The failure grader (app/failure_grader.py): one grade per ended call, kept so a call is never
-- graded twice unless forced. failure is the server's decision on the model's answer (raw_json);
-- dropped says why a failure the model gave was not kept. A row with an error is graded again.
CREATE TABLE IF NOT EXISTS grades (
    call_id TEXT PRIMARY KEY REFERENCES calls (id) ON DELETE CASCADE,
    tenant TEXT NOT NULL,
    failure INTEGER NOT NULL DEFAULT 0,
    kind TEXT,
    severity TEXT,
    reason TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    dropped TEXT,
    raw_json TEXT,
    -- Where the transcript came from: retell (GET /v2/get-call) or server (the turns table).
    source TEXT NOT NULL,
    model_profile TEXT NOT NULL,
    cost_usd REAL NOT NULL DEFAULT 0,
    flag_id INTEGER REFERENCES flags (id) ON DELETE SET NULL,
    error TEXT,
    graded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_grades_tenant ON grades (tenant, failure);

-- A grader run: one call, a time range, or the daily run. Progress is counted as it goes so the
-- pages can show it.
CREATE TABLE IF NOT EXISTS grader_runs (
    id INTEGER PRIMARY KEY,
    tenant TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('call', 'range', 'daily')),
    since TEXT,
    until TEXT,
    call_ids_json TEXT NOT NULL,
    force INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed')),
    total INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    flagged INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    estimate_usd REAL NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    requested_by TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_grader_runs_tenant ON grader_runs (tenant, scope, id);

-- The owner's daily schedule for the grader: on or off, and the local time. Every change is a new
-- row, like setting_changes; the newest row is the current choice.
CREATE TABLE IF NOT EXISTS grader_schedule (
    id INTEGER PRIMARY KEY,
    tenant TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    at_time TEXT NOT NULL,
    author TEXT NOT NULL,
    created_at TEXT NOT NULL
);
