"""Tenants: one folder per company under tenants/, picked by the number the caller dialed.

A tenant folder holds config.yaml (the company as its field service system knows it: hours, area,
job types, plans, refused customer types), agent.yaml (how the agent works for this company:
safety lines, priority rules, lines it says) and knowledge/*.md. Nothing company-specific lives in
app/.

An agent.yaml may start with `extends: <trade>`: it is then laid over trades/<trade>/agent.yaml,
what every company in that trade shares (merge() says how). docs/template.md has the layout.
"""

import copy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .pace import clamped_config
from .priority import LEVELS
from .text import phone_key

DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
TRADES_DIR = Path(__file__).resolve().parent.parent / "trades"


def merge(trade, tenant, path: str = ""):
    """The tenant's value laid over the trade's.

    Mappings merge key by key. A list of mappings that all carry an `id` merges by id: an entry with
    a trade id is merged into that entry, and new ids follow the trade's, in the tenant's order. Any
    other list is the trade's items then the tenant's new ones, so a tenant can add a phrase but not
    drop one the trade lists. A scalar is the tenant's value. A tenant value of another shape than
    the trade's (a phrase list replaced by a string) and a list where only some entries carry an
    `id` are errors, since either would quietly drop what the trade set."""
    where = path or "the top level"
    if isinstance(trade, (dict, list)) and type(tenant) is not type(trade):
        raise ValueError(f"{where}: the trade has a {type(trade).__name__} here")
    if isinstance(trade, dict):
        merged = dict(trade)
        for key, value in tenant.items():
            sub = f"{path}.{key}" if path else str(key)
            merged[key] = merge(trade[key], value, sub) if key in trade else value
        return merged
    if isinstance(trade, list):
        keyed = {_ids(trade, where), _ids(tenant, where)} - {"empty"}
        if keyed == {"all"}:
            ours = {item["id"]: item for item in tenant}
            merged = [
                merge(item, ours.pop(item["id"]), f"{path}[{item['id']}]")
                if item["id"] in ours
                else item
                for item in trade
            ]
            return merged + list(ours.values())
        if "all" in keyed:
            raise ValueError(f"{where}: every entry needs an id when the other file's entries have one")
        return trade + [item for item in tenant if item not in trade]
    return tenant


def _ids(items: list, where: str) -> str:
    """Whether every entry of a list carries an id ("all"), none does ("none"), or it is empty."""
    has = [isinstance(item, dict) and "id" in item for item in items]
    if not has:
        return "empty"
    if all(has):
        return "all"
    if any(has):
        raise ValueError(f"{where}: some entries have an id and some don't")
    return "none"


def loosened(trade: dict, tenant: dict) -> list[str]:
    """How a tenant's agent.yaml would weaken what its trade sets, which no tenant may do. A tenant
    may add phrases, job types and conditions to a trade rule and raise its level or a job type's
    base level. It may not lower a level, change another value the trade set on a rule (turning a
    rule's `group` or `safety_signal` off, say) or add denials, since a denial turns a safety
    check's answer into a no."""
    found = []
    if tenant.get("safety", {}).get("denials"):
        found.append("safety.denials: denials are the trade's alone")
    priority, trade_priority = tenant.get("priority", {}), trade.get("priority", {})
    for job_type, level in priority.get("base", {}).items():
        floor = trade_priority.get("base", {}).get(job_type)
        if floor and _rank(level) < _rank(floor):
            found.append(f"priority.base.{job_type}: {level} is below the trade's {floor}")
    rules = {r["id"]: r for r in trade_priority.get("rules", [])}
    for rule in priority.get("rules", []):
        base = rules.get(rule.get("id"), {})
        for key, value in rule.items():
            if key == "id" or key not in base or isinstance(base[key], list):
                continue
            where = f"priority.rules[{rule['id']}].{key}"
            if key in ("raise_to", "raise_to_when_mild"):
                if _rank(value) < _rank(base[key]):
                    found.append(f"{where}: {value} is below the trade's {base[key]}")
            elif value != base[key]:
                found.append(f"{where}: the trade sets {base[key]!r}; a tenant may only add to it")
    return found


