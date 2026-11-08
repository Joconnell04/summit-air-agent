"""The server-led arm (issue #171): the server tells the model each next step and holds the
booking order, so the prompt can stay short.

On an instance with SERVER_LED=1 every tool runs through `guide`:

- Before the tool, three order gates, each read from the server's own records and never from an
  argument the model sets: no openings before a confirmed read-back, no booking before the three
  visit questions, and no end_call while another result's sentence was never said.
- After it, `next` names the step after this one in plain words, with the exact question where
  there is one, and the first offer on a no-heat or no-cooling call ends with the open-door line.

Every result is recorded in flow_results with the caller turn it answered, so a later gate can
tell what the agent said after it.
"""

import re
from collections.abc import Callable

from . import records
from .db import iso, tx
from .envelope import Refused, Result
from .text import unit_settled, words

# The visit questions, the patterns that find each one in an agent line (said in any words), and
# the patterns that find a caller who answered it before being asked.
VISIT_QUESTIONS = (
    ("ask_approval", r"\bapprov", r"\bapprov|\bi'?m the owner\b|\bi own\b|\bmy (house|home|place)\b"),
    (
        "ask_someone_home",
        r"\b(someone|anyone|somebody|you)\b[^.?]*\bhome\b|\blet (the )?(technician|tech|them) in\b",
        (
            r"\b(i'?ll|i will|we'?ll|we will|someone will|somebody will|my \w+ will) be "
            r"(home|here|there)\b|\b(i'?m|i am|someone'?s|someone is|somebody is|we'?re|we are) "
            r"(always |usually )?(home|here)\b|\bwork from home\b"
        ),
    ),
    # Saying how to get in answers it too: "ring the downstairs bell" was followed by the gate
    # question (demo eval run 20261108-021040, tune-up-known-caller).
    ("ask_access", r"\bgate code\b|\bbuzzer\b",
     r"\bgate\b|\bbuzzer\b|\bdoorman\b|\bno code\b|\bring (the |my )?(\w+ )?(door)?bell\b|\bdoorbell\b|\bknock\b"),
)

# What a caller says before the opening that still answers a visit question, because it doesn't
# change with the day: who approves the work, the gate or buzzer, and being home as a habit. "I'm
# home" before a day is picked may mean right now, so only these count from earlier in the call
# (eval run 20261107-214125: card-pay-by-card-ask and tenant-landlord-approves were asked approval
# and access after saying "I own the place", "my landlord has to approve" and "the buzzer is
# broken" before the offer).
SETTLED_EARLY = {
    "ask_approval": r"\bapprov|\bi'?m the owner\b|\bi own (it|the|this|my)\b",
    "ask_someone_home": (
        r"\bwork(s|ing)? from home\b|\b(always|usually) (home|here)\b"
        r"|\bhome (all|most of the) day\b"
    ),
    "ask_access": r"\bgate\b|\bbuzzer\b|\bdoorman\b|\bno code\b|\bring (the |my )?(\w+ )?(door)?bell\b|\bdoorbell\b|\bknock\b",
}

# A caller saying nobody will be home, and the follow-up that asks how the technician gets in
# (Jackson's change set 25): asked once, unless the caller already said how.
NOBODY_HOME = (
    r"\b(no ?one|nobody)(\s+(will|is|'ll|'s))?\s+(be\s+)?(home|here|there)\b"
    r"|\bwon'?t be (home|there|here)\b|\bnot (going to|gonna) be (home|there|here)\b"
)
HOW_IN = (
    "ask_how_in",
    r"\bhow (can|could|will|would|should) (the )?(technician|tech|they|someone) get in\b",
    (r"\b(key|lockbox|lock box|super|superintendent|doorman|neighbou?r|landlord|code is|"
    r"leave (it|the door) (open|unlocked))\b"),
)

# The short connector before each visit question the server asks, in turn, so quick questions
# don't all open the same way (Jackson's change sets 18 and 22).
ACKS = ("Got it.", "Okay, thanks.", "Perfect.")

