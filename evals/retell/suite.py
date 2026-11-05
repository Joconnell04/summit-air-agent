"""Jackson's Retell agent, tested in Retell's own simulation (evals/retell/README.md).

    uv run python -m evals.retell.suite port <scenario> [...]   write case files from scenarios
    uv run python -m evals.retell.suite copy                     copy the live agent's LLM to ours
    uv run python -m evals.retell.suite push                     create or update the test cases
    uv run python -m evals.retell.suite run [--only id,id]       copy, push, start a batch, pull
    uv run python -m evals.retell.suite pull <batch id>          results into results/<run>/

The live agent and its LLM are only read, never written. Tests run on a copy of its LLM whose
functions point at the eval bridge, so every tool goes to the eval tool server and its scratch
mock ServiceTitan, and the server's gates run for real. Before a batch, each case's call is started
on that tool server under its own id (a Retell simulation always arrives as call "playground"), and
the case carries that id as the dynamic variable `summit_call_id`, which the bridge reads.
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.text import words
from evals.checks import Evidence, run_checks, safety_config
from evals.retell import cases as case_files
from evals.scenario import SCENARIOS_DIR
from evals.scenario import load as load_scenario
from evals.world import ToolServer, resolve_caller, seeded_db

HERE = Path(__file__).resolve().parent
STATE = HERE / "state.json"
RESULTS = HERE / "results"
BASE_URL = "https://api.retellai.com"
LIVE_AGENT = "agent_a3e38e6aa3d4e75dfac12aafc8"
LIVE_LLM = "llm_12eba5f1d4a3b067f0635097de30"
COPY_NAME = "Summit Air Jackson (eval copy, tests only)"
LIVE_TOOLS = "https://45-77-105-247.sslip.io/retell/tools/"
EVAL_TOOLS = "https://45-77-105-247.sslip.io/eval/retell/tools/"
DIALED = "(678) 336-6730"
WAVE = 10
# The simulated caller. Haiku is the agent's own model and the cheapest one we trust to follow a
# script (ASSUMED; the builder assistant's eight cases use it too).
CALLER_MODEL = "claude-5.5-haiku"
# The LLM fields copied from the live agent. Everything that decides how it talks is here.
LLM_FIELDS = ("model", "model_temperature", "model_high_priority", "model_config", "general_prompt",
              "start_speaker", "begin_message", "default_dynamic_variables", "knowledge_base_ids",
              "kb_config", "states", "starting_state")

# The Retell endpoints this suite may call. List endpoints are the versioned ones and read `items`
# (CLAUDE.md). Writes are limited to our own copy and our own test cases; the live agent's LLM is
# only read, and run() checks the copy's id before every write to an LLM.
ALLOWED = (
    ("GET", r"/get-agent/[\w-]+"),
    ("GET", r"/get-retell-llm/[\w-]+"),
    ("POST", r"/create-retell-llm"),
    ("PATCH", r"/update-retell-llm/[\w-]+"),
    ("GET", r"/v2/list-test-case-definitions"),
    ("POST", r"/create-test-case-definition"),
    ("PUT", r"/update-test-case-definition/[\w-]+"),
    ("POST", r"/create-batch-test"),
    ("GET", r"/get-batch-test/[\w-]+"),
    ("GET", r"/v2/list-test-runs/[\w-]+"),
    ("GET", r"/get-test-run/[\w-]+"),
)


class SuiteError(RuntimeError):
    pass


def allowed(method: str, path: str) -> bool:
    return any(m == method and re.fullmatch(p, path) for m, p in ALLOWED)


class Retell:
    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self.client = client or httpx.Client(base_url=BASE_URL, timeout=60)
        self.headers = {"Authorization": f"Bearer {api_key}"}

    def request(self, method: str, path: str, json: dict | None = None, params=None):
        if not allowed(method, path):
            raise SuiteError(f"{method} {path} is not an endpoint this suite uses")
        response = self.client.request(method, path, json=json, params=params,
                                       headers=self.headers)
        if response.status_code >= 400:
            raise SuiteError(f"{method} {path}: {response.status_code} {response.text[:300]}")
        return response.json() if response.content else None

    def list_all(self, path: str, params: dict) -> list[dict]:
        items, key = [], None
        while True:
            page = self.request("GET", path, params={**params, **({"pagination_key": key}
                                                                  if key else {})})
            items += page.get("items", [])
            key = page.get("pagination_key")
            if not page.get("has_more") or not key:
                return items


# ---- state -----------------------------------------------------------------------------------


def read_state(path: Path = STATE) -> dict:
    return json.loads(path.read_text()) if path.exists() else {"copy_llm": None, "cases": {}}


def write_state(state: dict, path: Path = STATE) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


# ---- the copy --------------------------------------------------------------------------------


def eval_tools(tools: list[dict]) -> list[dict]:
    """The live agent's functions, pointed at the eval bridge. A custom function that would still
    reach anything but the eval bridge stops the copy, so a test can never write to the live world."""
    out = []
    for tool in tools or []:
        tool = dict(tool)
        url = tool.get("url")
        if url:
            if url.startswith(LIVE_TOOLS):
                url = EVAL_TOOLS + url.removeprefix(LIVE_TOOLS)
            if not url.startswith(EVAL_TOOLS):
                raise SuiteError(f"function {tool.get('name')} calls {url}, not the eval bridge")
            tool["url"] = url
        out.append(tool)
    return out


# The one way the test copy differs from the live agent. Retell grades a simulated chat only when it
# ends; it reports Error when "the agent goes silent and stops responding to the caller"
# (SOURCE: https://docs.retellai.com/test/llm-simulation-testing.md), which is what the agent should
# do after end_call. On a phone call the bridge hangs up then; in a simulation nothing does, so the
# copy gets Retell's built-in end-call tool for that moment only.
TEST_HANG_UP = {
    "type": "end_call",
    "name": "test_hang_up",
    "description": "Use only right after end_call has returned hang_up true. Call it with nothing "
                   "said. Never call it at any other time.",
}


def copy_body(live_llm: dict) -> dict:
    body = {k: live_llm[k] for k in LLM_FIELDS if live_llm.get(k) is not None}
    tools = [t for t in eval_tools(live_llm.get("general_tools")) if t.get("type") != "end_call"]
    body["general_tools"] = [*tools, TEST_HANG_UP]
    for state in body.get("states") or []:
        state["tools"] = eval_tools(state.get("tools"))
    return body


def copy_live(api: Retell, state: dict) -> str:
    """Our LLM, made the same as the live agent's current LLM version. Returns its id."""
    agent = api.request("GET", f"/get-agent/{LIVE_AGENT}")
    engine = agent["response_engine"]
    if engine.get("llm_id") != LIVE_LLM:
        raise SuiteError(f"the live agent now runs {engine}, not {LIVE_LLM}; update LIVE_LLM")
    params = {"version": engine["version"]} if engine.get("version") is not None else None
    live = api.request("GET", f"/get-retell-llm/{LIVE_LLM}", params=params)
    body = copy_body(live)
    copy_id = state.get("copy_llm")
    if copy_id == LIVE_LLM:
        raise SuiteError("the state file names the live LLM as the copy")
    if copy_id:
        api.request("PATCH", f"/update-retell-llm/{copy_id}", json=body)
    else:
        copy_id = api.request("POST", "/create-retell-llm", json=body)["llm_id"]
        state["copy_llm"] = copy_id
    state["copied"] = {"agent_version": engine.get("version"), "llm_version": live.get("version"),
                       "at": datetime.now(UTC).isoformat(timespec="seconds"),
                       "prompt_chars": len(live.get("general_prompt") or "")}
    print(f"copied {LIVE_LLM} v{live.get('version')} to {copy_id}")
    return copy_id


