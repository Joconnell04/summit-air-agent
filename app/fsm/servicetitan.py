"""ServiceTitan adapter. It speaks ServiceTitan's wire shapes (docs/explorations/006), so moving from
the mock to a real tenant changes the base URL, the token URL and the credentials, nothing else.

What the adapter does not rely on, because a real tenant has none of it: an Idempotency-Key header
(a job is found again by a reference left in its summary), a company-profile endpoint (company
facts come from the tenant config), a health route (the token request stands in), and a way to
create a call-log entry (the phone system writes those; the optional log_calls_via_debug switch
below is the mock's stand-in for it).
"""

import hashlib
import threading
import time
from datetime import datetime, timedelta

import httpx

from ..db import parse_iso
from .base import FsmRejected, FsmUnavailable, fsm_timings

PAGE_SIZE = 200
MAX_PAGES = 10
CALL_MATCH_WINDOW = timedelta(minutes=10)


def _utc(moment: datetime) -> str:
    return moment.isoformat()


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def ref_of(key: str) -> str:
    """A short, stable token for an idempotency key. It goes into the job summary, where a person
    can read it and where the adapter can find it again."""
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _code(status: int) -> str:
    """ServiceTitan's errors carry no machine code, so the adapter names what the status means."""
    return {401: "unauthorized", 403: "unauthorized", 404: "not_found", 409: "slot_taken"}.get(
        status, "rejected"
    )


