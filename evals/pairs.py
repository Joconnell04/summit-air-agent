"""Two simulated calls at the same time against one world: one mock ServiceTitan, one tool server.

A pair file (evals/scenarios/pairs/<id>.yaml) holds what the two callers share (labels, time, the
checks each call must pass), each caller's own persona and script, how the world is set up before
they call, and the checks on the two calls together, which read the mock as well as both records.

The two calls are held at one point so they really race: each call's first booking after a
confirmed read-back waits until the other call reaches its own, then both go to the tool server at
once. A call that ends without booking lets the other go on alone, and `booked_together` says so.
"""
import json
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from evals.checks import BOOKED, Check, _one_of, check_names
from evals.results import ScenarioResult
from evals.scenario import Scenario, ScenarioError
from evals.stacks import InProcessAgent
from evals.world import TENANT_DIR, World, resolve_caller
from servicetitan_mock import capacity, worldkit
from servicetitan_mock.config import TenantConfig

PAIRS_DIR = Path(__file__).resolve().parent / "scenarios" / "pairs"
# How long a call waits at the booking for the other one. A call that ends lets the other go at
# once, so this only matters if a call hangs.
MEET_TIMEOUT_S = 300
LETTERS = "ab"


class KeepOpen(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    window: str
    # How many technicians a booking below Urgent may still take in the window.
    technicians: int = Field(ge=1)


class WorldSetup(BaseModel):
    """Days to book up before the calls, leaving only the windows in keep_open with room. Each
    window keeps its urgent hold free, as the seed does, so only non-urgent callers see it full."""

    model_config = ConfigDict(extra="forbid")

    job_type: str
    fill: list[date] = Field(min_length=1)
    keep_open: list[KeepOpen] = Field(default_factory=list)


class Pair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    flow: list[str]
    difficulty: str
    traits: list[str] = Field(default_factory=list)
    journeys: list[str]
    spec: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    time: str | None = None
    max_turns: int | None = None
    world: WorldSetup
    # Lines both simulated callers follow; a caller's own `script` lines come first.
    script: list[str] = Field(default_factory=list)
    # Each caller's own fields (caller_id, persona, hidden_facts, opening, script, ...).
    callers: list[dict[str, Any]] = Field(min_length=2, max_length=2)
    must: list[dict[str, Any]] = Field(min_length=1)
    should: list[str] = Field(default_factory=list)
    together: list[dict[str, Any]] = Field(min_length=1)

    @field_validator("together")
    @classmethod
    def known_checks(cls, value: list[dict]) -> list[dict]:
        for item in value:
            if len(item) != 1:
                raise ValueError(f"each together item has one check name, got {sorted(item)}")
            (name, arg), = item.items()
            if name not in PAIR_CHECKS:
                raise ValueError(f"unknown pair check {name!r}")
            PAIR_CHECKS[name].validate(arg)
        return value

    def scenarios(self) -> list[Scenario]:
        """One scenario per caller, `<pair id>-a` and `<pair id>-b`."""
        shared = self.model_dump(
            include={"flow", "difficulty", "traits", "journeys", "spec", "tags", "must", "should"}
        )
        if self.time:
            shared["time"] = self.time
        if self.max_turns:
            shared["max_turns"] = self.max_turns
        return [
            Scenario.model_validate({
                **shared, **caller, "id": f"{self.id}-{letter}",
                "script": caller.get("script", []) + self.script,
            })
            for letter, caller in zip(LETTERS, self.callers, strict=True)
        ]


def load(path: Path) -> Pair:
    try:
        pair = Pair.model_validate(yaml.safe_load(path.read_text()))
        pair.scenarios()
    except (OSError, yaml.YAMLError) as err:
        raise ScenarioError(f"cannot read {path.name}: {err}") from err
    except ValidationError as err:
        raise ScenarioError(f"{path.name} is not a valid pair: {err}") from err
    if pair.id != path.stem:
        raise ScenarioError(f"{path.name}: id {pair.id!r} does not match the file name")
    return pair


def load_all(directory: Path = PAIRS_DIR) -> list[Pair]:
    return [load(p) for p in sorted(directory.glob("*.yaml"))]


def select(pairs: list[Pair], wanted: str) -> list[Pair]:
    """`all`, or pair ids separated by commas."""
    if wanted == "all":
        return pairs
    ids = [i.strip() for i in wanted.split(",") if i.strip()]
    by_id = {p.id: p for p in pairs}
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        raise ScenarioError(f"no pair {', '.join(unknown)}; known: {', '.join(by_id)}")
    return [by_id[i] for i in ids]


def pair_file(scenario_id: str, directory: Path = PAIRS_DIR) -> Path | None:
    """The pair file a caller's scenario id comes from, or None for an ordinary scenario."""
    pair_id, _, letter = scenario_id.rpartition("-")
    path = directory / f"{pair_id}.yaml"
    return path if letter in LETTERS and path.exists() else None


# ---- the world before the calls -------------------------------------------------------------


def fill(st_db: Path, setup: WorldSetup, tenant_dir: Path = TENANT_DIR) -> None:
    """Book up the setup's days in the mock directly, leaving the urgent hold free in every window
    and, in the keep_open windows, that many more technicians who can do the job."""
    cfg = TenantConfig.load(tenant_dir)
    job_type = next(j for j in cfg.job_types if j["code"] == setup.job_type)
    unit = job_type["businessUnitIds"][0]
    keep = {(k.date, k.window): k.technicians for k in setup.keep_open}
    conn = sqlite3.connect(st_db)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            customer_id, location_id = conn.execute(
                """SELECT c.id, l.id FROM customers c JOIN locations l ON l.customer_id = c.id
                   WHERE c.type = 'Residential' ORDER BY c.id LIMIT 1"""
            ).fetchone()
            for day in setup.fill:
                for window in cfg.windows:
                    if window.after_hours or not cfg.window_offered(day, window):
                        continue
                    slots = capacity.slots(conn, cfg, day, window, unit, job_type["skills"])
                    room = cfg.hold_for(day, window.id, unit) + keep.get((day, window.id), 0)
                    # Technicians without the skills go first, so the ones left can do the job.
                    free = sorted(slots.free_ids, key=lambda t: t in slots.fit)
                    for tech_id in free[: max(0, len(free) - room)]:
                        worldkit.add_job(
                            conn, cfg, customer_id, location_id, job_type["id"], day, window.id,
                            tech_id=tech_id, summary="Booked before the calls.",
                        )
    finally:
        conn.close()


# ---- holding both bookings until both calls are there ----------------------------------------


class Meeting:
    """Each call's first booking after a confirmed read-back waits here for the other's."""

    def __init__(self, parties: int, timeout_s: float = MEET_TIMEOUT_S):
        self.barrier = threading.Barrier(parties, timeout=timeout_s)
        self.met = False

    def wait(self) -> None:
        try:
            self.barrier.wait()
            self.met = True
        except threading.BrokenBarrierError:
            pass

    def leave(self) -> None:
        """A call ended without meeting the other: whoever is still waiting goes on alone. After a
        meeting it does nothing, so a call that finishes fast can't break the barrier under one
        that was released but hasn't woken yet."""
        if not self.met:
            self.barrier.abort()


class HeldServer:
    """The tool server as one call sees it, with that call's first real booking held."""

    def __init__(self, server, meeting: Meeting):
        self.server = server
        self.meeting = meeting
        self.confirmed = False
        self.held = False

    def tool(self, name: str, call_id: str, args: dict, key: str | None = None) -> dict:
        if name == "book_appointment" and self.confirmed and not self.held:
            self.held = True
            self.meeting.wait()
        result = self.server.tool(name, call_id, args, key)
        if name == "read_back" and args.get("confirmed") and result.get("ok"):
            self.confirmed = True
        return result

    def __getattr__(self, name):
        return getattr(self.server, name)


# ---- checks on the two calls together --------------------------------------------------------


class PairEvidence:
    def __init__(self, traces: list[dict], st_db: Path, met: bool):
        self.traces = traces
        self.st_db = st_db
        self.met = met

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        conn = sqlite3.connect(self.st_db)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def job_ids(self) -> list[int]:
        return [
            b["fsm_job_id"] for t in self.traces for b in t["bookings"]
            if b["kind"] == "book" and b["status"] in BOOKED and b["fsm_job_id"]
        ]


def one_job_per_technician_window(arg: bool, ev: PairEvidence) -> list[str]:
    """Read from ServiceTitan itself, over the whole mock: no technician has two live jobs whose
    windows overlap (windows are any run of 30-minute blocks)."""
    rows = ev.query(
        """SELECT a.technician_id, a.window_date, a.window_id, b.window_id AS other
           FROM appointments a JOIN appointments b
             ON a.technician_id = b.technician_id AND a.id < b.id
            AND a.arrival_window_start < b.arrival_window_end
            AND b.arrival_window_start < a.arrival_window_end
           WHERE a.status <> 'Canceled' AND b.status <> 'Canceled'"""
    )
    return [
        f"technician {r['technician_id']} has jobs in overlapping windows {r['window_id']} and "
        f"{r['other']} on {r['window_date']}"
        for r in rows
    ]


def window_jobs(arg: dict, ev: PairEvidence) -> list[str]:
    """The two calls' jobs in ServiceTitan in this window: how many, each with its own technician."""
    ids = ev.job_ids()
    rows = ev.query(
        f"""SELECT job_id, technician_id FROM appointments
            WHERE job_id IN ({','.join('?' * len(ids))}) AND window_date = ? AND window_id = ?
              AND status <> 'Canceled'""",
        (*ids, str(arg["date"]), arg["window"]),
    ) if ids else []
    problems = []
    if len(rows) != arg["count"]:
        problems.append(
            f"{len(rows)} of the calls' jobs are in the {arg['window']} window on {arg['date']}, "
            f"expected {arg['count']}"
        )
    technicians = {r["technician_id"] for r in rows}
    if len(technicians) != len(rows):
        problems.append(f"{len(rows)} jobs share {len(technicians)} technician(s)")
    return problems


def booked_together(arg: bool, ev: PairEvidence) -> list[str]:
    if ev.met:
        return []
    return ["the calls never reached a booking at the same moment, so nothing raced"]


def taken_refusals(arg: int, ev: PairEvidence) -> list[str]:
    """How many of the calls were told by book_appointment that the window was just taken."""
    told = sum(
        any(e["type"] == "tool" and e["tool"] == "book_appointment"
            and e["refused"] == "slot_taken" for e in t["events"])
        for t in ev.traces
    )
    return [] if told == arg else [f"{told} call(s) told the window was taken, expected {arg}"]


def _window_arg(arg) -> None:
    if not isinstance(arg, dict) or set(arg) != {"date", "window", "count"}:
        raise ValueError("expected a mapping with date, window and count")


def _count(arg) -> None:
    if not isinstance(arg, int) or arg < 0:
        raise ValueError("expected a whole number")


PAIR_CHECKS: dict[str, Check] = {
    "one_job_per_technician_window": Check(_one_of(True), one_job_per_technician_window),
    "window_jobs": Check(_window_arg, window_jobs),
    "booked_together": Check(_one_of(True), booked_together),
    "taken_refusals": Check(_count, taken_refusals),
}


def run_pair_checks(together: list[dict], ev: PairEvidence) -> list[str]:
    failures = []
    for item in together:
        (name, arg), = item.items()
        failures += [f"{name}: {p}" for p in PAIR_CHECKS[name].run(arg, ev)]
    return failures


def collisions(st_log: Path) -> int:
    """How many job creations the mock answered 409 slot_taken: two bookings reached for the same
    technician and the loser had to look again."""
    if not st_log.exists():
        return 0
    rows = [json.loads(line) for line in st_log.read_text().splitlines() if line.strip()]
    return sum(
        1 for r in rows
        if r.get("method") == "POST" and str(r.get("path", "")).endswith("/jobs")
        and r.get("error") == "slot_taken"
    )


# ---- running a pair -------------------------------------------------------------------------


def run_pair(harness, pair: Pair, sample: int = 1, caller_turns: list[list[str]] | None = None,
             agent_models: list | None = None) -> list[ScenarioResult]:
    """Both calls at once on one world, each scored on its own, then the together checks added to
    both. caller_turns replays lines per caller and agent_models sets each call's model (tests)."""
    scenarios = pair.scenarios()
    workdir = harness.workdir / f"pair-{pair.id}-{sample}-{uuid.uuid4().hex[:8]}"
    world = World(workdir, harness.seed, scenarios[0].start_time, harness.overlay)
    fill(world.st_db, pair.world)
    meeting = Meeting(len(scenarios))

    def opener(i: int):
        model = agent_models[i] if agent_models else harness.models.agent

        def open_agent(scenario: Scenario, call_id: str):
            caller_id = resolve_caller(scenario.caller_id, harness.seed[1])
            held = HeldServer(world.server, meeting)
            return InProcessAgent(held, model, call_id, caller_id, world.clock), world.server

        return open_agent

    def one(i: int) -> ScenarioResult:
        try:
            turns = caller_turns[i] if caller_turns else None
            return harness.run(scenarios[i], sample, turns, opener(i))
        finally:
            meeting.leave()

    with ThreadPoolExecutor(len(scenarios)) as pool:
        results = list(pool.map(one, range(len(scenarios))))
    try:
        traces = [world.server.trace(r.call_id) for r in results]
    except Exception as err:  # noqa: BLE001 - a broken harness run is an error, not a fail
        return [r.model_copy(update={"outcome": "error", "error": r.error or f"harness: {err}"})
                for r in results]
    failures = run_pair_checks(pair.together, PairEvidence(traces, world.st_db, meeting.met))
    print(f"{pair.id} #{sample}: met at the booking {meeting.met}, "
          f"mock answered slot_taken {collisions(world.st_log)} time(s)", flush=True)
    return [with_pair_checks(r, s, pair, failures) for r, s in zip(results, scenarios, strict=True)]


def with_pair_checks(result: ScenarioResult, scenario: Scenario, pair: Pair,
                     failures: list[str]) -> ScenarioResult:
    """A failed together check fails both calls, even one that ended with no verdict: a double
    booking is wrong however the calls ended. A broken harness stays an error."""
    if result.error.startswith("harness") or (result.outcome == "error" and not failures):
        return result
    update: dict = {"checks": result.checks + check_names(pair.together)}
    if failures:
        update["check_failures"] = result.check_failures + failures
        update["outcome"] = "fail"
        if scenario.must_pass:
            update["must_pass_failures"] = [
                f for f in result.check_failures + failures if not f.startswith("grader:")
            ]
    return result.model_copy(update=update)

