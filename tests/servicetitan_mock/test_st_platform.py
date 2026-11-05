"""Errors, auth, request logs, paging and per-company config."""

import json
import shutil
from datetime import UTC, date, datetime

import yaml
from conftest import TENANT_DIR, TOMORROW, Api, build_small_world, job_body

from servicetitan_mock import logs, worldkit
from servicetitan_mock.app import create_app
from servicetitan_mock.config import TenantConfig
from servicetitan_mock.db import connect

# ServiceTitan's problem-details body. data.code is the mock's own (not in the real API).
ERROR_KEYS = {"type", "title", "status", "traceId", "errors", "data"}


def test_every_kind_of_failure_uses_one_json_shape(small):
    api, ids = small
    customer_id, location_id = ids["home"]
    failures = {
        400: api.post("/jpm/jobs", {"customerId": "not a number"}),
        404: api.get("/crm/customers/99999"),
        409: None,
        422: api.post("/jpm/jobs", job_body(*ids["home"], TOMORROW, "morning", job_type=999)),
    }
    for _ in range(3):  # all three technicians
        api.post("/jpm/jobs", job_body(customer_id, location_id, TOMORROW, "morning"))
    failures[409] = api.post("/jpm/jobs", job_body(customer_id, location_id, TOMORROW, "morning"))
    failures[401] = api.client.get(api.url("/crm/customers"))
    failures[403] = api.client.get(
        api.url("/crm/customers"), headers={**api.headers, "ST-App-Key": "wrong"}
    )
    failures[404.1] = api.client.get("/no/such/path")
    for expected, response in failures.items():
        body = response.json()
        assert set(body) == ERROR_KEYS, expected
        assert body["status"] == response.status_code == int(expected)
        assert body["traceId"] == response.headers["X-Request-Id"]
        assert body["type"].startswith("https://tools.ietf.org/html/")
        assert isinstance(body["errors"], dict) and isinstance(body["data"], dict)
    assert failures[400].json()["errors"]  # says which field was wrong
    assert all(isinstance(v, list) for v in failures[400].json()["errors"].values())
    assert failures[409].json()["data"]["code"] == "no_capacity"


def test_the_token_endpoint_checks_credentials(small):
    api, _ = small
    bad = api.client.post(
        "/connect/token",
        data={"grant_type": "client_credentials", "client_id": "x", "client_secret": "y"},
    )
    assert bad.status_code == 400 and bad.json()["data"]["code"] == "invalid_client"
    wrong_grant = api.client.post(
        "/connect/token", data={"grant_type": "password", "client_id": "x", "client_secret": "y"}
    )
    assert wrong_grant.json()["data"]["code"] == "unsupported_grant_type"
    auth = api.app.state.cfg.auth
    good = api.client.post(
        "/connect/token",
        data={
            "grant_type": "client_credentials",
            "client_id": auth["client_id"],
            "client_secret": auth["client_secret"],
        },
    )
    assert good.json()["token_type"] == "Bearer" and good.json()["expires_in"] == 900


def test_an_expired_token_is_refused(small):
    api, _ = small
    token = api.headers["Authorization"].split()[1]
    api.app.state.tokens[token] = 0
    response = api.get("/crm/customers")
    assert response.status_code == 401
    assert response.json()["data"]["code"] == "unauthorized"


def test_a_request_for_another_tenant_is_refused(small):
    api, _ = small
    response = api.client.get("/crm/v2/tenant/999/customers", headers=api.headers)
    assert response.status_code == 404
    assert response.json()["data"]["code"] == "unknown_tenant"


def test_lists_page_like_servicetitan(world):
    api, _ = world
    page = api.get("/crm/customers", pageSize=10, page=2).json()
    assert (page["page"], page["pageSize"], page["hasMore"], page["totalCount"]) == (
        2,
        10,
        True,
        300,
    )
    assert len(page["data"]) == 10
    last = api.get("/crm/customers", pageSize=100, page=3).json()
    assert last["hasMore"] is False
    assert api.get("/crm/customers", pageSize=500).status_code == 400