class ServiceTitanAdapter:
    def __init__(
        self,
        http: httpx.Client,
        tenant_id: int,
        client_id: str,
        client_secret: str,
        app_key: str,
        token_url: str = "/connect/token",
        state: str = "",
        country: str = "USA",
        log_calls_via_debug: bool = False,
    ):
        self.http = http
        self.tenant_id = tenant_id
        self.credentials = {"client_id": client_id, "client_secret": client_secret}
        self.app_key = app_key
        self.token_url = token_url
        # Required on every address ServiceTitan stores; the company's own, from the tenant config.
        self.state = state
        self.country = country
        self.log_calls_via_debug = log_calls_via_debug
        self._token = ""
        self._token_expires = 0.0
        self._token_lock = threading.Lock()

    # ---- plumbing ---------------------------------------------------------------------------

    def _send(self, method: str, url: str, op: str, **kwargs) -> httpx.Response:
        started = time.perf_counter()
        status = None
        try:
            response = self.http.request(method, url, **kwargs)
            status = response.status_code
        except httpx.HTTPError as exc:
            raise FsmUnavailable(f"{op}: {type(exc).__name__}") from exc
        finally:
            timings = fsm_timings.get()
            if timings is not None:
                ms = round((time.perf_counter() - started) * 1000, 1)
                timings.append({"op": op, "ms": ms, "status": status})
        if status >= 500:
            raise FsmUnavailable(f"{op}: HTTP {status}")
        return response

    def _token_header(self, refresh: bool = False) -> str:
        with self._token_lock:
            if refresh or not self._token or time.time() >= self._token_expires - 30:
                response = self._send(
                    "POST",
                    self.token_url,
                    "token",
                    data={"grant_type": "client_credentials", **self.credentials},
                )
                if response.status_code != 200:
                    raise FsmUnavailable(f"token: HTTP {response.status_code}")
                body = response.json()
                self._token = body["access_token"]
                self._token_expires = time.time() + body.get("expires_in", 900)
            return self._token

    def _call(
        self,
        method: str,
        module: str,
        path: str,
        op: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        version: str = "v2",
    ) -> dict:
        url = f"/{module}/{version}/tenant/{self.tenant_id}/{path}"
        for attempt in range(2):
            headers = {
                "Authorization": f"Bearer {self._token_header(refresh=attempt > 0)}",
                "ST-App-Key": self.app_key,
            }
            response = self._send(method, url, op, params=params, json=json, headers=headers)
            if response.status_code != 401:
                break
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {}
            raise FsmRejected(
                response.status_code, _code(response.status_code), str(body.get("title", ""))
            )
        return response.json() if response.content else {}

    def _list(self, module: str, path: str, op: str, version: str = "v2", **params) -> list[dict]:
        """Every page of a list, up to MAX_PAGES of them."""
        params = {k: v for k, v in params.items() if v is not None}
        found: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            body = self._call(
                "GET",
                module,
                path,
                op,
                params={**params, "pageSize": PAGE_SIZE, "page": page},
                version=version,
            )
            found += body["data"]
            if not body.get("hasMore"):
                break
        return found

    # ---- reads ------------------------------------------------------------------------------

    def customers_by_phone(self, phone: str) -> list[dict]:
        return self._list("crm", "customers", "customers_by_phone", phone=phone)

    def locations_by_address(self, street: str, zip_code: str | None, unit: str | None) -> list[dict]:
        return self._list(
            "crm", "locations", "locations_by_address", street=street, zip=zip_code, unit=unit
        )

    def customer(self, customer_id: int) -> dict:
        return self._call("GET", "crm", f"customers/{customer_id}", "customer")

    def contacts(self, customer_id: int) -> list[dict]:
        """A customer's phone numbers and emails. The customer record does not hold them."""
        return self._list("crm", f"customers/{customer_id}/contacts", "contacts")

    def locations(self, customer_id: int, include_inactive: bool = False) -> list[dict]:
        """The customer's active locations, as ServiceTitan lists them by default. A location the
        customer moved out of is inactive (tools.record_move); include_inactive lists those too."""
        return self._list(
            "crm",
            "locations",
            "locations",
            customerId=customer_id,
            active="Any" if include_inactive else None,
        )

    def location(self, location_id: int) -> dict:
        return self._call("GET", "crm", f"locations/{location_id}", "location")

    def memberships(self, customer_id: int) -> list[dict]:
        return self._list("memberships", "memberships", "memberships", customerIds=customer_id)

    def jobs(self, customer_id: int, statuses: list[str] | None = None) -> list[dict]:
        """The customer's jobs. ServiceTitan filters on one status at a time, so a list of statuses
        is applied here."""
        found = self._list("jpm", "jobs", "jobs", customerId=customer_id)
        return [j for j in found if not statuses or j["jobStatus"] in statuses]

    def job(self, job_id: int) -> dict:
        return self._call("GET", "jpm", f"jobs/{job_id}", "job")

    def appointments(self, job_id: int) -> list[dict]:
        return self._list("jpm", "appointments", "appointments", jobId=job_id)

    def job_notes(self, job_id: int) -> list[dict]:
        return self._list("jpm", f"jobs/{job_id}/notes", "job_notes")

    def calls_from(self, phone: str, since: datetime | None = None) -> list[dict]:
        """Calls from this number, newest first, as the v3 call feed lists them."""
        return self._list(
            "telecom",
            "calls",
            "calls",
            version="v3",
            callerPhoneNumber=phone,
            createdOnOrAfter=_stamp(since) if since else None,
        )

    def calls_to(self, phone: str, since: datetime | None = None) -> list[dict]:
        """Outbound calls to this number since a time, newest first."""
        found = self._list(
            "telecom",
            "calls",
            "calls",
            version="v3",
            phoneNumberCalled=phone,
            createdOnOrAfter=_stamp(since) if since else None,
        )
        return [c for c in found if c["leadCall"]["direction"] == "Outbound"]

    def find_call(self, sid: str) -> dict | None:
        found = self._list("telecom", "calls", "find_call", version="v3", sid=sid)
        return found[0] if found else None

    def find_outbound_call(self, sid: str, phone: str, started: datetime) -> dict | None:
        """The call ServiceTitan logged for a call we placed. By its sid when ServiceTitan holds the
        id our telephony gave it (ASSUMED to match); otherwise the outbound call to that number
        logged closest to the time it started, within CALL_MATCH_WINDOW."""
        found = self.find_call(sid)
        if found is not None:
            return found
        near = [
            c
            for c in self.calls_to(phone, started - CALL_MATCH_WINDOW)
            if abs(parse_iso(c["leadCall"]["receivedOn"]) - started) <= CALL_MATCH_WINDOW
        ]
        return min(
            near, key=lambda c: abs(parse_iso(c["leadCall"]["receivedOn"]) - started), default=None
        )

    def notes(self, customer_id: int) -> list[dict]:
        return self._list("crm", f"customers/{customer_id}/notes", "notes")

    def equipment(self, customer_id: int, location_ids: list[int] | None = None) -> list[dict]:
        """Installed equipment at the customer's locations (the list filters on locations). Pass the
        location ids when the caller has them already."""
        ids = (
            location_ids
            if location_ids is not None
            else [loc["id"] for loc in self.locations(customer_id)]
        )
        if not ids:
            return []
        return self._list(
            "equipmentsystems",
            "installed-equipment",
            "equipment",
            locationIds=",".join(map(str, ids)),
        )

    def capacity(
        self, start: datetime, end: datetime, business_unit_id: int, job_type_id: int
    ) -> list[dict]:
        """Time frames in the range with the technicians free in each. The company's rules on top
        (urgent holds, after-hours) are applied by the caller, app/capacity.py."""
        body = {
            "startsOnOrAfter": _utc(start),
            "endsOnOrBefore": _utc(end),
            "businessUnitIds": [business_unit_id],
            "jobTypeId": job_type_id,
            "skillBasedAvailability": True,
        }
        return self._call("POST", "dispatch", "capacity", "capacity", json=body)["availabilities"]

    def find_job(self, customer_id: int, key: str) -> dict | None:
        """The job an earlier attempt with this key already made, found by the reference in its
        summary. This is how a retry after a lost answer avoids booking twice."""
        marker = f"Ref: {ref_of(key)}"
        for job in self._list("jpm", "jobs", "find_job", customerId=customer_id):
            if marker in (job.get("summary") or ""):
                return job
        return None

    def health(self) -> bool:
        """Can the adapter sign in? (ServiceTitan has no health route.)"""
        try:
            self._token_header()
        except FsmUnavailable:
            return False
        return True

    # ---- writes -----------------------------------------------------------------------------

    def _address(self, address: dict) -> dict:
        return {
            "street": address["street"],
            "unit": address.get("unit") or "",
            "city": address.get("city") or "",
            "state": address.get("state") or self.state,
            "zip": address["zip"],
            "country": self.country,
        }

    def create_customer(
        self, key: str, name: str, address: dict, phone: str, contact_name: str | None = None
    ) -> dict:
        """contact_name is the person the phone belongs to when it isn't the account holder (a
        caller booking a parent's house): ServiceTitan keeps who a number reaches in its memo."""
        place = self._address(address)
        body = {
            "name": name,
            "type": "Residential",
            "address": place,
            "locations": [{"name": place["street"], "address": place}],
            "contacts": [{"type": "MobilePhone", "value": phone, "memo": contact_name or name}],
        }
        return self._call("POST", "crm", "customers", "create_customer", json=body)

    def create_location(self, key: str, customer_id: int, address: dict) -> dict:
        place = self._address(address)
        body = {"customerId": customer_id, "name": place["street"], "address": place}
        return self._call("POST", "crm", "locations", "create_location", json=body)

    def set_location_active(self, key: str, location_id: int, active: bool) -> dict:
        return self._call(
            "PATCH", "crm", f"locations/{location_id}", "set_location_active",
            json={"active": active},
        )

    def move_job(self, key: str, job_id: int, location_id: int) -> dict:
        """Point an open job at another of its customer's locations (PATCH jobs/{id})."""
        return self._call(
            "PATCH", "jpm", f"jobs/{job_id}", "move_job", json={"locationId": location_id}
        )

    def create_job(
        self,
        key: str,
        customer_id: int,
        location_id: int,
        business_unit_id: int,
        job_type_id: int,
        priority: str,
        campaign_id: int,
        summary: str,
        window_start: datetime,
        window_end: datetime,
        technician_ids: list[int] | None = None,
    ) -> dict:
        """The appointment's start and end are the arrival window; ServiceTitan requires both."""
        appointment = {
            "start": _utc(window_start),
            "end": _utc(window_end),
            "arrivalWindowStart": _utc(window_start),
            "arrivalWindowEnd": _utc(window_end),
        }
        if technician_ids:
            appointment["technicianIds"] = technician_ids
        body = {
            "customerId": customer_id,
            "locationId": location_id,
            "businessUnitId": business_unit_id,
            "jobTypeId": job_type_id,
            "priority": priority,
            "campaignId": campaign_id,
            "summary": f"{summary}\nRef: {ref_of(key)}",
            "appointments": [appointment],
        }
        return self._call("POST", "jpm", "jobs", "create_job", json=body)

    def reschedule(
        self,
        key: str,
        appointment_id: int,
        window_start: datetime,
        window_end: datetime,
        technician_id: int | None = None,
    ) -> dict:
        body = {
            "start": _utc(window_start),
            "end": _utc(window_end),
            "arrivalWindowStart": _utc(window_start),
            "arrivalWindowEnd": _utc(window_end),
        }
        moved = self._call(
            "PATCH", "jpm", f"appointments/{appointment_id}/reschedule", "reschedule", json=body
        )
        if technician_id is not None:
            # The move stands even if another caller took this technician in the meantime.
            try:
                self._call(
                    "POST",
                    "dispatch",
                    "appointment-assignments/assign-technicians",
                    "assign_technician",
                    json={"jobAppointmentId": appointment_id, "technicianIds": [technician_id]},
                )
            except FsmRejected as exc:
                if exc.status != 409:
                    raise
        return moved

    def cancel_job(self, key: str, job_id: int, reason_id: int, memo: str) -> dict:
        body = {"reasonId": reason_id, "memo": memo}
        return self._call("PUT", "jpm", f"jobs/{job_id}/cancel", "cancel_job", json=body)

    def create_lead(self, key: str, lead: dict) -> dict:
        return self._call("POST", "crm", "leads", "create_lead", json=lead)

    def add_note(
        self, key: str, customer_id: int, text: str, job_id: int | None = None, pin: bool = False
    ) -> dict:
        """A job note comes back without an id, as ServiceTitan's does. A pinned note shows above
        the others in ServiceTitan; nothing can edit or remove it afterwards."""
        body = {"text": text, "pinToTop": pin}
        if job_id is not None:
            return self._call("POST", "jpm", f"jobs/{job_id}/notes", "add_note", json=body)
        return self._call("POST", "crm", f"customers/{customer_id}/notes", "add_note", json=body)

    # ---- the call log -----------------------------------------------------------------------

    def record_outbound_call(
        self,
        sid: str,
        from_number: str,
        to_number: str,
        started: datetime,
        seconds: int,
        customer_id: int | None = None,
    ) -> None:
        """Only with log_calls_via_debug (the mock). ServiceTitan's phone system logs the calls made
        through it; there is no endpoint to create one. The mock has none either, so it offers
        POST /debug/calls, which this posts to. With the switch off it does nothing."""
        if not self.log_calls_via_debug:
            return
        body = {
            "sid": sid,
            "direction": "Outbound",
            "fromNumber": from_number,
            "toNumber": to_number,
            "receivedOn": _utc(started),
            "durationSeconds": seconds,
            "customerId": customer_id,
        }
        response = self._send("POST", "/debug/calls", "record_call", json=body)
        if response.status_code >= 400:
            raise FsmRejected(response.status_code, "rejected", "the mock refused the call record")

    def tag_call(self, call_id: int, tag: str) -> dict:
        """Put a tag type (by its name) on a call, the way a person does in ServiceTitan. The call is
        read first so the classification it already has is sent back unchanged. ServiceTitan's PUT
        also asks for the customer and location; those are not sent here (see the mock README)."""
        detail = self._call("GET", "telecom", f"calls/{call_id}", "call")
        reason = detail.get("reason") or {"name": "", "lead": False}
        body = {
            "callType": detail.get("callType"),
            "excuseMemo": "",
            "reason": {"name": reason["name"], "lead": reason["lead"]},
            "tag": tag,
        }
        matched = detail.get("customer")
        if matched:
            # ServiceTitan's PUT asks for the customer and a location with their addresses and
            # contacts. Without a customer matched to the call there is nothing to send.
            customer = self.customer(matched["id"])
            locations = self.locations(matched["id"])
            body["customer"] = {
                "id": customer["id"],
                "name": customer["name"],
                "address": customer["address"],
                "contacts": [
                    {
                        "id": c["id"],
                        "type": c["type"],
                        "value": c["value"],
                        "memo": c.get("memo") or "",
                    }
                    for c in self.contacts(matched["id"])
                ],
            }
            if locations:
                body["location"] = {
                    "id": locations[0]["id"],
                    "name": locations[0]["name"],
                    "address": locations[0]["address"],
                    "contacts": [],
                }
        return self._call("PUT", "telecom", f"calls/{call_id}", "tag_call", json=body)
