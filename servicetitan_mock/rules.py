"""Checks shared by bookings, jobs and leads: ids that exist. The company's own rules (which
customer types it books, which ZIPs it serves) are not ServiceTitan's and live in the tool server."""

import sqlite3

from .config import TenantConfig
from .errors import ApiError


def require_customer(conn: sqlite3.Connection, customer_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    if not row:
        raise ApiError(
            422,
            "unknown_reference",
            f"Customer {customer_id} does not exist.",
            {"customerId": "unknown"},
        )
    return row


def check_refs(
    cfg: TenantConfig,
    business_unit_id: int | None,
    job_type_id: int | None,
    campaign_id: int | None,
) -> None:
    """Reject ids that the company has not configured or has switched off."""
    if business_unit_id is not None:
        unit = next((b for b in cfg.business_units if b["id"] == business_unit_id), None)
        if not unit or not unit["active"]:
            raise ApiError(
                422,
                "unknown_reference",
                f"Business unit {business_unit_id} is not available.",
                {"businessUnitId": "unknown or inactive"},
            )
    if job_type_id is not None:
        job_type = cfg.job_type(job_type_id)
        if not job_type or not job_type["active"]:
            raise ApiError(
                422,
                "unknown_reference",
                f"Job type {job_type_id} is not available.",
                {"jobTypeId": "unknown or inactive"},
            )
        if business_unit_id is not None and business_unit_id not in job_type["businessUnitIds"]:
            raise ApiError(
                422,
                "unknown_reference",
                f"Job type {job_type_id} is not offered by business unit {business_unit_id}.",
                {"jobTypeId": "wrong business unit"},
            )
    if campaign_id is not None and not any(
        c["id"] == campaign_id and c["active"] for c in cfg.campaigns
    ):
        raise ApiError(
            422,
            "unknown_reference",
            f"Campaign {campaign_id} is not available.",
            {"campaignId": "unknown or inactive"},
        )
