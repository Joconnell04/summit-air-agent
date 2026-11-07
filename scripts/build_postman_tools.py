"""Write postman/tool-server.postman_collection.json and the matching environment.

Generated so every request carries the same envelope check and variable names. Edit this file, not
the JSON. Run: python scripts/build_postman_tools.py. Run the collection with: make postman.
"""

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "postman"

ENVELOPE = """
pm.test('HTTP 200 with the envelope', () => {
  pm.response.to.have.status(200);
  const b = pm.response.json();
  ['ok', 'say', 'data', 'refused'].forEach(k => pm.expect(b).to.have.property(k));
});"""


def ok(extra: str = "") -> str:
    return "pm.test('ok', () => pm.expect(pm.response.json().ok).to.eql(true));\n" + extra


def refused(code: str, extra: str = "") -> str:
    return (
        f"pm.test('refused: {code}', () => pm.expect(pm.response.json().refused).to.eql('{code}'));\n"
        + extra
    )


def item(
    name, method, path, *, body=None, form=None, check="", headers=None, pre="", envelope=True
):
    header = [{"key": "Content-Type", "value": "application/json"}] if body is not None else []
    header += [{"key": k, "value": v} for k, v in (headers or {}).items()]
    request = {"method": method, "header": header, "url": "{{base_url}}/" + path}
    if body is not None:
        request["body"] = {"mode": "raw", "raw": json.dumps(body, indent=2)}
    if form is not None:
        request["body"] = {
            "mode": "urlencoded",
            "urlencoded": [{"key": k, "value": v} for k, v in form.items()],
        }
    script = check + (ENVELOPE if envelope else "")
    events = [{"listen": "test", "script": {"type": "text/javascript", "exec": script.split("\n")}}]
    if pre:
        events.insert(
            0,
            {
                "listen": "prerequest",
                "script": {"type": "text/javascript", "exec": pre.split("\n")},
            },
        )
    return {"name": name, "event": events, "request": request}


def tool(name, title, args, check, *, call="call_id", key=None):
    headers = {"Idempotency-Key": key} if key else None
    return item(
        title,
        "POST",
        f"tools/{name}",
        body={"call_id": "{{" + call + "}}", **args},
        check=check,
        headers=headers,
    )


def turn(title, text, speaker="caller", call="call_id", check=""):
    return item(
        title,
        "POST",
        "calls/{{" + call + "}}/turn",
        body={"speaker": speaker, "text": text, "timings": {"stt_final_ms": 150}},
        check=ok(check),
    )


def folder(name, items):
    return {"name": name, "item": items}


ADDRESS = {"street": "101 Union St", "unit": "", "city": "Brooklyn", "zip": "11231"}
DETAILS = {
    "issue": "Annual tune-up",
    "since": "n/a",
    "tried": "nothing",
    "who_home": "Pat",
    "approver": "Pat, the owner",
    "system_type": "gas boiler",
    "arrival_notes": "Side door",
    "name": "Pat Postman",
}
NEW_IDS = """
const run = Date.now();
pm.collectionVariables.set('call_id', 'pm-' + run);
pm.collectionVariables.set('safety_call_id', 'pm-safety-' + run);
pm.collectionVariables.set('known_call_id', 'pm-known-' + run);
// No transfer or handoff has this id, so reporting on it is refused.
pm.collectionVariables.set('unknown_transfer_id', '0');
pm.collectionVariables.set('caller', '(212) 555-' + String(1000 + run % 9000).slice(-4));
"""
SCENARIOS = """
const s = pm.response.json(), v = pm.collectionVariables;
v.set('maria_phone', s.repeatCallerOpenJob.phone);
v.set('maria_customer_id', s.repeatCallerOpenJob.customerId);
v.set('maria_job_id', s.repeatCallerOpenJob.jobId);
v.set('park_customer_id', s.activeMemberWithAdultChild.customerId);
pm.test('the mock planted its scenarios', () => pm.expect(s.repeatCallerOpenJob.phone).to.be.a('string'));
"""
PICK_SLOT = """
const days = pm.response.json().data.days;
pm.test('offers the shape of availability', () => pm.expect(days.length).to.be.above(0));
pm.collectionVariables.set('slot_date', days[0].date);
pm.collectionVariables.set('slot_window', days[0].windows[0]);
"""
SLOT = {"date": "{{slot_date}}", "window": "{{slot_window}}"}
DEBUG = {"X-Debug-Token": "{{debug_token}}"}


