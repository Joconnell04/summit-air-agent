"""Write postman/servicetitan-mock.postman_collection.json and the matching environment.

The collection is generated so it stays consistent: every request carries the same auth headers,
the same error-shape check, and the same variable names. Edit this file, not the JSON.
Run: python scripts/build_postman_st.py
"""

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "postman"
TENANT = "{{base_url}}/%s/%s/tenant/{{tenant_id}}"

ERROR_SHAPE = """
if (pm.response.code >= 400) {
  pm.test('error uses the standard shape', () => {
    const b = pm.response.json();
    ['type', 'title', 'status', 'traceId', 'errors', 'data'].forEach(k => pm.expect(b).to.have.property(k));
    pm.expect(b.status).to.eql(pm.response.code);
  });
}"""

PICK_WINDOWS = """
const free = (f) => f.technicians.filter(t => t.status === 'Available' && t.hasRequiredSkills).length;
const open = pm.response.json().availabilities.filter(f => f.isAvailable && !f.start.includes('T20:00:00'));
pm.test('at least two open windows in the next ten days', () => pm.expect(open.length).to.be.at.least(2));
const v = pm.collectionVariables;
v.set('win1_start', open[0].startUtc); v.set('win1_end', open[0].endUtc); v.set('win1_open', free(open[0]));
v.set('win2_start', open[1].startUtc); v.set('win2_end', open[1].endUtc); v.set('win2_open', free(open[1]));
"""


def open_of(var_prefix: str) -> str:
    """JS that reads how many technicians are free in window 1 or 2 from a capacity response."""
    return (
        "const find = (start) => pm.response.json().availabilities.find(f => f.startUtc === start);\n"
        "const free = (f) => f.technicians.filter(t => t.status === 'Available' && t.hasRequiredSkills).length;\n"
        f"const open{var_prefix} = (n) => free(find(pm.collectionVariables.get('win' + n + '_start')));\n"
    )


def tests(code: int, *extra: str) -> str:
    lines = [f"pm.test('status is {code}', () => pm.response.to.have.status({code}));"]
    lines += list(extra)
    lines.append(ERROR_SHAPE)
    return "\n".join(lines)


def item(
    name,
    method,
    path,
    *,
    module=None,
    body=None,
    query=None,
    code=200,
    check="",
    auth=True,
    pre="",
    headers=None,
    raw_url=None,
    version="v2",
) -> dict:
    url = raw_url or (TENANT % (module, version) + "/" + path if module else "{{base_url}}/" + path)
    header = [{"key": "Content-Type", "value": "application/json"}] if body is not None else []
    if auth and module:
        header += [
            {"key": "Authorization", "value": "Bearer {{access_token}}"},
            {"key": "ST-App-Key", "value": "{{app_key}}"},
        ]
    header += [{"key": k, "value": v} for k, v in (headers or {}).items()]
    query_string = "?" + "&".join(f"{k}={v}" for k, v in query.items()) if query else ""
    request = {"method": method, "header": header, "url": url + query_string}
    if body is not None:
        request["body"] = {
            "mode": "raw",
            "raw": body if isinstance(body, str) else json.dumps(body, indent=2),
        }
    events = [
        {
            "listen": "test",
            "script": {"type": "text/javascript", "exec": tests(code, check).split("\n")},
        }
    ]
    if pre:
        events.insert(
            0,
            {
                "listen": "prerequest",
                "script": {"type": "text/javascript", "exec": pre.split("\n")},
            },
        )
    return {"name": name, "event": events, "request": request}


def form_item(name: str, fields: dict, code: int, check: str) -> dict:
    """A request to the OAuth token endpoint, which takes a form body and no auth headers."""
    form = {"grant_type": "client_credentials", **fields}
    return {
        "name": name,
        "event": [
            {
                "listen": "test",
                "script": {"type": "text/javascript", "exec": tests(code, check).split("\n")},
            }
        ],
        "request": {
            "method": "POST",
            "header": [],
            "url": "{{base_url}}/connect/token",
            "body": {
                "mode": "urlencoded",
                "urlencoded": [{"key": k, "value": v} for k, v in form.items()],
            },
        },
    }