def test_every_request_is_logged_as_one_json_line(small):
    api, _ = small
    api.get("/crm/customers", phone="718-555-0111")
    lines = api.app.state.log_path.read_text().splitlines()
    record = json.loads(lines[-1])
    assert {
        "ts",
        "requestId",
        "callId",
        "method",
        "path",
        "query",
        "status",
        "durationMs",
        "requestBody",
        "responseBody",
        "error",
    } <= set(record)
    assert (record["method"], record["status"], record["query"]) == (
        "GET",
        200,
        "phone=718-555-0111",
    )
    assert record["responseBody"]["totalCount"] == 0


def test_logs_can_be_filtered_by_call_id_request_id_and_errors(small):
    api, ids = small
    customer_id, location_id = ids["home"]
    api.post(
        "/jpm/jobs",
        job_body(customer_id, location_id, TOMORROW, "morning"),
        key="a",
        **{"X-Call-Id": "call-A"},
    )
    failed = api.post(
        "/jpm/jobs",
        job_body(customer_id, location_id, TOMORROW, "morning", job_type=999),
        **{"X-Call-Id": "call-A"},
    )
    api.get("/crm/customers")
    api.request("GET", "/crm/customers", headers={"X-Call-Id": "call-B"})

    by_call = api.client.get("/debug/logs", params={"callId": "call-A"}).json()
    assert by_call["count"] == 2
    assert {r["callId"] for r in by_call["data"]} == {"call-A"}
    errors = api.client.get("/debug/logs", params={"callId": "call-A", "errors": True}).json()[
        "data"
    ]
    assert [r["error"] for r in errors] == ["unknown_reference"]
    one = api.client.get("/debug/logs", params={"requestId": failed.headers["X-Request-Id"]}).json()
    assert (
        one["count"] == 1 and one["data"][0]["responseBody"]["data"]["code"] == "unknown_reference"
    )
    assert api.client.get("/debug/logs", params={"callId": "nobody"}).json()["count"] == 0


def test_a_caller_supplied_request_id_is_kept_and_secrets_are_not_logged(small):
    api, _ = small
    auth = api.app.state.cfg.auth
    response = api.client.post(
        "/connect/token",
        headers={"X-Request-Id": "req-123"},
        data={
            "grant_type": "client_credentials",
            "client_id": auth["client_id"],
            "client_secret": auth["client_secret"],
        },
    )
    assert response.headers["X-Request-Id"] == "req-123"
    logged = api.client.get("/debug/logs", params={"requestId": "req-123"}).json()["data"][0]
    assert logged["requestBody"]["client_secret"] == "***"
    assert logged["responseBody"]["access_token"] == "***"
    assert auth["client_secret"] not in api.app.state.log_path.read_text()


def test_company_facts_come_from_the_config_file_not_the_code(tmp_path):
    tenant = tmp_path / "frostline"
    shutil.copytree(TENANT_DIR, tenant)
    config = yaml.safe_load((tenant / "config.yaml").read_text())
    config["zones"] = [{"id": 1, "name": "Nassau", "county": "Nassau", "zips": ["11501", "11550"]}]
    config["tag_types"] = [{"id": 9, "name": "Called back", "code": "CB", "active": True}]
    config["call_reasons"] = [{"id": 5, "name": "Boiler", "isLead": False, "active": True}]
    (tenant / "config.yaml").write_text(yaml.safe_dump(config))

    build_small_world(tmp_path / "w.db")
    api = Api(create_app(tmp_path / "w.db", tenant, tmp_path / "w.log"))
    zones = api.get("/dispatch/zones").json()["data"]
    assert [(z["name"], z["zips"]) for z in zones] == [("Nassau", ["11501", "11550"])]
    assert "county" not in zones[0], "ServiceTitan zones carry zips and cities, not counties"
    assert [t["name"] for t in api.get("/settings/tag-types").json()["data"]] == ["Called back"]
    assert [r["name"] for r in api.get("/jbce/call-reasons").json()["data"]] == ["Boiler"]