# The tools whose sentence ends a piece of work: a booking, a change, a lead, a callback. end_call
# waits on these. The others are steps a later step answers (an offer the caller took, a read-back
# they confirmed) or are read, not said; holding the goodbye for them re-offered a window the caller
# had already booked (eval run 20261106-010112, no-heat-unknown-caller).
OUTCOME_TOOLS = (
    "book_appointment", "reschedule_appointment", "cancel_appointment", "record_move",
    "flag_person", "record_lead", "mark_do_not_call",
)

# Share of a sentence's words the agent's lines after it must hold to count as having said it. The
# gate is for a sentence never said at all (end_call in the same turn as the booking), which shares
# almost no words. Haiku's rewordings of the booking sentence shared 0.62 to 0.94 of its words
# (MEASURED, evals/rounds/server-led-01-data on branch archive/voice-stacks), and 0.7 held one back.
SAID_SHARE = 0.5


def set_mode(conn, call_id: str, mode: str) -> None:
    """The call's flow, once, at its start (the bridge sets it for the socket agent's calls)."""
    with tx(conn):
        conn.execute("INSERT OR IGNORE INTO call_flows (call_id, flow) VALUES (?, ?)",
                     (call_id, mode))


def server_led(conn, call_id: str) -> bool:
    """Whether this call runs the server-led flow. A call that started without it never does, so a
    single-prompt call on the same tool server never meets a gate."""
    row = conn.execute("SELECT flow FROM call_flows WHERE call_id = ?", (call_id,)).fetchone()
    return bool(row) and row["flow"] == "server_led"


def guide(ctx, tool: str, args, handler: Callable) -> Result:
    try:
        _gate(ctx, tool, args)
        result = handler(ctx, args)
    except Refused as exc:
        exc.result.next = exc.result.next or _next_refused(exc.result)
        _record(ctx, tool, exc.result)
        raise
    if tool == "find_slots":
        _open_door(ctx, args, result)
    # A tool's own next step (a dispatch's: the access question, then "Anything else?") wins over
    # the flow's, so the model never gets two that disagree.
    result.next = result.next or result.data.pop("next", "") or _next(ctx, tool, args, result)
    _record(ctx, tool, result)
    return result


# ---- gates -------------------------------------------------------------------------------------


def _gate(ctx, tool: str, args) -> None:
    if tool == "read_back" and not args.confirmed and not getattr(args, "job_type", None):
        # Dispatch at High is decided at the read-back from the job type, so the read-back never
        # goes without it (a gate shouldn't rest on the model remembering a parameter).
        raise Refused(
            "job_type_first",
            "",
            gate="flow_job_type",
            need="read_back needs job_type",
        ).with_next("Say nothing. Call read_back again with the same details and the job type.")
    if tool == "find_slots" and not args.moving_a_visit and not ctx.call["readback_confirmed_at"]:
        raise Refused(
            "read_back_first",
            "",
            gate="flow_read_back_first",
            need="openings come after the caller confirms the read-back",
        ).with_next(
            "Don't mention this or say what you need first. Ask for what you still need, one "
            "question a turn: the address, then the phone number. Then call read_back with the job type."
        )
    if tool == "book_appointment":
        asked = visit_questions(ctx, args.job_type)
        missing = visit_questions_missing(ctx, args.job_type)
        if missing:
            # The server asks each question itself, with a short acknowledgment that changes from
            # one question to the next.
            ack = ACKS[(len(asked) - len(missing)) % len(ACKS)]
            raise Refused(
                "visit_questions_first",
                f"{ack} {question_line(ctx, missing)}",
                gate="flow_visit_questions",
            ).with_next("Say this sentence as written and wait for the answer. Then call "
                        "book_appointment again with the answers so far in details, saying nothing.")
    if tool == "end_call":
        unsaid = unsaid_sentence(ctx)
        if unsaid:
            raise Refused("say_first", unsaid, gate="flow_say_first").with_next(
                "Say this sentence as written and wait for the caller. Call end_call only once "
                "they have nothing else."
            )


