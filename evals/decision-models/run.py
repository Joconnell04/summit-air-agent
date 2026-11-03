"""The quick side-model run (PLAN.md section 6): one question, the labelled lines, each system
asked once per line, scored against the phrase rules the server uses today.

Usage: PYTHONPATH=. python evals/decision-models/run.py [--question vulnerable_person]
           [--systems rules,haiku,openai-decisions,jev] [--threshold 0.5] [--max-usd 0.50]

`rules` is today's server: the tenant's priority phrase rules about people at home, read offline.
Every other name is a profile under `side` in config/models.yaml, called through app/side_model.py,
the same code the tool server uses. Lines are asked one at a time so latency is per request, and a
line that fails or takes over 2 s counts as a "no" (the server falls back to the rules). Lines
tagged `borderline` are answered and printed but left out of the scores.
"""

import argparse
import json
import math
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from app import priority, side_model
from app.tenant import Tenants

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
# The priority rules that say someone at home is at risk (trades/hvac/agent.yaml). Temperature
# brackets, improvised heating, pipes and safety rules are about the house, not the people.
PERSON_RULES = {
    "vulnerable_person", "medical_equipment", "bedbound", "symptoms_now", "young_child",
    "parent_no_age", "limited_mobility", "chronic_illness", "cognitive", "pregnancy", "recovering",
}
# Tokens per line, for the estimate printed before anything is paid for (ASSUMED from the prompt).
EST_TOKENS = (400, 15)


