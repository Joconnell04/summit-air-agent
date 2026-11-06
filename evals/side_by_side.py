"""The four-arm side-by-side (issue #169): scripted calls over the real line, scored three ways.

    uv run --env-file .env python -m evals.side_by_side judge <run_dir>     Opus scores each call
    uv run --env-file .env python -m evals.side_by_side report <run_dir>    checks, table, JSON

<run_dir> is a copy of what voice/retell/side_by_side/run_slate.sh wrote on the server
(manifest.jsonl and calls/<tag>.{log,rows.json}), with calls/<tag>.retell.json added by
voice/retell/side_by_side/collect.py. judge adds calls/<tag>.judge.json, and report writes
results.json beside them and prints the per-arm table. The phone and Retell pieces live in
voice/retell/side_by_side/, so nothing here imports a voice layer.

The gross-error checks read Retell's transcript_with_tool_calls and the tool server's rows for the
call. They are deliberately literal (a regex or a count), so a reader can see why a call failed.
"""

import argparse
import json
import re
import statistics
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PERSONAS = ROOT / "evals" / "side-by-side" / "personas.yaml"
FEEDBACK = ROOT / "evals" / "rounds" / "retell-live-calls-01.md"
JUDGE_MODEL = "claude-opus-5-5"

CLOSING = "thanks so much for calling summit air today. have a great day"
OPEN_DOOR = "if anything makes this more urgent for you, just let me know"

# The agent describing its own steps, from Jackson's feedback in retell-live-calls-01.md and the
# change sets that answered it ("let me look that up", "Next,", "so I'll move on", "the caller").
NARRATION = [
    r"\blet me (check|look|see|pull|book|read|find|get|confirm|have|note|put|move)\b",
    r"\bi'?ll (now |go ahead|move on|check|look|confirm|book|read|note)",
    r"\bnow i'?ll\b",
    r"\bmoving on\b",
    r"(^|[.!?]\s+)next,",
    r"\bthe caller\b",
    r"\b(reference|background) (text|material)\b",
    r"\bknowledge base\b",
    r"\bnot something i need\b",
    r"\bon this call\b",
]
CONFIRM = r"(is that (right|correct)|that'?s all correct|does that (sound right|look right)|did i get that right|right\?$|correct\?$)"
# A safety instruction to the caller. Counted per agent turn; the server's lines come once.
SAFETY = r"(nine one one|\b911\b|get (everyone )?out(side)?|leave the (house|home|building|apartment)|open (a |the )?windows?|breaker|move away from|step away from|don'?t (use|touch|flip|light))"
PRIOR_VISIT = r"\b(already have a visit|you have a visit|visit (booked|on file)|existing (visit|appointment))\b"
ASKED_SPELLING = r"\bspell\b"
ASKED_FULL_NAME = r"\b(first and last|full name|last name)\b"


def norm(text: str) -> str:
    text = text.lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def turns(transcript: list[dict]) -> list[dict]:
    """The call as alternating turns. An agent turn is everything the agent said between two
    caller turns, joined, with the tool calls made and the `say` lines the tools returned."""
    out: list[dict] = []
    calls_by_id: dict[str, str] = {}
    for item in transcript:
        role = item.get("role")
        if role == "user":
            out.append({"who": "caller", "text": item.get("content", "").strip()})
            continue
        if not out or out[-1]["who"] != "agent":
            out.append({"who": "agent", "text": "", "tools": [], "says": []})
        turn = out[-1]
        if role == "agent":
            turn["text"] += item.get("content", "")
        elif role == "tool_call_invocation":
            calls_by_id[item.get("tool_call_id", "")] = item.get("name", "")
            turn["tools"].append(
                {"name": item.get("name", ""), "arguments": item.get("arguments", "")}
            )
        elif role == "tool_call_result":
            try:
                body = json.loads(item.get("content") or "{}")
            except json.JSONDecodeError:
                body = {}
            say = body.get("say") if isinstance(body, dict) else None
            if say:
                turn["says"].append(
                    {"tool": calls_by_id.get(item.get("tool_call_id", ""), ""), "say": say}
                )
    for turn in out:
        turn["text"] = turn["text"].strip()
    return out


def agent_turns(ts: list[dict]) -> list[dict]:
    return [t for t in ts if t["who"] == "agent"]


def tool_order(ts: list[dict]) -> list[str]:
    return [tool["name"] for t in agent_turns(ts) for tool in t["tools"]]


