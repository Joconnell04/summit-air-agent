"""Errors, auth, request logs, paging and per-company config."""
import json
import shutil

import yaml
from conftest import TENANT_DIR, TOMORROW, Api, build_small_world, job_body

from servicetitan_mock.app import create_app

ERROR_KEYS = {"status", "code", "title", "detail", "errors", "requestId"}


def test_every_kind_of_failure_uses_one_json_shape(small):
    api, ids = small
    customer_id, location_id = ids["home"]
    failures = {
        400: api.post("/jpm/jobs", {"customerId": "not a number"}),
        404: api.get("/crm/customers/99999"),
        409: None,
        422: api.post("/jpm/jobs", job_body(*ids["commercial"], TOMORROW, "morning")),
    }
    api.post("/jpm/jobs", job_body(customer_id, location_id, TOMORROW, "morning"))
    failures[409] = api.post("/jpm/jobs", job_body(customer_id, location_id, TOMORROW, "morning"))
    failures[401] = api.client.get(api.url("/crm/customers"))
    failures[403] = api.client.get(api.url("/crm/customers"), headers={**api.headers, "ST-App-Key": "wrong"})
    failures[404.1] = api.client.get("/no/such/path")
    for expected, response in failures.items():
        body = response.json()
        assert set(body) == ERROR_KEYS, expected
        assert body["status"] == response.status_code == int(expected)
        assert body["requestId"] == response.headers["X-Request-Id"]
    assert failures[400].json()["errors"]  # says which field was wrong


def test_the_token_endpoint_checks_credentials(small):
    api, _ = small
    bad = api.client.post("/connect/token", data={"grant_type": "client_credentials", "client_id": "x", "client_secret": "y"})
    assert bad.status_code == 400 and bad.json()["code"] == "invalid_client"
    wrong_grant = api.client.post("/connect/token", data={"grant_type": "password", "client_id": "x", "client_secret": "y"})
    assert wrong_grant.json()["code"] == "unsupported_grant_type"
    auth = api.app.state.cfg.auth
    good = api.client.post("/connect/token", data={
        "grant_type": "client_credentials", "client_id": auth["client_id"], "client_secret": auth["client_secret"]})
    assert good.json()["token_type"] == "Bearer" and good.json()["expires_in"] == 900


def test_an_expired_token_is_refused(small):
    api, _ = small
    token = api.headers["Authorization"].split()[1]
    api.app.state.tokens[token] = 0
    response = api.get("/crm/customers")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


def test_a_request_for_another_tenant_is_refused(small):
    api, _ = small
    response = api.client.get("/crm/v2/tenant/999/customers", headers=api.headers)
    assert response.status_code == 404
    assert response.json()["code"] == "unknown_tenant"


def test_lists_page_like_servicetitan(world):
    api, _ = world
    page = api.get("/crm/customers", pageSize=10, page=2).json()
    assert (page["page"], page["pageSize"], page["hasMore"], page["totalCount"]) == (2, 10, True, 300)
    assert len(page["data"]) == 10
    last = api.get("/crm/customers", pageSize=100, page=3).json()
    assert last["hasMore"] is False
    assert api.get("/crm/customers", pageSize=500).status_code == 400


def test_every_request_is_logged_as_one_json_line(small):
    api, _ = small
    api.get("/crm/customers", phone="718-555-0111")
    lines = api.app.state.log_path.read_text().splitlines()
    record = json.loads(lines[-1])
    assert {"ts", "requestId", "callId", "method", "path", "query", "status", "durationMs",
            "requestBody", "responseBody", "error"} <= set(record)
    assert (record["method"], record["status"], record["query"]) == ("GET", 200, "phone=718-555-0111")
    assert record["responseBody"]["totalCount"] == 0


def test_logs_can_be_filtered_by_call_id_request_id_and_errors(small):
    api, ids = small
    customer_id, location_id = ids["home"]
    api.post("/jpm/jobs", job_body(customer_id, location_id, TOMORROW, "morning"), key="a", **{"X-Call-Id": "call-A"})
    failed = api.post("/jpm/jobs", job_body(*ids["commercial"], TOMORROW, "morning"), **{"X-Call-Id": "call-A"})
    api.get("/crm/customers")
    api.request("GET", "/crm/customers", headers={"X-Call-Id": "call-B"})

    by_call = api.client.get("/debug/logs", params={"callId": "call-A"}).json()
    assert by_call["count"] == 2
    assert {r["callId"] for r in by_call["data"]} == {"call-A"}
    errors = api.client.get("/debug/logs", params={"callId": "call-A", "errors": True}).json()["data"]
    assert [r["error"] for r in errors] == ["commercial_not_supported"]
    one = api.client.get("/debug/logs", params={"requestId": failed.headers["X-Request-Id"]}).json()
    assert one["count"] == 1 and one["data"][0]["responseBody"]["code"] == "commercial_not_supported"
    assert api.client.get("/debug/logs", params={"callId": "nobody"}).json()["count"] == 0


def test_a_caller_supplied_request_id_is_kept_and_secrets_are_not_logged(small):
    api, _ = small
    auth = api.app.state.cfg.auth
    response = api.client.post(
        "/connect/token", headers={"X-Request-Id": "req-123"},
        data={"grant_type": "client_credentials", "client_id": auth["client_id"], "client_secret": auth["client_secret"]},
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
    config["company"]["name"] = "Frostline Heating"
    config["fees"]["diagnostic"] = 89
    config["zones"] = [{"id": 1, "name": "Nassau", "county": "Nassau", "zips": ["11501", "11550"]}]
    (tenant / "config.yaml").write_text(yaml.safe_dump(config))

    ids = build_small_world(tmp_path / "w.db")
    api = Api(create_app(tmp_path / "w.db", tenant, tmp_path / "w.log"))
    profile = api.get("/settings/company-profile").json()
    assert profile["name"] == "Frostline Heating"
    assert profile["fees"]["diagnostic"] == 89
    assert profile["serviceAreaZips"] == ["11501", "11550"]
    assert profile["counties"] == ["Nassau"]
    # 11201 is no longer in the service area, so the booking is refused for that reason.
    assert api.post("/jpm/jobs", job_body(*ids["home"], TOMORROW, "morning")).json()["code"] == "out_of_area"


def test_summit_air_leaves_fees_unset_so_the_agent_never_quotes_one(small):
    api, _ = small
    profile = api.get("/settings/company-profile").json()
    assert profile["fees"] == {"diagnostic": None, "after_hours": None}
    assert profile["afterHours"] == {"urgentOnly": True, "windows": ["after_hours"]}
    assert profile["counties"] == ["Kings", "Queens", "Richmond"]
    assert profile["hours"]["sun"] is None


def test_calls_can_be_logged_and_a_repeated_external_id_returns_the_first(small):
    api, ids = small
    body = {"externalId": "ext-1", "fromNumber": "(718) 555-0111", "customerId": ids["home"][0], "callType": "Booked"}
    first = api.post("/telecom/calls", body)
    second = api.post("/telecom/calls", body)
    assert (first.status_code, second.status_code) == (201, 200)
    assert second.json()["id"] == first.json()["id"]
    assert api.get("/telecom/calls", phone="7185550111").json()["totalCount"] == 1
    assert api.post("/telecom/calls", {**body, "fromNumber": "123"}).status_code == 400