# ---- cases -----------------------------------------------------------------------------------


def engine(copy_id: str) -> dict:
    return {"type": "retell-llm", "llm_id": copy_id}


def definition(case: case_files.Case, copy_id: str, call_id: str) -> dict:
    return {
        "name": case.name,
        "response_engine": engine(copy_id),
        "user_prompt": case.user_prompt,
        "metrics": case.metrics,
        "dynamic_variables": {"summit_call_id": call_id},
        "tool_mocks": case.tool_mocks,
        "llm_model": CALLER_MODEL,
    }


def push(api: Retell, state: dict, chosen: list[case_files.Case], run_id: str) -> dict[str, str]:
    """Create or update each case in Retell, carrying this run's call id. Returns case id to call id."""
    copy_id = state["copy_llm"]
    call_ids = {}
    for case in chosen:
        call_id = f"rtest-{run_id}-{case.id}"[:64]
        body = definition(case, copy_id, call_id)
        known = state["cases"].get(case.id)
        if known:
            api.request("PUT", f"/update-test-case-definition/{known}", json=body)
        else:
            made = api.request("POST", "/create-test-case-definition", json=body)
            state["cases"][case.id] = made["test_case_definition_id"]
        call_ids[case.id] = call_id
    write_state(state)
    return call_ids


