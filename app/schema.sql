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
