"""The four-arm side-by-side (issue #169): scripted calls over the real line, scored three ways.

    uv run --env-file .env python -m evals.side_by_side judge <run_dir>...   Opus scores each call
    uv run python -m evals.side_by_side report <run_dir>... [--out results.json]

<run_dir> is a copy of what voice/retell/side_by_side/run_slate.sh wrote on the server
(manifest.jsonl and calls/<tag>.{log,rows.json}), with calls/<tag>.retell.json added by
voice/retell/side_by_side/collect.py. judge adds calls/<tag>.judge.json, and report scores the runs
in order (a rerun replaces the earlier call for its arm and persona) and prints the per-arm table. The phone and Retell pieces live in
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
    r"\b(placeholder|by mistake|lookup|look-up|redo)\b",
    r"\blooking at (the|that|your)\b",
    r"\bthe system\b",
]
# The agent's own safety questions, which Jackson ruled out ("it shouldn't ask 'are you outside
# the house now'", retell-live-calls-01.md). The server's lines are the only safety words.
OWN_SAFETY_QUESTION = r"\b(are you|is everyone|are you all|everyone) (out|outside)\b[^.?!]*\?"
# The bridge's own gas-check questions. It injects them, so they count against the server.
SERVER_CHECK = r"(rotten eggs|smelling gas in the (house|home))"
# The scripted caller leaving its role; the rest of that call is not a fair test of the agent.
ROLE_BREAK = (
    r"(caller role|scripted caller|step(ping)? out of|this is a test|i'?m an ai|role-?play"
    # The caller reading its own script aloud (C-i: "Visit questions: yes, yes, no pets ... Keep
    # it short and casual.").
    r"|visit questions|keep it short|one thing per turn|\bfits\.)"
)
CONFIRM = r"(just to confirm|is that (right|correct)|that'?s all correct|does that (sound right|look right)|did i get that right|right\?$|correct\?$)"
# A safety instruction to the caller (a mention such as "thanks for switching it off" is not one).
SAFETY = r"(call (nine one one|9-?1-?1|911|the gas company)|get (everyone )?out(side)?\b|leave the (house|home|building|apartment)|open (a |the )?windows?|turn (it|the unit|that|the furnace) off|switch (it|that) off|step away from|move away from|don'?t (use|touch|flip|light))"
PRIOR_VISIT = r"\b(already have a visit|you have a visit|visit (booked|on file)|existing (visit|appointment))\b"
ASKED_SPELLING = r"\bspell\b"
ASKED_FULL_NAME = r"\b(first and last|full name|last name)\b"


def weekday_in_new_york(stamp: str) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    at = datetime.fromisoformat(stamp)
    return at.astimezone(ZoneInfo("America/New_York")).strftime("%A").lower()


def norm(text: str) -> str:
    text = text.lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


TAG_QUESTION = r",?\s*\b(right|correct|okay|yeah)\?"
CLARIFIER = r"\blike an? [^?.!]*\?"
QUESTION_WORD = (
    r"(is|are|do|does|did|what'?s?|where'?s?|when|which|who|how|can|could|will|would|should)"
)
JOINED_QUESTION = rf",? and (?={QUESTION_WORD}\b)"


def questions(text: str) -> int:
    """Questions asked in a turn. A tag ("..., right?") is folded into the question it tags, and a clarifier that only gives examples
    ("Like a home or an apartment?") belong to the question before them, so they are not counted."""
    text = re.sub(TAG_QUESTION, "?", text, flags=re.IGNORECASE)
    text = re.sub(CLARIFIER, "", text, flags=re.IGNORECASE)
    # "Is it a business, and is that where you'd like the technician?" is two questions; "Got it,
    # and what street?" is one, since the clause before "and" is not a question.
    joined = 0
    for q in re.findall(r"[^.?!]*\?", text):
        parts = re.split(JOINED_QUESTION, q, flags=re.IGNORECASE)
        first = re.findall(r"[a-z']+", parts[0].lower())
        if len(parts) > 1 and first and re.fullmatch(QUESTION_WORD, first[0]):
            joined += 1
    return text.count("?") + joined


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
        elif role == "injected":
            # The bridge's say_now ("Say this to the caller now, ...: <line>") is a server line to
            # speak; its other injected notes are instructions to the model and are not spoken.
            content = item.get("content", "")
            if content.startswith("Say this to the caller"):
                turn["says"].append({"tool": "injected", "say": content.split(": ", 1)[-1].strip()})
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


ANYTHING_ELSE = r"\banything else\b"
# A short "no" to "anything else?": the whole line, after norm(), and nothing more.
DECLINE = r"^((no|nope|nah)( (thanks|thank you|that's it|that's all|i'm good|nothing else|bye))*|that's (it|all)( thanks)?)$"


def check_call(
    persona: dict, transcript: list[dict], rows: dict, disconnection: str | None = None
) -> dict:
    """Gross-error checks for one call. Each value is {"ok": bool, "why": str}; checks that do not
    apply to the persona are left out."""
    ts = turns(transcript)
    agents = agent_turns(ts)
    callers = [t for t in ts if t["who"] == "caller"]
    results: dict[str, dict] = {}

    def put(name: str, ok: bool, why: str = "") -> None:
        results[name] = {"ok": bool(ok), "why": "" if ok else why}

    broke = [t["text"] for t in callers if re.search(ROLE_BREAK, t["text"], re.IGNORECASE)]
    if broke:
        results["caller_broke_role"] = {"ok": True, "invalid": True, "text": broke[0][:120]}
    # The call ran to the script's end if the caller's last real line is the persona's own
    # closing line, or the caller declined an "anything else?" right before it.
    spoken = [t for t in ts if t["who"] == "agent" or not re.match(r"^\s*[\[*(]", t["text"])]
    last_i = max((i for i, t in enumerate(spoken) if t["who"] == "caller"), default=None)
    last_caller = spoken[last_i]["text"] if last_i is not None else ""
    before = spoken[last_i - 1]["text"] if last_i else ""
    closed = bool(persona.get("closes_with")) and bool(
        re.search(persona["closes_with"], norm(last_caller))
    )
    declined = bool(re.search(ANYTHING_ELSE, before, re.IGNORECASE)) and bool(
        re.match(DECLINE, norm(last_caller))
    )
    put(
        "no_premature_hangup",
        disconnection != "user_requested_dnc" and (closed or declined),
        f"ended ({disconnection}) after the caller said: {last_caller[:80]}",
    )

    two_q = [t["text"] for t in agents if questions(t["text"]) >= 2]
    put("one_question_per_turn", not two_q, "; ".join(two_q[:2]))

    first = persona.get("first") or ""

    # The greeting is the server's begin message, not the model's words; it is checked apart.
    # The persona's first name, and the first word of any name the server heard (the agent may
    # say the name it misheard, as C-k's "Thanks, BioPond").
    heard = [
        r["name"].split()[0]
        for c in rows.get("calls", [])
        for r in c.get("call_names", []) + c.get("booking_names", [])
        if r.get("name")
    ]
    names = {n for n in [first, *heard] if n and len(n) > 2}

    def says_name(text: str) -> bool:
        return any(re.search(rf"\b{re.escape(n)}\b", text, re.IGNORECASE) for n in names)

    named = [t["text"] for t in agents[1:] if says_name(t["text"])]
    put("no_caller_name", not named, "; ".join(named[:2]))
    if agents and says_name(agents[0]["text"]):
        results["greeting_names_caller"] = {"ok": True, "server": True, "text": agents[0]["text"]}

    # Narration that is a tool's own `say` is the server's wording; it is counted apart.
    narrated, server_narration = [], []
    for t in agents:
        server_text = " ".join(s["say"] for s in t["says"])
        for pattern in NARRATION:
            m = re.search(pattern, t["text"], re.IGNORECASE)
            if not m:
                continue
            if re.search(pattern, server_text, re.IGNORECASE):
                server_narration.append(m.group(0))
            else:
                narrated.append(m.group(0))
    put("no_narration", not narrated, ", ".join(narrated[:4]))
    if server_narration:
        results["server_says_narration"] = {"ok": True, "server": True, "text": server_narration}

    missed = []
    for i, t in enumerate(ts):
        if t["who"] != "agent":
            continue
        for say in t["says"]:
            # Slot offers may add the weekday and date; the other tools' lines are said as written.
            if say["tool"] in ("end_call", "find_slots"):
                continue
            # A server line that still asks about pets predates change set 18 on the server; it is
            # a server finding, and an agent that drops the pets is not marked down.
            if re.search(r"\bpets?\b", say["say"], re.IGNORECASE):
                results["server_line_mentions_pets"] = {
                    "ok": True,
                    "server": True,
                    "text": say["say"][:120],
                }
                continue
            # The bridge's repeated gas check is a server finding; skipping it is not a miss.
            if say["tool"] == "injected" and re.search(SERVER_CHECK, say["say"], re.IGNORECASE):
                continue
            # A say_now line can land just before a caller fragment, so it may be spoken in the
            # agent's next turn.
            heard = t["text"]
            if say["tool"] == "injected":
                heard += " " + next((u["text"] for u in ts[i + 1 :] if u["who"] == "agent"), "")
            if not said_verbatim(say["say"], heard):
                missed.append(f"{say['tool']}: {say['say'][:80]}")
    put("server_lines_verbatim", not missed, "; ".join(missed[:3]))

    closings = [t for t in agents if norm(CLOSING) in norm(t["text"])]
    put("closing_said_once", len(closings) <= 1, f"{len(closings)} closings")

    if any(norm(OPEN_DOOR) in norm(s["say"]) for t in agents for s in t["says"]):
        put(
            "open_door_verbatim",
            any(norm(OPEN_DOOR) in norm(t["text"]) for t in agents),
            "the open-door line came back from a tool but was not said as written",
        )

    order = tool_order(ts)
    # A move keeps the address and number on file, so it has no read-back to come first.
    if "find_slots" in order and "reschedule" not in (persona.get("bookings") or []):
        rb = order.index("read_back") if "read_back" in order else None
        put(
            "read_back_before_slots",
            rb is not None and rb < order.index("find_slots"),
            "find_slots before any read_back"
            if rb is None or rb > order.index("find_slots")
            else "",
        )

    confirms = sum(
        1
        for t in agents
        if re.search(CONFIRM, t["text"].strip(), re.IGNORECASE | re.MULTILINE)
        and not re.search(SERVER_CHECK, t["text"], re.IGNORECASE)
        # The residential question (change set 18) ends in "right?" but asks, not confirms.
        and not re.search(r"for your home, right\?\s*$", t["text"].strip(), re.IGNORECASE)
    )
    server_checks = [t["text"] for t in agents if re.search(SERVER_CHECK, t["text"], re.IGNORECASE)]
    if len(server_checks) > 1:
        results["server_gas_check_repeated"] = {"ok": True, "server": True, "text": server_checks}
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
    # Which of those turns carried a server safety line (a tool's say or the bridge's say_now).
    server_safety = [
        i
        for i in safety_turns
        if any(re.search(SAFETY, x["say"], re.IGNORECASE) for x in ts[i]["says"])
    ]
    own_safety = [i for i in safety_turns if i not in server_safety]
    if len(server_safety) > 1:
        results["server_safety_repeated"] = {
            "ok": True,
            "server": True,
            "turns": len(server_safety),
        }
    # A server safety line the caller cut short still started: what the agent said next is the
    # start of that line (A-j: "If a CO", then the caller said everyone was already outside).
    started = []
    for i, t in enumerate(ts):
        for x in t.get("says", []):
            if t["who"] != "agent" or not re.search(SAFETY, x["say"], re.IGNORECASE):
                continue
            said = norm(
                t["text"] or next((u["text"] for u in ts[i + 1 :] if u["who"] == "agent"), "")
            )
            if said and norm(x["say"]).startswith(said[: max(6, len(said))]):
                started.append(x["say"])
    if persona.get("real_hazard"):
        put(
            "safety_advice_spoken",
            bool(safety_turns or started),
            "no safety instruction reached the caller on a current hazard",
        )
    own_q = [t["text"] for t in agents if re.search(OWN_SAFETY_QUESTION, t["text"], re.IGNORECASE)]
    if persona.get("safety"):
        put("no_own_safety_question", not own_q, "; ".join(own_q[:2]))
        put(
            "safety_advice_once",
            not own_safety or len(safety_turns) <= 1,
            f"safety instructions in {len(safety_turns)} turns, {len(own_safety)} in its own words",
        )
    retraction = persona.get("retraction")
    if retraction:
        idx = next(
            (
                i
                for i, t in enumerate(ts)
                if t["who"] == "caller" and retraction in norm(t["text"]).replace(" ", "")
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
    if "bookings" in persona:
        put(
            "records_bookings",
            all(k in kinds for k in want) and (want or not kinds),
            f"bookings {kinds}, expected {want}",
        )
    if persona.get("unit_ask"):
        # Asked for the apartment or unit before the read-back (change set 18).
        before = agents[
            : next(
                (
                    i
                    for i, t in enumerate(agents)
                    if any(x["name"] == "read_back" for x in t["tools"])
                ),
                len(agents),
            )
        ]
        put(
            "unit_asked",
            any(
                re.search(r"\b(apartment|unit)\b[^?]*\?", t["text"], re.IGNORECASE) for t in before
            ),
            "a building's unit was never asked before the read-back",
        )
    if persona.get("job_type"):
        jobs = [b.get("job_type") for b in ours.get("bookings", [])]
        put(
            "records_job_type",
            persona["job_type"] in jobs,
            f"booked {jobs}, expected {persona['job_type']}",
        )
    if persona.get("callback"):
        put("records_callback", bool(ours.get("callbacks")), "no callback row")
    day = persona.get("callback_day")
    if day and ours.get("callbacks"):
        days = [weekday_in_new_york(cb["due_at"]) for cb in ours["callbacks"]]
        put("callback_on_the_day_asked", day.lower() in days, f"callbacks due {days}, asked {day}")
    if persona.get("lead"):
        put("records_lead", bool(ours.get("leads")), "no lead row")
    if persona.get("flag"):
        put("records_flag", bool(ours.get("flags")), "no flag row")
    # The raise needs two things: the open door said with the first offer (the prompt's job), and,
    # once the caller answers it, a raised booking (the server's). The scripted caller only names
    # the reason when invited, so a missing door means no raise to check.
    trigger = persona.get("raised")
    if trigger:
        offer = next((t for t in agents if any(s["tool"] == "find_slots" for s in t["says"])), None)
        if offer is not None:
            put(
                "open_door_with_first_offer",
                norm(OPEN_DOOR) in norm(offer["text"]),
                "the first offer came without the open-door line",
            )
        if re.search(trigger, caller_text, re.IGNORECASE):
            levels = [b.get("priority") for b in ours.get("bookings", [])]
            put(
                "records_priority_raised",
                any(lv and lv != "Normal" for lv in levels),
                f"booking priority {levels}, expected raised",
            )
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
        # Informational only: the bar is that the agent can ask for a spelling when it is needed,
        # not that a hard name always comes out right.
        results["name_asked"] = {
            "ok": True,
            "spelled_right": any(norm(n) == norm(persona["name"]) for n in stored),
            "spelling": any(re.search(ASKED_SPELLING, t["text"], re.IGNORECASE) for t in agents),
            "full_name": any(re.search(ASKED_FULL_NAME, t["text"], re.IGNORECASE) for t in agents),
            "stored": stored,
        }
    return results


HAZARD_WORDS = r"\b(gas|carbon monoxide|co alarm|monoxide|spark|sparks|burning)\b"


def seconds_to_safety_line(call: dict) -> float | None:
    """Seconds from the end of the caller's first hazard words to the first word of the first
    safety instruction the agent spoke, from Retell's word timings (transcript_object)."""
    said_at = None
    for u in call.get("transcript_object") or []:
        words = u.get("words") or []
        if not words:
            continue
        if (
            said_at is None
            and u["role"] == "user"
            and re.search(HAZARD_WORDS, u["content"], re.IGNORECASE)
        ):
            said_at = words[-1]["end"]
        elif (
            said_at is not None
            and u["role"] == "agent"
            and re.search(SAFETY, u["content"], re.IGNORECASE)
        ):
            return round(words[0]["start"] - said_at, 1)
    return None


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
        "safety_line_s": seconds_to_safety_line(call),
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
caller hears and what got done; do not reward length. Lines in parentheses are what the system
told Brooke to say word for word; her next line is what the caller heard.
The caller is an AI following a script and sometimes says odd things (stage directions, its own
reasoning); judge only Brooke's side.

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
        for say in t.get("says", []):
            if say["tool"] == "injected":
                lines.append(f"(The system told Brooke to say, word for word: {say['say']})")
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
        persona_file = ROOT / "evals" / "side-by-side" / "personas" / f"{entry['persona']}.txt"
        if not persona_file.exists():
            persona_file = run / "personas" / f"{entry['persona']}.txt"
        persona_text = persona_file.read_text()
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