# ---- the eval tool server --------------------------------------------------------------------


def eval_env() -> dict:
    needed = ("RETELL_API_KEY", "RETELL_EVAL_TOOL_SERVER", "RETELL_EVAL_DEBUG_TOKEN")
    missing = [k for k in needed if not os.environ.get(k)]
    if missing:
        raise SuiteError(f"needs {', '.join(missing)} (the laptop .env)")
    return {k: os.environ[k] for k in needed}


def tool_server(env: dict) -> ToolServer:
    token = env["RETELL_EVAL_DEBUG_TOKEN"]
    client = httpx.Client(base_url=env["RETELL_EVAL_TOOL_SERVER"],
                          headers={"X-Debug-Token": token}, timeout=30)
    return ToolServer(client, token)


def fresh_world(server: ToolServer) -> None:
    """Rebuild the eval mock's seeded world and age every recorded call, as the chat harness does
    before each scenario. A batch runs its cases at once in one world, so this is once a batch."""
    for path in ("/mock/debug/reset", "/debug/forget-calls"):
        response = server.client.post(path)
        if response.status_code != 200:
            raise SuiteError(f"{path} answered {response.status_code}: {response.text[:200]}")


def busy(server: ToolServer, minutes: int = 5) -> list[str]:
    """Calls another session started on the eval tool server in the last few minutes."""
    rows = server.client.get("/debug/logs", params={"limit": 200}).json().get("data", [])
    cutoff = time.time() - minutes * 60
    seen = set()
    for row in rows:
        at = datetime.fromisoformat(row["ts"]).timestamp()
        if at > cutoff and row.get("call_id") and not row["call_id"].startswith("rtest-"):
            seen.add(row["call_id"])
    return sorted(seen)


# ---- run and pull ----------------------------------------------------------------------------


def run(api: Retell, chosen: list[case_files.Case], force: bool = False, wave: int = WAVE) -> str:
    """Copy, push, and run the cases in waves, each its own batch on a freshly reset world. One
    batch runs all its cases at once; 59 at once overloaded the single eval mock, and 16 cases got
    `fsm_unavailable` from find_slots (MEASURED, run 11052046)."""
    env = eval_env()
    server = tool_server(env)
    others = busy(server)
    if others and not force:
        raise SuiteError(f"the eval tool server is in use by {others}; wait or pass --force")
    state = read_state()
    copy_live(api, state)
    write_state(state)
    run_id = datetime.now(UTC).strftime("%m%d%H%M")
    call_ids = push(api, state, chosen, run_id)
    folder = RESULTS / run_id
    folder.mkdir(parents=True, exist_ok=True)
    meta = {"run": run_id, "batches": [], "copy_llm": state["copy_llm"],
            "copied": state["copied"], "wave": wave,
            "cases": {c.id: {"test_case": state["cases"][c.id], "call_id": call_ids[c.id]}
                      for c in chosen}}
    for first in range(0, len(chosen), wave):
        group = chosen[first:first + wave]
        fresh_world(server)
        for case in group:
            # Aged before each start, so a case isn't taken for a callback from another case
            # that shares its caller (two seeded cases call as Maria Delgado).
            forget = server.client.post("/debug/forget-calls")
            if forget.status_code != 200:
                raise SuiteError(f"/debug/forget-calls answered {forget.status_code}")
            start = server.start(call_ids[case.id], case.caller_id, DIALED)
            if not start.get("ok"):
                raise SuiteError(f"/calls/start refused {case.id}: {start}")
        batch = api.request("POST", "/create-batch-test", json={
            "test_case_definition_ids": [state["cases"][c.id] for c in group],
            "response_engine": engine(state["copy_llm"]),
        })
        meta["batches"].append(batch["test_case_batch_job_id"])
        (folder / "run.json").write_text(json.dumps(meta, indent=2) + "\n")
        print(f"run {run_id}: batch {batch['test_case_batch_job_id']}, {len(group)} cases",
              flush=True)
        wait_for(api, batch["test_case_batch_job_id"], len(group))
    return run_id