TEST_ADDRESS = {
    "street": "9 Test St",
    "unit": "",
    "city": "Brooklyn",
    "state": "NY",
    "zip": "11201",
    "country": "USA",
}


def folder(name: str, items: list[dict]) -> dict:
    return {"name": name, "item": items}


SET_VARS = """
const s = pm.response.json(), v = pm.collectionVariables;
v.set('park_customer_id', s.activeMemberWithAdultChild.customerId);
v.set('park_location_id', s.activeMemberWithAdultChild.locationId);
v.set('park_membership_id', s.activeMemberWithAdultChild.membershipId);
v.set('shared_phone', s.sharedPhone.phone);
v.set('maria_phone', s.repeatCallerOpenJob.phone);
v.set('maria_customer_id', s.repeatCallerOpenJob.customerId);
v.set('maria_job_id', s.repeatCallerOpenJob.jobId);
v.set('moretti_customer_id', s.expiredMember.customerId);
v.set('commercial_customer_id', s.commercial.customerId);
v.set('commercial_location_id', s.commercial.locationId);
v.set('far_customer_id', s.outOfArea.customerId);
v.set('far_location_id', s.outOfArea.locationId);
v.set('street', s.ownerAndTenant.street); v.set('zip', s.ownerAndTenant.zip);
"""


