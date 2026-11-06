"""Perceived wait and the hold phrase, from Retell's own record of each call (issue #176).

    python retell/arms/socket/waits.py <run_dir>... [--out waits.json]

Reads calls/<tag>.retell.json (voice/retell/side_by_side/collect.py) and, from the word timings in
`transcript_object`, measures for every caller turn the agent answered:

- the perceived wait: the caller's last word to the agent's first word, whatever it is (a hold
  phrase or the answer). Retell's backchannels are said while the caller talks and aren't in the
  transcript, so they never end a wait here;
- the hold phrase: whether the agent's reply opened with one of the tenant's hold phrases, and the
  wait to the real line after it;
- talk-over: the caller started speaking before the hold phrase finished, or within 300 ms of it,
  in which case Retell's transcript shows the caller's line right after a reply that is only the
  hold phrase.

Prints p50 and p90 of the perceived wait next to Retell's own e2e latency, and the hold-phrase rate.
"""

import argparse
import json
import statistics
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
TALK_OVER_S = 0.3


def hold_phrases() -> list[str]:
    voice = yaml.safe_load((ROOT / "tenants" / "summit-air" / "agent.yaml").read_text())["voice"]
    return [p.lower() for p in voice.get("hold_phrases") or []]


def turns(call: dict, phrases: list[str]) -> list[dict]:
    entries = [e for e in call.get("transcript_object") or [] if e.get("words")]
    out = []
    for i in range(1, len(entries)):
        before, after = entries[i - 1], entries[i]
        nxt = entries[i + 1] if i + 1 < len(entries) else None
        if before["role"] != "user" or after["role"] != "agent":
            continue
        last = before["words"][-1]["end"]
        first = after["words"][0]["start"]
        text = after["content"].strip().lower()
        phrase = next((p for p in phrases if text.startswith(p)), None)
        turn = {"wait_s": round(first - last, 3), "hold": bool(phrase), "talk_over": False}
        if phrase:
            n = len(phrase.split())
            words = after["words"]
            phrase_end = words[min(n, len(words)) - 1]["end"]
            if len(words) > n:
                turn["line_wait_s"] = round(words[n]["start"] - last, 3)
            only_phrase = len(words) <= n
            if nxt and nxt["role"] == "user" and only_phrase:
                turn["talk_over"] = nxt["words"][0]["start"] <= phrase_end + TALK_OVER_S
        out.append(turn)
    return out


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return round(values[min(len(values) - 1, int(q * len(values)))], 3)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+")
    parser.add_argument("--out")
    args = parser.parse_args()
    phrases = hold_phrases()
    calls, all_turns = [], []
    for run in args.runs:
        for path in sorted(Path(run).glob("calls/*.retell.json")):
            call = json.loads(path.read_text())
            found = turns(call, phrases)
            all_turns += found
            e2e = (call.get("latency") or {}).get("e2e") or {}
            calls.append({
                "call": path.name.removesuffix(".retell.json"), "call_id": call.get("call_id"),
                "turns": len(found), "holds": sum(t["hold"] for t in found),
                "talk_over": sum(t["talk_over"] for t in found),
                "perceived_p50_s": pct([t["wait_s"] for t in found], 0.5),
                "perceived_p90_s": pct([t["wait_s"] for t in found], 0.9),
                "e2e_p50_s": e2e.get("p50") and round(e2e["p50"] / 1000, 3),
                "e2e_p90_s": e2e.get("p90") and round(e2e["p90"] / 1000, 3),
            })
    waits = [t["wait_s"] for t in all_turns]
    holds = [t for t in all_turns if t["hold"]]
    summary = {
        "calls": len(calls), "turns": len(all_turns),
        "perceived_p50_s": pct(waits, 0.5), "perceived_p90_s": pct(waits, 0.9),
        "e2e_p50_s_median_of_calls": calls and statistics.median(
            c["e2e_p50_s"] for c in calls if c["e2e_p50_s"]),
        "e2e_p90_s_median_of_calls": calls and statistics.median(
            c["e2e_p90_s"] for c in calls if c["e2e_p90_s"]),
        "hold_phrases": len(holds), "hold_rate": round(len(holds) / len(all_turns), 3) if all_turns else 0,
        "talk_over": sum(t["talk_over"] for t in holds),
        "hold_wait_p50_s": pct([t["wait_s"] for t in holds], 0.5),
        "line_after_hold_p50_s": pct([t["line_wait_s"] for t in holds if "line_wait_s" in t], 0.5),
    }
    print(json.dumps(summary, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": summary, "calls": calls}, indent=1))


if __name__ == "__main__":
    main()