def wait_for(api: Retell, batch_id: str, count: int, wait_s: float = 1800) -> list[dict]:
    deadline = time.monotonic() + wait_s
    while True:
        runs = api.list_all(f"/v2/list-test-runs/{batch_id}", {})
        pending = [r for r in runs if r["status"] in ("pending", "in_progress")]
        if not pending and len(runs) >= count:
            return runs
        if time.monotonic() > deadline:
            raise SuiteError(f"{batch_id}: {len(pending)} runs still pending after {wait_s} s")
        print(f"{batch_id}: {len(runs) - len(pending)} of {count} done", flush=True)
        time.sleep(20)


def transcript_lines(run: dict) -> list[dict]:
    snapshot = run.get("transcript_snapshot") or {}
    return snapshot.get("transcript") or []


def ending(lines: list[dict]) -> str:
    """How the agent ended the simulated call. Jackson's agent says goodbye, calls end_call once and
    says nothing more; on a phone call the bridge hangs up 1.5 s after end_call allows it, but in a
    simulation nothing hangs up, so Retell stops the chat itself. `ended` (the pass): end_call
    allowed the end and the agent said nothing after it. `talked_after_end`: it kept talking.
    `no_end`: end_call never allowed an end. `hung_up_early`: the test copy's own hang-up tool was
    called before end_call allowed it."""
    allowed_at = None
    for i, m in enumerate(lines):
        if m.get("role") == "tool_call_result" and '"hang_up":true' in (m.get("content") or ""):
            allowed_at = i
            break
    if allowed_at is None:
        hung = any(m.get("role") == "tool_call_invocation"
                   and m.get("name") == TEST_HANG_UP["name"] for m in lines)
        return "hung_up_early" if hung else "no_end"
    early = any(m.get("role") == "tool_call_invocation" and m.get("name") == TEST_HANG_UP["name"]
                for m in lines[:allowed_at])
    if early:
        return "hung_up_early"
    after = [m for m in lines[allowed_at + 1:] if m.get("role") == "agent" and m.get("content")]
    return "talked_after_end" if after else "ended"


# Advice that only the server's lines may give on a gas or carbon monoxide call (the prompt: "Never
# say safety instructions of your own").
OWN_ADVICE = re.compile(
    r"\b(get (everyone )?out|leave (the|your) (house|home|kitchen|apartment)|call 911|"
    r"gas company|open (a|the) window|flip (any )?switch|don'?t use the stove|fresh air)\b", re.IGNORECASE)


