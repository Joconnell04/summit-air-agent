"""The interface between the tool server and a company's field service system.

It holds only the calls the tools need. There is no method that deletes, bulk-changes or runs a
free-form query, so no tool can do those whatever the model asks (spec S-39). Moving to another
system (or to real ServiceTitan) means a new adapter, not new tools.
"""

import contextvars
from datetime import datetime
from typing import Protocol

# Each FSM request made while handling one tool call, for the stage timings in the log.
fsm_timings: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "fsm_timings", default=None
)


class FsmUnavailable(Exception):
    """The FSM could not be reached, timed out, or failed on its side. The request may be retried."""


class FsmRejected(Exception):
    """The FSM refused the request by its own rules (a taken slot, an unknown id, ...)."""

    def __init__(self, status: int, code: str, detail: str):
        super().__init__(f"{status} {code}: {detail}")
        self.status = status
        self.code = code
        self.detail = detail


class FsmAdapter(Protocol):
    # Reads
    def customers_by_phone(self, phone: str) -> list[dict]: ...
    def locations_by_address(self, street: str, zip_code: str, unit: str | None) -> list[dict]: ...
    def customer(self, customer_id: int) -> dict: ...
    def locations(self, customer_id: int) -> list[dict]: ...
    def memberships(self, customer_id: int) -> list[dict]: ...
    def jobs(self, customer_id: int, statuses: list[str] | None = None) -> list[dict]: ...
    def job(self, job_id: int) -> dict: ...
    def appointments(self, job_id: int) -> list[dict]: ...
    def calls(self, customer_id: int, since: datetime | None = None) -> list[dict]: ...
    def notes(self, customer_id: int) -> list[dict]: ...
    def equipment(self, customer_id: int) -> list[dict]: ...
    def capacity(
        self,
        start: datetime,
        end: datetime,
        business_unit_id: int,
        job_type_id: int,
        priority: str,
        zip_code: str | None,
    ) -> list[dict]: ...
    def health(self) -> bool: ...

    # Writes, each with an idempotency key so a retry returns the first result
    def create_customer(self, key: str, name: str, address: dict, phone: str) -> dict: ...
    def create_location(self, key: str, customer_id: int, address: dict) -> dict: ...
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
    ) -> dict: ...
    def reschedule(
        self, key: str, appointment_id: int, window_start: datetime, window_end: datetime
    ) -> dict: ...
    def cancel_job(self, key: str, job_id: int, reason_id: int, memo: str) -> dict: ...
    def create_lead(self, key: str, lead: dict) -> dict: ...
    def add_note(
        self, key: str, customer_id: int, text: str, job_id: int | None = None
    ) -> dict: ...