def _turns(ctx) -> list:
    return ctx.conn.execute(
        "SELECT seq, speaker, text FROM turns WHERE call_id = ? ORDER BY seq", (ctx.call["id"],)
    ).fetchall()


def _offer_seq(ctx) -> int:
    row = ctx.conn.execute(
        "SELECT turn_seq FROM flow_results WHERE call_id = ? AND tool = 'find_slots' AND ok = 1 "
        "ORDER BY id DESC LIMIT 1",
        (ctx.call["id"],),
    ).fetchone()
    return row["turn_seq"] if row else 0


def visit_questions(ctx, job_type: str = "") -> tuple:
    """The visit questions for this job. A job the tenant books without the repair intake (an
    estimate for a new system) has nothing to approve, so it skips the approval question."""
    skip = ctx.tenant.agent.get("intake", {}).get("skip_for", [])
    return tuple(q for q in VISIT_QUESTIONS if not (q[0] == "ask_approval" and job_type in skip))


def question_line(ctx, missing: list[str]) -> str:
    """The next visit question as the server says it. Getting in is one question, someone home and
    a gate code or buzzer (the tenant's arrival question), and only its missing half when the caller
    answered the other (Jackson's change set 19)."""
    if missing[0] in ("ask_approval", "ask_how_in"):
        return ctx.tenant.line(missing[0])
    if "ask_someone_home" in missing and "ask_access" in missing:
        return ctx.tenant.arrival_question
    return ctx.tenant.line("ask_gate" if missing[0] == "ask_access" else missing[0])


def visit_questions_missing(ctx, job_type: str = "") -> list[str]:
    """The visit questions not yet settled since the opening was offered, in the order they are
    asked. One is settled when an agent line asked it and a caller line came after, or when the
    caller answered it unasked; earlier in the call, only the answers in SETTLED_EARLY count."""
    turns = _turns(ctx)
    offered = _offer_seq(ctx)
    after = [t for t in turns if t["seq"] > offered]
    before = [t for t in turns if t["seq"] <= offered and t["speaker"] == "caller"]
    missing = []
    for key, asked, volunteered in visit_questions(ctx, job_type):
        early = SETTLED_EARLY.get(key)
        if early and any(re.search(early, t["text"], re.IGNORECASE) for t in before):
            continue
        settled = any(
            t["speaker"] == "caller" and re.search(volunteered, t["text"], re.IGNORECASE) for t in after
        ) or any(
            t["speaker"] == "agent"
            and re.search(asked, t["text"], re.IGNORECASE)
            and any(c["speaker"] == "caller" and c["seq"] > t["seq"] for c in after)
            for t in after
        )
        if not settled:
            missing.append(key)
    nobody = [t for t in after if t["speaker"] == "caller"
              and re.search(NOBODY_HOME, t["text"], re.IGNORECASE)]
    if nobody and not _settled(HOW_IN, [t for t in after if t["seq"] >= nobody[0]["seq"]]):
        missing.append(HOW_IN[0])
    return missing


def _settled(question: tuple, turns: list) -> bool:
    _, asked, volunteered = question
    return any(
        t["speaker"] == "caller" and re.search(volunteered, t["text"], re.IGNORECASE) for t in turns
    ) or any(
        t["speaker"] == "agent" and re.search(asked, t["text"], re.IGNORECASE)
        and any(c["speaker"] == "caller" and c["seq"] > t["seq"] for c in turns)
        for t in turns
    )


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def said(sentence: str, lines: list[str]) -> bool:
    want = _words(sentence)
    if not want:
        return True
    heard = set(_words(" ".join(lines)))
    return sum(w in heard for w in want) / len(want) >= SAID_SHARE


