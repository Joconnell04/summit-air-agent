"""The lookup a caller line asks for, run by the server before the model answers (issue #142).

Off by default (`voice.prefetch_lookups` in config/models.yaml, or PREFETCH_LOOKUPS). On a silent
lookup turn the model makes two requests: one writes the tool call, the next reads the result and
speaks (docs/explorations/014-latency-and-decision-models.md, idea 4). When a caller line gives a
ZIP or a house number and street, the server runs the same tool the model would call next and
hands its result back with the turn, so the model answers in one request.

Only the lookups the call order puts on that turn: `check_service_area` for a ZIP, `find_address`
for a street with no ZIP. `find_slots` is never run here. The spine reads back the address before
offering times, and `find_slots` records that the caller heard a wait and decides priority from
what the caller has said so far, so running it at the ZIP would book the wait too early and on
half the facts. Every gate stays in the tool: the prefetch calls the same handler, with the same
ZIP and address checks, and logs it as that tool. Read-back and booking still go through the
model's own tool calls.
"""

import json

from . import geocode
from .text import digit_runs, has_phrase, words

# Words a caller uses to take back an address they just gave ("sorry, I mean 248", "no wait, that
# was wrong"). Only read on the line right after a prefetch, together with an address word.
CORRECTION_PHRASES = (
    "wrong", "not right", "not correct", "i mean", "i meant", "actually", "sorry", "correction",
    "my mistake", "misspoke", "scratch that", "hold on", "no wait", "thats not",
)
ADDRESS_WORDS = ("zip", "address", "street", "avenue", "apartment", "building")


def zip_in(text: str, area: dict, zip_boroughs: dict) -> str | None:
    """The one ZIP a caller line gives, or None. A five-digit run counts if the company serves it
    or it is in the lookup's area, as `tools._plausible_zip` reads it, and isn't the house number
    of an address in the same line ("21230 41st Avenue"). Two different ZIPs in one line ("11231,
    no, 11215") are a caller still deciding, so neither is looked up."""
    houses = {geocode.house_key(house) for house, _ in geocode.addresses_in(text)}
    prefixes = {str(p) for p in zip_boroughs}
    found = set()
    for run in digit_runs(text):
        if len(run) == 9:
            run = run[:5]
        if len(run) != 5 or run in houses:
            continue
        if run in area or run[:3] in prefixes:
            found.add(run)
    return found.pop() if len(found) == 1 else None


def street_in(text: str) -> str | None:
    """The one house number and street a caller line gives, as find_address takes it, or None."""
    found = geocode.addresses_in(text)
    if len(found) != 1:
        return None
    house, street = found[0]
    return f"{house} {street}"


def looked_up(conn, call_id: str) -> list[dict]:
    """This call's area and address lookups that answered ok, from the tool log, oldest first."""
    rows = conn.execute(
        "SELECT tool, args_json FROM tool_calls WHERE call_id = ? AND ok = 1 "
        "AND tool IN ('check_service_area', 'find_address') ORDER BY id",
        (call_id,),
    ).fetchall()
    return [{"tool": r["tool"], **json.loads(r["args_json"] or "{}")} for r in rows]


def pick(conn, tenant, call, text: str, data: dict) -> tuple[str, dict] | None:
    """The tool and arguments this caller line asks for, or None. `call` is the row as the turn
    left it; `data` is the turn's result. Nothing on a safety call, a line the voice layer speaks
    for (the card stop, billing, electrical lines, a handoff), a line said to someone else, or a
    commercial call, which declines first. A ZIP or street already checked on this call isn't
    looked up again, and no street is looked up once any ZIP has been."""
    if call["safety_state"] not in ("none", "cleared") or data.get("safety"):
        return None
    if data.get("spoken") or data.get("handoff") or data.get("hold"):
        return None
    if call["commercial"] or "commercial" in data.get("hints", []):
        return None
    done = looked_up(conn, call["id"])
    lookup_cfg = tenant.address_lookup or {}
    zip_code = zip_in(text, tenant.service_area, lookup_cfg.get("zip_boroughs") or {})
    if zip_code:
        if any(d["tool"] == "check_service_area" and d.get("zip") == zip_code for d in done):
            return None
        return "check_service_area", {"call_id": call["id"], "zip": zip_code}
    street = street_in(text)
    if street and not done:
        return "find_address", {"call_id": call["id"], "street": street, "borough": ""}
    return None


def taken_back(conn, call, text: str) -> bool:
    """The caller line right after a prefetched lookup takes back the address it was for ("sorry,
    I mean 248 Duffield"), so the model is told not to rely on it. A line with a new ZIP or street
    gets its own lookup instead, which replaces the earlier one."""
    last = conn.execute(
        "SELECT args_json FROM tool_calls WHERE call_id = ? AND request_id LIKE 'prefetch-%' "
        "ORDER BY id DESC LIMIT 1",
        (call["id"],),
    ).fetchone()
    if not last:
        return False
    seq = json.loads(last["args_json"]).get("turn_seq")
    previous = conn.execute(
        "SELECT MAX(seq) FROM turns WHERE call_id = ? AND speaker = 'caller' AND seq < ?",
        (call["id"], call["turn_seq"]),
    ).fetchone()[0]
    if seq is None or seq != previous:
        return False
    line = words(text)
    return any(has_phrase(line, p) for p in CORRECTION_PHRASES) and any(
        has_phrase(line, w) for w in ADDRESS_WORDS
    )


def logged_args(args: dict, seq: int) -> dict:
    """What the tool log keeps for a prefetched lookup: the tool's own arguments, so every reader
    of the log (already_known, the trace, the owner view) sees the lookup it is, plus who ran it
    and on which caller line."""
    return {**args, "by": "server", "turn_seq": seq}


def note(tool: str, args: dict, body: dict) -> dict:
    """The lookup as the turn result carries it to the voice layer (prompts/assemble.py)."""
    shown = {k: v for k, v in args.items() if k != "call_id"}
    return {"tool": tool, "args": shown, "result": body}