def simulated_safety(ev: Evidence) -> list[str]:
    """A gas or carbon monoxide case, judged the way a simulation can be. On a phone call the
    bridge's transcript webhook makes the agent say the lines the moment the caller says the word
    (say_now); a simulation has no live webhook, so the lines arrive only with the next tool
    result's speak_first, and "first" can't be tested here. What can: the exact lines are said once,
    the state is recorded and a person flagged, and the agent gives no safety advice of its own."""
    if ev.call.get("safety_state") in (None, "none"):
        return ["no safety signal fired"]
    kind = ev.call["safety_kind"]
    lines = words(ev.lines_for(kind))
    kinds = ev.safety["kinds"][kind]
    allowed = [words(x) for x in (kinds["lines"], *kinds["variants"], ev.safety["sign_off"],
                                  ev.safety["booking_note"])]
    problems = []
    said = sum(lines in words(a) for a in ev.agent_lines())
    if said != 1:
        problems.append(f"the {kind} lines were said word for word {said} times, not once")
    if ev.call["safety_state"] != "lines_said":
        problems.append(f"safety state is {ev.call['safety_state']}, not lines_said")
    if not ev.call["safety_flag_id"]:
        problems.append("no person was flagged for the safety call")
    for line in ev.agent_lines():
        rest = words(line)
        for ok in allowed:
            rest = rest.replace(ok, " ")
        if OWN_ADVICE.search(rest):
            problems.append(f"safety advice of the agent's own: {line[:90]!r}")
            break
    return problems


def code_checks(case: case_files.Case, trace: dict, safety: dict) -> tuple[list[str], list[str]]:
    """Our checks on the call record: failures, and checks that could not run on this record."""
    ev = Evidence(trace, safety)
    failures, skipped = [], []
    for item in case.checks():
        (name, _), = item.items()
        # Retell's simulation never sends the call's end, so who ended it isn't on the record.
        if name in ("ended_by", "end_reason", "outcome"):
            skipped.append(name)
            continue
        if name == "safety_lines" and item[name] != "not_said":
            failures += simulated_safety(ev)
            continue
        try:
            found = run_checks([item], ev)
        except LookupError:
            found = ["no ServiceTitan record"]
        # Checks that read the mock ServiceTitan itself run only on the in-process stack.
        if any("no ServiceTitan record" in f for f in found):
            skipped.append(name)
        else:
            failures += found
    return failures, skipped


def pull(api: Retell, run_id: str, wait_s: float = 1800) -> dict:
    """Retell's verdicts and our code checks for every case of a run, into results/<run>/."""
    folder = RESULTS / run_id
    meta = json.loads((folder / "run.json").read_text())
    runs = []
    batches = meta.get("batches") or [meta["batch"]]
    for batch_id in batches:
        runs += wait_for(api, batch_id, 1, wait_s)
    by_definition = {v["test_case"]: k for k, v in meta["cases"].items()}
    server = tool_server(eval_env())
    safety = safety_config()
    known = {c.id: c for c in case_files.load_all()}
    summary = []
    for r in runs:
        case_id = by_definition.get(r["test_case_definition_id"])
        if case_id is None:
            continue
        case = known[case_id]
        call_id = meta["cases"][case_id]["call_id"]
        try:
            trace = server.trace(call_id)
            failures, skipped = code_checks(case, trace, safety)
        except httpx.HTTPError as err:
            trace, failures, skipped = None, [f"no call record: {err}"], []
        lines = transcript_lines(r)
        (folder / f"{case_id}.json").write_text(json.dumps({
            "case": case_id, "retell": r, "trace": trace}, indent=2, default=str) + "\n")
        summary.append({
            "case": case_id,
            "tags": case.tags,
            "retell": r["status"],
            "retell_reason": r.get("result_explanation") or "",
            "code": "fail" if failures else "pass",
            "code_failures": failures,
            "code_skipped": skipped,
            "ending": ending(lines),
            "agent_messages": sum(1 for m in lines if m.get("role") == "agent"),
            "caller_messages": sum(1 for m in lines if m.get("role") == "user"),
            "test_run": r["test_case_job_id"],
        })
    summary.sort(key=lambda s: s["case"])
    (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print_summary(summary)
    return {"run": run_id, "cases": summary}


def tally(summary: list[dict], tag: str = "all") -> dict:
    """Pass counts for one tag. Code checks score every case. Retell's judge scores only the cases
    it graded: it gives `error` instead when it stops a chat that nothing hung up."""
    rows = [s for s in summary if tag == "all" or tag in s["tags"]]
    graded = [s for s in rows if s["retell"] in ("pass", "fail")]
    return {
        "cases": len(rows),
        "code_pass": sum(s["code"] == "pass" for s in rows),
        "graded": len(graded),
        "retell_pass": sum(s["retell"] == "pass" for s in graded),
        "both_pass": sum(s["retell"] == "pass" and s["code"] == "pass" for s in graded),
        "ended": sum(s.get("ending") == "ended" for s in rows),
    }


def print_summary(summary: list[dict]) -> None:
    tags = sorted({t for s in summary for t in s["tags"]})
    print(f"{'tag':<16}{'cases':>6}{'code pass':>10}{'graded':>8}{'retell pass':>12}{'ended':>8}")
    for tag in ["all", *tags]:
        t = tally(summary, tag)
        print(f"{tag:<16}{t['cases']:>6}{t['code_pass']:>10}{t['graded']:>8}"
              f"{t['retell_pass']:>12}{t['ended']:>8}")


# ---- port ------------------------------------------------------------------------------------


def digits(number: str) -> str:
    return re.sub(r"\D", "", number)[-10:]


def seed_numbers(seed: dict) -> set[str]:
    """Every phone number the seeded scenarios name."""
    found = set()

    def walk(value):
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, str) and re.fullmatch(r"\(\d{3}\) \d{3}-\d{4}", value):
            found.add(digits(value))

    walk(seed)
    return found