def unsaid_sentence(ctx) -> str | None:
    """The newest outcome sentence that no agent line after it said. Each one holds
    end_call back once: a transcript that never matches can't keep the call open."""
    turns = _turns(ctx)
    rows = ctx.conn.execute(
        "SELECT tool, say, turn_seq FROM flow_results WHERE id IN ("
        " SELECT MAX(id) FROM flow_results WHERE call_id = ? GROUP BY tool)"
        " ORDER BY id DESC",
        (ctx.call["id"],),
    ).fetchall()
    for row in rows:
        if row["tool"] not in OUTCOME_TOOLS or not row["say"]:
            continue
        lines = [t["text"] for t in turns if t["speaker"] == "agent" and t["seq"] > row["turn_seq"]]
        if said(row["say"], lines):
            continue
        held = ctx.conn.execute(
            "SELECT 1 FROM flow_results WHERE call_id = ? AND tool = 'end_call' AND ok = 0 "
            "AND say = ?",
            (ctx.call["id"], row["say"]),
        ).fetchone()
        if not held:
            return row["say"]
    return None


# ---- what comes next -------------------------------------------------------------------------


def _open_door(ctx, args, result: Result) -> None:
    """The open-door line ends the first offer on a no-heat or no-cooling call, once a call. Not
    after a priority line: the caller just heard they were moved up."""
    from .tools import _hand_out_once

    data = result.data
    if (
        result.ok
        and args.job_type in _out_jobs(ctx)
        and not args.moving_a_visit
        and (data.get("offer") or data.get("windows"))
        and not data.get("spoken_first")
    ):
        line = ctx.tenant.line("open_door")
        if _hand_out_once(ctx, line):
            # Before the offer's closing question, not after it: the model dropped a sentence that
            # came after the question on every offer (eval runs 20261106-010112 and -011527).
            asked = re.match(r"(.*[.!])\s+([^.!?]*\?)$", result.say)
            if asked:
                result.say = f"{asked[1]} {line} {asked[2]}"
            else:
                result.say = f"{result.say} {line}".strip()


def _out_jobs(ctx) -> list[str]:
    """The job types for heat or cooling that is out, from the tenant's after-hours cutoffs."""
    cutoffs = ctx.tenant.after_hours_cutoffs
    return cutoffs["heating_job_types"] + cutoffs["cooling_job_types"]


UNIT_QUESTION = "'Is there an apartment or unit number?'"


def _unit_said(ctx) -> str:
    """What the caller said that settles the unit ("unit 4B", or "none" for "it's just the
    house"), so it isn't asked again; "" when they haven't."""
    if getattr(ctx, "conn", None) is None:
        return ""
    return unit_settled([t["text"] for t in _turns(ctx) if t["speaker"] == "caller"])


def _safety_call(ctx) -> bool:
    """The caller reported a gas smell or an alarm on this call, so the visit checks that."""
    call = getattr(ctx, "call", None)
    # keys(), since `in` on a sqlite3.Row looks at its values.
    keys = call.keys() if call is not None else ()
    return bool("safety_signal_at" in keys and call["safety_signal_at"])


def _corrected_address(ctx, needs_unit: bool) -> str:
    """The caller corrected the address after a read-back: everything else was already asked, so
    only the unit (when this building needs one) and the read-back again (Jackson's call
    call_9e278a30dcdb1ff084f39a279c8 heard the heat, since-when, anything-else and phone questions
    all over again)."""
    said_unit = _unit_said(ctx)
    unit = f"{UNIT_QUESTION}, unless they said it's a house; then " if (
        needs_unit and not said_unit) else ""
    return (
        f"The caller corrected the address. Don't ask anything you already asked. {unit}call "
        "read_back with the corrected address, the same phone and the job type."
    )