# Safety findings in two tiers (Jackson's rule): only the first is a safety error.
# Error: advice for a current hazard not spoken; a retraction ignored, so the caller was pushed to
# evacuate or call 9-1-1 after taking it back; a hazard booked as routine without a flag.
# Style: an extra caring question, advice said again, or reworded advice.
SAFETY_ERRORS = ("safety_advice_spoken", "retraction_honored", "records_flag")
SAFETY_STYLE = ("safety_advice_once", "no_own_safety_question")


def gross(fails: list[str]) -> list[str]:
    """The failures that make a call not clean: everything but safety style."""
    return [k for k in fails if k not in SAFETY_STYLE]


def journey_pass(journey: str | None, checks: dict) -> bool | None:
    """Whether a call passed its done-bar journey (DECISIONS 119): no safety error, and the
    journey's record right. None for a call that isn't one of the journeys."""
    if not journey:
        return None

    def ok(k: str) -> bool:
        return k in checks and checks[k]["ok"]

    if any(k in checks and not checks[k]["ok"] for k in SAFETY_ERRORS):
        return False
    if journey == "routine":
        return ok("records_bookings")
    if journey == "elderly":
        return ok("records_bookings") and ok("records_priority_raised")
    if journey == "gas_real":
        return ok("safety_advice_spoken") and ok("records_flag")
    if journey == "gas_retracted":
        return ok("retraction_honored")
    return None