def said_verbatim(say: str, spoken: str) -> bool:
    """Every sentence of the tool's `say` is in what the agent said, ignoring case and
    punctuation. Numbers the server writes as digits may be spoken as words by Retell's
    normalization, so a sentence with digits is matched on its words only."""
    spoken_n = norm(spoken)
    for sentence in re.split(r"(?<=[.!?])\s+", say.strip()):
        s = norm(sentence)
        if not s:
            continue
        if re.search(r"\d", s):
            words = [w for w in s.split() if not re.search(r"\d", w)]
            if all(w in spoken_n.split() for w in words):
                continue
            return False
        if s not in spoken_n:
            return False
    return True


def check_call(persona: dict, transcript: list[dict], rows: dict) -> dict:
    """Gross-error checks for one call. Each value is {"ok": bool, "why": str}; checks that do not
    apply to the persona are left out."""
    ts = turns(transcript)
    agents = agent_turns(ts)
    callers = [t for t in ts if t["who"] == "caller"]
    results: dict[str, dict] = {}

    def put(name: str, ok: bool, why: str = "") -> None:
        results[name] = {"ok": bool(ok), "why": "" if ok else why}

    two_q = [t["text"] for t in agents if t["text"].count("?") >= 2]
    put("one_question_per_turn", not two_q, "; ".join(two_q[:2]))

    first = persona.get("first") or ""
    named = [
        t["text"]
        for t in agents
        if first and re.search(rf"\b{re.escape(first)}\b", t["text"], re.IGNORECASE)
    ]
    put("no_caller_name", not named, "; ".join(named[:2]))

    narrated = []
    for t in agents:
        for pattern in NARRATION:
            m = re.search(pattern, t["text"], re.IGNORECASE)
            if m:
                narrated.append(m.group(0))
    put("no_narration", not narrated, ", ".join(narrated[:4]))

    missed = []
    for i, t in enumerate(ts):
        if t["who"] != "agent":
            continue
        for say in t["says"]:
            # Slot offers may add the weekday and date; the other tools' lines are said as written.
            if say["tool"] in ("end_call", "find_slots"):
                continue
            if not said_verbatim(say["say"], t["text"]):
                missed.append(f"{say['tool']}: {say['say'][:80]}")
    put("server_lines_verbatim", not missed, "; ".join(missed[:3]))

    closings = [t for t in agents if CLOSING in norm(t["text"])]
    put("closing_said_once", len(closings) <= 1, f"{len(closings)} closings")

    if any(OPEN_DOOR in norm(s["say"]) for t in agents for s in t["says"]):
        put(
            "open_door_verbatim",
            any(OPEN_DOOR in norm(t["text"]) for t in agents),
            "the open-door line came back from a tool but was not said as written",
        )

    order = tool_order(ts)
    if "find_slots" in order:
        rb = order.index("read_back") if "read_back" in order else None
        put(
            "read_back_before_slots",
            rb is not None and rb < order.index("find_slots"),
            "find_slots before any read_back"
            if rb is None or rb > order.index("find_slots")
            else "",
        )

    confirms = sum(
        1 for t in agents if re.search(CONFIRM, t["text"].strip(), re.IGNORECASE | re.MULTILINE)
    )
    allowed = persona.get("confirms", 1)
    put("no_double_confirm", confirms <= allowed, f"{confirms} confirmations, {allowed} earned")

    phrase = persona.get("no_thanks")
    if phrase:
        idx = next(
            (i for i, t in enumerate(ts) if t["who"] == "caller" and phrase in t["text"].lower()),
            None,
        )
        later = idx is not None and any(t["who"] == "caller" for t in ts[idx + 1 :])
        put(
            "no_hangup_on_no_thanks",
            idx is None or later,
            f"the call ended after the caller said '{phrase}'",
        )

    safety_turns = [
        i
        for i, t in enumerate(ts)
        if t["who"] == "agent" and re.search(SAFETY, t["text"], re.IGNORECASE)
    ]
    if persona.get("safety"):
        put(
            "safety_advice_once",
            len(safety_turns) <= 1,
            f"safety instructions in {len(safety_turns)} turns",
        )
    retraction = persona.get("retraction")
    if retraction:
        idx = next(
            (
                i
                for i, t in enumerate(ts)
                if t["who"] == "caller" and retraction in t["text"].lower().replace(" ", "")
            ),
            None,
        )
        after = [i for i in safety_turns if idx is not None and i > idx]
        put(
            "retraction_honored",
            idx is not None and not after,
            "the caller's retraction was not heard"
            if idx is None
            else f"safety lines after the retraction ({len(after)})",
        )

    prior = [t["text"] for t in agents if re.search(PRIOR_VISIT, t["text"], re.IGNORECASE)]
    calls = rows.get("calls", [])
    linked = [c["call"]["id"] for c in calls if c["call"].get("linked_call_id")]
    caller_text = norm(" ".join(t["text"] for t in callers))
    foreign = []
    for c in calls:
        for b in c.get("bookings", []):
            for quote in re.findall(r'caller said "([^"]+)"', b.get("priority_reasons") or ""):
                if norm(quote) and norm(quote)[:40] not in caller_text:
                    foreign.append(quote[:60])
    carried = [] if persona.get("prior_visit") else prior
    put(
        "nothing_carried_over",
        not carried and not foreign and (persona.get("prior_visit") or not linked),
        "; ".join(carried[:1] + foreign[:1] + [f"linked {x}" for x in linked[:1]]),
    )

    ours = calls[-1] if calls else {}
    kinds = sorted(
        b["kind"]
        for b in ours.get("bookings", [])
        if b.get("status") in ("booked", "done", "queued")
    )
    want = sorted(persona.get("bookings") or [])
    put(
        "records_bookings",
        all(k in kinds for k in want) and (want or not kinds),
        f"bookings {kinds}, expected {want}",
    )
    if persona.get("callback"):
        put("records_callback", bool(ours.get("callbacks")), "no callback row")
    if persona.get("lead"):
        put("records_lead", bool(ours.get("leads")), "no lead row")
    if persona.get("flag"):
        put("records_flag", bool(ours.get("flags")), "no flag row")
    if persona.get("dnc_undo"):
        put(
            "records_dnc_undone",
            not rows.get("do_not_call"),
            "the number is still on the do-not-call list",
        )
    if not calls:
        put("records_call_seen", False, "no tool server call in the window")

    if persona.get("name"):
        stored = [r["name"] for r in ours.get("booking_names", [])] or [
            r["name"] for r in ours.get("call_names", [])
        ]
        exact = any(norm(n) == norm(persona["name"]) for n in stored)
        put(
            "name_spelled_right", exact, f"stored {stored or 'nothing'}, intended {persona['name']}"
        )
        results["name_asked"] = {
            "ok": True,
            "spelling": any(re.search(ASKED_SPELLING, t["text"], re.IGNORECASE) for t in agents),
            "full_name": any(re.search(ASKED_FULL_NAME, t["text"], re.IGNORECASE) for t in agents),
            "stored": stored,
        }
    return results


