-- Tables added after the live world was seeded. Every statement is IF NOT EXISTS, so
-- db.add_missing_tables can run this whole file on an existing database at start-up, without the
-- reseed that would wipe its bookings. create_schema runs it after schema.sql.

-- Time a technician is away from jobs: a meeting, training, a block the office put on the
-- schedule. ServiceTitan calls these non-job appointments. One with remove_from_capacity takes the
-- technician out of capacity for any arrival window it overlaps. A block is never deleted here;
-- taking it off the schedule sets active to 0.
CREATE TABLE IF NOT EXISTS non_job_appointments (
    id INTEGER PRIMARY KEY,
    technician_id INTEGER NOT NULL REFERENCES technicians (id),
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    name TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    all_day INTEGER NOT NULL DEFAULT 0,
    remove_from_capacity INTEGER NOT NULL DEFAULT 1,
    show_on_schedule INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 1,
    created_by_id INTEGER NOT NULL,
    created_on TEXT NOT NULL,
    modified_on TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_non_job_start ON non_job_appointments (start);