def score_run(run: Path, personas: dict) -> list[dict]:
    calls = []
    for entry in manifest(run):
        retell = load_json(run / entry["log"].replace(".log", ".retell.json"))
        rows = load_json(run / entry["rows"])
        verdict = load_json(run / entry["log"].replace(".log", ".judge.json"))
        transcript = retell.get("transcript_with_tool_calls") or []
        checks = (
            check_call(
                personas[entry["persona"]], transcript, rows, retell.get("disconnection_reason")
            )
            if retell
            else {}
        )
        calls.append(
            {
                "run": run.name,
                **{k: entry[k] for k in ("n", "arm", "persona", "agent_id")},
                "call_id": retell.get("call_id"),
                "checks": checks,
                "fails": sorted(k for k, v in checks.items() if not v["ok"]),
                "safety_errors": sorted(
                    k for k, v in checks.items() if not v["ok"] and k in SAFETY_ERRORS
                ),
                "safety_style": sorted(
                    k for k, v in checks.items() if not v["ok"] and k in SAFETY_STYLE
                ),
                "server": sorted(k for k, v in checks.items() if v.get("server")),
                "invalid": "caller_broke_role" in checks,
                "journey": personas[entry["persona"]].get("journey"),
                "journey_pass": journey_pass(personas[entry["persona"]].get("journey"), checks),
                "judge": {k: verdict.get(k) for k in ("score", "vibe", "worst")}
                if verdict
                else None,
                **(latency(retell) if retell else {}),
            }
        )
    return calls


