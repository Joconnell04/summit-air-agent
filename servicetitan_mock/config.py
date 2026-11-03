"""Tenant configuration: everything that differs between companies."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

DEFAULT_TENANT_DIR = "tenants/summit-air"
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("timestamp needs a UTC offset")
    return dt.astimezone(UTC)


@dataclass(frozen=True)
class Window:
    id: str
    start: time
    end: time
    ends_next_day: bool
    after_hours: bool


class TenantConfig:
    def __init__(self, raw: dict):
        self.raw = raw
        self.tenant_id: int = raw["tenant_id"]
        self.company: dict = raw["company"]
        self.tz = ZoneInfo(self.company["timezone"])
        self.auth: dict = raw["auth"]
        self.fees: dict = raw["fees"]
        self.zones: list[dict] = raw["zones"]
        self.business_units: list[dict] = raw["business_units"]
        self.job_types: list[dict] = raw["job_types"]
        self.campaigns: list[dict] = raw["campaigns"]
        self.cancel_reasons: list[dict] = raw["cancel_reasons"]
        self.call_reasons: list[dict] = raw["call_reasons"]
        self.tag_types: list[dict] = raw["tag_types"]
        self.booking_providers: list[dict] = raw["booking_providers"]
        self.membership_types: list[dict] = raw["membership_types"]
        self.urgent_hold: dict = raw["urgent_hold"]
        self.rejected_customer_types: dict[str, str] = self.company["rejected_customer_types"]
        self.equipment_labels: dict[str, str] = raw["equipment_labels"]
        self.windows = [
            Window(
                w["id"],
                time.fromisoformat(w["start"]),
                time.fromisoformat(w["end"]),
                w.get("ends_next_day", False),
                w.get("after_hours", False),
            )
            for w in raw["arrival_windows"]
        ]
        self.zone_by_zip = {zip_code: z for z in self.zones for zip_code in z["zips"]}
        self.service_area_zips = sorted(self.zone_by_zip)

    @classmethod
    def load(cls, tenant_dir: str | Path) -> "TenantConfig":
        path = Path(tenant_dir) / "config.yaml"
        return cls(yaml.safe_load(path.read_text()))

    def window(self, window_id: str) -> Window:
        return next(w for w in self.windows if w.id == window_id)

    def window_bounds(self, day: date, window: Window) -> tuple[datetime, datetime]:
        start = datetime.combine(day, window.start, tzinfo=self.tz)
        end_day = day + timedelta(days=1) if window.ends_next_day else day
        end = datetime.combine(end_day, window.end, tzinfo=self.tz)
        return start.astimezone(UTC), end.astimezone(UTC)

    def match_window(self, start: datetime, end: datetime) -> tuple[date, Window] | None:
        local = start.astimezone(self.tz)
        for w in self.windows:
            if (start, end) == self.window_bounds(local.date(), w):
                return local.date(), w
        return None

    def window_offered(self, day: date, window: Window) -> bool:
        """Regular windows are offered only inside the company's hours for that weekday. After-hours
        windows are always offered; who can take them is decided by the on-call shifts."""
        if window.after_hours:
            return True
        hours = self.company["hours"][DAY_NAMES[day.weekday()]]
        if not hours:
            return False
        return time.fromisoformat(hours[0]) <= window.start and window.end <= time.fromisoformat(
            hours[1]
        )

    def job_type(self, job_type_id: int) -> dict | None:
        return next((j for j in self.job_types if j["id"] == job_type_id), None)

    def hold_for(self, day: date, window_id: str, business_unit_id: int) -> int:
        """Technicians held back for urgent jobs, from the urgent_hold block of the config. This is
        the company's own rule, not ServiceTitan's: the mock never applies it, the seed uses it to
        shape a believable schedule, and the tool server applies it when it offers times."""
        if business_unit_id not in self.urgent_hold["business_unit_ids"]:
            return 0
        return self.urgent_hold["per_day"].get(DAY_NAMES[day.weekday()], {}).get(window_id, 0)

    def visit_name(self, membership_type: dict, due: date) -> str:
        """The name of an included service visit that falls on the due date."""
        for visit in membership_type["visits"]:
            if due.month in visit["months"]:
                return visit["name"]
        raise ValueError(f"{membership_type['name']} has no visit name for month {due.month}")

    def equipment_label(self, system_type: str) -> str:
        return self.equipment_labels.get(system_type, system_type.replace("_", " "))