def _after_address(ctx, needs_unit: bool) -> str:
    """The questions between the address and the read-back, built from what the caller already
    said (Jackson's call call_046f37112cac691a50019893717): the unit only for a building with more
    than one home (find_address `needs_unit`); how bad only on a no-heat or no-cooling call, in
    their words when they named a symptom; the system type fit to the issue; a catch-all that
    never offers an example they already gave."""
    said_unit = _unit_said(ctx)
    if not needs_unit or said_unit == "none":
        unit = ""
    elif said_unit:
        unit = f"never the unit, which they gave ('{said_unit}'); "
    else:
        unit = f"{UNIT_QUESTION}, unless they said it's a house; "
    if _safety_call(ctx):
        # A gas smell or an alarm is the issue already: no outage, since-when, tried or system
        # questions (calls call_51d7bc63abd9040be451a8bc596, call_d101fce0cddb6792cb6214aaacb).
        return (
            "This visit is a safety check, so never ask how bad it is, since when, what was tried, "
            "what the issue is or what kind of system it is. Ask the next of these you don't have "
            f"yet, one a turn: {unit}'What's the best phone number to reach you?'. Once you have "
            "the phone number, call read_back with the job type."
        )
    said = words(records.caller_text(ctx.conn, ctx.call["id"])) if getattr(ctx, "call", None) else ""

    named = _symptoms_named(said)

    def line(key: str) -> str:
        if key == "ask_how_bad_symptom":
            return ctx.tenant.line(key, symptom=named[0] if named else "the problem")
        text = ctx.tenant.line(key)
        return _catch_all(text, said) if key.startswith("ask_catch_all") else text

    return (
        # Up front, not inside the list: as a clause there it was skipped 3 times in 3 after "the
        # heat's still on" (eval check of 6 November, ab-boiler-dripping-still-on).
        "If the caller already said whether it still works (the heat's still on, it's still warm, "
        "it's running) or that it's out, never ask how bad it is. "
        "Ask the next of these you don't have yet, one a turn, built from what the caller already "
        "said and never asking for anything they told you: "
        f"{unit}only on a no-heat or no-cooling call (never a tune-up, maintenance visit or quote), "
        # Skipped when the caller already said: A/B of 6 November, 48 runs a side, the how-bad
        # question was the most common re-ask after "the heat's still on" or "it's still warm".
        + "how bad it is, "
        + (f"word for word: '{line('ask_how_bad_symptom')}', " if named else
           "'Is it completely out, or just not keeping up?', ")
        + "and then, still only on a "
        "no-heat or no-cooling call, 'Since when?'; if they haven't said "
        f"what system it is, word for word: for heating '{line('ask_system_type_heating')}', for "
        f"cooling '{line('ask_system_type_cooling')}', for a tune-up '{line('ask_system_type')}' "
        # A tune-up isn't heating or cooling yet: Jackson's call call_55b467b41ae58e725fa142d29b7
        # heard "a tune-up on a system you already have, or for a heating or cooling system?".
        "; when heating, cooling or airflow isn't working (not "
        "a maintenance visit or a quote), one catch-all in its own turn, never with an example "
        f"they already gave: heating '{line('ask_catch_all_heating')}', cooling "
        f"'{line('ask_catch_all_cooling')}', vents or airflow '{line('ask_catch_all_airflow')}' "
        "(word for word: never add an example), with the answer in the job's issue; then "
        "'What's the best phone number to reach you?'. Once you have the phone number, call "
        "read_back with the job type."
    )


ANYTHING_ELSE = (
    "If the sentence didn't ask already, ask 'Anything else?'. When the caller has nothing else, "
    "say the goodbye and call end_call."
)