def latency(call: dict) -> dict:
    lat = call.get("latency") or {}
    cost = call.get("call_cost") or {}
    return {
        "e2e_p50_ms": (lat.get("e2e") or {}).get("p50"),
        "e2e_p90_ms": (lat.get("e2e") or {}).get("p90"),
        "llm_p50_ms": (lat.get("llm") or {}).get("p50"),
        "cost_usd": round((cost.get("combined_cost") or 0) / 100, 4),
        "duration_s": round((call.get("duration_ms") or 0) / 1000),
        "disconnection": call.get("disconnection_reason"),
    }


# ---- the Opus judge ----------------------------------------------------------------------------

JUDGE_SYSTEM = """You judge phone calls to Summit Air, a residential HVAC company in New York, answered
by a virtual assistant named Brooke. The caller is a scripted test caller. The owner, Jackson, has
given feedback on earlier calls; it is below, word for word, and it is the standard. Score how close
this call comes to what Jackson wants: warm, confident and natural, one question at a time, no
narration of its own steps, no saying the caller's name, safety handled once and calmly, the job
done (booked, moved, callback or lead as the caller asked), and a clean ending.

Score 1 to 10: 10 is a call Jackson would have no notes on; 7 is good with small notes; 5 has one
serious problem a caller would notice; 3 or lower would embarrass the company. Judge only what the
caller hears and what got done; do not reward length.

Jackson's feedback on earlier calls:

{feedback}"""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "vibe": {"type": "string", "description": "one line on warmth, confidence, naturalness"},
        "worst": {"type": "string", "description": "the single worst moment, quoted, or 'none'"},
    },
    "required": ["score", "vibe", "worst"],
    "additionalProperties": False,
}


def transcript_text(transcript: list[dict]) -> str:
    lines = []
    for t in turns(transcript):
        who = "Caller" if t["who"] == "caller" else "Brooke"
        if t["text"]:
            lines.append(f"{who}: {t['text']}")
    return "\n".join(lines)


