"""The debug bench, round 2 (issue #191): the schedule editor's proof that a block changes what
find_slots offers, cancelling a job from the bench, the call log behind the bench's own sign-in,
the call Summary's outcomes and the Why tab's plain decisions, the System page cut to what the live
call runs, the Questions page, the prompt versions, the evals journey filter and the cost chart."""

import html
import json
import re
import sqlite3

from toolkit import ready_to_book, sign_in

from app import bench_call, schedule


def _post(agent, path: str, **form):
    return agent.client.post(path, data=form, follow_redirects=False)


def _offered(agent, call_id: str) -> set[tuple[str, str]]:
    found = agent.tool("find_slots", call_id, job_type="tune_up")
    assert found["ok"], found
    return {(d["date"], w) for d in found["data"]["days"] for w in d["windows"]}


def _edits(agent) -> list[dict]:
    conn = agent.db()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM schedule_edits ORDER BY id")]
    finally:
        conn.close()


def _openings(page: str) -> int:
    return int(re.search(r"find_slots offers (\d+) opening", page).group(1))


# ---- schedule ------------------------------------------------------------------------------


def test_a_block_from_the_bench_logs_what_find_slots_offered_before_and_after(agent):
    sign_in(agent, "admin")
    call_id = ready_to_book(agent)
    day, window = min(_offered(agent, call_id))
    before = _openings(agent.client.get(f"/bench/schedule?day={day}").text)
    _post(agent, "/owner/schedule/block", day=day, window=window,
          next=f"/bench/schedule?day={day}")
    [edit] = _edits(agent)
    assert edit["ok"] == 1
    assert "find_slots for" in edit["detail"] and f"{before} openings" in edit["detail"]
    after = _openings(agent.client.get(f"/bench/schedule?day={day}").text)
    assert after < before
    assert f"{after} in" in edit["detail"]
    assert (day, window) not in _offered(agent, call_id)  # the agent's own read agrees


def test_an_owner_view_edit_logs_no_find_slots_note(agent):
    sign_in(agent, "admin")
    call_id = ready_to_book(agent)
    day, window = min(_offered(agent, call_id))
    _post(agent, "/owner/schedule/block", day=day, window=window, next="/owner/schedule")
    assert "find_slots" not in _edits(agent)[0]["detail"]


