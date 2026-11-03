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
    status TEXT NOT NULL CHECK (status IN ('booked', 'queued', 'failed', 'done')),
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