def load_lines(question: str) -> list[dict]:
    path = HERE / "lines" / f"{question}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def rules_system():
    tenant = Tenants.load(ROOT / "tenants", "", ROOT / "trades").by_slug["summit-air"]
    rules = [r for r in tenant.priority["rules"] if r["id"] in PERSON_RULES]

    def ask(text: str) -> float:
        facts = priority.Facts("no_heat", text)
        return 1.0 if any(priority._rule_fires(rule, facts) for rule in rules) else 0.0

    return ask


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = hits / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def mcnemar(b: int, c: int) -> float:
    """Exact two-sided McNemar p from the two discordant counts."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(min(b, c) + 1)) / 2**n
    return min(1.0, 2 * tail)


def percentile(values: list[float], share: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def scores(answers: list[dict], lines: list[dict], threshold: float) -> dict:
    """Precision and recall on the scored lines, alone and joined to the rules with OR (how the
    server would use it: the phrase rules stay, the side model can only add a yes)."""
    by_id = {line["id"]: line for line in lines}
    scored = [a for a in answers if "borderline" not in by_id[a["id"]]["tags"]]

    def counts(says):
        tp = sum(1 for a in scored if says(a) and by_id[a["id"]]["label"])
        fp = sum(1 for a in scored if says(a) and not by_id[a["id"]]["label"])
        fn = sum(1 for a in scored if not says(a) and by_id[a["id"]]["label"])
        positives = tp + fn
        return {
            "tp": tp, "fp": fp, "fn": fn,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / positives, 3) if positives else None,
            "recall_95": [round(x, 3) for x in wilson(tp, positives)],
        }

    alone = counts(lambda a: a["p"] is not None and a["p"] >= threshold)
    joined = counts(lambda a: a["rules"] >= 0.5 or (a["p"] is not None and a["p"] >= threshold))
    # Against the rules alone: a fix is a line the join gets right that the rules get wrong.
    fixes = [a["id"] for a in scored if by_id[a["id"]]["label"] and a["rules"] < 0.5
             and a["p"] is not None and a["p"] >= threshold]
    breaks = [a["id"] for a in scored if not by_id[a["id"]]["label"] and a["rules"] < 0.5
              and a["p"] is not None and a["p"] >= threshold]
    latencies = [a["ms"] for a in answers if a["status"] == "ok"]
    return {
        "alone": alone,
        "with_rules": joined,
        "fixes": fixes,
        "new_alarms": breaks,
        "mcnemar_p": round(mcnemar(len(fixes), len(breaks)), 4),
        "ms_p50": round(percentile(latencies, 0.5)),
        "ms_p95": round(percentile(latencies, 0.95)),
        "ms_max": round(max(latencies)) if latencies else 0,
        "failed": sum(a["status"] != "ok" for a in answers),
    }


def ask_all(name: str, ask, lines: list[dict], rules) -> list[dict]:
    answers = []
    for line in lines:
        started = time.perf_counter()
        try:
            p, status = ask(line["text"]), "ok"
        except Exception as exc:  # noqa: BLE001 - a failed answer is scored as the fallback
            p, status = None, f"error: {type(exc).__name__}"
        ms = (time.perf_counter() - started) * 1000
        answers.append({"id": line["id"], "p": p, "status": status, "ms": round(ms, 1),
                        "rules": rules(line["text"])})
    return answers


def summary_md(run: dict, lines: list[dict]) -> str:
    by_id = {line["id"]: line for line in lines}
    out = [
        f"# Side-model quick run {run['run_id']}",
        "",
        (f"Question `{run['question']}`, {len(lines)} lines "
        f"({sum(1 for x in lines if 'borderline' not in x['tags'])} scored, the rest borderline), "
        f"threshold {run['threshold']}, cost ${run['cost_usd']:.4f}. All numbers MEASURED by this "
        "run (evals/decision-models/run.py)."),
        "",
        ("| System | Recall alone | Precision alone | Recall with rules | Precision with rules "
        "| Fixes | New alarms | McNemar p | p50 ms | p95 ms | Max ms | Failed |"),
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, s in run["systems"].items():
        a, j = s["scores"]["alone"], s["scores"]["with_rules"]
        sc = s["scores"]
        out.append(
            f"| {name} | {a['recall']} {a['recall_95']} | {a['precision']} | {j['recall']} | "
            f"{j['precision']} | {len(sc['fixes'])} | {len(sc['new_alarms'])} | {sc['mcnemar_p']} | "
            f"{sc['ms_p50']} | {sc['ms_p95']} | {sc['ms_max']} | {sc['failed']} |"
        )
    for name, s in run["systems"].items():
        misses = [a for a in s["answers"] if by_id[a["id"]]["label"]
                  and not (a["p"] is not None and a["p"] >= run["threshold"])]
        alarms = [a for a in s["answers"] if not by_id[a["id"]]["label"]
                  and a["p"] is not None and a["p"] >= run["threshold"]]
        out += ["", f"## {name}", ""]
        for kind, rows in (("Missed", misses), ("False alarm", alarms)):
            for a in rows:
                tags = ",".join(by_id[a["id"]]["tags"])
                out.append(f"- {kind} {a['id']} ({tags}, p={a['p']}): {by_id[a['id']]['text']}")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Quick side-model run on one question.")
    parser.add_argument("--question", default="vulnerable_person")
    parser.add_argument("--systems", default="rules,haiku,openai-decisions,jev")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--max-usd", type=float, default=0.50)
    args = parser.parse_args(argv)

    lines = load_lines(args.question)
    question = side_model.load_question(args.question)
    rules = rules_system()
    names = [n.strip() for n in args.systems.split(",") if n.strip()]
    profiles = {n: side_model.load_profile(n) for n in names if n != "rules"}
    estimate = sum(
        len(lines) * (EST_TOKENS[0] * p["price"]["input"] + EST_TOKENS[1] * p["price"]["output"])
        / 1_000_000
        for p in profiles.values()
    )
    print(f"{len(lines)} lines x {len(profiles)} paid system(s), estimate ${estimate:.4f}")
    if estimate > args.max_usd:
        print(f"over --max-usd {args.max_usd:.2f}; stopping", file=sys.stderr)
        return 2

    now = datetime.now(UTC)
    run = {"run_id": f"{now:%Y%m%d-%H%M%S}-{args.question}", "question": args.question,
           "threshold": args.threshold, "systems": {}, "cost_usd": 0.0}
    for name in names:
        if name == "rules":
            ask = rules
        else:
            ask = side_model.Asker(profiles[name], question)
        answers = ask_all(name, ask, lines, rules)
        cost = ask.cost_usd() if name != "rules" else 0.0
        run["cost_usd"] += cost
        run["systems"][name] = {
            "model": profiles[name]["model"] if name != "rules" else "phrase rules",
            "cost_usd": round(cost, 5),
            "scores": scores(answers, lines, args.threshold),
            "answers": answers,
        }
        s = run["systems"][name]["scores"]
        print(f"{name}: recall {s['alone']['recall']}, precision {s['alone']['precision']}, "
              f"with rules recall {s['with_rules']['recall']} precision "
              f"{s['with_rules']['precision']}, p50 {s['ms_p50']} ms, p95 {s['ms_p95']} ms, "
              f"failed {s['failed']}, ${cost:.4f}", flush=True)
        if run["cost_usd"] > args.max_usd:
            print(f"spent over --max-usd {args.max_usd:.2f}; stopping", file=sys.stderr)
            break
    out_dir = HERE / "results" / run["run_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(run, indent=1) + "\n")
    (out_dir / "summary.md").write_text(summary_md(run, lines))
    print(f"results: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