def _rank(level: str) -> int:
    return LEVELS.index(level) if level in LEVELS else -1


def load_agent(folder: Path, trades_dir: Path = TRADES_DIR) -> tuple[dict, str | None]:
    """A tenant's agent.yaml merged over its trade's, and the trade's name (None without one)."""
    agent = yaml.safe_load((Path(folder) / "agent.yaml").read_text())
    trade = agent.pop("extends", None)
    if trade is None:
        return agent, None
    path = Path(trades_dir) / trade / "agent.yaml"
    if not path.exists():
        raise ValueError(f"{folder}/agent.yaml extends {trade!r}, but {path} does not exist")
    shared = yaml.safe_load(path.read_text())
    try:
        problems = loosened(shared, agent)
        if problems:
            raise ValueError("; ".join(problems))
        return merge(shared, agent), trade
    except ValueError as err:
        raise ValueError(f"{folder}/agent.yaml over {path}: {err}") from None


def missing_safety_wording(safety: dict) -> list[str]:
    """The safety wording the server says word for word. The trade lists the signals but not the
    words, so a tenant that leaves one out would fail only once a caller mentions gas."""
    missing = []
    for kind, cfg in safety.get("kinds", {}).items():
        if not cfg.get("lines"):
            missing.append(f"kinds.{kind}.lines")
        if not cfg.get("variants"):
            missing.append(f"kinds.{kind}.variants")
    for kind, cfg in safety.get("vague", {}).items():
        if kind not in safety.get("kinds", {}):
            missing.append(f"kinds.{kind} (vague.{kind} has no kind)")
        if not cfg.get("question"):
            missing.append(f"vague.{kind}.question")
        if cfg.get("confirm_phrases") and not cfg.get("confirm_question"):
            missing.append(f"vague.{kind}.confirm_question")
    missing += [key for key in ("sign_off", "flag_reason") if not safety.get(key)]
    return missing


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
    def __init__(
        self,
        folder: Path,
        company: dict,
        agent: dict,
        knowledge: dict[str, str],
        trade: str | None = None,
    ):
        self.folder = folder
        self.slug = folder.name
        self.trade = trade
        self.raw = company
        self.agent = agent
        self.knowledge = knowledge
        self.fsm_tenant_id: int = company["tenant_id"]
        self.name: str = company["company"]["name"]
        self.tz = ZoneInfo(company["company"]["timezone"])
        self.state: str = company["company"].get("state", "")
        self.country: str = company["company"].get("country", "USA")
        self.hours: dict = company["company"]["hours"]
        self.fees: dict = company["fees"]
        self.rejected_customer_types: dict[str, str] = company["company"]["rejected_customer_types"]
        self.job_types = {j["code"]: j for j in company["job_types"] if j["active"]}
        self.job_type_codes = {j["id"]: j["code"] for j in company["job_types"]}
        self.membership_types = {m["id"]: m for m in company["membership_types"]}
        self.equipment_labels: dict = company.get("equipment_labels", {})
        self.service_area = {z: zone for zone in company["zones"] for z in zone["zips"]}
        # Where the address lookup asks and the place names callers use (app/geocode.py).
        self.address_lookup: dict | None = company.get("address_lookup")
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
        missing = missing_safety_wording(self.safety)
        if missing:
            raise ValueError(f"{folder}/agent.yaml safety needs {missing}")
        self.commercial: dict = agent["commercial"]
        # What the company does and doesn't do, and how callers ask for work it doesn't (app/asks.py).
        self.services: dict = agent.get("services", {})
        self.approval: dict = agent.get(
            "approval",
            {"unclear_phrases": [], "unclear_approver": [], "callback_hours_before_window": 2},
        )
        self.priority: dict = agent["priority"]
        # Where to read the outside temperature (app/weather.py). None leaves it unknown.
        self.weather: dict | None = agent.get("weather")
        self.lines: dict = agent["lines"]
        self.callbacks: dict = agent["callbacks"]
        self.on_call_alerts: dict = agent["on_call_alerts"]
        self.repeat: dict = agent["repeat_callers"]
        # Speaking rate (app/pace.py). None leaves the voice at its default speed.
        self.pace: dict | None = clamped_config(agent.get("pace"))
        # Card and billing phrases (app/cards.py).
        self.payments: dict = agent.get("payments", {})
        self.retention: dict = agent["retention"]
        self.after_hours_cutoffs: dict = agent["after_hours"]
        # Words for the trade's equipment, so swearing at a broken system isn't read as anger at the
        # call (app/transfer.py).
        self.equipment_words: frozenset[str] = frozenset(
            w.lower() for w in agent.get("equipment_words", [])
        )
        # The systems a caller names, so the agent doesn't ask for one already given (app/lifecycle.py).
        self.system_words: tuple[str, ...] = tuple(agent.get("system_words", []))
        # Things and places that are never a person's name in the name check ("It's the furnace",
        # "It's Brooklyn"): the trade's equipment and home words, and the words of the service
        # area's zone names (app/tools.py _gave_a_name).
        self.not_names: frozenset[str] = (
            self.equipment_words
            | {w.lower() for w in agent.get("home_words", [])}
            | {w for zone in company["zones"] for w in zone["name"].lower().replace(",", " ").split()}
        )
        # Warm transfer to a person (app/transfer.py). None turns it off.
        self.transfer: dict | None = agent.get("transfer")
        self.fsm: dict = agent["fsm"]
        missing = {"resolved", "open", "no_contact"} - set(self.fsm.get("callback_tags", {}))
        if missing:
            raise ValueError(f"agent.yaml fsm.callback_tags needs {sorted(missing)}")
        self.mock_auth: dict = company.get("auth", {})
        # Technicians held back from normal jobs, per day and window (config.yaml urgent_hold).
        self.urgent_hold: dict = company.get(
            "urgent_hold", {"business_unit_ids": [], "per_day": {}}
        )

    @classmethod
    def load(cls, folder: str | Path, trades_dir: Path = TRADES_DIR) -> "Tenant":
        folder = Path(folder)
        company = yaml.safe_load((folder / "config.yaml").read_text())
        agent, trade = load_agent(folder, trades_dir)
        knowledge = {
            p.stem: p.read_text()
            for p in sorted((folder / "knowledge").glob("*.md"))
            if p.name != "README.md"
        }
        return cls(folder, company, agent, knowledge, trade)

    def line(self, key: str, **values) -> str:
        return self.lines[key].format(**values)

    def with_lines(self, lines: dict[str, str]) -> "Tenant":
        """A copy that says these lines instead of the defaults (owner edits, app/rules.py). The
        shared tenant is never changed, so each call keeps the version it started under."""
        if not lines:
            return self
        changed = copy.copy(self)
        changed.lines = {**self.lines, **lines}
        return changed

    def window(self, window_id: str) -> Window | None:
        return next((w for w in self.windows if w.id == window_id), None)

    def hold_for(self, day: date, window_id: str, business_unit_id: int) -> int:
        """How many technicians stay free for urgent jobs in this window. It is the company's rule,
        not ServiceTitan's, so it is applied here to the technicians ServiceTitan says are free."""
        if business_unit_id not in self.urgent_hold["business_unit_ids"]:
            return 0
        return self.urgent_hold["per_day"].get(DAY_NAMES[day.weekday()], {}).get(window_id, 0)

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
    def load(cls, directory: str | Path, default: str, trades_dir: Path = TRADES_DIR) -> "Tenants":
        folders = sorted(p.parent for p in Path(directory).glob("*/agent.yaml"))
        return cls([Tenant.load(f, trades_dir) for f in folders], default)

    def for_number(self, dialed: str) -> Tenant:
        key = phone_key(dialed)
        for tenant in self.by_slug.values():
            if key in tenant.phone_keys:
                return tenant
        return self.default

    def __iter__(self):
        return iter(self.by_slug.values())


if __name__ == "__main__":
    # Prints a tenant's agent config as the server sees it, trade included:
    #   uv run python -m app.tenant tenants/summit-air
    import sys

    merged, _trade = load_agent(Path(sys.argv[1]))
    sys.stdout.write(yaml.safe_dump(merged, sort_keys=True, allow_unicode=True, width=100))
