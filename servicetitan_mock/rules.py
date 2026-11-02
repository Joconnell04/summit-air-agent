"""Business rules shared by bookings and jobs."""

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


def require_bookable_type(cfg: TenantConfig, customer: sqlite3.Row) -> None:
    """Refuse customer types the company does not book. The message comes from the config."""
    message = cfg.rejected_customer_types.get(customer["type"])
    if message:
        raise ApiError(
            422, "customer_type_not_supported", message, {"customerId": customer["type"].lower()}
        )


def require_in_area(cfg: TenantConfig, zip_code: str) -> None:
    if zip_code not in cfg.zone_by_zip:
        raise ApiError(
            422,
            "out_of_area",
            f"ZIP {zip_code} is outside the service area.",
            {"zip": "out of area"},
        )


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
