"""Run scenarios against a stack and model profile, score them, and write the results file.

Usage: python -m evals.run [--suite smoke] [--tags flow:safety,...] [--stacks livekit,retell]
                           [--profiles fast,quality] [--samples N] [--workers 4] [--no-upload]
       python -m evals.run --pairs all|<id>,... [--samples N]   two calls at once (evals/pairs.py)

Code checks decide pass or fail; the grader can then fail a pass, never rescue a fail. A run where
the simulated caller loops, never finishes or the harness breaks is marked error and has no
verdict (decision 06). Each run is compared with the previous one of the same suite, stack and
profile, and mirrored to LangSmith when LANGSMITH_API_KEY is set.
"""

import argparse
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.tools import TOOLS
from evals import compare, langsmith_sync, pairs, results, suites
from evals.caller import SimulatedCaller
from evals.checks import Evidence, check_names, run_checks, safety_config
from evals.compare import percentile
from evals.fingerprint import prompt_hash, run_hash
from evals.grader import grade, set_up
from evals.models import Model, Usage, for_profile, load_config
from evals.models import profile as models_profile
from evals.results import RESULTS_DIR, Run, ScenarioResult, ToolCall, Turn
from evals.scenario import Scenario, ScenarioError, load_all
from evals.stacks import InProcessAgent, load_adapter
from evals.transcribe import level_for, mishear, roughen, seeded
from evals.world import World, resolve_caller, seeded_db

STACKS = ("livekit", "retell", "retell_flow")
LOOP_REPEATS = 3


@dataclass
class Models:
    agent: Model
    caller: Model
    grader: Model


class Harness:
    def __init__(
        self,
        models: Models,
        stack: str,
        workdir: Path,
        open_agent: Callable | None = None,
        overlay: dict | None = None,
    ):
        if stack not in STACKS:
            raise ValueError(f"unknown stack {stack!r}; known: {', '.join(STACKS)}")
        self.models = models
        self.stack = stack
        self.workdir = workdir
        self.seed = seeded_db(workdir / "seed")
        self.safety = safety_config()
        self.open_agent = open_agent or self._open_agent
        # The owner's draft under test (evals/gate.py), served as the live version in each world.
        self.overlay = overlay

    def _open_agent(self, scenario: Scenario, call_id: str):
        """The agent under test and the tool server whose record the checks read."""
        caller_id = resolve_caller(scenario.caller_id, self.seed[1])
        if self.stack != "livekit":
            return load_adapter(self.stack).open_agent(call_id, caller_id)
        world = World(
            self.workdir / call_id,
            self.seed,
            scenario.start_time,
            self.overlay,
            scenario.fsm_down,
            transfer=scenario.transfer is not None,
            weather=scenario.weather,
        )
        if scenario.earlier_call:
            world.earlier_call(f"{call_id}-earlier", caller_id, scenario.earlier_call)
        # So the checks can read what ServiceTitan holds after the call (evals/checks.py).
        world.server.st_db = world.st_db
        agent = InProcessAgent(
            world.server,
            self.models.agent,
            call_id,
            caller_id,
            world.clock,
            handoff=scenario.transfer or "sent",
        )
        return agent, world.server

    def run(
        self,
        scenario: Scenario,
        sample: int = 1,
        caller_turns: list[str] | None = None,
        open_agent: Callable | None = None,
    ) -> ScenarioResult:
        """One simulated call. With caller_turns, those lines are replayed instead of a caller.
        open_agent replaces the harness's own, as when two calls share one world (evals/pairs.py)."""
        call_id = f"eval-{scenario.id}-{sample}-{uuid.uuid4().hex[:8]}"
        began = time.perf_counter()
        transcript: list[Turn] = []
        timings = []
        usage = Usage()
        agent_usage = Usage()
        error = ""
        server = None
        try:
            agent, server = (open_agent or self.open_agent)(scenario, call_id)
            call_id = getattr(agent, "call_id", call_id)
            # The caller knows the digits of the phone they're on, so a read-back of the caller ID
            # can be confirmed or corrected instead of guessed at.
            calling_from = resolve_caller(scenario.caller_id, self.seed[1])
            caller = SimulatedCaller(
                scenario, self.models.caller, extra_facts={"calling_from": calling_from}
            )
            rng = seeded(scenario.id, sample)
            level = level_for(scenario.difficulty, scenario.traits)
            turn = agent.start()
            agent_usage.add(turn.usage)
            if turn.text:
                transcript.append(Turn(role="agent", text=turn.text))
            replay = list(caller_turns) if caller_turns is not None else None
            said = replay.pop(0) if replay is not None else caller.first_line(turn.text)
            recent: list[str] = []
            reason = None
            for _ in range(scenario.max_turns):
                heard = (
                    said
                    if replay is not None
                    else roughen(mishear(said, scenario.misheard), level, rng)
                )
                transcript.append(Turn(role="caller", text=heard))
                turn = agent.hear(heard)
                agent_usage.add(turn.usage)
                timings.append(turn.timing)
                if turn.text:
                    transcript.append(Turn(role="agent", text=turn.text))
                if turn.ended:
                    reason = turn.end_reason
                    break
                # The caller asked the agent to wait and walks away: the worker's timer runs on a
                # short clock (voice/dead_air.py), and the call ends as stepped_away.
                if scenario.steps_away and getattr(agent, "holding", False):
                    turn = agent.step_away()
                    transcript.append(Turn(role="agent", text=turn.text))
                    reason = turn.end_reason
                    break
                if replay is not None:
                    said = replay.pop(0) if replay else None
                else:
                    said = caller.reply(turn.text, said)
                if said is None:
                    reason = "caller_hangup"
                    break
                recent = (recent + [said.strip().lower()])[-LOOP_REPEATS:]
                if len(recent) == LOOP_REPEATS and len(set(recent)) == 1:
                    error = "the simulated caller repeated the same line three times"
                    break
            else:
                error = f"the call did not finish within {scenario.max_turns} caller turns"
            agent.end(reason or "caller_hangup")
            usage.add(caller.usage)
        except Exception as err:  # noqa: BLE001 - a broken harness run is an error, not a fail
            error = f"harness: {type(err).__name__}: {err}"
        usage.add(agent_usage)
        return self._score(
            scenario,
            sample,
            call_id,
            server,
            transcript,
            timings,
            usage,
            error,
            began,
            agent_usage.cost_usd,
        )

    def _score(
        self,
        scenario,
        sample,
        call_id,
        server,
        transcript,
        timings,
        usage,
        error,
        began,
        agent_cost=0.0,
    ):
        return score(
            scenario,
            sample,
            call_id,
            server,
            transcript,
            timings,
            usage,
            error,
            began,
            safety=self.safety,
            grader=self.models.grader,
            final=self.stack.startswith("retell"),
            agent_cost=agent_cost,
        )