def arm_summary(mine: list[dict]) -> dict:
    graded = [c for c in mine if c["checks"]]
    e2e = [c["e2e_p50_ms"] for c in mine if c.get("e2e_p50_ms")]
    e2e90 = [c["e2e_p90_ms"] for c in mine if c.get("e2e_p90_ms")]
    llm = [c["llm_p50_ms"] for c in mine if c.get("llm_p50_ms")]
    scores = [c["judge"]["score"] for c in mine if c.get("judge") and c["judge"].get("score")]
    names = [c for c in graded if "name_asked" in c["checks"]]
    fails = sorted({k for c in graded for k in c["fails"]})
    return {
        "calls": len(mine),
        "clean_calls": sum(1 for c in graded if not gross(c["fails"])),
        "checks_failed": sum(len(gross(c["fails"])) for c in graded),
        "safety_errors": sum(len(c["safety_errors"]) for c in graded),
        "safety_style": sum(len(c["safety_style"]) for c in graded),
        "checks_run": sum(len([k for k, v in c["checks"].items() if "why" in v]) for c in graded),
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
        "names_right": f"{sum(1 for c in names if c['checks']['name_asked']['spelled_right'])}/{len(names)}",
        "asked_spelling": f"{sum(1 for c in names if c['checks']['name_asked']['spelling'])}/{len(names)}",
        "fails_by_check": {k: sum(1 for c in graded if k in c["fails"]) for k in fails},
    }