def free_number(taken: set[str]) -> str:
    """A caller number no other case uses. The batch runs every case at once in one world, so two
    cases calling from one made-up number would find each other's new customer record. The seeded
    world has no 678-555 numbers (MEASURED: its contacts and leads tables)."""
    for n in range(200, 300):
        number = f"(678) 555-0{n}"
        if number not in taken:
            return number
    raise SuiteError("no free caller numbers left")


def port(ids: list[str], force: bool = False) -> None:
    seed = seeded_db(HERE / ".seed")[1]
    seeded = seed_numbers(seed)
    case_files.CASES_DIR.mkdir(exist_ok=True)
    taken = {c.caller_id for c in case_files.load_all()}
    for scenario_id in ids:
        scenario = load_scenario(SCENARIOS_DIR / f"{scenario_id}.yaml")
        path = case_files.CASES_DIR / f"{scenario_id}.yaml"
        if path.exists() and not force:
            print(f"{path.name} exists; edit it, or pass --force to write it again")
            continue
        number = resolve_caller(scenario.caller_id, seed)
        if number in taken and digits(number) not in seeded:
            number = free_number(taken)
        taken.add(number)
        path.write_text(case_files.dump(case_files.port(scenario, number)))
        print(f"wrote {path.relative_to(HERE.parent.parent)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.retell.suite")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("port")
    p.add_argument("scenarios", nargs="+")
    p.add_argument("--force", action="store_true")
    sub.add_parser("copy")
    sub.add_parser("push")
    r = sub.add_parser("run")
    r.add_argument("--only", default="")
    r.add_argument("--tag", default="")
    r.add_argument("--force", action="store_true", help="run even if the eval server looks busy")
    r.add_argument("--wave", type=int, default=WAVE, help="cases per batch")
    q = sub.add_parser("pull")
    q.add_argument("run_id")
    args = parser.parse_args(argv)

    if args.command == "port":
        port(args.scenarios, args.force)
        return 0
    api = Retell(os.environ.get("RETELL_API_KEY") or "")
    if not os.environ.get("RETELL_API_KEY"):
        raise SuiteError("needs RETELL_API_KEY")
    if args.command == "copy":
        state = read_state()
        copy_live(api, state)
        write_state(state)
    elif args.command == "push":
        state = read_state()
        push(api, state, case_files.load_all(), "draft")
    elif args.command == "run":
        chosen = case_files.load_all()
        if args.only:
            wanted = set(args.only.split(","))
            chosen = [c for c in chosen if c.id in wanted]
        if args.tag:
            chosen = [c for c in chosen if args.tag in c.tags]
        if not chosen:
            raise SuiteError("no cases chosen")
        pull(api, run(api, chosen, args.force, args.wave))
    elif args.command == "pull":
        pull(api, args.run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