def test_cancelling_a_job_on_the_bench_frees_its_window(agent, mock, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    sign_in(agent, "admin")
    job = schedule.job_detail(agent.app.state.services.fsm["summit-air"],
                              agent.app.state.services.tenants.default, maria["jobId"],
                              agent.app.state.services.clock())
    page = agent.client.get(f"/owner/jobs/{maria['jobId']}?back=/bench/schedule")
    assert "Cancel job" in page.text and 'popovertarget="cancel-job"' in page.text
    day = job["window"].split("|")[0]
    sent = _post(agent, f"/bench/schedule/jobs/{maria['jobId']}/cancel", day=day,
                 next=f"/bench/schedule?day={day}")
    assert sent.status_code == 303 and sent.headers["location"].startswith("/bench/schedule")
    [edit] = _edits(agent)
    assert edit["ok"] == 1 and edit["action"] == "cancel_job"
    assert edit["detail"].startswith(f"Cancelled job {job['number']}")
    conn = sqlite3.connect(mock[0].state.db_path)
    try:
        status = conn.execute("SELECT status FROM jobs WHERE id = ?", (maria["jobId"],)).fetchone()[0]
    finally:
        conn.close()
    assert status == "Canceled"
    again = _post(agent, f"/bench/schedule/jobs/{maria['jobId']}/cancel", day=day)
    assert again.status_code == 303
    assert _edits(agent)[-1]["error"] == "That job is already closed."


def test_only_an_admin_cancels_a_job_and_the_owner_view_offers_no_cancel(agent, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    sign_in(agent, "staff")
    assert _post(agent, f"/bench/schedule/jobs/{maria['jobId']}/cancel").status_code == 403
    assert "Cancel job" not in agent.client.get(f"/owner/jobs/{maria['jobId']}").text
    assert _edits(agent) == []


def test_each_grid_column_counts_the_half_hour_windows_find_slots_offers(agent):
    sign_in(agent, "admin")
    call_id = ready_to_book(agent)
    day = max(d for d, _ in _offered(agent, call_id))
    page = agent.client.get(f"/bench/schedule?day={day}").text
    assert _openings(page) > 0
    counts = [int(n) for n in re.findall(r"Agent offers: (\d+) normal", page)]
    assert counts and any(counts), "a day with open windows shows them in its columns"


# ---- the call page: log, summary, why ------------------------------------------------------


def _key(call_id: str) -> str:
    from app import redact

    return redact.call_key(call_id)


def _booked_new_caller(agent) -> str:
    from toolkit import book, first_open

    call_id = ready_to_book(agent)
    assert book(agent, call_id, first_open(agent, call_id))["ok"]
    agent.end(call_id)
    return call_id


def test_the_log_button_opens_the_calls_log_behind_the_bench_sign_in(agent):
    sign_in(agent, "admin")
    call_id = _booked_new_caller(agent)
    page = agent.client.get(f"/bench/calls/{_key(call_id)}").text
    assert f'href="/bench/calls/{_key(call_id)}/log"' in page and "/debug/logs" not in page
    log = agent.client.get(f"/bench/calls/{_key(call_id)}/log")
    assert log.status_code == 200 and "book_appointment" in log.text and "find_slots" in log.text
    data = agent.client.get(f"/bench/calls/{_key(call_id)}/log.json").json()
    assert data["call_id"] == call_id and data["count"] == len(data["data"]) > 0
    assert {r["call_id"] for r in data["data"]} == {call_id}
    token = agent.app.state.services.settings.debug_token
    assert not token or token not in log.text
    assert agent.client.get("/bench/calls/000000000000/log").status_code == 404
    sign_in(agent, "staff")
    assert agent.client.get(f"/bench/calls/{_key(call_id)}/log").status_code == 403
    assert agent.client.get(f"/bench/calls/{_key(call_id)}/log.json").status_code == 403


def _outcome(agent, call_id: str) -> dict:
    from app import trace

    services = agent.app.state.services
    conn = agent.db()
    try:
        found = trace.timeline(conn, call_id)
        tenant = services.tenants.default
        customers = bench_call.customers(conn, found, None, services.fsm[tenant.slug], tenant)
        return bench_call.outcomes(conn, found, customers, tenant)
    finally:
        conn.close()


def test_a_new_callers_booking_shows_the_customer_it_added_and_what_was_captured(agent):
    sign_in(agent, "admin")
    call_id = _booked_new_caller(agent)
    found = _outcome(agent, call_id)
    tags = [line["tag"] for line in found["lines"]]
    assert tags[0] == "Booked" and "Added" in tags and "New caller" not in tags
    added = next(line for line in found["lines"] if line["tag"] == "Added")
    assert "Pat Caller" in added["text"] and added["sub"].startswith("Customer ")
    captured = dict(found["captured"])
    assert captured["Name"] == "Pat Caller" and captured["Address"].startswith("101 Union St")
    assert captured["Problem"].startswith("No heat since last night")
    page = agent.client.get(f"/bench/calls/{_key(call_id)}").text
    assert "No customer matched" not in page and "New customer in ServiceTitan" in page


def test_a_known_number_shows_as_matched_by_caller_id_with_the_name(agent, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    sign_in(agent, "admin")
    call_id = agent.start(maria["phone"])["data"]["call_id"]
    agent.end(call_id)
    found = _outcome(agent, call_id)
    matched = next(line for line in found["lines"] if line["tag"] == "Matched")
    assert matched["text"].endswith("by caller ID") and not matched["text"].startswith("Customer ")
    assert "open job" in matched["sub"]
    assert found["lines"][-1]["tag"] == "No action"


def test_a_flag_and_its_callback_show_once_not_twice(agent):
    sign_in(agent, "admin")
    call_id = agent.start()["data"]["call_id"]
    agent.end(call_id)
    reason = "Caller wants the owner to call about a refund"
    conn = agent.db()
    try:
        conn.execute("INSERT INTO flags (call_id, kind, reason, priority, created_at) "
                     "VALUES (?, 'person', ?, 'High', '2026-11-02T14:00:00Z')", (call_id, reason))
        conn.execute(
            "INSERT INTO callbacks (call_id, phone, phone_key, due_at, owner, reason, created_at, "
            "updated_at) VALUES (?, '(212) 555-0999', '2125550999', '2026-11-02T16:00:00Z', "
            "'Office', ?, '2026-11-02T14:00:00Z', '2026-11-02T14:00:00Z')", (call_id, reason))
        conn.commit()
    finally:
        conn.close()
    from app import trace

    conn = agent.db()
    try:
        rows = bench_call.follow_ups(trace.timeline(conn, call_id),
                                     agent.app.state.services.tenants.default)
    finally:
        conn.close()
    [row] = rows
    assert row["reason"] == reason and row["callback"]["owner"] == "Office"
    page = agent.client.get(f"/bench/calls/{_key(call_id)}").text
    summary = page.split('id="summary"')[1].split('id="why"')[0]
    assert summary.count(reason) == 1
    assert "Office calls back by Mon Nov 2, 11:00 AM" in summary


def test_why_says_what_happened_and_why_in_plain_words(agent):
    from test_ts_bench_walkthrough import gas_call

    from app import trace

    sign_in(agent, "admin")
    call_id = gas_call(agent)
    conn = agent.db()
    try:
        rows = bench_call.decisions(conn, trace.timeline(conn, call_id))
    finally:
        conn.close()
    held = next(r for r in rows if r["kind"] == "Refused")
    assert held["what"] == "Book the visit: held" and held["result"] == "Held back"
    assert held["why"].startswith("No booking while a safety signal is open")
    heard = next(r for r in rows if r["kind"] == "Safety" and r["source"] == "Caller's words")
    assert heard["what"] == "Hazard heard" and heard["result"] == "Fixed lines next"
    end = rows[-1]
    assert end["what"] == "Call ended" and end["why"] == "The caller hung up"
    assert all(r["what"] and r["why"] for r in rows), "no row is left without words"
    page = agent.client.get(f"/bench/calls/{_key(call_id)}").text
    assert "<th>What happened</th><th>Why</th><th>Result</th>" in page


# ---- System, Questions, Rules, Evals, Cost ---------------------------------------------------


def _visible(page: str) -> str:
    """The page's text as read on screen: tags and their attributes (tooltips included) dropped."""
    body = page.split("<body", 1)[1]
    return re.sub(r"<script.*?</script>", " ", re.sub(r"<[^>]+>", " ", body), flags=re.DOTALL)


def test_system_shows_no_basis_labels_and_lists_the_side_model_only_when_on(agent):
    from app import bench_data

    sign_in(agent, "admin")
    shown = _visible(agent.client.get("/bench/settings").text)
    for label in ("ASSUMED", "MEASURED", "SOURCE", "Basis"):
        assert label not in shown, label
    assert "Off: the phrase rules decide alone" in shown
    on = bench_data.models_in_use("openai-decisions", "")
    side = next(m for m in on if m["job"] == "Side model")
    assert side["model"] == "gpt-6-luna" and side["live"]
    assert all("jev" not in m["profile"] for m in on)


def test_each_voice_knob_has_its_source_in_a_tooltip_not_on_the_page(agent):
    from app import bench_data

    sign_in(agent, "admin")
    page = agent.client.get("/bench/settings").text
    for knob in bench_data.voice_knobs():
        assert f'data-tip="{knob["source"]}"' in page, knob["label"]


def test_the_questions_page_says_what_it_is_for_and_answers_each_question(agent):
    sign_in(agent, "admin")
    page = agent.client.get("/bench/questions").text
    assert "What the owner is likely to ask, answered from the calls on record." in page
    assert page.count('class="q what"') >= 15
    shown = _visible(page)
    for cut in ("Curveball", "Why not a dispatcher", "Two more weeks", "Prompt structure"):
        assert cut not in shown, cut


def test_rules_lists_every_prompt_version_newest_first_with_its_measure(agent):
    from app import bench_data

    versions = bench_data.prompt_versions()
    numbers = [v["number"] for v in versions]
    assert numbers == sorted(numbers, reverse=True) and numbers[-1] == 1 and numbers[0] >= 15
    assert all(v["title"] and v["words"] and v["commit"] for v in versions), "the table is backfilled"
    v13 = next(v for v in versions if v["version"] == "v13")
    assert v13["title"] == "A leak isn't a tune-up" and v13["commit"] == "67ef197"
    sign_in(agent, "admin")
    page = agent.client.get("/bench/rules").text
    assert 'id="prompt-versions"' in page and "Owner rule versions" in page
    assert f"{bench_data.REPO_COMMITS}{versions[0]['commit']}" in page


def test_the_prompt_log_table_matches_the_prompt_at_each_commit():
    import subprocess

    from app import bench_data

    for v in bench_data.prompt_versions():
        listed = subprocess.run(
            ["git", "show", "--name-only", "--format=", v["commit"]],
            capture_output=True, text=True, cwd=bench_data.ROOT, check=False,
        )
        if listed.returncode:
            continue  # a shallow clone without the history
        path = next((p for p in listed.stdout.split() if p.endswith("socket/prompt.md")), None)
        if path is None:
            continue
        text = subprocess.run(["git", "show", f"{v['commit']}:{path}"], capture_output=True,
                              text=True, cwd=bench_data.ROOT, check=True).stdout
        assert f"{len(text.split()):,}" == v["words"], v["version"]


def test_evals_filter_by_what_was_tested_counts_only_those_calls(agent):
    from app import bench_data

    runs = bench_data.runs()
    choices = bench_data.tested_choices(runs)
    keys = [key for key, _, _ in choices]
    assert "safety" in keys and "commercial" in keys
    run = next(r for r in runs if len(r.scenarios) >= bench_data.SUITE_MIN)
    whole = bench_data.run_summary(run)
    safety = bench_data.run_summary(run, "safety")
    assert safety["judged"] + safety["errors"] < whole["judged"] + whole["errors"]
    assert whole["tested"], "each run says what it tested"
    sign_in(agent, "admin")
    page = agent.client.get("/bench/evals?tested=safety").text
    assert 'href="/bench/evals?tested=safety&amp;runs=all"' in page
    for gone in ("Gate-rerun", ">Smoke<", ">Custom<"):
        assert gone not in page
    assert agent.client.get("/bench/evals?tested=nope").status_code == 200


def test_rounds_show_what_they_settled_and_link_their_runs(agent):
    from app import bench_data

    found = bench_data.rounds()
    slugs = [r["slug"] for r in found]
    assert slugs[0] == "symptom-questions-ab-01", "newest first"
    assert "reliability-loop-01" in slugs
    assert found[0]["settled"].startswith("Server-written symptom questions")
    reliability = next(r for r in found if r["slug"] == "reliability-01")
    assert "20261103-215934" in reliability["runs"]
    assert bench_data.round_of(found)["20261103-215934"]["slug"] == "reliability-01"
    sign_in(agent, "admin")
    page = agent.client.get("/bench/evals?runs=all").text
    assert '<a href="/bench/rounds/reliability-01"' in page


def test_the_ab_rounds_results_files_load_and_are_listed_in_the_round():
    from app import bench_data
    from evals import results as eval_results

    folder = bench_data.ROUNDS / "symptom-questions-ab-01-data"
    files = sorted(folder.glob("*.json"))
    assert len(files) == 7
    text = (bench_data.ROUNDS / "symptom-questions-ab-01.md").read_text()
    for f in files:
        run = eval_results.load(f)
        assert run.stack == "retell_llm"
        assert f.name[:15] in text


def test_the_cost_chart_has_a_source_for_every_bar(agent):
    from app import bench_pages

    chart = bench_pages.stack_chart()
    keys = {m["key"] for m in chart["measures"]}
    assert keys == {"wait", "slow", "lookup", "cost"}
    for row in chart["rows"]:
        assert row["values"], row["name"]
        for value in row["values"].values():
            assert value["v"] > 0 and value["src"].startswith(("evals/rounds/", "socket-buildout"))
    assert sum(r["live"] for r in chart["rows"]) == 2  # the live stack and the live model
    sign_in(agent, "admin")
    page = agent.client.get("/bench/cost").text
    assert page.count("data-row") == len(chart["rows"])
    assert 'data-v-wait="1.83"' in page and "data-src-wait=" in page
    shown = _visible(page)
    for label in ("ASSUMED", "MEASURED", "Measured", "Assumed"):
        assert label not in shown, label


# ---- a tool call, its result and the records it wrote ----------------------------------------


def _tool_id(agent, call_id: str, tool: str) -> int:
    conn = agent.db()
    try:
        return conn.execute("SELECT id FROM tool_calls WHERE call_id = ? AND tool = ? AND ok = 1 "
                            "ORDER BY id DESC LIMIT 1", (call_id, tool)).fetchone()[0]
    finally:
        conn.close()


def test_a_booking_in_the_transcript_opens_to_its_bookings_row_and_the_servicetitan_job(agent, mock):
    sign_in(agent, "admin")
    call_id = _booked_new_caller(agent)
    key = _key(call_id)
    tool_id = _tool_id(agent, call_id, "book_appointment")
    page = agent.client.get(f"/bench/calls/{key}").text
    transcript = page.split('id="transcript"')[1].split('id="timing"')[0]
    assert 'class="tool-call"' in transcript and "book_appointment" in transcript
    assert f'href="/bench/calls/{key}/tools/{tool_id}"' in transcript and "Records it wrote" in transcript

    conn = agent.db()
    try:
        booking = conn.execute("SELECT * FROM bookings WHERE call_id = ?", (call_id,)).fetchone()
    finally:
        conn.close()
    shown = agent.client.get(f"/bench/calls/{key}/tools/{tool_id}")
    assert shown.status_code == 200
    text = html.unescape(shown.text)
    assert f'"booking_id": {booking["fsm_job_id"]}' in text, "the result from the log"
    assert f"job {booking['fsm_job_id']}" in text and '<td class="fit">booked' in text
    assert "Found" in text
    db = sqlite3.connect(mock[0].state.db_path)
    try:
        job = db.execute("SELECT job_number, summary FROM jobs WHERE id = ?",
                         (booking["fsm_job_id"],)).fetchone()
        appointment = db.execute("SELECT id FROM appointments WHERE job_id = ?",
                                 (booking["fsm_job_id"],)).fetchone()
    finally:
        db.close()
    assert f"ServiceTitan job {job[0]}" in text, "read live from the mock"
    assert f">{appointment[0]}, " in text, "the appointment the booking made"
    assert "Pat Caller" in text


def test_a_flag_opens_to_its_flag_and_callback_rows(agent):
    sign_in(agent, "admin")
    call_id = ready_to_book(agent)
    flagged = agent.tool("flag_person", call_id, reason="Wants the owner about a refund",
                         callback_time="2 PM")
    assert flagged["ok"], flagged
    tool_id = _tool_id(agent, call_id, "flag_person")
    text = agent.client.get(f"/bench/calls/{_key(call_id)}/tools/{tool_id}").text
    assert "Wants the owner about a refund" in text
    assert f'<td class="fit num">{flagged["data"]["flag_id"]}</td>' in text
    if flagged["data"].get("callback_id"):
        assert f'<td class="fit num">{flagged["data"]["callback_id"]}</td>' in text


def test_without_the_log_a_booking_alone_in_its_second_is_matched_by_time(agent):
    from toolkit import book, first_open

    sign_in(agent, "admin")
    call_id = ready_to_book(agent)
    slot = first_open(agent, call_id)
    agent.clock.advance(seconds=5)  # the booking runs in a second of its own
    assert book(agent, call_id, slot)["ok"]
    agent.app.state.services.settings.log_path.unlink()
    text = agent.client.get(f"/bench/calls/{_key(call_id)}/tools/"
                            f"{_tool_id(agent, call_id, 'book_appointment')}").text
    assert "Not in this server&#39;s log." in text or "Not in this server's log." in text
    assert "Bookings matched by time" in text
    assert "ServiceTitan job" in text and "Found" in text


def test_two_writing_tools_in_one_second_without_ids_are_not_guessed(agent):
    sign_in(agent, "admin")
    call_id = _booked_new_caller(agent)  # read_back, find_slots and the booking share a second
    agent.app.state.services.settings.log_path.unlink()
    key = _key(call_id)
    for tool in ("book_appointment", "find_slots"):
        text = html.unescape(agent.client.get(
            f"/bench/calls/{key}/tools/{_tool_id(agent, call_id, tool)}").text)
        assert "No record matched by id; more than one tool ran in that second." in text, tool
        assert "ServiceTitan job" not in text and "<th>Booking</th>" not in text, tool


def test_the_result_is_never_another_calls_log_line_with_the_same_request_id(agent):
    sign_in(agent, "admin")
    call_id = _booked_new_caller(agent)
    tool_id = _tool_id(agent, call_id, "book_appointment")
    conn = agent.db()
    try:
        request_id = conn.execute("SELECT request_id FROM tool_calls WHERE id = ?",
                                  (tool_id,)).fetchone()[0]
    finally:
        conn.close()
    path = agent.app.state.services.settings.log_path
    lines = [line for line in path.read_text().splitlines()
             if json.loads(line).get("request_id") != request_id]
    foreign = {"ts": "2026-11-02T14:00:00Z", "request_id": request_id, "call_id": "someone-else",
               "tool": "book_appointment", "args": {}, "ok": True, "refused": None, "gate": None,
               "say": "", "next": "", "result": {"booking_id": 987654}, "replayed": False,
               "ms": 1.0, "lock_ms": 0.0, "fsm": [], "error": None}
    path.write_text("\n".join(lines + [json.dumps(foreign)]) + "\n")
    key = _key(call_id)
    for page in (f"/bench/calls/{key}/tools/{tool_id}", f"/bench/calls/{key}/tools/{tool_id}/result"):
        text = html.unescape(agent.client.get(page).text)
        assert "987654" not in text and "Not in this server's log." in text, page


def test_a_tool_call_page_is_for_admins_and_only_for_its_own_call(agent):
    sign_in(agent, "admin")
    first = _booked_new_caller(agent)
    other = agent.start()["data"]["call_id"]
    tool_id = _tool_id(agent, first, "book_appointment")
    assert agent.client.get(f"/bench/calls/{_key(other)}/tools/{tool_id}").status_code == 404
    sign_in(agent, "staff")
    assert agent.client.get(f"/bench/calls/{_key(first)}/tools/{tool_id}").status_code == 403


def test_a_booking_made_from_the_read_back_links_to_its_bookings_and_dispatch_rows(agent):
    from test_ts_dispatch import _dispatch, _start_no_heat

    sign_in(agent, "admin")
    call_id = _start_no_heat(agent)
    sent = _dispatch(agent, call_id)
    assert sent["ok"] and sent["data"]["dispatched"], sent
    agent.end(call_id)
    key = _key(call_id)
    tool_id = _tool_id(agent, call_id, "read_back")
    conn = agent.db()
    try:
        booking = conn.execute("SELECT * FROM bookings WHERE call_id = ? AND kind = 'book'",
                               (call_id,)).fetchone()
        dispatch = conn.execute("SELECT id FROM dispatches WHERE call_id = ?", (call_id,)).fetchone()
    finally:
        conn.close()

    transcript = agent.client.get(f"/bench/calls/{key}").text.split('id="transcript"')[1]
    link = f'href="/bench/calls/{key}/tools/{tool_id}"'
    assert link in transcript.split('id="timing"')[0] and "Records it wrote" in transcript

    result = html.unescape(agent.client.get(f"/bench/calls/{key}/tools/{tool_id}/result").text)
    assert "right away" in result, "say"
    assert "Will someone be home" in result, "next"
    assert f'"booking_id": {booking["fsm_job_id"]}' in result, "data"

    page = html.unescape(agent.client.get(f"/bench/calls/{key}/tools/{tool_id}").text)
    wrote = page.split('id="wrote"')[1]
    assert f'<td class="fit num">{booking["id"]}' in wrote and '<td class="fit">booked' in wrote
    assert "no heat" in wrote and f"job {booking['fsm_job_id']}" in wrote
    assert f'<td class="fit num">{dispatch["id"]}</td>' in wrote, "the dispatch row"
    assert "ServiceTitan job" in page and "Found" in page


def test_a_refused_tool_call_shows_its_refusal_and_gate(agent):
    from test_ts_bench_walkthrough import gas_call

    sign_in(agent, "admin")
    call_id = gas_call(agent)
    conn = agent.db()
    try:
        tool_id = conn.execute("SELECT id FROM tool_calls WHERE call_id = ? AND ok = 0 AND "
                               "tool = 'book_appointment'", (call_id,)).fetchone()[0]
    finally:
        conn.close()
    result = agent.client.get(f"/bench/calls/{_key(call_id)}/tools/{tool_id}/result").text
    assert "Refused" in result and "gate safety" in result
    assert agent.client.get(f"/bench/calls/{_key(call_id)}/tools/999999/result").status_code == 404


def test_the_log_keeps_the_next_step_the_server_named():
    from app import logs

    body = {"ok": True, "say": "Okay.", "next": "Ask who will be home.", "data": {}, "refused": None}
    line = logs.record(now=__import__("datetime").datetime(2026, 11, 2), request_id="r", call_id="c",
                       tool="read_back", args={}, body=body, gate=None, ms=1.0, fsm=[], error=None,
                       replayed=False)
    assert line["next"] == "Ask who will be home."