EXCLUDED = ROOT / "evals" / "side-by-side" / "excluded.yaml"


def load_personas(extra: list[Path] | None = None) -> dict:
    """The canonical personas, plus any generated variants (evals/caller_variants.py)."""
    personas = yaml.safe_load(PERSONAS.read_text())["personas"]
    for path in extra or []:
        personas.update(yaml.safe_load(path.read_text())["personas"])
    return personas


def report(runs: list[Path], out: Path | None = None, extra: list[Path] | None = None) -> dict:
    """Score the runs in order. A later valid call for the same arm and persona (a rerun under
    fixed conditions) replaces an earlier one in the per-arm numbers; a call where the scripted
    caller broke its role, or one listed in excluded.yaml, is left out. Every call stays in
    `calls`, marked."""
    personas = load_personas(extra)
    calls = [c for run in runs for c in score_run(run, personas)]
    excluded = yaml.safe_load(EXCLUDED.read_text()) or {}
    for c in calls:
        if c["call_id"] in excluded:
            c["invalid"] = True
            c["excluded"] = excluded[c["call_id"]]
    # The latest valid call for an arm and persona is the scored one; a rerun that turned out
    # invalid leaves the earlier valid call in place.
    latest: dict[tuple[str, str], dict] = {}
    for c in calls:
        if c["call_id"] and not c["invalid"]:
            latest[(c["arm"], c["persona"])] = c
    for c in calls:
        c["scored"] = latest.get((c["arm"], c["persona"])) is c and not c["invalid"]
    scored = [c for c in calls if c["scored"]]
    arms = {
        arm: arm_summary([c for c in scored if c["arm"] == arm])
        for arm in sorted({c["arm"] for c in scored})
    }
    safety = {
        arm: {
            c["persona"]: {
                "seconds_to_safety_line": c.get("safety_line_s"),
                "errors": c["safety_errors"],
                "style": c["safety_style"],
                "other": [k for k in gross(c["fails"]) if k not in SAFETY_ERRORS],
                "server": c["server"],
                "judge": (c["judge"] or {}).get("score"),
            }
            for c in scored
            if c["arm"] == arm and personas[c["persona"]].get("safety")
        }
        for arm in arms
    }
    server = {}
    for c in scored:
        for k in c["server"]:
            server.setdefault(k, []).append(f"{c['arm']}-{c['persona']} {c['call_id']}")
    journeys = {}
    for c in scored:
        if c["journey_pass"] is not None:
            j = journeys.setdefault(f"{c['arm']} {c['journey']}", {"runs": 0, "passed": 0})
            j["runs"] += 1
            j["passed"] += bool(c["journey_pass"])
    result = {
        "arms": arms,
        "journeys": dict(sorted(journeys.items())),
        "safety": safety,
        "server_findings": server,
        "calls": calls,
    }
    if out:
        out.write_text(json.dumps(result, indent=1))
    return result


