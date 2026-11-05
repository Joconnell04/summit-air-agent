"""A location the customer moved out of (issue #128): PATCH locations/{id} makes it inactive, lists
leave it out unless asked, and PATCH jobs/{id} moves an open job to another active location."""

from conftest import TOMORROW, job_body

ADDRESS = {"street": "380 Washington Ave", "unit": "", "city": "Brooklyn", "state": "NY",
           "zip": "11238", "country": "USA"}


def test_an_inactive_location_is_listed_only_when_asked_for(small):
    api, ids = small
    customer_id, location_id = ids["home"]
    patched = api.request("PATCH", f"/crm/locations/{location_id}", json={"active": False})
    assert patched.status_code == 200 and patched.json()["active"] is False
    listed = api.get("/crm/locations", customerId=customer_id).json()["data"]
    assert location_id not in [loc["id"] for loc in listed]
    every = api.get("/crm/locations", customerId=customer_id, active="Any").json()["data"]
    assert location_id in [loc["id"] for loc in every]


def test_an_open_job_moves_only_to_an_active_location_of_its_customer(small):
    api, ids = small
    customer_id, old_id = ids["home"]
    job = api.post("/jpm/jobs", job_body(customer_id, old_id, TOMORROW, "0800-1200")).json()
    new = api.post("/crm/locations", {"customerId": customer_id, "name": "New",
                                      "address": ADDRESS}).json()
    moved = api.request("PATCH", f"/jpm/jobs/{job['id']}", json={"locationId": new["id"]})
    assert moved.status_code == 200 and moved.json()["locationId"] == new["id"]
    api.request("PATCH", f"/crm/locations/{old_id}", json={"active": False})
    back = api.request("PATCH", f"/jpm/jobs/{job['id']}", json={"locationId": old_id})
    assert back.status_code == 422
    someone_else = ids["far"][1]
    assert api.request(
        "PATCH", f"/jpm/jobs/{job['id']}", json={"locationId": someone_else}
    ).status_code == 422


def test_the_customer_address_is_the_first_active_location(small):
    api, ids = small
    customer_id, old_id = ids["home"]
    api.post("/crm/locations", {"customerId": customer_id, "name": "New", "address": ADDRESS})
    api.request("PATCH", f"/crm/locations/{old_id}", json={"active": False})
    assert api.get(f"/crm/customers/{customer_id}").json()["address"]["street"] == ADDRESS["street"]
