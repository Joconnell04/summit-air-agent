"""The bake-off table across stacks, from audio-rig results files, and the decision 026 verdict.

    uv run python -m evals.audio_rig.report evals/results/<run>.json [...] [--ours livekit-rig]

Per stack: plain and lookup reply delay (median and slowest 10%), must-pass results and whether
the safety lines were word for word, and cost per call. "Word for word" is checked three ways:
the tool server's own check on the platform's reported text (the `safety_lines` check), that text
against the signed-off lines, and what the caller heard (the rig's transcription) against them,
which allows for transcription noise.
"""

import argparse
import difflib
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field

from evals import suites
from evals.audio_rig.rig import STACK_PARTS
from evals.checks import safety_config
from evals.compare import percentile
from evals.results import Run, ScenarioResult, load
from evals.scenario import load as load_scenario
from evals.scenario import load_all, scenario_path

# Rule from DECISIONS.md 026: a newcomer must beat our plain-turn median by this much, on six
# safety calls (two gas or CO scenarios, three repeats each) and REPEATS runs of every must-pass
# scenario.
MARGIN_MS = 300
SAFETY_CALLS = 6
REPEATS = 3
# Share of the signed-off words the caller must hear, in order, to count as heard word for word.
# Below 1.0 only for the rig's own transcription errors (ASSUMED).
HEARD_SHARE = 0.9
# A reply that starts within one 20 ms frame of the caller's end was already playing when the
# caller finished: the agent talked over them, and the rig can only date it to that frame.
TALK_OVER_MS = 20


def gates_held(s: ScenarioResult) -> bool:
    """Every code check passed. The grader's notes are scored apart: on the rig it reads the
    caller's transcription of the agent, so a mishearing can look like an agent's slip."""
    return s.outcome != "error" and not [f for f in s.check_failures if not f.startswith("grader:")]


def must_pass(scenario_id: str) -> bool:
    return load_scenario(scenario_path(scenario_id)).must_pass


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower().replace("nine one one", "911"))


def heard_share(lines: str, heard: str) -> float:
    """Share of the lines' words that appear, in order, in what was heard."""
    want, got = words(lines), words(heard)
    if not want:
        return 0.0
    matcher = difflib.SequenceMatcher(None, want, got, autojunk=False)
    return sum(b.size for b in matcher.get_matching_blocks()) / len(want)


def contains(lines: str, text: str) -> bool:
    want, got = " ".join(words(lines)), " ".join(words(text))
    return bool(want) and want in got


@dataclass
class Safety:
    """One must-pass safety call."""

    key: str
    outcome: str
    server_ok: bool
    said_exact: bool | None
    heard: float


@dataclass
class Stack:
    name: str
    runs: list[Run] = field(default_factory=list)

    @property
    def scenarios(self) -> list[ScenarioResult]:
        return [s for r in self.runs for s in r.scenarios]

    def delays(self, kind: str) -> list[float]:
        """Reply delays of one kind. A reply that was already playing when the caller finished
        (recorded as zero, or within a frame of it) talked over the caller; it is counted by
        `talk_overs`, not as a fast reply."""
        return [
            t.reply_delay_ms
            for s in self.scenarios
            if s.outcome != "error"
            for t in s.turn_timings
            if t.kind == kind and t.reply_delay_ms >= TALK_OVER_MS
        ]

    def talk_overs(self) -> int:
        return sum(
            1
            for s in self.scenarios
            if s.outcome != "error"
            for t in s.turn_timings
            if t.reply_delay_ms < TALK_OVER_MS
        )

    def cut_ins(self) -> int:
        """Turns where the agent made a sound before the caller had finished their line. On our
        stack, which never backchannels, each is the agent starting a reply in a pause mid-line."""
        return sum(
            1
            for s in self.scenarios
            if s.outcome != "error"
            for t in s.turn_timings
            if t.over_caller_ms is not None
        )

    def silent(self) -> int:
        return sum(s.silent_turns for s in self.scenarios)

    def platform_delays(self) -> list[float]:
        return [
            t.platform_reply_ms
            for s in self.scenarios
            for t in s.turn_timings
            if t.platform_reply_ms is not None and t.kind == "plain"
        ]

    def cost_per_call(self) -> tuple[float, float]:
        """Mean stack cost and mean all-in cost (with the rig's own parts) per call."""
        calls = self.scenarios
        if not calls:
            return 0.0, 0.0
        stack = sum(sum(s.cost_parts.get(p, 0) for p in STACK_PARTS) for s in calls) / len(calls)
        return stack, sum(s.cost_usd for s in calls) / len(calls)

    def minutes_per_call(self) -> float:
        calls = [s for s in self.scenarios if s.duration_ms]
        return sum(s.duration_ms for s in calls) / len(calls) / 60000 if calls else 0.0

    def safety(self, signed_off: dict) -> list[Safety]:
        out = []
        for s in self.scenarios:
            if "safety" not in s.flow or not must_pass(s.scenario_id):
                continue
            agent = [t for t in s.transcript if t.role == "agent"]
            heard = " ".join(t.text for t in agent)
            said = " ".join(t.said for t in agent if t.said)
            best = max((heard_share(lines, heard) for lines in signed_off.values()), default=0.0)
            exact = any(contains(lines, said) for lines in signed_off.values()) if said else None
            server_ok = not any(f.startswith("safety_lines") for f in s.check_failures)
            out.append(Safety(s.key, s.outcome, server_ok and s.outcome != "error", exact, best))
        return out


def lines_by_kind() -> dict[str, str]:
    return {kind: " ".join(v["lines"].split()) for kind, v in safety_config()["kinds"].items()}


def ms(values: list[float], share: float) -> str:
    return f"{percentile(values, share) / 1000:.2f} s" if values else "n/a"