def _next(ctx, tool: str, args, result: Result) -> str:
    data = result.data
    if tool == "find_customer":
        return "Go on with the next question you need. Never mention what this found."
    if tool == "get_customer_history":
        return "Answer only what the caller asked, in a sentence. Never read out an address on file."
    if tool in ("find_address", "check_service_area"):
        # An address booked unverified says so and goes on; only a question waits for the answer.
        if result.say and (not result.ok or result.say.rstrip().endswith("?")):
            return "Wait for the caller's answer."
        call = getattr(ctx, "call", None)
        # keys(), since `in` on a sqlite3.Row looks at its values.
        if call is not None and "readback_json" in call.keys() and call["readback_json"]:  # noqa: SIM118
            return _corrected_address(ctx, bool(data.get("needs_unit")))
        return _after_address(ctx, bool(data.get("needs_unit")))
    if tool == "read_back":
        if data.get("confirmed"):
            then = (
                "call find_slots for the visit, or record_move if the caller said they moved, "
                "saying nothing first."
            )
            return f"Wait for the answer, then {then}" if result.say else then.capitalize()
        return (
            "Wait for the answer. On a yes, call read_back again with the same details and "
            "confirmed true, saying nothing. If they change something, call read_back with the "
            "change."
        )
    if tool == "find_slots":
        if args.moving_a_visit:
            return "When the caller picks a window, call reschedule_appointment with it."
        if not (data.get("offer") or data.get("windows")):
            return "Ask whether another day would work, and call find_slots with it."
        return (
            "When the caller takes a window, call book_appointment with it, saying nothing; a "
            "plain yes or okay takes the first window offered. Put "
            "what you know in details and 'not asked yet' for anything you don't. The server asks "
            "the visit questions itself, one a turn: after each answer, call book_appointment "
            "again with the answers so far, saying nothing. If they want another time, call "
            "find_slots with what they asked for."
        )
    if tool == "book_appointment":
        return "Wait for the answer. " + ANYTHING_ELSE
    if tool == "end_call":
        if data.get("hang_up") and not result.say:
            return "Say nothing more. If the caller speaks, say only 'Take care.'"
        return "Wait for the caller."
    if tool == "lookup_hvac_info":
        return (
            "Answer in a sentence or two from this, in your own words, then go back to the "
            "question you were on."
        )
    return ANYTHING_ELSE


def _next_refused(result: Result) -> str:
    if result.say:
        return "Say the sentence as written and wait for the caller."
    return "Don't mention this to the caller. Do what `need` says."


def _record(ctx, tool: str, result: Result) -> None:
    with tx(ctx.conn):
        ctx.conn.execute(
            "INSERT INTO flow_results (call_id, tool, ok, say, turn_seq, at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ctx.call["id"], tool, int(result.ok), result.say, records.get_call(
                ctx.conn, ctx.call["id"])["turn_seq"], iso(ctx.now)),
        )


# What the caller already described, named back in the catch-all ("Besides the leak and the
# banging, anything else ..."), by the start of the word they used. Leak
# replay t5, 6 November: told to start "Besides the leak", the model added "like the banging"
# back after the server had taken it out of the examples.
SYMPTOMS = (("leak", "the leak"), ("bang", "the banging"), ("knock", "the banging"),
            ("clank", "the banging"), ("clang", "the banging"), ("grind", "the grinding"),
            ("squeal", "the squealing"), ("rattl", "the rattling"), ("hiss", "the hissing"),
            ("screech", "the screeching"))
# Words within three before a symptom that deny it: "it doesn't leak", "no banging at all".
DENIES = {"no", "not", "isnt", "never", "dont", "doesnt", "didnt", "without", "wasnt", "arent"}


def _symptoms_named(said: str) -> list[str]:
    heard = said.split()
    return list(dict.fromkeys(
        name for i, w in enumerate(heard) for stem, name in SYMPTOMS
        if w.startswith(stem) and not DENIES & set(heard[max(0, i - 3):i])
    ))


def _catch_all(question: str, said: str) -> str:
    """The catch-all as said: without an example the caller gave, and after what they described."""
    question = _without_said_examples(question, said)
    named = _symptoms_named(said)
    if not named or not question.startswith("Anything else"):
        return question
    return f"Besides {' and '.join(named)}, anything else{question[len('Anything else'):]}"


def _without_said_examples(question: str, said: str) -> str:
    """The catch-all question without an example the caller already gave ("I said it was
    banging", Jackson's call call_9e278a30dcdb1ff084f39a279c8): an example goes when one of its
    words, cut to its first four letters, is in what the caller said."""
    match = re.match(r"(.*?), like (.*)\?$", question)
    if not match:
        return question
    lead, examples = match[1], [e.strip() for e in match[2].split(" or ")]
    heard = {w[:4] for w in said.split() if len(w) > 3}
    kept = [e for e in examples if not any(w[:4] in heard for w in words(e).split() if len(w) > 3)]
    if not kept:
        return f"{lead}?"
    return f"{lead}, like {' or '.join(kept)}?"