def test_there_is_no_company_profile_endpoint_because_servicetitan_has_none(small):
    api, _ = small
    assert api.get("/settings/company-profile").status_code == 404


def test_summit_air_leaves_fees_unset_so_the_agent_never_quotes_one():
    cfg = TenantConfig.load(TENANT_DIR)
    assert cfg.fees == {"diagnostic": None, "after_hours": None}
    # Open every day since issue #149 (DECISIONS.md 095).
    assert cfg.company["hours"]["sun"] == ["08:00", "20:00"]
    # All five boroughs since issue #149 (DECISIONS.md 099).
    assert sorted({z["county"] for z in cfg.zones}) == [
        "Bronx", "Kings", "New York", "Queens", "Richmond"
    ]


def test_the_arrival_windows_are_listed_the_way_servicetitan_lists_them(small):
    api, _ = small
    windows = api.get("/dispatch/arrival-windows").json()["data"]
    assert [(w["start"], w["duration"]) for w in windows] == [
        ("08:00:00", "04:00:00"),
        ("12:00:00", "04:00:00"),
        ("16:00:00", "04:00:00"),
        ("20:00:00", "12:00:00"),
    ]
    assert set(windows[0]) == {"id", "start", "duration", "businessUnitIds", "active"}


def tenant_copy(tmp_path, edit) -> TenantConfig:
    """A copy of the Summit Air tenant folder with its config changed by edit(config)."""
    tenant = tmp_path / "other-co"
    shutil.copytree(TENANT_DIR, tenant)
    config = yaml.safe_load((tenant / "config.yaml").read_text())
    edit(config)
    (tenant / "config.yaml").write_text(yaml.safe_dump(config))
    return TenantConfig.load(tenant)


def test_a_lead_carries_a_call_reason_from_the_config_and_servicetitans_field_names(small):
    api, ids = small
    lead = {
        "campaignId": 301,
        "callReasonId": 701,
        "leadCustomerName": "Pat Home",
        "leadPhone": "(718) 555-0123",
        "leadStreet": "1 Main St",
        "leadZip": "11201",
        "summary": "New boiler.",
    }
    made = api.post("/crm/leads", lead)
    assert made.status_code == 200
    body = made.json()
    assert body["status"] == "Open" and body["callReasonId"] == 701
    assert body["leadCustomerName"] == "Pat Home" and body["leadZip"] == "11201"
    assert not {"kind", "customerType", "name", "phone"} & set(body)
    unknown = api.post("/crm/leads", {**lead, "callReasonId": 999})
    assert unknown.status_code == 422
    assert unknown.json()["errors"]["callReasonId"]
    assert (
        api.post("/crm/leads", {k: v for k, v in lead.items() if k != "campaignId"}).status_code
        == 400
    )
    found = api.get("/crm/leads", leadPhone="7185550123").json()["data"]
    assert [lead_["id"] for lead_ in found] == [body["id"]]
    # The company's rule that a commercial caller leaves a lead and cannot book is not ServiceTitan's.
    commercial = api.post(
        "/crm/leads", {**lead, "callReasonId": 704, "customerId": ids["commercial"][0]}
    )
    assert commercial.status_code == 200


def test_the_world_builder_reads_names_and_contact_details_from_the_config(tmp_path):
    cfg = tenant_copy(
        tmp_path,
        lambda c: (
            c["company"].update(phone="(516) 555-0100", email_domain="frostline.example"),
            c["equipment_labels"].update(furnace="gas furnace"),
            c["membership_types"][0].update(
                visits=[{"name": "Seasonal check", "months": list(range(1, 13))}]
            ),
        ),
    )
    db = tmp_path / "w.db"
    ids = build_small_world(db)
    conn = connect(db)
    worldkit.add_technician(conn, cfg, 9001, "Sam Rivera", 11, 1, ["heating"])
    worldkit.add_equipment(conn, cfg, *ids["home"], "furnace", "Carrier", "F-1", date(2020, 1, 1))
    worldkit.add_equipment(conn, cfg, *ids["home"], "wood_stove", "Jotul", "W-1", date(2020, 1, 1))
    worldkit.add_call(conn, cfg, datetime(2026, 11, 2, 14, tzinfo=UTC), "(718) 555-0111")
    assert conn.execute("SELECT email FROM technicians WHERE id = 9001").fetchone()[0] == (
        "sam.rivera@frostline.example"
    )
    assert [r[0] for r in conn.execute("SELECT name FROM installed_equipment ORDER BY id")] == [
        "Carrier gas furnace",
        "Jotul wood stove",
    ]
    assert conn.execute("SELECT to_number FROM calls").fetchone()[0] == "(516) 555-0100"
    conn.close()


