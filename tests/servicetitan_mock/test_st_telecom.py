"""The call log as ServiceTitan has it: a v3 feed of bundles, a v2 call, classification by PUT, tags.
Calls are created by the phone system; this API cannot create one (the mock's /debug/calls stands in)."""

import sqlite3
from datetime import UTC, datetime

from conftest import CFG, build_small_world

from servicetitan_mock import worldkit
from servicetitan_mock.db import connect

V3 = "/telecom/v3/tenant/1000/calls"
V2 = "/telecom/v2/tenant/1000/calls"
WHEN = datetime(2026, 11, 2, 14, tzinfo=UTC)


def log_call(api, **overrides) -> dict:
    body = {
        "sid": "sid-1",
        "direction": "Outbound",
        "fromNumber": "(718) 555-0100",
        "toNumber": "(718) 555-0111",
        "durationSeconds": 95,
        **overrides,
    }
    response = api.client.post("/debug/calls", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def feed(api, **params) -> dict:
    return api.client.get(V3, params=params, headers=api.headers).json()


def test_a_v3_call_is_a_bundle_around_a_nested_lead_call(small):
    api, ids = small
    call = log_call(api, customerId=ids["home"][0], reason="No heat", callType="Booked")
    lead_call = call["leadCall"]
    assert set(call) == {"id", "jobNumber", "projectId", "businessUnit", "type", "leadCall"}
    assert {"sid", "tags", "agent", "modifiedOn"} <= set(call["leadCall"]), "inside leadCall"
    assert lead_call["sid"] == "sid-1"
    assert {"receivedOn", "duration", "from", "to", "direction", "callType", "reason"} <= set(
        lead_call
    )
    assert lead_call["duration"] == "00:01:35", "a time span, not a number of seconds"
    assert lead_call["reason"] == {"id": 710, "name": "No heat", "lead": False, "active": True}
    assert lead_call["customer"] == {"id": ids["home"][0], "name": "Pat Home"}
    assert "customerId" not in call and "externalId" not in call and "reason" not in call


def test_an_outbound_call_has_no_call_type_and_an_inbound_one_can_have_any_of_the_five(small):
    api, _ = small
    assert log_call(api, callType="Booked")["leadCall"]["callType"] is None
    for kind in ("Abandoned", "Unbooked", "Excused", "Booked", "NotLead"):
        sid = f"in-{kind}"
        made = log_call(api, sid=sid, direction="Inbound", callType=kind)
        assert made["leadCall"]["callType"] == kind
    unclassified = log_call(api, sid="in-none", direction="Inbound", callType=None)
    assert unclassified["leadCall"]["callType"] is None
    refused = api.client.post(
        "/debug/calls",
        json={
            "direction": "Inbound",
            "fromNumber": "(718) 555-0111",
            "toNumber": "x",
            "callType": "Hot",
        },
    )
    assert refused.status_code == 400


def test_the_feed_filters_by_who_called_who_was_called_sid_tag_and_time(small):
    api, _ = small
    log_call(
        api, sid="a", direction="Inbound", fromNumber="(718) 555-0111", toNumber="(718) 555-0100"
    )
    log_call(api, sid="b", toNumber="(718) 555-0122", tags=["Callback resolved"])
    log_call(
        api,
        sid="c",
        toNumber="(718) 555-0122",
        tags=["No contact"],
        receivedOn="2026-11-01T10:00:00Z",
    )
    assert [c["leadCall"]["sid"] for c in feed(api, callerPhoneNumber="7185550111")["data"]] == [
        "a"
    ]
    assert [
        c["leadCall"]["sid"] for c in feed(api, phoneNumberCalled="(718) 555-0122")["data"]
    ] == ["b", "c"]
    assert [c["leadCall"]["sid"] for c in feed(api, sid="b")["data"]] == ["b"]
    assert [c["leadCall"]["sid"] for c in feed(api, tag="Callback resolved")["data"]] == ["b"]
    assert feed(api, tag="No such tag")["data"] == []
    since = feed(api, createdOnOrAfter="2026-11-02T00:00:00Z")
    assert [c["leadCall"]["sid"] for c in since["data"]] == ["b", "a"], "newest first"
    recent = feed(api, phoneNumberCalled="7185550122", createdOnOrAfter="2026-11-02T00:00:00Z")
    assert [c["leadCall"]["sid"] for c in recent["data"]] == ["b"]
    assert feed(api, minDuration=100)["data"] == []
    everything = feed(api)
    assert everything["totalCount"] == 3 and everything["hasMore"] is False
    assert [c["leadCall"]["sid"] for c in everything["data"]] == ["b", "a", "c"]


def test_a_tag_is_the_tag_type_name_and_shows_with_its_id(small):
    api, _ = small
    call = log_call(api, tags=["Callback resolved"])
    assert call["leadCall"]["tags"] == [{"id": 801, "typeName": "Callback resolved"}]
    assert [t["name"] for t in api.get("/settings/tag-types").json()["data"]] == [
        "Callback resolved",
        "Callback open",
        "No contact",
        "Urgent",
        "Routine",
    ]


def test_one_call_by_id_is_the_v2_shape_and_put_classifies_it(small):
    api, _ = small
    call = log_call(api)
    detail = api.client.get(f"{V2}/{call['id']}", headers=api.headers).json()
    assert detail["sid"] == "sid-1" and detail["direction"] == "Outbound"
    assert "leadCall" not in detail and detail["duration"] == "00:01:35"
    classified = api.client.put(
        f"{V2}/{call['id']}",
        json={
            "callType": None,
            "excuseMemo": "",
            "reason": {"name": "Billing question", "lead": False},
            "tag": "Callback open",
        },
        headers=api.headers,
    )
    assert classified.status_code == 200
    assert classified.json()["reason"]["name"] == "Billing question"
    assert [t["typeName"] for t in classified.json()["tags"]] == ["Callback open"]
    # A second tag is added, never replaced, and the same tag twice is one tag.
    for tag in ("Callback resolved", "Callback resolved"):
        again = api.client.put(
            f"{V2}/{call['id']}",
            json={"tag": tag, "excuseMemo": "", "reason": {"name": "", "lead": False}},
            headers=api.headers,
        )
    assert [t["typeName"] for t in again.json()["tags"]] == ["Callback resolved", "Callback open"]
    assert again.json()["reason"]["name"] == "Billing question", "an empty reason changes nothing"


def test_classifying_refuses_unknown_tags_reasons_jobs_and_calls(small):
    api, _ = small
    call = log_call(api)
    for body, field in (
        ({"tag": "Made up"}, "tag"),
        ({"reason": {"name": "Made up", "lead": False}}, "reason"),
        ({"jobId": 9999}, "jobId"),
    ):
        response = api.client.put(f"{V2}/{call['id']}", json=body, headers=api.headers)
        assert response.status_code == 422 and field in response.json()["errors"]
    assert (
        api.client.put(f"{V2}/9999", json={"tag": "No contact"}, headers=api.headers).status_code
        == 404
    )
    assert api.client.get(f"{V2}/9999", headers=api.headers).status_code == 404


def test_servicetitans_api_cannot_create_a_call(small):
    api, _ = small
    body = {"fromNumber": "(718) 555-0111", "toNumber": "(718) 555-0100"}
    for path in (V2, V3):
        assert api.client.post(path, json=body, headers=api.headers).status_code in (404, 405)


def test_the_debug_call_endpoint_repeats_a_sid_as_the_first_record_and_needs_no_credentials(small):
    api, _ = small
    first = log_call(api, sid="same")
    second = log_call(api, sid="same", durationSeconds=5)
    assert second["id"] == first["id"] and second["leadCall"]["duration"] == "00:01:35"
    assert feed(api)["totalCount"] == 1
    assert api.client.post("/debug/calls", json={}).status_code == 400


def test_a_call_logged_without_a_time_gets_the_mocks_clock(small):
    api, _ = small
    assert log_call(api)["leadCall"]["receivedOn"] == "2026-11-02T14:00:00Z"


def test_world_builder_calls_carry_reasons_and_tags_by_name(tmp_path):
    db = tmp_path / "w.db"
    build_small_world(db)
    conn = connect(db)
    call_id = worldkit.add_call(
        conn, CFG, WHEN, "(718) 555-0111", reason="Tune-up", tags=("No contact",), sid="s"
    )
    assert (
        conn.execute("SELECT tag_type_id FROM call_tags WHERE call_id = ?", (call_id,)).fetchone()[
            0
        ]
        == 803
    )
    for kwargs in ({"reason": "Nonsense"}, {"tags": ("Nonsense",)}):
        try:
            worldkit.add_call(conn, CFG, WHEN, "(718) 555-0111", **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(kwargs)
    conn.close()
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 1