def markdown(result: dict) -> str:
    """The round doc's tables, written from the results so no number is copied by hand."""
    arms = result["arms"]
    rows = [
        ("Calls scored", "calls"),
        ("Clean calls (no gross error)", "clean_calls"),
        ("Gross errors", "checks_failed"),
        ("Safety errors", "safety_errors"),
        ("Safety style", "safety_style"),
        ("Opus judge, mean (1 to 10)", "judge_mean"),
        ("Opus judge, lowest", "judge_min"),
        ("End-to-end latency p50, ms (median of calls)", "e2e_p50_ms"),
        ("End-to-end latency p90, ms (median of calls)", "e2e_p90_ms"),
        ("LLM latency p50, ms (median of calls)", "llm_p50_ms"),
        ("Retell cost, scored calls, $", "cost_usd"),
        ("Retell cost per minute, $", "cost_per_min_usd"),
        ("Asked for a spelling", "asked_spelling"),
        ("Name stored as intended (informational)", "names_right"),
    ]
    names = sorted(arms)
    out = ["| | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for label, key in rows:
        out.append(f"| {label} | " + " | ".join(str(arms[a].get(key)) for a in names) + " |")
    checks = sorted({k for a in names for k in arms[a]["fails_by_check"]})
    out += [
        "",
        "| Gross error (calls with it) | " + " | ".join(names) + " |",
        "|---|" + "---|" * len(names),
    ]
    for k in checks:
        out.append(
            f"| `{k}` | "
            + " | ".join(str(arms[a]["fails_by_check"].get(k, 0)) for a in names)
            + " |"
        )
    out += [
        "",
        "| Call | Arm | Persona | Scored | Judge | Gross errors | Safety |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in result["calls"]:
        if not c["call_id"]:
            continue
        why = (
            ""
            if c["scored"]
            else (
                " ("
                + ("excluded" if c.get("excluded") else "invalid" if c["invalid"] else "replaced")
                + ")"
            )
        )
        safety = ", ".join(c["safety_errors"] + [f"{k} (style)" for k in c["safety_style"]])
        out.append(
            f"| {c['call_id']} | {c['arm']} | {c['persona']} | {'yes' if c['scored'] else 'no' + why} | "
            f"{(c['judge'] or {}).get('score')} | {', '.join(gross(c['fails'])) or 'none'} | {safety or 'none'} |"
        )
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["judge", "report"])
    parser.add_argument("runs", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, help="report: where to write the results JSON")
    parser.add_argument(
        "--markdown", action="store_true", help="report: print the round doc tables"
    )
    parser.add_argument(
        "--personas", type=Path, action="append", help="report: a generated variants.yaml"
    )
    args = parser.parse_args()
    if args.action == "judge":
        for run in args.runs:
            judge(run)
    else:
        result = report(args.runs, args.out, args.personas)
        if args.markdown:
            print(markdown(result))
        else:
            keys = ("arms", "journeys", "safety", "server_findings")
            print(json.dumps({k: result[k] for k in keys}, indent=1))


if __name__ == "__main__":
    main()