def score(
    scenario,
    sample,
    call_id,
    server,
    transcript,
    timings,
    usage,
    error,
    began,
    *,
    safety: dict,
    grader: Model,
    final: bool = False,
    agent_cost: float = 0.0,
    **extra,
) -> ScenarioResult:
    """Code checks on the tool server's record, then the grader, for one finished call. `final`
    waits for a call that is closed by webhook (Retell); `extra` are further result fields, such
    as the audio rig's."""
    failures, violations, tool_calls, grade_score, notes = [], [], [], None, ""
    ran: list[str] = []
    done: list[str] = []
    if server is not None:
        try:
            trace = server.final_trace(call_id) if final else server.trace(call_id)
            tool_calls = [
                ToolCall(name=e["tool"], arguments=e["args"])
                for e in trace["events"]
                if e["type"] == "tool" and e["tool"] in TOOLS
            ]
            evidence = Evidence(trace, safety, getattr(server, "st_db", None))
            done = set_up(trace, safety)
            if not error.startswith("harness"):
                failures = run_checks(scenario.must, evidence)
                violations = run_checks(scenario.must, evidence, prohibitions_only=True)
                ran = check_names(scenario.must)
        except Exception as err:  # noqa: BLE001
            error = error or f"harness: could not read the call record: {err}"
    elif not error:
        error = "harness: no tool server record"
    if error:
        # No verdict, unless the call broke a rule before it stopped (a booking before the
        # read-back stays wrong however the call ends).
        outcome = "fail" if violations and not error.startswith("harness") else "error"
        if outcome == "fail":
            failures = violations
            ran = check_names(scenario.must, prohibitions_only=True)
        else:
            ran = []
    else:
        outcome = "fail" if failures else "pass"
    if not error:
        try:
            g = grade(grader, transcript, scenario.should, done)
            usage.add(g.usage)
            grade_score, notes = g.score, g.notes
            if g.failed and outcome == "pass":
                outcome = "fail"
                failures.append(f"grader: {g.notes}")
        except Exception as err:  # noqa: BLE001
            # A broken grader never hides a failed check: the fail stands, ungraded.
            if outcome == "pass":
                outcome, error = "error", f"grader: {err}"
            else:
                notes = f"grader unavailable: {err}"
    delays = [t.reply_delay_ms for t in timings]
    return ScenarioResult(
        scenario_id=scenario.id,
        sample=sample,
        persona=scenario.persona.strip(),
        flow=scenario.flow,
        difficulty=scenario.difficulty,
        traits=scenario.traits,
        outcome=outcome,
        must_pass_failures=(
            [f for f in failures if not f.startswith("grader:")]
            if scenario.must_pass and outcome == "fail"
            else []
        ),
        check_failures=failures,
        checks=ran,
        error=error,
        grader_score=grade_score,
        grader_notes=notes,
        reply_delay_p50_ms=percentile(delays, 0.5),
        reply_delay_p90_ms=percentile(delays, 0.9),
        cost_usd=round(usage.cost_usd, 6),
        agent_cost_usd=round(agent_cost, 6),
        duration_ms=(time.perf_counter() - began) * 1000,
        transcript=transcript,
        tool_calls=tool_calls,
        turn_timings=timings,
        call_id=call_id,
        **extra,
    )