def table(stacks: list[Stack]) -> list[str]:
    signed_off = lines_by_kind()
    header = (
        "| Stack | Calls | Plain median | Plain slowest 10% | Lookup median "
        "| Lookup slowest 10% | Talked over / sound over caller / silent | Must-pass checks held | Grader passed "
        "| Safety lines word for word | Stack cost per call | All-in per call |"
    )
    rows = [header, "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for st in stacks:
        plain, lookup = st.delays("plain"), st.delays("lookup")
        safety = st.safety(signed_off)
        must = [s for s in st.scenarios if must_pass(s.scenario_id)]
        held = sum(1 for s in must if gates_held(s))
        graded = [s for s in st.scenarios if gates_held(s)]
        liked = sum(1 for s in graded if s.outcome == "pass")
        exact = sum(1 for s in safety if s.server_ok and s.heard >= HEARD_SHARE)
        stack_cost, all_in = st.cost_per_call()
        rows.append(
            f"| {st.name} | {len(st.scenarios)} | {ms(plain, 0.5)} ({len(plain)}) | {ms(plain, 0.9)} "
            f"| {ms(lookup, 0.5)} ({len(lookup)}) | {ms(lookup, 0.9)} "
            f"| {st.talk_overs()} / {st.cut_ins()} / {st.silent()} | {held} of {len(must)} | {liked} of {len(graded)} | {exact} of {len(safety)} "
            f"| ${stack_cost:.3f} | ${all_in:.3f} |"
        )
    return rows


def safety_detail(stacks: list[Stack]) -> list[str]:
    signed_off = lines_by_kind()
    rows = [
        "| Stack | Call | Outcome | Server check | Platform text exact | Heard |",
        "|---|---|---|---|---|---|",
    ]
    for st in stacks:
        for s in st.safety(signed_off):
            exact = "n/a" if s.said_exact is None else ("yes" if s.said_exact else "no")
            rows.append(
                f"| {st.name} | {s.key} | {s.outcome} | {'pass' if s.server_ok else 'fail'} "
                f"| {exact} | {s.heard:.0%} |"
            )
    return rows


def expected_must_pass(st: "Stack") -> set[str] | None:
    """The must-pass scenarios the run's suite holds, or None when it can't be told (a custom
    filter), in which case no stack from it can be chosen."""
    names = {r.suite for r in st.runs}
    if len(names) != 1 or "custom" in names:
        return None
    try:
        chosen, _ = suites.select(load_all(), names.pop())
    except suites.SuiteError:
        return None
    return {s.id for s in chosen if s.must_pass}


def missing(st: "Stack", safety: list) -> list[str]:
    """What a stack's runs lack for a verdict that can pick it: six scored safety calls, and every
    must-pass scenario of its suite scored REPEATS times. A run stopped by --max-cost lacks some."""
    gaps = []
    scored = [s for s in safety if s.outcome != "error"]
    if len(scored) < SAFETY_CALLS:
        gaps.append(f"{len(scored)} of {SAFETY_CALLS} safety calls scored")
    expected = expected_must_pass(st)
    if expected is None:
        return gaps + ["not a named suite, so the must-pass set is unknown"]
    counts = defaultdict(int)
    for s in st.scenarios:
        if s.outcome != "error":
            counts[s.scenario_id] += 1
    short = sorted(i for i in expected if counts[i] < REPEATS)
    if short:
        gaps.append(f"under {REPEATS} scored runs of {', '.join(short)}")
    return gaps


def verdict(stacks: list[Stack], ours: str) -> list[str]:
    """DECISIONS.md 026: plain median at least 0.3 s lower than ours, and every must-pass
    scenario passing with the safety lines word for word on three repeats, or it is a tie and our
    stack stays. A stack whose runs don't cover all of that is a tie too, however it did."""
    base = next((s for s in stacks if s.name == ours), None)
    if base is None or not base.delays("plain"):
        return [f"no run for {ours}; no verdict"]
    ours_ms = percentile(base.delays("plain"), 0.5)
    signed_off = lines_by_kind()
    out = []
    for st in stacks:
        if st.name == ours:
            continue
        plain = st.delays("plain")
        faster = bool(plain) and ours_ms - percentile(plain, 0.5) >= MARGIN_MS
        safety = st.safety(signed_off)
        clean = bool(safety) and all(s.server_ok and s.heard >= HEARD_SHARE for s in safety)
        must = [s for s in st.scenarios if must_pass(s.scenario_id)]
        held = all(gates_held(s) for s in must)
        gaps = missing(st, safety)
        result = "chosen" if faster and clean and held and not gaps else "tie, keep ours"
        gap = (ours_ms - percentile(plain, 0.5)) / 1000 if plain else 0
        pace = f"{abs(gap):.2f} s {'faster' if gap >= 0 else 'slower'} than ours"
        line = (
            f"- {st.name}: {result} (plain median {pace}; "
            f"safety lines {'clean' if clean else 'not clean'} on {len(safety)} calls; "
            f"must-pass checks {'all held' if held else 'not all held'})"
        )
        if gaps:
            line += f"; incomplete: {'; '.join(gaps)}"
        out.append(line)
    return out


def stacks_from(paths: list[str]) -> list[Stack]:
    by_name: dict[str, Stack] = defaultdict(lambda: Stack(""))
    for path in paths:
        run = load(path)
        by_name[run.stack].name = run.stack
        by_name[run.stack].runs.append(run)
    return list(by_name.values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The audio bake-off table.")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--ours", default="livekit-rig")
    args = parser.parse_args(argv)
    stacks = stacks_from(args.paths)
    print(
        "\n".join(table(stacks) + [""] + safety_detail(stacks) + [""] + verdict(stacks, args.ours))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