def build() -> dict:
    cap_pre = (
        "const day = 86400000, start = new Date(Date.now() + day);\n"
        "pm.collectionVariables.set('cap_from', start.toISOString());\n"
        "pm.collectionVariables.set('cap_to', new Date(start.getTime() + 10 * day).toISOString());"
    )
    cap_body = json.dumps(
        {
            "startsOnOrAfter": "{{cap_from}}",
            "endsOnOrBefore": "{{cap_to}}",
            "businessUnitIds": [11],
            "jobTypeId": 203,
            "skillBasedAvailability": True,
        },
        indent=2,
    )
    job_body = json.dumps(
        {
            "customerId": "{{park_customer_id}}",
            "locationId": "{{park_location_id}}",
            "businessUnitId": 11,
            "jobTypeId": 203,
            "priority": "Normal",
            "campaignId": 301,
            "summary": "Newman check",
            "appointments": [
                {
                    "start": "{{win1_start}}",
                    "end": "{{win1_end}}",
                    "arrivalWindowStart": "{{win1_start}}",
                    "arrivalWindowEnd": "{{win1_end}}",
                }
            ],
        },
        indent=2,
    )
    # Numbers must not be quoted in the raw body; Postman substitutes the text.
    for name in ("park_customer_id", "park_location_id"):
        job_body = job_body.replace(f'"{{{{{name}}}}}"', f"{{{{{name}}}}}")

    def capacity_check(name, code_lines):
        return item(
            name,
            "POST",
            "capacity",
            module="dispatch",
            body=cap_body,
            pre=cap_pre,
            check=open_of("") + code_lines,
        )

    return {
        "info": {
            "name": "ServiceTitan mock",
            "description": "Every endpoint of the Summit Air ServiceTitan mock, with assertions. "
            "Run with: make postman-st. Generated by scripts/build_postman_st.py.",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": [
            folder(
                "1 Setup",
                [
                    item(
                        "Health",
                        "GET",
                        "health",
                        check="pm.test('has a clock', () => pm.expect(pm.response.json().now).to.be.a('string'));",
                    ),
                    form_item(
                        "Token",
                        {"client_id": "{{client_id}}", "client_secret": "{{client_secret}}"},
                        200,
                        "pm.collectionVariables.set('access_token', pm.response.json().access_token);\n"
                        "pm.test('token is a bearer token', () => pm.expect(pm.response.json().token_type).to.eql('Bearer'));",
                    ),
                    form_item(
                        "Bad token request",
                        {"client_id": "nobody", "client_secret": "wrong"},
                        400,
                        "pm.test('says invalid_client', () => pm.expect(pm.response.json().data.code).to.eql('invalid_client'));",
                    ),
                    item("Scenarios", "GET", "debug/scenarios", check=SET_VARS),
                    item(
                        "Request without a token",
                        "GET",
                        "customers",
                        module="crm",
                        code=401,
                        auth=False,
                    ),
                    item(
                        "No company profile",
                        "GET",
                        "company-profile",
                        module="settings",
                        code=404,
                        check="pm.test('ServiceTitan has no such endpoint', () => pm.expect(pm.response.json().status).to.eql(404));",
                    ),
                ],
            ),
            folder(
                "2 CRM",
                [
                    item(
                        "Customers by shared phone",
                        "GET",
                        "customers",
                        module="crm",
                        query={"phone": "{{shared_phone}}"},
                        check="pm.test('both customers on the shared number come back', () => pm.expect(pm.response.json().totalCount).to.eql(2));",
                    ),
                    item(
                        "Customer",
                        "GET",
                        "customers/{{park_customer_id}}",
                        module="crm",
                        check="pm.test('is residential', () => pm.expect(pm.response.json().type).to.eql('Residential'));",
                    ),
                    item(
                        "Customer contacts",
                        "GET",
                        "customers/{{park_customer_id}}/contacts",
                        module="crm",
                        check="pm.test('includes the adult child', () => pm.expect(pm.response.json().data.map(c => c.memo)).to.include('Adult child: David Park'));",
                    ),
                    item(
                        "Add customer contact",
                        "POST",
                        "customers/{{park_customer_id}}/contacts",
                        module="crm",
                        body={"type": "Phone", "value": "(718) 555-0199", "memo": "Owner"},
                    ),
                    item(
                        "Customer notes",
                        "GET",
                        "customers/{{park_customer_id}}/notes",
                        module="crm",
                        check="pm.test('has the seeded note', () => pm.expect(pm.response.json().totalCount).to.be.at.least(1));",
                    ),
                    item(
                        "Add customer note",
                        "POST",
                        "customers/{{park_customer_id}}/notes",
                        module="crm",
                        body={"text": "Prefers mornings.", "pinToTop": False},
                        check="pm.test('keeps the text as given', () => pm.expect(pm.response.json().text).to.eql('Prefers mornings.'));",
                    ),
                    item(
                        "Locations by address",
                        "GET",
                        "locations",
                        module="crm",
                        query={"street": "{{street}}", "zip": "{{zip}}"},
                        check="pm.test('finds the building', () => pm.expect(pm.response.json().totalCount).to.be.at.least(2));\n"
                        "pm.collectionVariables.set('building_location_id', pm.response.json().data[0].id);",
                    ),
                    item("Location", "GET", "locations/{{building_location_id}}", module="crm"),
                    item(
                        "Location contacts",
                        "GET",
                        "locations/{{building_location_id}}/contacts",
                        module="crm",
                    ),
                    item(
                        "Create customer",
                        "POST",
                        "customers",
                        module="crm",
                        body={
                            "name": "Newman Test",
                            "type": "Residential",
                            "address": TEST_ADDRESS,
                            "locations": [{"name": "9 Test St", "address": TEST_ADDRESS}],
                            "contacts": [{"type": "MobilePhone", "value": "(646) 555-0100"}],
                        },
                        headers={"Idempotency-Key": "newman-customer-{{run_id}}"},
                        pre="pm.collectionVariables.set('run_id', Date.now());",
                        check="pm.collectionVariables.set('new_customer_id', pm.response.json().id);\n"
                        "pm.collectionVariables.set('new_location_id', pm.response.json().locations[0].id);",
                    ),
                    item(
                        "Create location",
                        "POST",
                        "locations",
                        module="crm",
                        body='{"customerId": {{new_customer_id}}, "name": "10 Test St", "address": '
                        + json.dumps({**TEST_ADDRESS, "street": "10 Test St"})
                        + "}",
                    ),
                    item(
                        "Customers",
                        "GET",
                        "customers",
                        module="crm",
                        query={"name": "Newman"},
                        check="pm.test('finds the new customer', () => pm.expect(pm.response.json().totalCount).to.be.at.least(1));",
                    ),
                    item(
                        "Create booking",
                        "POST",
                        "booking-provider/501/bookings",
                        module="crm",
                        body={
                            "externalId": "newman-{{run_id}}",
                            "source": "newman",
                            "name": "Newman Test",
                            "summary": "No heat",
                            "isFirstTimeClient": True,
                            "address": TEST_ADDRESS,
                            "contacts": [{"type": "MobilePhone", "value": "(646) 555-0100"}],
                        },
                        check="pm.collectionVariables.set('booking_id', pm.response.json().id);\n"
                        "pm.test('is new, under the provider', () => pm.expect([pm.response.json().status, pm.response.json().bookingProviderId]).to.eql(['New', 501]));",
                    ),
                    item(
                        "Repeat booking returns the first",
                        "POST",
                        "booking-provider/501/bookings",
                        module="crm",
                        body={
                            "externalId": "newman-{{run_id}}",
                            "source": "newman",
                            "name": "Newman Test",
                            "summary": "No heat",
                            "isFirstTimeClient": True,
                            "address": TEST_ADDRESS,
                        },
                        check="pm.test('same booking', () => pm.expect(pm.response.json().id).to.eql(Number(pm.collectionVariables.get('booking_id'))));",
                    ),
                    item(
                        "Bookings",
                        "GET",
                        "bookings",
                        module="crm",
                        query={"externalId": "newman-{{run_id}}"},
                    ),
                    item("Booking", "GET", "bookings/{{booking_id}}", module="crm"),
                    item(
                        "Commercial lead",
                        "POST",
                        "leads",
                        module="crm",
                        body={
                            "campaignId": 302,
                            "callReasonId": 704,
                            "leadCustomerName": "Newman Deli",
                            "leadPhone": "(718) 555-0199",
                            "summary": "Walk-in cooler down.",
                        },
                        check="pm.collectionVariables.set('lead_id', pm.response.json().id);\n"
                        "pm.test('open, with the Commercial call reason', () => pm.expect([pm.response.json().status, pm.response.json().callReasonId]).to.eql(['Open', 704]));",
                    ),
                    item(
                        "Leads", "GET", "leads", module="crm", query={"leadPhone": "(718) 555-0199"}
                    ),
                    item("Lead", "GET", "leads/{{lead_id}}", module="crm"),
                ],
            ),
            folder(
                "3 Memberships and equipment",
                [
                    item(
                        "Membership types",
                        "GET",
                        "membership-types",
                        module="memberships",
                        check="pm.test('Summit Standard Membership has no perks block, only ServiceTitan fields', () => { const t = pm.response.json().data.find(t => t.name === 'Summit Standard Membership'); pm.expect(t).to.not.have.property('perks'); pm.expect(t.recurringServices.length).to.eql(2); });",
                    ),
                    item("Membership type", "GET", "membership-types/601", module="memberships"),
                    item(
                        "Expired membership reads as expired",
                        "GET",
                        "memberships",
                        module="memberships",
                        query={"customerIds": "{{moretti_customer_id}}"},
                        check="pm.test('listed and Expired', () => pm.expect(pm.response.json().data[0].status).to.eql('Expired'));",
                    ),
                    item(
                        "Member found by customer",
                        "GET",
                        "memberships",
                        module="memberships",
                        query={"customerIds": "{{park_customer_id}}"},
                        check="pm.test('one active membership', () => pm.expect(pm.response.json().data[0].status).to.eql('Active'));",
                    ),
                    item(
                        "Membership",
                        "GET",
                        "memberships/{{park_membership_id}}",
                        module="memberships",
                        check="pm.test('active', () => pm.expect(pm.response.json().status).to.eql('Active'));",
                    ),
                    item(
                        "Visits due",
                        "GET",
                        "recurring-service-events",
                        module="memberships",
                        query={"locationId": "{{park_location_id}}"},
                        check="pm.test('visits are named', () => pm.expect(pm.response.json().data[0].locationRecurringServiceName).to.be.a('string'));",
                    ),
                    item(
                        "Installed equipment",
                        "GET",
                        "installed-equipment",
                        module="equipmentsystems",
                        query={"locationIds": "{{park_location_id}}"},
                        check="pm.test('has a type object', () => pm.expect(pm.response.json().data[0].type.name).to.be.a('string'));",
                    ),
                ],
            ),
            folder(
                "4 Dispatch",
                [
                    item("Zones", "GET", "zones", module="dispatch"),
                    item(
                        "Arrival windows",
                        "GET",
                        "arrival-windows",
                        module="dispatch",
                        check="pm.test('four windows', () => pm.expect(pm.response.json().totalCount).to.eql(4));",
                    ),
                    item(
                        "Tag types",
                        "GET",
                        "tag-types",
                        module="settings",
                        check="pm.test('has the callback tags', () => pm.expect(pm.response.json().data.map(t => t.name)).to.include('Callback resolved'));",
                    ),
                    item(
                        "Call reasons",
                        "GET",
                        "call-reasons",
                        module="jbce",
                        check="pm.test('has lead reasons', () => pm.expect(pm.response.json().data.filter(r => r.isLead).length).to.be.at.least(1));",
                    ),
                    item(
                        "Technician shifts (on call)",
                        "GET",
                        "technician-shifts",
                        module="dispatch",
                        query={"shiftType": "OnCall"},
                    ),
                    item("Business units", "GET", "business-units", module="settings"),
                    item(
                        "Technicians",
                        "GET",
                        "technicians",
                        module="settings",
                        check="pm.collectionVariables.set('technician_id', pm.response.json().data[0].id);",
                    ),
                    item("Technician", "GET", "technicians/{{technician_id}}", module="settings"),
                    item("Campaigns", "GET", "campaigns", module="marketing"),
                    item(
                        "Capacity",
                        "POST",
                        "capacity",
                        module="dispatch",
                        body=cap_body,
                        pre=cap_pre,
                        check=PICK_WINDOWS,
                    ),
                ],
            ),
            folder(
                "5 Booking, reschedule, cancel",
                [
                    item(
                        "Job types",
                        "GET",
                        "job-types",
                        module="jpm",
                        check="pm.test('five job types', () => pm.expect(pm.response.json().totalCount).to.eql(5));",
                    ),
                    item("Job type", "GET", "job-types/203", module="jpm"),
                    item("Cancel reasons", "GET", "job-cancel-reasons", module="jpm"),
                    item(
                        "Book a job",
                        "POST",
                        "jobs",
                        module="jpm",
                        body=job_body,
                        pre="pm.collectionVariables.set('idem_key', 'newman-job-' + Date.now());",
                        headers={"Idempotency-Key": "{{idem_key}}", "X-Call-Id": "newman-call-1"},
                        check="pm.collectionVariables.set('job_id', pm.response.json().id);\n"
                        "pm.collectionVariables.set('appointment_id', pm.response.json().firstAppointmentId);\n"
                        "pm.test('scheduled', () => pm.expect(pm.response.json().jobStatus).to.eql('Scheduled'));",
                    ),
                    item(
                        "Book again with the same key",
                        "POST",
                        "jobs",
                        module="jpm",
                        body=job_body,
                        headers={"Idempotency-Key": "{{idem_key}}", "X-Call-Id": "newman-call-1"},
                        check="pm.test('replayed', () => pm.expect(pm.response.headers.get('Idempotent-Replayed')).to.eql('true'));\n"
                        "pm.test('same job', () => pm.expect(pm.response.json().id).to.eql(Number(pm.collectionVariables.get('job_id'))));",
                    ),
                    capacity_check(
                        "Capacity went down",
                        "pm.test('one fewer open', () => pm.expect(open(1)).to.eql(Number(pm.collectionVariables.get('win1_open')) - 1));",
                    ),
                    item(
                        "Who is on the appointment",
                        "GET",
                        "appointment-assignments",
                        module="dispatch",
                        query={"jobId": "{{job_id}}"},
                        check="pm.test('one technician', () => pm.expect(pm.response.json().totalCount).to.eql(1));\n"
                        "pm.collectionVariables.set('assigned_technician_id', pm.response.json().data[0].technicianId);",
                    ),
                    item(
                        "Assign the same technician",
                        "POST",
                        "appointment-assignments/assign-technicians",
                        module="dispatch",
                        body='{"jobAppointmentId": {{appointment_id}}, "technicianIds": [{{assigned_technician_id}}]}',
                        check="pm.test('answers with the appointment', () => pm.expect(pm.response.json().id).to.eql(Number(pm.collectionVariables.get('appointment_id'))));",
                    ),
                    item("Job", "GET", "jobs/{{job_id}}", module="jpm"),
                    item(
                        "Jobs for the customer",
                        "GET",
                        "jobs",
                        module="jpm",
                        query={"customerId": "{{park_customer_id}}", "jobStatus": "Scheduled"},
                        check="pm.test('the new job is open', () => pm.expect(pm.response.json().data.map(j => j.id)).to.include(Number(pm.collectionVariables.get('job_id'))));",
                    ),
                    item(
                        "Appointments for the job",
                        "GET",
                        "appointments",
                        module="jpm",
                        query={"jobId": "{{job_id}}"},
                        check="pm.test('one appointment', () => pm.expect(pm.response.json().totalCount).to.eql(1));",
                    ),
                    item("Appointment", "GET", "appointments/{{appointment_id}}", module="jpm"),
                    item(
                        "Add job note",
                        "POST",
                        "jobs/{{job_id}}/notes",
                        module="jpm",
                        body={"text": "Use the side gate."},
                        check="pm.test('a job note has no id', () => pm.expect(pm.response.json()).to.not.have.property('id'));",
                    ),
                    item(
                        "Job notes",
                        "GET",
                        "jobs/{{job_id}}/notes",
                        module="jpm",
                        check="pm.test('has the note', () => pm.expect(pm.response.json().totalCount).to.eql(1));",
                    ),
                    item(
                        "Reschedule",
                        "PATCH",
                        "appointments/{{appointment_id}}/reschedule",
                        module="jpm",
                        body={
                            "start": "{{win2_start}}",
                            "end": "{{win2_end}}",
                            "arrivalWindowStart": "{{win2_start}}",
                            "arrivalWindowEnd": "{{win2_end}}",
                        },
                        check="pm.test('moved', () => pm.expect(pm.response.json().arrivalWindowStart).to.eql(pm.collectionVariables.get('win2_start')));",
                    ),
                    capacity_check(
                        "Capacity moved with it",
                        "pm.test('window 1 is back', () => pm.expect(open(1)).to.eql(Number(pm.collectionVariables.get('win1_open'))));\n"
                        "pm.test('window 2 is one down', () => pm.expect(open(2)).to.eql(Number(pm.collectionVariables.get('win2_open')) - 1));",
                    ),
                    item(
                        "Job history",
                        "GET",
                        "jobs/{{job_id}}/history",
                        module="jpm",
                        check="pm.test('records the move', () => pm.expect(pm.response.json().data.map(e => e.eventType)).to.include('AppointmentRescheduled'));",
                    ),
                    item(
                        "Cancel",
                        "PUT",
                        "jobs/{{job_id}}/cancel",
                        module="jpm",
                        body={"reasonId": 401, "memo": "Newman cleanup"},
                        check="pm.test('answers with no body', () => pm.expect(pm.response.json()).to.eql({}));",
                    ),
                    item(
                        "Job after cancel",
                        "GET",
                        "jobs/{{job_id}}",
                        module="jpm",
                        check="pm.test('canceled', () => pm.expect(pm.response.json().jobStatus).to.eql('Canceled'));",
                    ),
                    item(
                        "Cancel again",
                        "PUT",
                        "jobs/{{job_id}}/cancel",
                        module="jpm",
                        body={"reasonId": 401, "memo": "Newman cleanup again"},
                    ),
                    capacity_check(
                        "Capacity came back",
                        "pm.test('window 2 is restored', () => pm.expect(open(2)).to.eql(Number(pm.collectionVariables.get('win2_open'))));",
                    ),
                ],
            ),
            folder(
                "6 Calls and logs",
                [
                    item(
                        "Phone system logs a call (mock only)",
                        "POST",
                        "debug/calls",
                        body={
                            "sid": "newman-call-{{run_id}}",
                            "direction": "Outbound",
                            "fromNumber": "(718) 555-0100",
                            "toNumber": "{{maria_phone}}",
                            "durationSeconds": 95,
                            "customerId": "{{maria_customer_id}}",
                        },
                        check="pm.collectionVariables.set('call_id', pm.response.json().id);\n"
                        "pm.test('an outbound call has no call type', () => pm.expect(pm.response.json().leadCall.callType).to.eql(null));\n"
                        "pm.test('duration is a time span', () => pm.expect(pm.response.json().leadCall.duration).to.eql('00:01:35'));",
                    ),
                    item(
                        "Calls from the repeat caller",
                        "GET",
                        "calls",
                        module="telecom",
                        version="v3",
                        query={"callerPhoneNumber": "{{maria_phone}}"},
                        check="pm.test('at least three calls', () => pm.expect(pm.response.json().totalCount).to.be.at.least(3));\n"
                        "pm.test('the call sits inside leadCall', () => pm.expect(pm.response.json().data[0].leadCall.from).to.be.a('string'));",
                    ),
                    item(
                        "Find a call by sid",
                        "GET",
                        "calls",
                        module="telecom",
                        version="v3",
                        query={"sid": "newman-call-{{run_id}}"},
                        check="pm.test('found', () => pm.expect(pm.response.json().totalCount).to.eql(1));",
                    ),
                    item(
                        "Outbound calls to a number",
                        "GET",
                        "calls",
                        module="telecom",
                        version="v3",
                        query={"phoneNumberCalled": "{{maria_phone}}"},
                        check="pm.test('includes the outbound call', () => pm.expect(pm.response.json().data.map(c => c.leadCall.direction)).to.include('Outbound'));",
                    ),
                    item(
                        "Call",
                        "GET",
                        "calls/{{call_id}}",
                        module="telecom",
                        check="pm.test('has a sid', () => pm.expect(pm.response.json().sid).to.be.a('string'));",
                    ),
                    item(
                        "Tag a call (how a callback is dispositioned)",
                        "PUT",
                        "calls/{{call_id}}",
                        module="telecom",
                        body={
                            "callType": None,
                            "excuseMemo": "",
                            "reason": {"name": "", "lead": False},
                            "tag": "Callback resolved",
                        },
                        check="pm.test('carries the tag', () => pm.expect(pm.response.json().tags.map(t => t.typeName)).to.include('Callback resolved'));",
                    ),
                    item(
                        "Calls tagged resolved",
                        "GET",
                        "calls",
                        module="telecom",
                        version="v3",
                        query={"tag": "Callback resolved"},
                        check="pm.test('the tagged call is listed', () => pm.expect(pm.response.json().data.map(c => c.leadCall.sid)).to.include('newman-call-' + pm.collectionVariables.get('run_id')));",
                    ),
                    item(
                        "Logs for a call id",
                        "GET",
                        "debug/logs",
                        query={"callId": "newman-call-1"},
                        check="pm.test('both booking requests are logged', () => pm.expect(pm.response.json().count).to.eql(2));",
                    ),
                    item(
                        "Logs, errors only",
                        "GET",
                        "debug/logs",
                        query={"errors": "true"},
                        check="pm.test('only failures', () => pm.response.json().data.forEach(r => pm.expect(r.status).to.be.at.least(400)));",
                    ),
                ],
            ),
        ],
    }


def environment() -> dict:
    values = {
        "base_url": "http://127.0.0.1:8101",
        "tenant_id": "1000",
        "client_id": "cid.summit-air-mock",
        "client_secret": "cs.summit-air-mock",
        "app_key": "ak1.summit-air-mock",
    }
    return {
        "name": "ServiceTitan mock (local)",
        "values": [{"key": k, "value": v, "enabled": True} for k, v in values.items()],
        "_postman_variable_scope": "environment",
    }


def main() -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / "servicetitan-mock.postman_collection.json").write_text(
        json.dumps(build(), indent=2) + "\n"
    )
    (OUT / "servicetitan-mock.postman_environment.json").write_text(
        json.dumps(environment(), indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
