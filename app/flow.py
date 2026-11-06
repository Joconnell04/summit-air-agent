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
    ("ask_access", r"\bgate code\b|\bbuzzer\b", r"\bgate\b|\bbuzzer\b|\bdoorman\b|\bno code\b"),
)

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
# (MEASURED, evals/rounds/server-led-01-data), and 0.7 held one back.
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
    caller answered it unasked."""
    after = [t for t in _turns(ctx) if t["seq"] > _offer_seq(ctx)]
    missing = []
    for key, asked, volunteered in visit_questions(ctx, job_type):
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


UNIT_SAID = re.compile(
    r"\b(unit|apt|apartment|suite|floor)\s+\w+|#\s*\w+|\b\d+(st|nd|rd|th)\s+floor\b", re.IGNORECASE
)


def _unit_said(ctx) -> bool:
    """The caller already gave the unit ("1700 Bedford Avenue, unit 4B"): it isn't asked again."""
    if getattr(ctx, "conn", None) is None:
        return False
    return any(t["speaker"] == "caller" and UNIT_SAID.search(t["text"]) for t in _turns(ctx))


def _after_address(ctx, needs_unit: bool) -> str:
    """The questions between the address and the read-back, built from what the caller already
    said (Jackson's call call_046f37112cac691a50019893717): the unit only for a building with more
    than one home (find_address `needs_unit`); how bad only on a no-heat or no-cooling call, in
    their words when they named a symptom; the system type fit to the issue; a catch-all that
    never offers an example they already gave."""
    unit = (f"{UNIT_QUESTION}, unless they said it's a house; "
            if needs_unit and not _unit_said(ctx) else "")
    line = ctx.tenant.line
    return (
        "Ask the next of these you don't have yet, one a turn, built from what the caller already "
        "said and never asking for anything they told you: "
        f"{unit}only on a no-heat or no-cooling call, how bad it is: when they named a symptom, "
        f"in their words ('{line('ask_how_bad_symptom')}'), otherwise "
        "'Is it completely out, or just not keeping up?'; then 'Since when?'; if they haven't said "
        f"what system it is, for heating '{line('ask_system_type_heating')}', for cooling "
        f"'{line('ask_system_type_cooling')}'; when heating, cooling or airflow isn't working (not "
        "a maintenance visit or a quote), one catch-all in its own turn, never with an example "
        f"they already gave: heating '{line('ask_catch_all_heating')}', cooling "
        f"'{line('ask_catch_all_cooling')}', vents or airflow '{line('ask_catch_all_airflow')}' "
        "(after a symptom they named: 'Besides the leak, anything else ...'), with the answer in "
        "the job's issue; then 'What's the best phone number to reach you?'. Once you have the "
        "phone number, call read_back with the job type."
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
