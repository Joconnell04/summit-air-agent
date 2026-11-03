"""ServiceTitan adapter, pointed at the mock today (servicetitan_mock/README.md).

Moving to real ServiceTitan changes the base URL, the token URL and the credentials. The README's
"Pointing the adapter at real ServiceTitan" list is what else would change here: idempotency keys
kept on our side, a capacity recheck before creating a job, and no /debug or X-Call-Id traffic.
"""

import threading
import time
from datetime import datetime

import httpx

from .base import FsmRejected, FsmUnavailable, fsm_timings

OPEN_JOB_STATUSES = ["Scheduled", "Dispatched", "Working"]
PAGE_SIZE = 200


def _utc(moment: datetime) -> str:
    return moment.isoformat()


class ServiceTitanAdapter:
    def __init__(
        self,
        http: httpx.Client,
        tenant_id: int,
        client_id: str,
        client_secret: str,
        app_key: str,
        token_url: str = "/connect/token",
    ):
        self.http = http
        self.tenant_id = tenant_id
        self.credentials = {"client_id": client_id, "client_secret": client_secret}
        self.app_key = app_key
        self.token_url = token_url
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
        key: str | None = None,
    ) -> dict:
        url = f"/{module}/v2/tenant/{self.tenant_id}/{path}"
        for attempt in range(2):
            headers = {
                "Authorization": f"Bearer {self._token_header(refresh=attempt > 0)}",
                "ST-App-Key": self.app_key,
            }
            if key:
                headers["Idempotency-Key"] = key
            response = self._send(method, url, op, params=params, json=json, headers=headers)
            if response.status_code != 401:
                break
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {}
            raise FsmRejected(
                response.status_code, body.get("code", "error"), body.get("detail", "")
            )
        return response.json()

    def _list(self, module: str, path: str, op: str, **params) -> list[dict]:
        params = {k: v for k, v in params.items() if v is not None}
        return self._call("GET", module, path, op, params={**params, "pageSize": PAGE_SIZE})["data"]

    # ---- reads ------------------------------------------------------------------------------

    def customers_by_phone(self, phone: str) -> list[dict]:
        return self._list("crm", "customers", "customers_by_phone", phone=phone)

    def locations_by_address(self, street: str, zip_code: str, unit: str | None) -> list[dict]:
        return self._list(
            "crm", "locations", "locations_by_address", street=street, zip=zip_code, unit=unit
        )

    def customer(self, customer_id: int) -> dict:
        return self._call("GET", "crm", f"customers/{customer_id}", "customer")

    def locations(self, customer_id: int) -> list[dict]:
        return self._list("crm", "locations", "locations", customerId=customer_id)

    def memberships(self, customer_id: int) -> list[dict]:
        return self._list("memberships", "memberships", "memberships", customerId=customer_id)

    def jobs(self, customer_id: int, statuses: list[str] | None = None) -> list[dict]:
        return self._list(
            "jpm",
            "jobs",
            "jobs",
            customerId=customer_id,
            jobStatus=",".join(statuses) if statuses else None,
        )

    def job(self, job_id: int) -> dict:
        return self._call("GET", "jpm", f"jobs/{job_id}", "job")

    def appointments(self, job_id: int) -> list[dict]:
        return self._list("jpm", "appointments", "appointments", jobId=job_id)

    def calls(self, customer_id: int, since: datetime | None = None) -> list[dict]:
        return self._list(
            "telecom",
            "calls",
            "calls",
            customerId=customer_id,
            receivedOnOrAfter=since.strftime("%Y-%m-%dT%H:%M:%SZ") if since else None,
        )

    def notes(self, customer_id: int) -> list[dict]:
        return self._list("crm", f"customers/{customer_id}/notes", "notes")

    def equipment(self, customer_id: int) -> list[dict]:
        return self._list(
            "equipmentsystems", "installed-equipment", "equipment", customerId=customer_id
        )

    def capacity(
        self,
        start: datetime,
        end: datetime,
        business_unit_id: int,
        job_type_id: int,
        priority: str,
        zip_code: str | None,
    ) -> list[dict]:
        body = {
            "startsOnOrAfter": _utc(start),
            "endsOnOrBefore": _utc(end),
            "businessUnitIds": [business_unit_id],
            "jobTypeId": job_type_id,
            "priority": priority,
            "zip": zip_code,
        }
        return self._call("POST", "dispatch", "capacity", "capacity", json=body)["availabilities"]

    def health(self) -> bool:
        try:
            return self._send("GET", "/health", "health").status_code == 200
        except FsmUnavailable:
            return False

    # ---- writes -----------------------------------------------------------------------------

    def create_customer(self, key: str, name: str, address: dict, phone: str) -> dict:
        contact = {"type": "MobilePhone", "value": phone, "name": name}
        body = {
            "name": name,
            "type": "Residential",
            "locations": [{"address": address, "contacts": [contact]}],
        }
        return self._call("POST", "crm", "customers", "create_customer", json=body, key=key)

    def create_location(self, key: str, customer_id: int, address: dict) -> dict:
        body = {"customerId": customer_id, "address": address}
        return self._call("POST", "crm", "locations", "create_location", json=body, key=key)

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
    ) -> dict:
        body = {
            "customerId": customer_id,
            "locationId": location_id,
            "businessUnitId": business_unit_id,
            "jobTypeId": job_type_id,
            "priority": priority,
            "campaignId": campaign_id,
            "summary": summary,
            "appointments": [
                {"arrivalWindowStart": _utc(window_start), "arrivalWindowEnd": _utc(window_end)}
            ],
        }
        return self._call("POST", "jpm", "jobs", "create_job", json=body, key=key)

    def reschedule(
        self, key: str, appointment_id: int, window_start: datetime, window_end: datetime
    ) -> dict:
        body = {"arrivalWindowStart": _utc(window_start), "arrivalWindowEnd": _utc(window_end)}
        return self._call(
            "PATCH",
            "jpm",
            f"appointments/{appointment_id}/reschedule",
            "reschedule",
            json=body,
            key=key,
        )

    def cancel_job(self, key: str, job_id: int, reason_id: int, memo: str) -> dict:
        body = {"reasonId": reason_id, "memo": memo}
        return self._call("PUT", "jpm", f"jobs/{job_id}/cancel", "cancel_job", json=body, key=key)

    def create_lead(self, key: str, lead: dict) -> dict:
        return self._call("POST", "crm", "leads", "create_lead", json=lead, key=key)

    def add_note(self, key: str, customer_id: int, text: str, job_id: int | None = None) -> dict:
        body = {"text": text, "createdBy": "agent"}
        if job_id is not None:
            return self._call("POST", "jpm", f"jobs/{job_id}/notes", "add_note", json=body, key=key)
        return self._call(
            "POST", "crm", f"customers/{customer_id}/notes", "add_note", json=body, key=key
        )
