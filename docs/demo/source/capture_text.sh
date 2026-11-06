#!/bin/sh
# Writes the terminal panels the demo videos show: call timelines, test runs and mock ServiceTitan
# queries, each as the command followed by its real output. Run from the repo root after
# `make owner-demo` has seeded data/owner-demo/ (docs/demo/README.md).
set -eu

OUT=docs/demo/source/captures
DEMO=data/owner-demo
mkdir -p "$OUT"

run() {
    # run <file> <command...>: the command as typed, then what it printed.
    file=$1
    shift
    printf '$ %s\n' "$*" > "$OUT/$file"
    sh -c "$*" >> "$OUT/$file" 2>&1 || true
}

# Each test run is short and named, so the panel shows exactly what is being proved.
pytest_names() {
    # The header names the files; the lines below name each test that ran.
    echo "\$ uv run pytest -v $(for t in "$@"; do echo "${t%%::*}"; done | sort -u | tr '\n' ' ')"
    uv run pytest -v -p no:cacheprovider --no-header "$@" 2>&1 \
        | grep -E 'PASSED|FAILED|ERROR|passed|failed' | sed 's/ *\[ *[0-9]*%\]$//'
}

# Video 1: the urgent call
run trace-c01.txt "uv run python -m app.trace c01 --db $DEMO/tool.db"
run trace-c05.txt "uv run python -m app.trace c05 --db $DEMO/tool.db"
{
    pytest_names \
        "tests/tool_server/test_ts_priority_caller_words.py::test_the_reason_quotes_the_callers_line_that_raised_it" \
        "tests/tool_server/test_ts_priority_caller_words.py::test_asking_for_urgent_with_no_fact_raises_nothing" \
        "tests/tool_server/test_ts_priority_caller_words.py::test_a_fact_first_given_after_the_wait_raises_and_is_flagged"
} > "$OUT/tests-priority.txt"

# Video 2: gas and carbon monoxide
run trace-c03.txt "uv run python -m app.trace c03 --db $DEMO/tool.db"
{
    pytest_names \
        "tests/tool_server/test_ts_safety.py::test_a_clear_signal_on_any_turn_returns_the_exact_lines_and_flags_a_person" \
        "tests/tool_server/test_ts_safety.py::test_booking_tools_wait_for_the_lines_then_an_insisting_caller_can_book" \
        "tests/tool_server/test_ts_safety.py::test_gas_alone_a_gas_bill_or_a_gas_furnace_triggers_nothing" \
        "tests/tool_server/test_ts_safety.py::test_a_safety_call_that_drops_before_the_lines_gets_a_person_and_a_callback"
} > "$OUT/tests-safety.txt"

# Video 3: the schedule and double-booking
run sql-double-booked.txt "sqlite3 -header -column $DEMO/world.db \"SELECT technician_id, window_date, window_id, COUNT(*) AS jobs FROM appointments WHERE status <> 'Canceled' GROUP BY 1, 2, 3 HAVING COUNT(*) > 1;\" && echo '(no rows: no technician has two jobs in one window)' && sqlite3 $DEMO/world.db \"SELECT COUNT(*) || ' live appointments checked' FROM appointments WHERE status <> 'Canceled';\""
run sql-job-608.txt "sqlite3 -header -column $DEMO/world.db \"SELECT a.job_id, t.name AS technician, a.window_date, a.window_id, a.status FROM appointments a JOIN technicians t ON t.id = a.technician_id WHERE a.job_id = 608;\""
# The duplicate is tried on a throwaway copy, never on the world the owner view is reading.
cp "$DEMO/world.db" "$DEMO/world-copy.db"
run sql-duplicate.txt "sqlite3 $DEMO/world-copy.db \"INSERT INTO appointments (job_id, appointment_number, technician_id, business_unit_id, window_date, window_id, start, end, arrival_window_start, arrival_window_end, status, created_on, modified_on) SELECT job_id, '608-2', technician_id, business_unit_id, window_date, window_id, start, end, arrival_window_start, arrival_window_end, 'Scheduled', created_on, modified_on FROM appointments WHERE job_id = 608;\""
rm -f "$DEMO/world-copy.db"
{
    pytest_names \
        "tests/tool_server/test_ts_booking.py::test_eight_callers_racing_for_the_last_slot_get_it_once" \
        "tests/tool_server/test_ts_booking.py::test_eight_parallel_retries_of_one_booking_book_once" \
        "tests/tool_server/test_ts_booking.py::test_the_same_visit_twice_on_one_call_is_refused" \
        "tests/servicetitan_mock/test_st_booking.py::test_assigning_a_technician_who_is_busy_is_a_conflict"
} > "$OUT/tests-booking.txt"

# Video 4: callbacks and leads
run sql-call-log.txt "sqlite3 -header -column $DEMO/world.db \"SELECT c.sid, c.to_number, substr(c.received_on, 12, 5) AS at_utc, t.name AS tag FROM calls c JOIN call_tags ct ON ct.call_id = c.id JOIN (SELECT 801 AS id, 'Callback resolved' AS name) t ON t.id = ct.tag_type_id WHERE c.direction = 'Outbound' ORDER BY c.id;\""
run sql-callbacks.txt "sqlite3 -header -column $DEMO/tool.db \"SELECT call_id, phone, owner, status, substr(reason, 1, 44) AS reason FROM callbacks ORDER BY id;\""
{
    pytest_names \
        "tests/tool_server/test_ts_callback_done.py::test_a_call_back_from_the_office_tagged_resolved_marks_the_callback_done" \
        "tests/tool_server/test_ts_callback_done.py::test_a_voicemail_or_no_answer_is_no_contact_and_never_marks_the_callback_done" \
        "tests/tool_server/test_ts_callback_done.py::test_the_owner_view_counts_the_callback_done_only_after_the_log_shows_it"
} > "$OUT/tests-callbacks.txt"

# Video 5: rules, hours, area and prices. Test folders that share module names are collected one
# folder per run.
{
    pytest_names \
        "tests/tool_server/test_ts_rules.py::test_a_draft_is_not_live_until_the_gate_passes" \
        "tests/tool_server/test_ts_rules.py::test_priority_brackets_edited_downward_are_refused" \
        "tests/tool_server/test_ts_rules.py::test_a_read_back_rewording_that_drops_the_home_check_is_refused"
} > "$OUT/tests-rules.txt"
{
    pytest_names \
        "tests/servicetitan_mock/test_st_platform.py::test_summit_air_leaves_fees_unset_so_the_agent_never_quotes_one"
    pytest_names \
        "tests/voice_retell/test_rt_native.py::test_the_knowledge_base_has_all_five_boroughs_and_no_prices"
} > "$OUT/tests-prices.txt"

# The live Retell agent's prompt, as snapshotted on its branch (DECISIONS.md 112).
run retell-prompt-prices.txt "git show origin/joco/retell-agent:retell/summit-air-jackson/prompt.md | grep -n -i -E 'price|fee'"

echo "wrote $(ls "$OUT" | wc -l | tr -d ' ') captures to $OUT"