def run_suite(
    harness: Harness, chosen: list[Scenario], samples: int, workers: int
) -> list[ScenarioResult]:
    jobs = [(s, n) for s in chosen for n in range(1, samples + 1)]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(lambda job: harness.run(*job), jobs))


def run_pair_suite(
    harness: Harness, chosen: list[pairs.Pair], samples: int, workers: int
) -> list[ScenarioResult]:
    """Each pair's two calls run together; separate pairs and samples run side by side."""
    jobs = [(p, n) for p in chosen for n in range(1, samples + 1)]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        found = pool.map(lambda job: pairs.run_pair(harness, *job), jobs)
        return [r for pair_results in found for r in pair_results]


def workers_for(stack: str, asked: int) -> int:
    """Retell scenarios share one mock that is reset before each, so they go one at a time."""
    if stack != "livekit" and asked > 1:
        print(f"{stack}: running one scenario at a time (they share one mock world)", flush=True)
        return 1
    return asked


def profile_model(profile: str) -> str | None:
    return models_profile(profile).get("model")


def build_run(
    suite: str,
    tags: list[str],
    stack: str,
    profile: str,
    scenario_results,
    now: datetime | None = None,
    overlay: dict | None = None,
    transport: str = "text",
) -> Run:
    now = now or datetime.now(UTC)
    ids = sorted({r.scenario_id for r in scenario_results})
    return Run(
        run_id=f"{now:%Y%m%d-%H%M%S}-{stack}-{profile}-{suite}",
        created=now,
        suite=suite,
        tags=tags,
        stack=stack,
        profile=profile,
        hash=run_hash(ids, profile, overlay=overlay),
        prompt_hash=prompt_hash(),
        model=profile_model(profile),
        transport=transport,
        overlay=overlay,
        scenarios=scenario_results,
    )


def upload(run: Run, path: Path) -> None:
    """Mirror to LangSmith. A failure leaves the results file as the record and says how to retry."""
    try:
        langsmith_sync.sync(run)
    except langsmith_sync.api_errors() as err:
        print(f"LangSmith upload failed ({type(err).__name__}); retry: make langsmith RUN={path}")


def default_models(profile: str) -> Models:
    return Models(
        for_profile(profile), for_profile("offline.caller"), for_profile("offline.grader")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the eval suite.")
    parser.add_argument("--suite", default="")
    parser.add_argument("--tags", default="")
    parser.add_argument("--stacks", default="livekit")
    parser.add_argument("--profiles", default="")
    parser.add_argument("--samples", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--results-dir", default=str(RESULTS_DIR))
    parser.add_argument(
        "--pairs",
        default="",
        help="all, or pair ids: two calls at once on one world (evals/pairs.py)",
    )
    args = parser.parse_args(argv)

    tags = suites.parse_tags(args.tags)
    if args.pairs:
        if suites.parse_tags(args.stacks) != ["livekit"]:
            print(
                "pairs run on the livekit stack only: Retell's eval mock is reset before each "
                "call, so two calls cannot share it",
                file=sys.stderr,
            )
            return 2
        try:
            chosen, samples, suite = pairs.select(pairs.load_all(), args.pairs), 1, "pairs"
        except ScenarioError as err:
            print(err, file=sys.stderr)
            return 2
    else:
        named = args.suite or (None if tags else "smoke")
        suite = named or "custom"
        try:
            chosen, samples = suites.select(load_all(), named, tags)
        except suites.SuiteError as err:
            print(err, file=sys.stderr)
            return 2
    samples = args.samples or samples
    profiles = suites.parse_tags(args.profiles) or [load_config()["default"]]
    results_dir = Path(args.results_dir)
    status = 0
    for stack in suites.parse_tags(args.stacks):
        for profile in profiles:
            print(f"{stack} / {profile}: {len(chosen)} scenario(s) x {samples}", flush=True)
            with tempfile.TemporaryDirectory(prefix="summit-eval-") as tmp:
                harness = Harness(default_models(profile), stack, Path(tmp))
                workers = workers_for(stack, args.workers)
                run_all = run_pair_suite if args.pairs else run_suite
                scenario_results = run_all(harness, chosen, samples, workers)
            run = build_run(suite, tags, stack, profile, scenario_results)
            path = results.save(run, results_dir)
            print("\n".join(compare.report(run, compare.previous(run, results_dir))))
            print(f"results: {path}")
            if not args.no_upload:
                upload(run, path)
            if any(s.must_pass_failures for s in run.scenarios):
                status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