def test_membership_visit_names_come_from_the_config(tmp_path):
    cfg = tenant_copy(
        tmp_path,
        lambda c: c["membership_types"][0].update(
            visits=[
                {"name": "Spring check", "months": [3, 4, 5]},
                {"name": "Autumn check", "months": [9, 10, 11]},
            ]
        ),
    )
    plan = cfg.membership_types[0]
    assert cfg.visit_name(plan, date(2026, 4, 1)) == "Spring check"
    assert cfg.visit_name(plan, date(2026, 10, 1)) == "Autumn check"
    try:
        cfg.visit_name(plan, date(2026, 1, 1))
    except ValueError as error:
        assert "month 1" in str(error)
    else:
        raise AssertionError("a month with no visit name should be an error")
    # Summit Air names its visits by season.
    summit = TenantConfig.load(TENANT_DIR)
    assert (
        summit.visit_name(summit.membership_types[0], date(2026, 11, 1)) == "Heating system tune-up"
    )
    assert (
        summit.visit_name(summit.membership_types[0], date(2026, 6, 1)) == "Cooling system tune-up"
    )


def test_the_urgent_hold_comes_from_the_config(tmp_path):
    cfg = tenant_copy(
        tmp_path,
        lambda c: c["urgent_hold"].update(business_unit_ids=[12], per_day={"tue": {"morning": 1}}),
    )
    tuesday, wednesday = date(2026, 11, 3), date(2026, 11, 4)
    assert cfg.hold_for(tuesday, "morning", 12) == 1
    assert cfg.hold_for(tuesday, "morning", 11) == 0  # business unit not listed
    assert cfg.hold_for(tuesday, "afternoon", 12) == 0  # window not listed
    assert cfg.hold_for(wednesday, "morning", 12) == 0  # day not listed
    summit = TenantConfig.load(TENANT_DIR)
    assert summit.hold_for(tuesday, "morning", 11) == 2
    assert summit.hold_for(date(2026, 11, 7), "morning", 11) == 1  # Saturday
    assert summit.hold_for(date(2026, 11, 8), "morning", 11) == 1  # Sunday, open since issue #149


def test_debug_logs_limit_must_be_from_1_to_1000(small):
    api, _ = small
    api.get("/crm/customers")
    for bad in (0, -1, 1001):
        response = api.client.get("/debug/logs", params={"limit": bad})
        assert response.status_code == 422
        assert set(response.json()) == ERROR_KEYS
    assert api.client.get("/debug/logs", params={"limit": 1000}).status_code == 200
    assert api.client.get("/debug/logs", params={"limit": 1}).json()["count"] == 1


def test_a_log_line_that_cannot_be_parsed_is_skipped(small):
    api, _ = small
    api.get("/crm/customers")
    with api.app.state.log_path.open("a") as f:
        f.write('{"ts": "2026-11-02T14:00:00Z", "requ\n')  # a write cut off by a crash
    api.get("/crm/customers", phone="718-555-0111")
    lines = api.app.state.log_path.read_text().splitlines()
    rows = logs.read_logs(api.app.state.log_path)
    assert len(rows) == len(lines) - 1
    assert rows[-1]["query"] == "phone=718-555-0111"
    assert api.client.get("/debug/logs").json()["count"] == len(rows)
