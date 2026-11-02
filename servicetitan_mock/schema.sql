-- All timestamps are UTC, formatted YYYY-MM-DDTHH:MM:SSZ, so they sort as text.
-- window_date is the local calendar date the arrival window starts on.

CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('Residential', 'Commercial')),
    active INTEGER NOT NULL DEFAULT 1,
    created_on TEXT NOT NULL,
    modified_on TEXT NOT NULL
);

CREATE TABLE locations (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers (id),
    name TEXT NOT NULL,
    street TEXT NOT NULL,
    street_key TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    unit_key TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT '',
    zip TEXT NOT NULL,
    county TEXT,
    zone_id INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    created_on TEXT NOT NULL,
    modified_on TEXT NOT NULL
);
CREATE INDEX ix_locations_customer ON locations (customer_id);
CREATE INDEX ix_locations_address ON locations (street_key, zip);

-- A contact belongs to a customer, and optionally to one of its locations.
CREATE TABLE contacts (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers (id),
    location_id INTEGER REFERENCES locations (id),
    type TEXT NOT NULL CHECK (type IN ('Phone', 'MobilePhone', 'Email')),
    value TEXT NOT NULL,
    phone_key TEXT NOT NULL DEFAULT '',
    role TEXT,
    name TEXT,
    memo TEXT NOT NULL DEFAULT '',
    created_on TEXT NOT NULL
);
CREATE INDEX ix_contacts_customer ON contacts (customer_id);
CREATE INDEX ix_contacts_phone ON contacts (phone_key);

CREATE TABLE bookings (
    id INTEGER PRIMARY KEY,
    provider_id INTEGER NOT NULL,
    external_id TEXT,
    source TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'New',
    customer_id INTEGER REFERENCES customers (id),
    name TEXT NOT NULL,
    street TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT '',
    zip TEXT NOT NULL DEFAULT '',
    contacts_json TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'Normal',
    requested_start TEXT,
    business_unit_id INTEGER,
    job_type_id INTEGER,
    campaign_id INTEGER,
    created_on TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_bookings_external ON bookings (provider_id, external_id)
    WHERE external_id IS NOT NULL;

-- Replacement and install interest, and the one-line commercial record.
CREATE TABLE leads (
    id INTEGER PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'Open',
    kind TEXT NOT NULL,
    customer_type TEXT NOT NULL CHECK (customer_type IN ('Residential', 'Commercial')),
    customer_id INTEGER REFERENCES customers (id),
    name TEXT NOT NULL,
    phone TEXT NOT NULL DEFAULT '',
    phone_key TEXT NOT NULL DEFAULT '',
    street TEXT NOT NULL DEFAULT '',
    zip TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL,
    campaign_id INTEGER,
    created_on TEXT NOT NULL
);

-- status is what was stored. The API reads it together with to_date; see memberships.py.
CREATE TABLE memberships (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers (id),
    location_id INTEGER NOT NULL REFERENCES locations (id),
    membership_type_id INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('Active', 'Expired', 'Canceled')),
    from_date TEXT NOT NULL,
    to_date TEXT NOT NULL,
    memo TEXT NOT NULL DEFAULT '',
    created_on TEXT NOT NULL,
    modified_on TEXT NOT NULL
);
CREATE INDEX ix_memberships_customer ON memberships (customer_id);
CREATE INDEX ix_memberships_location ON memberships (location_id);

CREATE TABLE recurring_service_events (
    id INTEGER PRIMARY KEY,
    membership_id INTEGER NOT NULL REFERENCES memberships (id),
    location_id INTEGER NOT NULL REFERENCES locations (id),
    name TEXT NOT NULL,
    date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('NotAttempted', 'Scheduled', 'Completed')),
    job_id INTEGER
);

CREATE TABLE installed_equipment (
    id INTEGER PRIMARY KEY,
    location_id INTEGER NOT NULL REFERENCES locations (id),
    customer_id INTEGER NOT NULL REFERENCES customers (id),
    name TEXT NOT NULL,
    system_type TEXT NOT NULL,
    manufacturer TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    installed_on TEXT NOT NULL,
    memo TEXT NOT NULL DEFAULT ''
);
CREATE INDEX ix_equipment_location ON installed_equipment (location_id);

CREATE TABLE technicians (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    business_unit_id INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    phone TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    home_zone_id INTEGER,
    skills_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE technician_shifts (
    id INTEGER PRIMARY KEY,
    technician_id INTEGER NOT NULL REFERENCES technicians (id),
    shift_type TEXT NOT NULL CHECK (shift_type IN ('Normal', 'OnCall')),
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX ix_shifts_start ON technician_shifts (start);

CREATE TABLE urgent_holds (
    window_date TEXT NOT NULL,
    window_id TEXT NOT NULL,
    business_unit_id INTEGER NOT NULL,
    held INTEGER NOT NULL,
    PRIMARY KEY (window_date, window_id, business_unit_id)
);

CREATE TABLE jobs (
    id INTEGER PRIMARY KEY,
    job_number TEXT NOT NULL,
    customer_id INTEGER NOT NULL REFERENCES customers (id),
    location_id INTEGER NOT NULL REFERENCES locations (id),
    business_unit_id INTEGER NOT NULL,
    job_type_id INTEGER NOT NULL,
    priority TEXT NOT NULL,
    campaign_id INTEGER,
    summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('Scheduled', 'Dispatched', 'Working', 'Done', 'Canceled')),
    cancel_reason_id INTEGER,
    completed_on TEXT,
    created_on TEXT NOT NULL,
    modified_on TEXT NOT NULL
);
CREATE INDEX ix_jobs_customer ON jobs (customer_id);
CREATE INDEX ix_jobs_location ON jobs (location_id);

CREATE TABLE appointments (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs (id),
    appointment_number TEXT NOT NULL,
    technician_id INTEGER NOT NULL REFERENCES technicians (id),
    business_unit_id INTEGER NOT NULL,
    window_date TEXT NOT NULL,
    window_id TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    arrival_window_start TEXT NOT NULL,
    arrival_window_end TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('Scheduled', 'Dispatched', 'Working', 'Done', 'Canceled')),
    created_on TEXT NOT NULL,
    modified_on TEXT NOT NULL
);
CREATE INDEX ix_appointments_job ON appointments (job_id);
CREATE INDEX ix_appointments_window ON appointments (window_date, window_id);
-- The rule that makes double booking impossible: one live appointment per technician per window.
CREATE UNIQUE INDEX ux_appointments_slot ON appointments (technician_id, window_date, window_id)
    WHERE status <> 'Canceled';

CREATE TABLE job_history (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs (id),
    date TEXT NOT NULL,
    event_type TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT ''
);
CREATE INDEX ix_history_job ON job_history (job_id);

CREATE TABLE calls (
    id INTEGER PRIMARY KEY,
    external_id TEXT UNIQUE,
    direction TEXT NOT NULL DEFAULT 'Inbound',
    from_number TEXT NOT NULL,
    from_key TEXT NOT NULL,
    to_number TEXT NOT NULL DEFAULT '',
    received_on TEXT NOT NULL,
    duration INTEGER NOT NULL DEFAULT 0,
    call_type TEXT NOT NULL DEFAULT 'Unbooked',
    customer_id INTEGER REFERENCES customers (id),
    job_id INTEGER REFERENCES jobs (id),
    reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX ix_calls_from ON calls (from_key, received_on);

CREATE TABLE idempotency (
    key TEXT NOT NULL,
    route TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status INTEGER NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (key, route)
);

CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