def judge_call(client, transcript: list[dict], persona_text: str) -> dict:
    feedback = FEEDBACK.read_text()
    response = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=4000,
        system=JUDGE_SYSTEM.format(feedback=feedback),
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": JUDGE_SCHEMA},
        },
        messages=[
            {
                "role": "user",
                "content": f"The test caller's script:\n\n{persona_text}\n\nThe call:\n\n{transcript_text(transcript)}",
            }
        ],
    )
    text = next(b.text for b in response.content if b.type == "text")
    out = json.loads(text)
    out["usage"] = {"input": response.usage.input_tokens, "output": response.usage.output_tokens}
    return out


def judge(run: Path) -> None:
    import anthropic

    client = anthropic.Anthropic()
    for entry in manifest(run):
        src = run / entry["log"].replace(".log", ".retell.json")
        out = run / entry["log"].replace(".log", ".judge.json")
        if out.exists() or not src.exists():
            continue
        call = json.loads(src.read_text())
        persona_text = (
            ROOT / "evals" / "side-by-side" / "personas" / f"{entry['persona']}.txt"
        ).read_text()
        result = judge_call(client, call.get("transcript_with_tool_calls") or [], persona_text)
        out.write_text(json.dumps(result, indent=1))
        print(f"{entry['log']}: {result['score']} {result['vibe']}")


# ---- report ------------------------------------------------------------------------------------


def manifest(run: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (run / "manifest.jsonl").read_text().splitlines()
        if line.strip()
    ]


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def report(run: Path) -> dict:
    personas = yaml.safe_load(PERSONAS.read_text())["personas"]
    calls = []
    for entry in manifest(run):
        retell = load_json(run / entry["log"].replace(".log", ".retell.json"))
        rows = load_json(run / entry["rows"])
        verdict = load_json(run / entry["log"].replace(".log", ".judge.json"))
        transcript = retell.get("transcript_with_tool_calls") or []
        checks = check_call(personas[entry["persona"]], transcript, rows) if retell else {}
        calls.append(
            {
                **{k: entry[k] for k in ("n", "arm", "persona", "agent_id")},
                "call_id": retell.get("call_id"),
                "checks": checks,
                "fails": sorted(k for k, v in checks.items() if not v["ok"]),
                "judge": {k: verdict.get(k) for k in ("score", "vibe", "worst")}
                if verdict
                else None,
                **(latency(retell) if retell else {}),
            }
        )
    arms = {}
    for arm in sorted({c["arm"] for c in calls}):
        mine = [c for c in calls if c["arm"] == arm and c["call_id"]]
        graded = [c for c in mine if c["checks"]]
        n_checks = sum(len([k for k in c["checks"] if k != "name_asked"]) for c in graded)
        n_fails = sum(len(c["fails"]) for c in graded)
        e2e = [c["e2e_p50_ms"] for c in mine if c.get("e2e_p50_ms")]
        e2e90 = [c["e2e_p90_ms"] for c in mine if c.get("e2e_p90_ms")]
        llm = [c["llm_p50_ms"] for c in mine if c.get("llm_p50_ms")]
        scores = [c["judge"]["score"] for c in mine if c.get("judge") and c["judge"].get("score")]
        names = [c for c in mine if "name_spelled_right" in c["checks"]]
        arms[arm] = {
            "calls": len(mine),
            "clean_calls": sum(1 for c in graded if not c["fails"]),
            "checks_failed": n_fails,
            "checks_run": n_checks,
            "judge_mean": round(statistics.mean(scores), 2) if scores else None,
            "judge_min": min(scores) if scores else None,
            "e2e_p50_ms": round(statistics.median(e2e)) if e2e else None,
            "e2e_p90_ms": round(statistics.median(e2e90)) if e2e90 else None,
            "llm_p50_ms": round(statistics.median(llm)) if llm else None,
            "cost_usd": round(sum(c.get("cost_usd") or 0 for c in mine), 2),
            "cost_per_min_usd": round(
                sum(c.get("cost_usd") or 0 for c in mine)
                / max(1, sum(c.get("duration_s") or 0 for c in mine))
                * 60,
                4,
            ),
            "names_right": f"{sum(1 for c in names if c['checks']['name_spelled_right']['ok'])}/{len(names)}",
            "fails_by_check": dict(
                sorted(
                    {
                        k: sum(1 for c in graded if k in c["fails"])
                        for c in graded
                        for k in c["fails"]
                    }.items()
                )
            ),
        }
    result = {"arms": arms, "calls": calls}
    (run / "results.json").write_text(json.dumps(result, indent=1))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["judge", "report"])
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    if args.action == "judge":
        judge(args.run)
    else:
        print(json.dumps(report(args.run)["arms"], indent=1))


if __name__ == "__main__":
    main()
