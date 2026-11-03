"""Tenants: one folder per company under tenants/, picked by the number the caller dialed.

A tenant folder holds config.yaml (the company as its field service system knows it: hours, area,
job types, plans, refused customer types), agent.yaml (how the agent works for this company:
safety lines, priority rules, lines it says) and knowledge/*.md. Nothing company-specific lives in
app/.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .text import phone_key

DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True)
class Window:
    id: str
    start: time
    end: time
    after_hours: bool

    def label(self) -> str:
        """Spoken form of the window, such as "8 to 12"."""

        def hour(t: time) -> str:
            h = t.hour % 12 or 12
            return f"{h}" if t.minute == 0 else f"{h}:{t.minute:02d}"

        return f"{hour(self.start)} to {hour(self.end)}"


class Tenant:
    def __init__(self, folder: Path, company: dict, agent: dict, knowledge: dict[str, str]):
        self.folder = folder
        self.slug = folder.name
        self.raw = company
        self.agent = agent
        self.knowledge = knowledge
        self.fsm_tenant_id: int = company["tenant_id"]
        self.name: str = company["company"]["name"]
        self.tz = ZoneInfo(company["company"]["timezone"])
        self.hours: dict = company["company"]["hours"]
        self.fees: dict = company["fees"]
        self.rejected_customer_types: dict[str, str] = company["company"]["rejected_customer_types"]
        self.job_types = {j["code"]: j for j in company["job_types"] if j["active"]}
        self.job_type_codes = {j["id"]: j["code"] for j in company["job_types"]}
        self.membership_types = {m["id"]: m for m in company["membership_types"]}
        self.equipment_labels: dict = company.get("equipment_labels", {})
        self.service_area = {z: zone for zone in company["zones"] for z in zone["zips"]}
        self.windows = [
            Window(
                w["id"],
                time.fromisoformat(w["start"]),
                time.fromisoformat(w["end"]),
                w.get("after_hours", False),
            )
            for w in company["arrival_windows"]
        ]
        self.phone_keys = {phone_key(n) for n in agent["phone_numbers"]}
        self.safety: dict = agent["safety"]
        self.commercial: dict = agent["commercial"]
        self.priority: dict = agent["priority"]
        # Where to read the outside temperature (app/weather.py). None leaves it unknown.
        self.weather: dict | None = agent.get("weather")
        self.lines: dict = agent["lines"]
        self.callbacks: dict = agent["callbacks"]
        self.on_call_alerts: dict = agent["on_call_alerts"]
        self.repeat: dict = agent["repeat_callers"]
        self.retention: dict = agent["retention"]
        self.after_hours_cutoffs: dict = agent["after_hours"]
        self.fsm: dict = agent["fsm"]
        self.mock_auth: dict = company.get("auth", {})

    @classmethod
    def load(cls, folder: str | Path) -> "Tenant":
        folder = Path(folder)
        company = yaml.safe_load((folder / "config.yaml").read_text())
        agent = yaml.safe_load((folder / "agent.yaml").read_text())
        knowledge = {
            p.stem: p.read_text()
            for p in sorted((folder / "knowledge").glob("*.md"))
            if p.name != "README.md"
        }
        return cls(folder, company, agent, knowledge)

    def line(self, key: str, **values) -> str:
        return self.lines[key].format(**values)

    def window(self, window_id: str) -> Window | None:
        return next((w for w in self.windows if w.id == window_id), None)

    def local(self, moment: datetime) -> datetime:
        return moment.astimezone(self.tz)

    def is_open(self, moment: datetime) -> bool:
        """Inside the company's hours for that weekday."""
        local = self.local(moment)
        hours = self.hours[DAY_NAMES[local.weekday()]]
        if not hours:
            return False
        return time.fromisoformat(hours[0]) <= local.time() < time.fromisoformat(hours[1])

    def window_bounds(self, day: date, window: Window) -> tuple[datetime, datetime]:
        start = datetime.combine(day, window.start, tzinfo=self.tz)
        end_day = day + timedelta(days=1) if window.end <= window.start else day
        return start, datetime.combine(end_day, window.end, tzinfo=self.tz)

    def day_spoken(self, day: date, now: datetime) -> str:
        """ "today", "tomorrow" or the weekday."""
        today = self.local(now).date()
        if day == today:
            return "today"
        if day == today + timedelta(days=1):
            return "tomorrow"
        return day.strftime("%A")

    def time_spoken(self, moment: datetime, now: datetime) -> str:
        """A named time a person would say: "4:30 PM", "tomorrow at 9 AM", "Friday at 10 AM"."""
        local, today = self.local(moment), self.local(now).date()
        clock = local.strftime("%-I:%M %p").replace(":00 ", " ")
        if local.date() == today:
            return clock
        if local.date() == today + timedelta(days=1):
            return f"tomorrow at {clock}"
        return f"{local.strftime('%A')} at {clock}"


class Tenants:
    """Every tenant folder under one directory, looked up by dialed number."""

    def __init__(self, tenants: list[Tenant], default: str):
        self.by_slug = {t.slug: t for t in tenants}
        if not default and len(tenants) == 1:
            default = tenants[0].slug
        if default not in self.by_slug:
            raise ValueError(f"set DEFAULT_TENANT to one of {sorted(self.by_slug)}")
        self.default = self.by_slug[default]

    @classmethod
    def load(cls, directory: str | Path, default: str) -> "Tenants":
        folders = sorted(p.parent for p in Path(directory).glob("*/agent.yaml"))
        return cls([Tenant.load(f) for f in folders], default)

    def for_number(self, dialed: str) -> Tenant:
        key = phone_key(dialed)
        for tenant in self.by_slug.values():
            if key in tenant.phone_keys:
                return tenant
        return self.default

    def __iter__(self):
        return iter(self.by_slug.values())
