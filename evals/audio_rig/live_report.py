"""The live-like suite's table from one or more rig results files (evals/README.md, "Live-like
suite"): per scenario, the audio checks that failed with the line each quotes, and the time to
first sound across the run against the S-36 targets.

    uv run python -m evals.audio_rig.live_report evals/results/<run>.json [...]
    uv run python -m evals.audio_rig.live_report --rescore data/sim/<batch>

Several files (a run and its reruns) are read as one, the later file winning for a scenario both
ran. Prints Markdown. `--rescore` runs today's checks again on a batch's saved timelines and
writes `<results>-rescored.json` beside the batch's results file, for a check that changed after
the calls were made.
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from evals import results as results_mod
from evals.audio_rig import live_checks
from evals.audio_rig.live_checks import NAMES
from evals.audio_rig.recording import MANIFEST, ROOT
from evals.audio_rig.timeline import from_dict
from evals.compare import percentile
from evals.models import load_config
from evals.results import ScenarioResult, load


def audio_failures(s: ScenarioResult) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for line in s.check_failures:
        name, _, detail = line.partition(": ")
        if name in NAMES:
            out[name].append(detail)
    return out


def record_failures(s: ScenarioResult) -> list[str]:
    return [f for f in s.check_failures if f.partition(": ")[0] not in NAMES]


def first_sound(scenarios: list[ScenarioResult]) -> dict[str, tuple[float, float, int]]:
    by_kind: dict[str, list[float]] = defaultdict(list)
    for s in scenarios:
        for t in s.turn_timings:
            by_kind[t.kind].append(t.reply_delay_ms)
    return {k: (percentile(v, 0.5), percentile(v, 0.9), len(v)) for k, v in sorted(by_kind.items())}


def requests(scenarios: list[ScenarioResult]) -> dict[str, tuple[float, float, int]]:
    """Model requests per turn, median and most, by turn kind, where the worker logged them."""
    by_kind: dict[str, list[float]] = defaultdict(list)
    for s in scenarios:
        for t in s.turn_timings:
            if "llm_requests" in t.platform_stages:
                by_kind[t.kind].append(t.platform_stages["llm_requests"])
    return {k: (percentile(v, 0.5), max(v), len(v)) for k, v in sorted(by_kind.items())}


def report(scenarios: list[ScenarioResult], config: dict) -> list[str]:
    targets = config["audio_rig"]["live_checks"]["first_sound_ms"]
    lines = ["| Scenario | Outcome | " + " | ".join(NAMES) + " | Record checks |",
             "|---|---|" + "---|" * len(NAMES) + "---|"]
    totals = dict.fromkeys(NAMES, 0)
    for s in scenarios:
        failed = audio_failures(s)
        for name in NAMES:
            totals[name] += bool(failed.get(name))
        cells = [str(len(failed[n])) if failed.get(n) else "" for n in NAMES]
        other = "; ".join(record_failures(s)) or ""
        outcome = s.outcome + (f" ({s.error})" if s.error else "")
        lines.append(f"| {s.scenario_id} | {outcome} | " + " | ".join(cells) + f" | {other} |")
    lines.append("| Scenarios failing | | " + " | ".join(str(totals[n]) for n in NAMES) + " | |")
    lines += ["", "Time to first sound, every timed turn in the run:", "",
              "| Turns | Count | Median ms | Target | Slowest 10% ms | Target |",
              "|---|---|---|---|---|---|"]
    for kind, (p50, p90, n) in first_sound(scenarios).items():
        t = targets[kind]
        lines.append(f"| {kind} | {n} | {p50:.0f} | {t['median']} | {p90:.0f} | {t['p90']} |")
    limits = config["audio_rig"]["live_checks"]["max_llm_requests"]
    lines += ["", "Model requests per turn:", "", "| Turns | Count | Median | Most | Limit |",
              "|---|---|---|---|---|"]
    for kind, (p50, most, n) in requests(scenarios).items():
        lines.append(f"| {kind} | {n} | {p50:.0f} | {most:.0f} | {limits[kind]} |")
    lines += ["", "Every audio check failure, by scenario:", ""]
    for s in scenarios:
        failed = audio_failures(s)
        if not failed:
            continue
        lines.append(f"- **{s.scenario_id}**")
        for name in NAMES:
            for detail in failed.get(name, []):
                lines.append(f"  - {name}: {detail}")
    return lines


def rescored(s: ScenarioResult, timeline, config: dict) -> ScenarioResult:
    """The result with its audio check failures replaced by today's checks on `timeline`."""
    kept = record_failures(s)
    fresh = live_checks.run(timeline, config)
    outcome = s.outcome
    if outcome != "error":
        outcome = "fail" if kept or fresh else "pass"
    return s.model_copy(update={"check_failures": kept + fresh, "outcome": outcome})


def rescore(batch: Path, config: dict) -> Path:
    manifest = json.loads((batch / MANIFEST).read_text())
    runs = manifest.get("results") or []
    runs = [runs] if isinstance(runs, str) else runs
    if not runs:
        raise SystemExit(f"{batch} has no results file to rescore")
    timelines = {}
    for call in manifest["calls"]:
        data = json.loads((batch / call["transcript"]).read_text())
        if "timeline" in data:
            timelines[data["call_id"]] = from_dict(data["timeline"])
    out = None
    for name in runs:
        path = Path(name) if Path(name).is_absolute() else ROOT / name
        run = results_mod.load(path)
        missing = [s.scenario_id for s in run.scenarios if s.call_id not in timelines]
        if missing:
            raise SystemExit(f"no saved timeline for {', '.join(missing)}; rerun those calls")
        run = run.model_copy(update={"scenarios": [
            rescored(s, timelines[s.call_id], config) for s in run.scenarios]})
        out = path.with_name(f"{path.stem}-rescored.json")
        out.write_text(run.model_dump_json(indent=2) + "\n")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The live-like suite's table.")
    parser.add_argument("runs", nargs="*")
    parser.add_argument("--rescore", default="", help="a batch folder under data/sim")
    args = parser.parse_args(argv)
    if args.rescore:
        print(f"rescored: {rescore(Path(args.rescore), load_config())}")
        return 0
    by_id: dict[str, ScenarioResult] = {}
    for path in args.runs:
        for s in load(path).scenarios:
            by_id[s.scenario_id] = s
    if not by_id:
        print("no scenarios in those files", file=sys.stderr)
        return 1
    print("\n".join(report(list(by_id.values()), load_config())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