def build() -> dict:
    booking_flow = [
        item(
            "Scenarios from the mock",
            "GET",
            "debug/scenarios",
            check=SCENARIOS,
            pre=NEW_IDS,
            envelope=False,
        ),
        item(
            "Start a call",
            "POST",
            "calls/start",
            body={
                "call_id": "{{call_id}}",
                "caller_id": "{{caller}}",
                "dialed_number": "(646) 624-0418",
            },
            check=ok(
                "pm.test('names the tenant', () => pm.expect(pm.response.json().data.tenant.name).to.be.a('string'));"
            ),
        ),
        turn("Caller turn", "Hi, I'd like to book my annual tune-up."),
        tool("check_service_area", "Service area: in", {"zip": "11231"}, ok()),
        tool(
            "check_service_area",
            "Service area: out",
            {"zip": "11501"},
            refused(
                "out_of_area",
                "pm.test('declines kindly', () => pm.expect(pm.response.json().say).to.include('outside the area'));",
            ),
        ),
        tool(
            "find_address",
            "Find address: a street the caller never said",
            {"street": "248 Duffield St"},
            refused("address_not_heard"),
        ),
        tool(
            "find_slots",
            "Find slots: shape",
            {"job_type": "tune_up", "time_of_day": "morning"},
            ok(PICK_SLOT),
        ),
        tool(
            "find_slots",
            "Find slots: one day",
            {"job_type": "tune_up", "day": "{{slot_date}}"},
            ok(
                "pm.test('exact windows', () => pm.expect(pm.response.json().data.windows.length).to.be.above(0));"
            ),
        ),
        tool(
            "book_appointment",
            "Book before the read-back",
            {"job_type": "tune_up", "slot": SLOT, "details": DETAILS},
            refused("gate_read_back"),
        ),
        tool(
            "read_back",
            "Read back: ask",
            {"address": ADDRESS, "phone": "caller_id"},
            ok(
                "pm.collectionVariables.set('sentence', pm.response.json().say);\n"
                "pm.test('reads the number in groups', () => pm.expect(pm.response.json().say).to.match(/212, 555, \\d{4}/));"
            ),
        ),
        tool(
            "read_back",
            "Read back: confirmed too soon",
            {"address": ADDRESS, "phone": "caller_id", "confirmed": True},
            refused("gate_read_back"),
        ),
        turn("Agent reads it back", "{{sentence}}", speaker="agent"),
        turn("Caller confirms", "Yes, that's right."),
        tool(
            "read_back",
            "Read back: confirmed",
            {"address": ADDRESS, "phone": "caller_id", "confirmed": True},
            ok(),
        ),
        tool(
            "book_appointment",
            "Book",
            {"job_type": "tune_up", "slot": SLOT, "details": DETAILS},
            ok("pm.collectionVariables.set('booking_id', pm.response.json().data.booking_id);"),
            key="{{call_id}}-book",
        ),
        tool(
            "book_appointment",
            "Book again with the same key",
            {"job_type": "tune_up", "slot": SLOT, "details": DETAILS},
            ok(
                "pm.test('returns the first booking', () => pm.expect(pm.response.json().data.booking_id).to.eql(Number(pm.collectionVariables.get('booking_id'))));"
            ),
            key="{{call_id}}-book",
        ),
        tool(
            "add_note",
            "Add a job note",
            {"text": "Prefers a call 30 minutes ahead.", "attach_to": "job"},
            ok(),
            key="{{call_id}}-note",
        ),
        tool(
            "end_call",
            "End in the confirmation turn",
            {"reason": "resolved"},
            refused("anything_else_first"),
        ),
        turn("Caller is done", "No, that's everything. Thanks!"),
        tool("end_call", "End the call", {"reason": "resolved"}, ok(), key="{{call_id}}-end"),
        item(
            "Call ended",
            "POST",
            "calls/{{call_id}}/end",
            body={"reason": "agent_end"},
            check=ok(
                "pm.test('booked', () => pm.expect(pm.response.json().data.outcome).to.eql('booked'));"
            ),
        ),
    ]
    safety_flow = [
        item(
            "Start a safety call",
            "POST",
            "calls/start",
            body={
                "call_id": "{{safety_call_id}}",
                "caller_id": "{{caller}}",
                "dialed_number": "(646) 624-0418",
            },
            check=ok(),
        ),
        turn(
            "Caller smells gas",
            "it smells a bit like gas, but anyway the heat's out",
            call="safety_call_id",
            check="pm.collectionVariables.set('safety_lines', pm.response.json().say);\n"
            "pm.test('safety lines first', () => pm.expect(pm.response.json().data.safety.action).to.eql('speak_lines'));",
        ),
        tool(
            "book_appointment",
            "Booking waits for the lines",
            {"job_type": "safety_check", "slot": SLOT, "details": DETAILS},
            refused("gate_safety"),
            call="safety_call_id",
        ),
        tool(
            "end_call",
            "End call waits for the lines",
            {"reason": "resolved"},
            refused("gate_safety"),
            call="safety_call_id",
        ),
        turn("Agent says the lines", "{{safety_lines}}", speaker="agent", call="safety_call_id"),
        tool(
            "end_call",
            "A safety call never takes a spam label",
            {"reason": "spam"},
            refused("gate_safety"),
            call="safety_call_id",
        ),
        tool(
            "request_transfer",
            "Never put through on a safety call",
            {"reason": "person", "summary": "Wants a person about the gas smell"},
            refused("transfer_safety"),
            call="safety_call_id",
        ),
        tool(
            "flag_person",
            "Flag a person on the safety call",
            {"reason": "Caller wants to talk to a person about the gas smell", "priority": "Urgent"},
            ok("pm.collectionVariables.set('safety_flag_id', pm.response.json().data.flag_id);"),
            call="safety_call_id",
            key="{{safety_call_id}}-flag",
        ),
        item(
            "Never transferred on a safety call",
            "POST",
            "calls/{{safety_call_id}}/transfer",
            body={"flag_id": "{{safety_flag_id}}"},
            check=ok(
                "pm.test('stays with the agent', () => pm.expect(pm.response.json().data.transfer).to.eql(null));"
            ),
        ),
        item(
            "A transfer that was never started can't be reported",
            "POST",
            "calls/{{safety_call_id}}/transfer/{{unknown_transfer_id}}/done",
            body={"outcome": "no_answer"},
            check=refused("invalid_state"),
        ),
        item(
            "A handoff that was never started can't be reported",
            "POST",
            "calls/{{safety_call_id}}/handoff/{{unknown_transfer_id}}/done",
            body={"outcome": "handed_off"},
            check=refused("invalid_state"),
        ),
        item(
            "Recording only under the key the server gave",
            "POST",
            "calls/{{safety_call_id}}/recording",
            body={"egress_id": "EG_test", "object_key": "summit-air/another-call.mp4"},
            check=refused("invalid_arguments"),
        ),
        tool(
            "end_call",
            "End call signs off",
            {"reason": "resolved"},
            ok(
                "pm.test('signs off, no hang-up', () => pm.expect(pm.response.json().data.hang_up).to.eql(false));"
            ),
            call="safety_call_id",
        ),
        item(
            "Safety call ended",
            "POST",
            "calls/{{safety_call_id}}/end",
            body={"reason": "caller_hangup"},
            check=ok(),
        ),
    ]
    known_flow = [
        item(
            "Start a call from a known number",
            "POST",
            "calls/start",
            body={
                "call_id": "{{known_call_id}}",
                "caller_id": "{{maria_phone}}",
                "dialed_number": "(646) 624-0418",
            },
            check=ok(
                "const c = pm.response.json().data.customers;\n"
                "pm.test('matched before hello', () => pm.expect(c.length).to.eql(1));\n"
                "pm.test('notes come with the lookup', () => pm.expect(c[0].notes.length).to.be.above(0));"
            ),
        ),
        tool(
            "find_customer",
            "Find customer by phone",
            {"phone": "{{maria_phone}}"},
            ok(),
            call="known_call_id",
        ),
        tool(
            "get_customer_history",
            "History of this caller",
            {"customer_id": "{{maria_customer_id}}", "what": "jobs"},
            ok(),
            call="known_call_id",
        ),
        tool(
            "get_customer_history",
            "History of someone else",
            {"customer_id": "{{park_customer_id}}", "what": "jobs"},
            refused("not_owner"),
            call="known_call_id",
        ),
        tool(
            "reschedule_appointment",
            "Reschedule a dispatched job",
            {"booking_id": "{{maria_job_id}}", "slot": SLOT, "reason": "later works better"},
            refused("invalid_state"),
            call="known_call_id",
        ),
        tool(
            "cancel_appointment",
            "Cancel another caller's job",
            {"booking_id": "{{booking_id}}", "reason": "not mine"},
            refused("not_owner"),
            call="known_call_id",
        ),
        tool(
            "record_move",
            "A move nobody said they made",
            {},
            refused("move_not_said"),
            call="known_call_id",
        ),
        tool(
            "flag_person",
            "Flag a person with a callback time",
            {
                "reason": "Wants to know when the tech arrives",
                "priority": "Normal",
                "callback_in_minutes": 30,
            },
            ok(
                "pm.test('names a time', () => pm.expect(pm.response.json().say).to.match(/call you by/));"
            ),
            call="known_call_id",
            key="{{known_call_id}}-flag",
        ),
        tool(
            "record_lead",
            "Membership lead",
            {
                "kind": "membership",
                "details": {
                    "contact_name": "Maria",
                    "need": "Wants to join",
                    "callback_in_minutes": 120,
                },
            },
            ok(),
            call="known_call_id",
            key="{{known_call_id}}-lead",
        ),
        tool(
            "add_note",
            "Add a customer note",
            {"text": "Daughter Ana usually answers."},
            ok(),
            call="known_call_id",
            key="{{known_call_id}}-note",
        ),
        tool(
            "lookup_hvac_info",
            "Equipment basics",
            {"question": "Why is my radiator banging?"},
            ok("pm.test('facts come back', () => pm.expect(pm.response.json().data.facts).to.not.be.empty);"),
            call="known_call_id",
        ),
        tool(
            "mark_do_not_call",
            "Do not call, asked first",
            {},
            refused("confirm_first"),
            call="known_call_id",
            key="{{known_call_id}}-dnc",
        ),
        tool(
            "record_lead",
            "Commercial lead",
            {
                "kind": "commercial",
                "details": {
                    "contact_name": "Lou",
                    "company": "Lou's Bakery",
                    "need": "Rooftop unit",
                },
            },
            ok(
                "pm.test('declines kindly', () => pm.expect(pm.response.json().say).to.include('works on homes and apartments'));"
            ),
            call="known_call_id",
            key="{{known_call_id}}-commercial",
        ),
        item(
            "Known call ended",
            "POST",
            "calls/{{known_call_id}}/end",
            body={"reason": "caller_hangup"},
            check=ok(),
        ),
    ]
    triage = [
        item(
            "Call timeline",
            "GET",
            "debug/calls/{{call_id}}",
            headers=DEBUG,
            envelope=False,
            check="pm.test('status is 200', () => pm.response.to.have.status(200));\n"
            "pm.test('has the turns and tools', () => pm.expect(pm.response.json().events.length).to.be.above(5));",
        ),
        item(
            "Logs for the call",
            "GET",
            "debug/logs?call_id={{call_id}}",
            headers=DEBUG,
            envelope=False,
            check="pm.test('one line per request', () => pm.expect(pm.response.json().count).to.be.above(5));",
        ),
        item(
            "Refusals and errors only",
            "GET",
            "debug/logs?call_id={{safety_call_id}}&errors=true",
            headers=DEBUG,
            envelope=False,
            check="pm.test('the safety gate shows', () => pm.expect(pm.response.json().data.map(r => r.refused)).to.include('gate_safety'));",
        ),
        item(
            "Debug without a token",
            "GET",
            "debug/logs",
            envelope=False,
            check="pm.test('is refused', () => pm.response.to.have.status(401));",
        ),
        item(
            "Forget calls, eval instance only",
            "POST",
            "debug/forget-calls",
            headers=DEBUG,
            envelope=False,
            check="pm.test('refused off the Retell eval instance', () => pm.response.to.have.status(403));",
        ),
    ]
    setup = [
        item(
            "Health",
            "GET",
            "healthz",
            check=ok(
                "pm.test('the FSM is reachable', () => Object.values(pm.response.json().data.fsm).forEach(v => pm.expect(v).to.eql(true)));"
            ),
        ),
        item(
            "Worker heartbeat", "POST", "healthz/heartbeat", body={"worker": "postman"}, check=ok()
        ),
    ]
    html = "pm.test('is HTML', () => pm.expect(pm.response.headers.get('Content-Type')).to.include('text/html'));"
    outbound_and_owner = [
        item(
            "Claim the next outbound call",
            "POST",
            "outbound/claim",
            body={"worker": "postman"},
            check=ok(
                "pm.test('returns a call or none', () => pm.expect(pm.response.json().data).to.have.property('outbound'));"
            ),
        ),
        item(
            "Report an outbound call that was never claimed",
            "POST",
            "outbound/{{outbound_id}}/done",
            body={"answered": False},
            check=refused("invalid_state"),
            pre="pm.collectionVariables.set('outbound_id', '999999');",
        ),
        item(
            "Report a heads-up call that was never claimed",
            "POST",
            "outbound/heads-up/{{outbound_id}}/done",
            body={"answered": False},
            check=refused("invalid_state"),
            pre="pm.collectionVariables.set('outbound_id', '999999');",
        ),
        item(
            "Owner view without signing in",
            "GET",
            "owner",
            envelope=False,
            check="pm.test('shows the sign-in form', () => pm.response.to.have.status(401));\n"
            + html,
        ),
        item(
            "Owner view sign-in with a wrong password",
            "POST",
            "owner/login",
            envelope=False,
            form={"username": "{{owner_username}}", "password": "not-the-password"},
            check="pm.test('is refused', () => pm.response.to.have.status(401));",
        ),
        item(
            "Sign in to the owner view as the admin",
            "POST",
            "owner/login",
            envelope=False,
            form={"username": "{{owner_username}}", "password": "{{owner_password}}"},
            check="pm.test('opens the page', () => pm.response.to.have.status(200));\n"
            + "pm.test('shows the day board', () => pm.expect(pm.response.text()).to.include('Day board'));",
        ),
        item(
            "The day's calls",
            "GET",
            "owner/calls",
            envelope=False,
            check="pm.test('opens the list', () => pm.response.to.have.status(200));\n" + html,
        ),
        item(
            "A call's page",
            "GET",
            "owner/calls/{{call_id}}",
            envelope=False,
            check="pm.test('opens the call', () => pm.response.to.have.status(200));\n" + html,
        ),
        item(
            "A call's full log as JSON",
            "GET",
            "owner/calls/{{call_id}}/log",
            envelope=False,
            check="pm.test('downloads the log', () => pm.response.to.have.status(200));",
        ),
        item(
            "A call's recording, none kept here",
            "GET",
            "owner/calls/{{call_id}}/recording",
            envelope=False,
            check="pm.test('no recording without a bucket', () => pm.response.to.have.status(404));",
        ),
        item(
            "Search calls, jobs, flags and words said",
            "GET",
            "owner/search?q=Union",
            envelope=False,
            check="pm.test('opens the matches', () => pm.response.to.have.status(200));\n" + html,
        ),
        item(
            "Search suggestions as JSON",
            "GET",
            "owner/search/suggest?q=Union",
            envelope=False,
            check="pm.test('suggests matches', () => pm.response.to.have.status(200));",
        ),
        item(
            "Results for a custom range",
            "GET",
            "owner/results?start=2026-10-27&day=2026-11-02",
            envelope=False,
            check="pm.test('opens results', () => pm.response.to.have.status(200));\n" + html,
        ),
        item(
            "Tell Revin something was wrong on a call",
            "POST",
            "owner/calls/{{call_id}}/report",
            envelope=False,
            form={"text": "It should have asked who is at home.", "turn": ""},
            check="pm.test('stores it and goes back to the call', () => pm.expect(pm.response.code).to.be.oneOf([200, 303]));",
        ),
        item(
            "Results for seven days",
            "GET",
            "owner/results?days=7",
            envelope=False,
            check="pm.test('opens results', () => pm.response.to.have.status(200));\n" + html,
        ),
        item(
            "The design system specimen",
            "GET",
            "owner/design",
            envelope=False,
            check="pm.test('opens the specimen', () => pm.response.to.have.status(200));\n" + html,
        ),
        item(
            "The old Rules address leads to the bench's page",
            "GET",
            "owner/rules",
            envelope=False,
            check="pm.test('lands on the bench', () => pm.response.to.have.status(200));\n" + html,
        ),
        item(
            "The Rules page opens for an admin",
            "GET",
            "bench/rules",
            envelope=False,
            check="pm.test('opens the editor', () => pm.response.to.have.status(200));\n" + html,
        ),
        item(
            "The settings and users",
            "GET",
            "owner/settings",
            envelope=False,
            check="pm.test('opens the settings', () => pm.response.to.have.status(200));\n" + html,
        ),
        item(
            "An admin saves the settings",
            "POST",
            "owner/settings",
            envelope=False,
            form={"s.two_party_consent": "0", "s.call_recordings": "1"},
            check="pm.test('saved', () => pm.response.to.have.status(200));\n"
            + "pm.test('back on the settings', () => pm.expect(pm.response.text()).to.include('Saved'));",
        ),
        item(
            "The Checks page opens",
            "GET",
            "owner/checks",
            envelope=False,
            check="pm.test('opens', () => pm.response.to.have.status(200));\n" + html,
        ),
        item(
            "An admin checks the past hour",
            "POST",
            "owner/checks",
            envelope=False,
            form={"span": "hour"},
            # 409 where the server runs without FAILURE_GRADER=1.
            check="pm.test('starts, or says checks are off', () => "
            "pm.expect(pm.response.code).to.be.oneOf([200, 409]));",
        ),
        item(
            "A check's progress, for a run that doesn't exist",
            "GET",
            "owner/checks/runs/{{no_run_id}}",
            envelope=False,
            pre="pm.collectionVariables.set('no_run_id', '0');",
            check="pm.test('no such run', () => pm.response.to.have.status(404));",
        ),
        item(
            "Check this call",
            "POST",
            "owner/calls/{{call_id}}/check",
            envelope=False,
            form={},
            check="pm.test('back on the call', () => pm.response.to.have.status(200));\n"
            + "pm.test('shows the check', () => pm.expect(pm.response.text()).to.include('Call check'));",
        ),
        item(
            "An admin sets the daily check",
            "POST",
            "owner/checks/schedule",
            envelope=False,
            form={"enabled": "1", "at": "06:00"},
            check="pm.test('saved', () => pm.response.to.have.status(200));\n"
            + "pm.test('on the settings', () => pm.expect(pm.response.text()).to.include('Daily check'));",
        ),
        item(
            "An admin adds a staff user",
            "POST",
            "owner/users",
            envelope=False,
            form={"username": "temp", "name": "Temp", "role": "staff", "password": "temp-password-1"},
            check="pm.test('added', () => pm.response.to.have.status(200));\n"
            + "pm.test('listed', () => pm.expect(pm.response.text()).to.include('Temp'));",
        ),
        item(
            "An admin changes a user's role",
            "POST",
            "owner/users/{{temp_user_id}}/role",
            envelope=False,
            form={"role": "admin"},
            pre="pm.collectionVariables.set('temp_user_id', '3');",
            check="pm.test('changed', () => pm.response.to.have.status(200));",
        ),
        item(
            "An admin removes a user",
            "POST",
            "owner/users/{{temp_user_id}}/remove",
            envelope=False,
            form={},
            check="pm.test('removed', () => pm.response.to.have.status(200));\n"
            + "pm.test('gone from the list', () => pm.expect(pm.response.text()).to.not.include('>Temp<'));",
        ),
        item(
            "The schedule, read from ServiceTitan",
            "GET",
            "owner/schedule",
            envelope=False,
            check="pm.test('opens the schedule', () => pm.response.to.have.status(200));\n" + html,
        ),
        item(
            "Block tomorrow 8 to 10 for every free technician",
            "POST",
            "owner/schedule/block",
            envelope=False,
            form={"day": "{{sched_day}}", "window": "0800-1000", "name": "Team meeting",
                  "next": "/owner/schedule?day={{sched_day}}"},
            pre="const d = new Date(Date.now() + 86400000);\npm.collectionVariables.set('sched_day', d.toISOString().slice(0, 10));",
            check="pm.test('back on the schedule', () => pm.response.to.have.status(200));\n"
            + "pm.test('the event is on it', () => pm.expect(pm.response.text()).to.include('Team meeting'));",
        ),
        item(
            "Take the block off with its group",
            "POST",
            "owner/schedule/blocks/{{block_id}}/remove",
            envelope=False,
            form={"day": "{{sched_day}}", "group": "1", "next": "/owner/schedule?day={{sched_day}}"},
            # A fresh world has no blocks, so the one just made is the first.
            pre="pm.collectionVariables.set('block_id', '1');",
            check="pm.test('back on the schedule', () => pm.response.to.have.status(200));\n"
            + "pm.test('saved', () => pm.expect(pm.response.text()).to.include('Took block 1 off'));",
        ),
        item(
            "Add a job for a customer found by phone",
            "POST",
            "owner/schedule/jobs",
            envelope=False,
            form={"day": "{{sched_day}}", "window": "1200-1400", "phone": "(718) 555-0142",
                  "job_type": "tune_up", "notes": "Side gate",
                  "next": "/owner/schedule?day={{sched_day}}"},
            check="pm.test('back on the schedule', () => pm.response.to.have.status(200));\n"
            + "pm.test('saved', () => pm.expect(pm.response.text()).to.include('Saved'));",
        ),
        item(
            "A job and its customer",
            "GET",
            "owner/jobs/{{sched_job_id}}",
            # The seeded world's first job is Maria Delgado's, customer 1.
            pre="pm.collectionVariables.set('sched_job_id', '1');\n"
            "pm.collectionVariables.set('sched_customer_id', '1');",
            envelope=False,
            check="pm.test('opens the job', () => pm.response.to.have.status(200));\n" + html,
        ),
        item(
            "Change a job's notes",
            "POST",
            "owner/jobs/{{sched_job_id}}",
            envelope=False,
            form={"notes": "Ring the side bell"},
            check="pm.test('saved', () => pm.expect(pm.response.text()).to.include('Changed job 1: notes'));",
        ),
        item(
            "An admin renames a customer",
            "POST",
            "owner/customers/{{sched_customer_id}}",
            envelope=False,
            form={"name": "Maria Delgado Ruiz", "next": "/owner/jobs/{{sched_job_id}}"},
            check="pm.test('saved', () => pm.expect(pm.response.text()).to.include('Changed customer 1: name'));",
        ),
        item(
            "Sign out",
            "POST",
            "owner/logout",
            envelope=False,
            form={},
            check="pm.test('back to the sign-in form', () => pm.response.to.have.status(401));",
        ),
        item(
            "Sign in as staff",
            "POST",
            "owner/login",
            envelope=False,
            form={"username": "{{staff_username}}", "password": "{{staff_password}}"},
            check="pm.test('opens the page', () => pm.response.to.have.status(200));",
        ),
        item(
            "Staff can read the settings",
            "GET",
            "owner/settings",
            envelope=False,
            check="pm.test('opens the settings', () => pm.response.to.have.status(200));\n"
            + "pm.test('without a Save button', () => pm.expect(pm.response.text()).to.not.include('>Save</button>'));",
        ),
        item(
            "Staff can't change a setting",
            "POST",
            "owner/settings",
            envelope=False,
            form={"s.two_party_consent": "1"},
            check="pm.test('refused', () => pm.response.to.have.status(403));",
        ),
        item(
            "Staff can't add a user",
            "POST",
            "owner/users",
            envelope=False,
            form={"username": "eve", "name": "Eve", "role": "admin", "password": "long-enough-1"},
            check="pm.test('refused', () => pm.response.to.have.status(403));",
        ),
        item(
            "Staff can't open the rules editor",
            "GET",
            "bench/rules",
            envelope=False,
            check="pm.test('refused', () => pm.response.to.have.status(403));",
        ),
        item(
            "Staff can't save a rules draft",
            "POST",
            "bench/rules/draft",
            envelope=False,
            form={"guide.booking.0": "Ask about pets."},
            check="pm.test('refused', () => pm.response.to.have.status(403));",
        ),
        item(
            "Staff can't discard a rules draft",
            "POST",
            "bench/rules/discard",
            envelope=False,
            form={},
            check="pm.test('refused', () => pm.response.to.have.status(403));",
        ),
        item(
            "Staff can't stop a rules check",
            "POST",
            "bench/rules/cancel",
            envelope=False,
            form={},
            check="pm.test('refused', () => pm.response.to.have.status(403));",
        ),
        item(
            "Staff can't change a customer",
            "POST",
            "owner/customers/{{sched_customer_id}}",
            envelope=False,
            form={"name": "Someone Else"},
            check="pm.test('refused', () => pm.response.to.have.status(403));",
        ),
        item(
            "Staff can't roll back the rules",
            "POST",
            "bench/rules/rollback",
            envelope=False,
            form={"number": "0"},
            check="pm.test('refused', () => pm.response.to.have.status(403));",
        ),
    ]
    # The owner view folder ends signed in as staff, who can't open the bench; then the admin can.
    staff_refused = "pm.test('admins only', () => pm.response.to.have.status(403));"
    opens = "pm.test('opens', () => pm.response.to.have.status(200));\n" + html
    eval_run = "pm.collectionVariables.set('eval_run', '20261104-013438-livekit-fast-standard');"
    bench = [
        *[
            item(name, "GET", path, envelope=False, check=staff_refused, pre=pre or "")
            for name, path, pre in (
                ("Staff can't open the bench", "bench?q=0418", None),
                ("Staff can't open a call's node graph", "bench/calls/{{call_id}}", None),
                ("Staff can't fetch the graph alone", "bench/calls/{{call_id}}/graph", None),
                ("Staff can't open the live stream", "bench/calls/{{call_id}}/stream", None),
                ("Staff can't draft a scenario from a call", "bench/calls/{{call_id}}/scenario.yaml",
                 None),
                ("Staff can't open the bench's settings", "bench/settings", None),
                ("Staff can't open the questions", "bench/questions", None),
                ("Staff can't open the bench's cost", "bench/cost", None),
                ("Staff can't read what Retell heard", "bench/calls/{{call_id}}/heard", None),
                ("Staff can't read a call's log", "bench/calls/{{call_id}}/log", None),
                ("Staff can't download a call's log", "bench/calls/{{call_id}}/log.json", None),
                ("Staff can't open the bench's schedule", "bench/schedule", None),
                ("Staff can't read what changed", "bench/schedule/changes", None),
                ("Staff can't open an eval run", "bench/evals/{{eval_run}}", eval_run),
                ("Staff can't open an eval scenario", "bench/evals/{{eval_run}}/{{eval_key}}",
                 eval_run + "\npm.collectionVariables.set('eval_key', 'elderly-no-heat');"),
                ("Staff can't open an eval round", "bench/rounds/{{eval_round}}",
                 "pm.collectionVariables.set('eval_round', 'model-matrix-01');"),
            )
        ],
        item("Staff can't cancel a job from the bench", "POST",
             "bench/schedule/jobs/{{sched_job_id}}/cancel", envelope=False, form={},
             check=staff_refused),
        item(
            "Sign in to the bench as the admin",
            "POST",
            "owner/login",
            envelope=False,
            form={"username": "{{owner_username}}", "password": "{{owner_password}}",
                  "next": "/bench"},
            check="pm.test('opens the bench', () => pm.response.to.have.status(200));",
        ),
        item("The bench's calls", "GET", "bench", envelope=False, check=opens),
        item("A call's node graph", "GET", "bench/calls/{{call_id}}/graph", envelope=False,
             check=opens),
        item("The bench's settings", "GET", "bench/settings", envelope=False, check=opens),
        item("The walkthrough questions", "GET", "bench/questions", envelope=False, check=opens),
        item("The bench's cost", "GET", "bench/cost", envelope=False, check=opens),
        item("What Retell heard on a call", "GET", "bench/calls/{{call_id}}/heard", envelope=False,
             check=opens),
        item("A call's log", "GET", "bench/calls/{{call_id}}/log", envelope=False, check=opens),
        item("A call's log, as JSON", "GET", "bench/calls/{{call_id}}/log.json", envelope=False,
             check="pm.test('the call\'s lines', () => pm.expect(pm.response.json().data).to.be.an('array'));"),
        item("The eval runs", "GET", "bench/evals", envelope=False, check=opens),
        item("The bench's schedule", "GET", "bench/schedule", envelope=False, check=opens),
        item(
            "What changed in ServiceTitan, as JSON",
            "GET",
            "bench/schedule/changes",
            envelope=False,
            check="pm.test('lists the owner view edits', () => pm.expect(pm.response.json().items.length).to.be.at.least(3));",
        ),
        item(
            "Find a customer by phone",
            "POST",
            "bench/schedule/find",
            envelope=False,
            form={"phone": "(718) 555-0142"},
            check=opens,
        ),
        item("The bench's checks", "GET", "bench/checks", envelope=False, check=opens),
        item(
            "The bench checks the past day",
            "POST",
            "bench/checks",
            envelope=False,
            form={"span": "day"},
            check="pm.test('starts, or says checks are off', () => "
            "pm.expect(pm.response.code).to.be.oneOf([200, 409]));",
        ),
        item(
            "The bench grades a call again",
            "POST",
            "bench/checks/calls/{{call_id}}",
            envelope=False,
            form={},
            check="pm.test('back on the bench', () => pm.response.to.have.status(200));",
        ),
        item(
            "An override needs a reason",
            "POST",
            "bench/rules/draft",
            envelope=False,
            form={"guide.booking.0": "Ask about pets.", "override": "1", "reason": ""},
            check="pm.test('refused, nothing published', () => pm.response.to.have.status(422));\n"
            + "pm.test('says why', () => pm.expect(pm.response.text()).to.include('Say why the check is being skipped.'));",
        ),
        item(
            "Discard the draft that override left",
            "POST",
            "bench/rules/discard",
            envelope=False,
            form={},
            check="pm.test('back to the editor', () => pm.response.to.have.status(200));",
        ),
    ]
    scenarios = booking_flow[0]
    scenarios["request"]["url"] = "{{st_url}}/debug/scenarios"
    return {
        "info": {
            "name": "Tool server",
            "description": "Every tool server endpoint, with assertions, against the mock ServiceTitan. "
            "Run with: make postman. Generated by scripts/build_postman_tools.py.",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": [
            folder("1 Setup", setup),
            folder("2 Booking call", booking_flow),
            folder("3 Safety call", safety_flow),
            folder("4 Known caller", known_flow),
            folder("5 Triage", triage),
            folder("6 Outbound and owner view", outbound_and_owner),
            folder("7 Debug bench", bench),
        ],
    }


def environment() -> dict:
    values = {
        "base_url": "http://127.0.0.1:8100",
        "st_url": "http://127.0.0.1:8101",
        "debug_token": "dev-debug-token",
        "owner_username": "owner",
        "owner_password": "dev-owner-password",
        "staff_username": "staff",
        "staff_password": "dev-staff-password",
    }
    return {
        "name": "Tool server (local)",
        "values": [{"key": k, "value": v, "enabled": True} for k, v in values.items()],
    }


def main() -> None:
    (OUT / "tool-server.postman_collection.json").write_text(json.dumps(build(), indent=2) + "\n")
    (OUT / "tool-server.postman_environment.json").write_text(
        json.dumps(environment(), indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
