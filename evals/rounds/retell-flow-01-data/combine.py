"""The flow's must-pass set (42 runs) and standard suite (124 runs) as one set each, from the runs that
scored (evals/rounds/retell-flow-01.md, "What ran"). Each scenario and sample comes from the first
run, unless that one errored or was scored with a fault a later run fixed, in which case it comes
from the later run.

    uv run python evals/rounds/retell-flow-01-data/combine.py
"""

import json
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[2] / "results"
MUST_PASS = "20261104-215403-retell_flow-fast-custom.json"
STANDARD = "20261104-222214-retell_flow-fast-standard.json"  # 91 of 124 errored: laptop DNS
RERUN = "20261105-000710-retell_flow-fast-custom.json"  # the 46 scenarios that errored
FIXED = "20261105-001542-retell_flow-fast-custom.json"  # adapter fixed
# Scored before the eval adapters reported who ended a chat (ended_by), or with the agent's last
# line lost on the server (oven-heat-co-check, sample 2): taken from FIXED.
FROM_FIXED = {
    "co-alarm-after-details",
    "co-alarm-clear",
    "gas-smell-in-passing",
    "oven-heat-co-check",
}


def load(name: str) -> dict[tuple[str, int], dict]:
    data = json.loads((RESULTS / name).read_text())
    return {(s["scenario_id"], s["sample"]): s for s in data["scenarios"]}


def combined(first_name: str) -> list[dict]:
    first, rerun, fixed = load(first_name), load(RERUN), load(FIXED)
    out = []
    for key, scenario in sorted(first.items()):
        if key[0] in FROM_FIXED and key in fixed:
            out.append(fixed[key])
        elif scenario["outcome"] == "error" and key in rerun:
            out.append(rerun[key])
        else:
            out.append(scenario)
    return out


if __name__ == "__main__":
    for label, name in (("must-pass", MUST_PASS), ("standard", STANDARD)):
        runs = combined(name)
        scored = [s for s in runs if s["outcome"] != "error"]
        passed = sum(s["outcome"] == "pass" for s in scored)
        print(
            f"{label}: {len(runs)} runs, {len(scored)} scored, {passed} passed, "
            f"{passed / len(scored):.1%}"
        )
        out = Path(__file__).with_name(f"{label}-combined.json")
        out.write_text(json.dumps({"scenarios": runs}, indent=1))
