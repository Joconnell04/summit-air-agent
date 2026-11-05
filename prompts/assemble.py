"""Builds what the model sees on a call from the files in this folder, so every voice layer runs
the same conversation: the instructions (the prompt files plus the call context the server returned
from /calls/start), the tool list (descriptions from tools.yaml, arguments from the tool server's
own request models), the greeting, and the `[server]` notes added to caller lines."""

import copy
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from app.tools import TOOLS

PROMPTS = Path(__file__).resolve().parent
# Order matters: who you are, how every call goes, the journeys, the tools, then safety last so it
# sits closest to the call context.
PARTS = ("identity.md", "spine.md", "journeys.md", "tool-use.md", "safety.md")
# The owner's opening and whole-call guidance (app/rules.py) goes after the journeys, under a fixed
# preamble that says it is soft. Booking and closing guidance is not in the prompt: the server sends
# it in a [server] note when it sees the call reach that stage (server_notes). A call with no
# guidance gets the same prompt as before.
HOUSE_GUIDANCE = "house-guidance.md"
HOUSE_AFTER = "journeys.md"
STAGE_TITLES = {"opening": "Opening", "whole_call": "Whole call"}
# Said with the owner's notes, which are soft even though they arrive as a [server] note.
STAGE_NOTES = {
    "stage_booking": (
        "The call has reached booking. The owner's notes for this stage, to follow where they fit, "
        "never over a rule, a tool's answer or the safety lines:"
    ),
    "stage_closing": (
        "The call is closing. The owner's notes for this stage, to follow where they fit, never "
        "over a rule, a tool's answer or the safety lines:"
    ),
}


def instructions(start: dict, now: datetime | None = None) -> str:
    """The system prompt for one call. `start` is the data of the /calls/start result."""
    return "\n\n".join(text for _name, text in sections(start, now))


def sections(start: dict, now: datetime | None = None) -> list[tuple[str, str]]:
    """The system prompt for one call as (name, text) parts, in the order they are joined: the
    prompt files, the owner's guidance after the journeys when there is some, the company
    knowledge, then this call. The bench's Rules page shows these, so it can't drift from a call."""
    tenant = start["tenant"]
    parts = [(p, (PROMPTS / p).read_text().strip()) for p in PARTS]
    house = house_guidance((start.get("house_rules") or {}).get("guidance") or {})
    if house:
        parts.insert(PARTS.index(HOUSE_AFTER) + 1, (HOUSE_GUIDANCE, house))
    parts = [(name, text.replace("{company}", tenant["name"])) for name, text in parts]
    local = (now or datetime.now(ZoneInfo(tenant["timezone"]))).astimezone(
        ZoneInfo(tenant["timezone"])
    )
    knowledge = "\n\n".join(body.strip() for body in start.get("knowledge", {}).values())
    context = {
        k: v for k, v in start.items() if k not in ("knowledge", "tenant", "call_id", "house_rules")
    }
    notes = call_notes(start)
    noted = "".join(f"- {n}\n" for n in notes)
    return [
        *parts,
        ("Company knowledge", f"# Company knowledge\n\n{knowledge}"),
        (
            "This call",
            f"# This call\n\nNow: {local:%A %-d %B %Y, %-I:%M %p} ({tenant['timezone']}).\n\n"
            + (f"For this caller:\n{noted}\n" if notes else "")
            + f"What the server found before hello:\n{json.dumps(context, default=str)}\n",
        ),
    ]


def call_notes(start: dict) -> list[str]:
    """Plain lines for what the call context means for this caller, so the agent acts on it
    rather than finding it in the JSON (eval run 20261104-174038: a member was never thanked, and
    a caller moving a visit was booked a second one first)."""
    customers = [c for c in start.get("customers", []) if c.get("residential")]
    notes = []
    if len(customers) == 1:
        membership = customers[0].get("membership") or {}
        if membership.get("status") == "Active":
            plan = membership.get("plan") or "maintenance"
            notes.append(
                f"They're on an active {plan} plan. Thank them for being a member once, early in "
                "the call, and mention the plan's benefit that applies."
            )
    visits = [
        job for c in customers for job in c.get("open_jobs") or [] if job.get("window")
    ]
    upcoming = [v for v in visits if not v["window"].get("passed")]
    # A visit still open after its window ended is not coming up (a test call at 2 PM heard
    # "you're all set for today between eight and twelve").
    passed = [v for v in visits if v["window"].get("passed")]
    if upcoming:
        when = ", ".join(f"{v['window']['weekday']} {v['window']['label']}" for v in upcoming)
        notes.append(
            f"They already have a visit booked ({when}). To move it, call reschedule_appointment "
            "for that visit, never book_appointment; book a new visit only for a different problem "
            "the caller confirms. Moving their own visit needs no read-back: don't ask them to "
            "confirm the address or number, just offer times and move it."
        )
    if passed:
        when = ", ".join(
            f"{v['window']['weekday']} {v['window']['label']}, job {v['job_id']}" for v in passed
        )
        notes.append(
            f"The arrival window of their open visit ({when}) has already ended, and the visit is "
            "still open. Never call it upcoming or say they're all set for it. Ask whether the "
            "technician came, then offer a new time by moving that visit with "
            "reschedule_appointment (its job as booking_id), never book_appointment, or to have "
            "the team check what happened with flag_person."
        )
    return notes


def house_guidance(guidance: dict[str, list[str]]) -> str:
    """The owner's guidance as a prompt section, by stage, or "" when there is none."""
    sections = [
        f"## {title}\n\n" + "\n".join(f"- {item}" for item in guidance[stage])
        for stage, title in STAGE_TITLES.items()
        if guidance.get(stage)
    ]
    if not sections:
        return ""
    return "\n\n".join([(PROMPTS / HOUSE_GUIDANCE).read_text().strip(), *sections])


def tool_specs(job_types: list[str]) -> list[dict]:
    """One entry per model tool: name, description, JSON-schema parameters and whether it writes.
    `call_id` is left out; the voice layer adds it to every request. `job_types` are the tenant's
    job type codes, so the model can only name one the company has."""
    descriptions = yaml.safe_load((PROMPTS / "tools.yaml").read_text())
    specs = []
    for name, (_handler, model, write) in TOOLS.items():
        schema = _inline(model.model_json_schema())
        schema["properties"].pop("call_id", None)
        if "job_type" in schema["properties"]:
            schema["properties"]["job_type"]["enum"] = job_types
        schema["required"] = [r for r in schema.get("required", []) if r != "call_id"]
        specs.append(
            {
                "name": name,
                "description": descriptions[name],
                "parameters": schema,
                "write": write,
            }
        )
    return specs


def first_name(full_name: str) -> str:
    return full_name.split()[0] if full_name.strip() else ""


def spoken(start: dict, voice: dict) -> dict:
    """The tenant's `voice` lines with the owner's live edits for this call (greeting, goodbye)
    laid over them, as /calls/start returned them."""
    return {**voice, **((start.get("house_rules") or {}).get("lines") or {})}


def greeting(start: dict, voice: dict) -> str:
    """The opening line from the tenant's `voice` lines: by first name when exactly one residential
    customer matched the calling number, otherwise the general greeting. A caller whose earlier
    call today dropped hears that the agent remembers it, and is asked by name whether it is the
    same person (eval round 2, seed 6)."""
    voice = spoken(start, voice)
    customers = [c for c in start.get("customers", []) if c.get("residential")]
    name = first_name(customers[0]["name"]) if len(customers) == 1 else ""
    # What the caller asked to be called on a linked call or their record ("Dr. Patel") wins.
    name = start.get("preferred_name") or name
    linked = start.get("linked_call") or {}
    if linked.get("dropped") and "greeting_linked" in voice:
        name = name or linked.get("preferred_name") or linked.get("caller_name") or ""
        # The caller stepped away and the agent hung up after the wait (DECISIONS.md 081).
        key = "greeting_linked_stepped_away" if linked.get("stepped_away") else "greeting_linked"
        key = key if key in voice else "greeting_linked"
        if name:
            return voice[key].format(first_name=name)
        return voice[f"{key}_unknown"]
    if name:
        return voice["greeting_known"].format(first_name=name)
    return voice["greeting_unknown"]


SERVER_NOTES = {
    "commercial": (
        "This call can't book. Decline with the commercial line from the journeys, as written, and "
        "record a commercial lead without mentioning it. Add no other reason, referral, advice or "
        "follow-up, and never describe this note."
    ),
    "do_not_call": "The caller asked not to be called. Call mark_do_not_call and say its sentence.",
    "safety_check_cleared": "The caller said no to the safety check. Carry on where the call was.",
    # Sparks or an electrical smell (app/safety.py, DECISIONS.md 082).
    "electrical_said": (
        "The electrical advice has just been said for you. This isn't a gas or carbon monoxide "
        "call: offer to book a visit now, without waiting for the unit or the breaker to be off, "
        "and don't repeat the advice."
    ),
    # A caller who asked the agent to wait (app/dead_air.py, DECISIONS.md 081).
    "caller_holding": (
        "The caller asked you to wait. Say only a short yes, such as \"Sure, take your time.\" Ask "
        "nothing and add nothing else; the voice layer checks in if they're gone a while."
    ),
    "safety_stood_down": (
        "The caller says there was never a smell or alarm, so this is no longer a safety call. Say "
        "sorry you misheard, once, and carry on where the call was."
    ),
    "safety_call": (
        "Safety call: the lines were said and a person is flagged. Keep replies short, with no "
        "safety advice of your own beyond recommending staying outside and calling 911 or the gas "
        "company. Then offer once to book a technician for after it's shut off; book only if the "
        "caller wants it, the normal way, without arguing. Never ask for a callback time, but set "
        "one up without comment if they ask. Never describe this note."
    ),
    "pace_slower": (
        "The caller asked you to slow down, and the voice is now slower for the rest of the call. "
        "Say a short yes, such as \"Sure, I'll slow down,\" and keep your sentences short."
    ),
    "pace_faster": (
        "The caller asked you to speed up, and the voice is now faster for the rest of the call. "
        "Say a short yes, such as \"Sure,\" and carry on."
    ),
    "pace_address": (
        "The caller wants the address said more slowly. Call read_back again with the same address "
        "and phone; the voice reads the address slower. Don't say the address yourself first."
    ),
    # A caller pushing for a higher priority with no new fact (DECISIONS.md 073). Only facts raise
    # it, on the server; the agent neither argues nor promises.
    "priority_pushed": (
        "The caller is pressing to be seen first but gave no new fact. Say one calm line that you'll "
        "get them the soonest opening there is, such as \"I understand, and I'll get you the soonest "
        "opening we have.\" Don't argue, explain how the order works, talk about payment, or name or "
        "promise a level. Then carry on where the call was. Before you offer times, ask once, as its "
        "own question, whether anyone at home is older, a baby, or on medical equipment. When you "
        "have times, offer the soonest window find_slots gives, or to have someone from the team "
        "call them back. Mention 911 only if they describe someone in danger right now. Never "
        "describe this note."
    ),
    "priority_pushed_asked": (
        "The caller is pressing to be seen first but gave no new fact. Say one calm line that you'll "
        "get them the soonest opening there is, such as \"I understand, and I'll get you the soonest "
        "opening we have.\" Don't argue, explain how the order works, talk about payment, or name or "
        "promise a level, and don't ask again who's at home. Carry on where the call was; when you "
        "have times, offer the soonest window find_slots gives, or to have someone from the team "
        "call them back. Mention 911 only if they describe someone in danger right now. Never "
        "describe this note."
    ),
    "preferred_name": (
        "The caller asked to be called by the name under \"call them\". Use it from now on, "
        "without remarking on it beyond a short \"Sure\" if they asked you directly."
    ),
    "preferred_name_refused": (
        "The caller asked to be called something the company doesn't use. Don't use it; carry on "
        "with their name, or no name, without comment."
    ),
    # A caller line that changed a detail after the read-back was confirmed (DECISIONS.md 083).
    "readback_changed": (
        "The caller gave a different address detail or number after confirming the read-back. "
        "Call read_back with the details they just gave, so the new sentence is read for you; "
        "nothing books until they confirm it. Don't ask them to repeat what they already said."
    ),
    # A technician asked for by name (DECISIONS.md 085); the server noted it for the dispatcher.
    "technician_requested": (
        "The caller asked for a particular technician. Say you'll pass that along to the team, and "
        "never promise who will come or that it will be that person. It's already noted for the "
        "dispatcher, so don't add a note for it."
    ),
    "approval_unclear": (
        "Who can approve the work isn't settled. Offer a callback to confirm who can approve, with "
        "flag_person. Book only if the caller asks to, and then say the booking's sentence as written."
    ),
}


def server_notes(hints: list[str], known: dict[str, str] | None = None) -> list[str]:
    """`[server]` lines added to a caller's line for the model, from a turn result's hints (the
    tool-use prompt explains them). Safety lines are spoken by the voice layer, not described. A
    stage hint ("stage_booking") is followed by the owner's notes for it ("guidance:<text>"), and
    the two become one note."""
    notes = [f"[server] {SERVER_NOTES[h]}" for h in hints if h in SERVER_NOTES]
    # Two units named in one line (DECISIONS.md 085).
    for units in (h.removeprefix("units:") for h in hints if h.startswith("units:")):
        notes.append(
            f"[server] The caller named more than one unit ({units.replace(',', ', ')}). Each "
            "unit is its own job: read back and book one unit at a time, with that unit alone in "
            "the read-back, then the next. Never book one visit to cover several units."
        )
    # Work the company doesn't do (DECISIONS.md 084): the server's own sentence, said once.
    for line in (h.removeprefix("not_offered:") for h in hints if h.startswith("not_offered:")):
        notes.append(
            "[server] The caller asked for work the company doesn't do. Say this once, as written: "
            f"\"{line}\" Book nothing for that work and add no other referral. If they also have "
            "a heating or cooling problem, carry on with that as usual."
        )
    stage = next((h for h in hints if h in STAGE_NOTES), None)
    items = [h.removeprefix("guidance:") for h in hints if h.startswith("guidance:")]
    if stage and items:
        notes.append(f"[server] {STAGE_NOTES[stage]} " + " ".join(f"- {i}" for i in items))
    if known:
        facts = "; ".join(f"{k} {v}" for k, v in known.items())
        notes.append(f"[server] Already known, don't ask again: {facts}.")
    return notes


# The model saying it is about to do something ("I'll look up the visit now", "Let me find that",
# "One moment"). In a reply with no tool call it is a dead end: the caller waits on work that never
# starts (live call 1, issue #149: "Got it." then 19 s of silence).
PROMISES_ACTION = re.compile(
    r"\b(?:let me|i'?ll|i will|i'?m going to|i am going to)\s+(?:just\s+)?(?:go ahead and\s+)?"
    r"(?:look|check|find|pull|search|cancel|book|move|reschedule|set|put|get|send|update|"
    r"take care|verify|grab|request|let (?:the|our) team)\b"
    r"|\b(?:one|a|just a) (?:moment|sec)\b|\b(?:one|just a) second\b|\bgive me a (?:moment|sec|second)\b"
    r"|\bbear with me\b|\bhold on\b",
    re.IGNORECASE,
)
# Look-ups whose answer needs a second model step before the caller hears anything. A reply that
# calls one with nothing said first gets a bridge clip at once (voice/livekit/bridge.py lead_in).
# Bookings, moves and cancels are left out: the server's confirmation is spoken the moment the tool
# answers, so a clip would only push it back.
LOOKUPS = ("find_customer", "get_customer_history", "find_slots")
# A sentence that only acknowledges the caller ("Got it.", "Yes.", "Sure, I can set up an AC
# tune-up."). A reply of nothing else, with no tool call and no question, leaves the caller waiting
# for the agent's next step (the second live call, call-_+16784774024_MXJ5xsvpZQ5H: 20 s of silence
# after each of those two lines).
ONLY_ACKNOWLEDGES = re.compile(
    r"(?:(?:sure|okay|ok|got it|yes|yeah|yep|alright|all right|of course|absolutely|no problem|"
    r"great|perfect|understood|sounds good|right)[,.!]?\s*)+(?:i can [^.?!]*[.!]?)?"
    r"|i can (?:do|set up|help with|book|get|take care of) [^.?!]*[.!]?",
    re.IGNORECASE,
)
# What the voice layer asks the model for when a reply would leave the caller waiting in silence.
FOLLOW_UP_NOTES = {
    "promised": (
        "[server] You said you would do something but called no tool, so nothing is happening and "
        "the caller is waiting. Call the tool now. Say nothing before it."
    ),
    "silent": (
        "[server] The caller heard nothing from your last reply. Answer them now in one short "
        "sentence, without repeating their address, ZIP or phone number."
    ),
    "stalled": (
        "[server] Your last reply only acknowledged the caller, and they are waiting on you. Take "
        "the next step now: call the tool it needs, or ask your next question."
    ),
}


# A sentence that announces the read-back ("let me read that back", "I'll read back the details",
# "let me confirm your address and number"). The server's own sentence says it; the caller hears
# it once (eval round 3).
ANNOUNCES_READ_BACK = re.compile(
    r"\bread(?:\s+\w+){0,3}\s+back\b|\bread-back\b"
    r"|\b(?:let me|i'?ll|i will|i need to|i have to|i'?m going to)\s+confirm\s+(?:\w+\s+){0,2}"
    r"(?:details|address|number)",
    re.IGNORECASE,
)
# The model describing its own tools ("The tool says it's marked top priority"). Never said to a
# caller (eval round 4, burning-dust-first-heat).
DESCRIBES_TOOLS = re.compile(
    r"\b(?:the|my|our) (?:tool|tools|system|server|records?)(?:'s)?\s+"
    r"(?:says?|said|shows?|showed|marked|returned|gave|is giving|tells? me|flagged|came back)\b",
    re.IGNORECASE,
)
# Talk about a tool's run or its result ("The reschedule ran without a confirmation message", "Let
# me check the appointment again"), from a scripted test call after #135. The caller only ever
# hears what the call means for them.
TOOL_TALK = re.compile(
    r"\bran without\b|\bconfirmation message\b|\bwithout a confirmation\b"
    r"|\b(?:let me|i'?ll|i need to) (?:check|look at|pull up|verify) (?:the|your) "
    r"(?:appointment|booking|visit|reschedule|request) again\b"
    r"|\b(?:the|my|that) (?:reschedule|rescheduling|booking|move|cancel(?:lation)?|request|update|"
    r"change|lookup|call) (?:ran|didn'?t (?:return|come back|confirm)|returned|came back|went "
    r"through without|failed to (?:return|confirm))\b"
    r"|\bi can'?t confirm the new time yet\b"
    # Live call 1 (issue #149): "I haven't cancelled anything yet, because I need to find the
    # booking first", and "I'll cancel it now" said before the name check had passed.
    r"|\bi haven'?t (?:cancel+ed|moved|rescheduled|changed|booked) anything\b"
    r"|\bi (?:need|have) to (?:find|locate|look up|pull up) (?:the|your) "
    r"(?:booking|visit|appointment|record|account)\b"
    r"|\b(?:i'?ll|i will|i'?m going to|let me)\s+(?:go ahead and\s+)?(?:cancel|reschedule|move)\s+"
    r"(?:it|that|this|the (?:visit|appointment|booking|tune-?up)|your (?:visit|appointment|booking))\b",
    re.IGNORECASE,
)
# A question confirming the address or the number ("Is 142 Main Street still the right address?",
# "And the number ending in 0418, the best to reach you?"). The server's read-back confirms them,
# and a caller's own visit is moved with no read-back at all (the same test call).
ASKS_TO_CONFIRM = re.compile(
    r"\bis\b[^.?!]{0,50}\b(?:still )?(?:the )?(?:right|correct) (?:address|number|place)\b[^.?!]*\?"
    r"|\bnumber ending in\b[^.?!]*\?"
    r"|\bstill the (?:best|right|correct)\b[^.?!]*\?",
    re.IGNORECASE,
)
# The model working out the date aloud ("Today is Wednesday, so Friday is two days from now"):
# the server's sentence names the day plainly (live call 1, issue #149).
EXPLAINS_CALENDAR = re.compile(
    r"\btoday is (?:a )?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r"|\b(?:is|are|that'?s) (?:one|two|three|four|five|six|\d) days? (?:from now|from today|away)\b",
    re.IGNORECASE,
)
# Coaching on how to care for someone ("stay with your partner", "keep them sitting up"). The
# agent's safety job is to escalate politely, not to coach (DECISIONS.md 033); dropped on every
# call (eval run 20261104-232053, asks-agent-to-call-911).
COACHING = re.compile(
    r"\bstay (?:with|beside|next to|close to) (?:your|the|him|her|them)\b"
    r"|\bkeep (?:him|her|them|your \w+|the \w+) (?:calm|still|sitting|lying|talking|warm|awake|"
    r"comfortable|upright|breathing)\b"
    r"|\b(?:sitting|sit (?:him|her|them)) up\b|\blie (?:him|her|them )?down\b|\blay (?:him|her|them) down\b"
    r"|\bkeep talking to (?:him|her|them)\b|\bkeep an eye on (?:him|her|them|your \w+)\b"
    r"|\bmake sure (?:he|she|they)(?:'s|'re| is| are)? (?:breathing|comfortable|warm|sitting)\b",
    re.IGNORECASE,
)
# A word from inside the company ("I'll flag this for our team"). The server's own sentences say
# "I've let our team know" (eval run 20261104-053435, complaint-flag).
INTERNAL_WORD = re.compile(r"\bflag(?:s|ged|ging)?\b", re.IGNORECASE)
# A fee or price the server didn't give ("There's no charge for the visit itself"): the company
# knowledge says the technician goes over costs (eval run 20261104-052332, co-op-board-asks-to-book).
CLAIMS_FEE = re.compile(
    r"\bno (?:charge|fee|cost)\b|\bfree of charge\b|\bfree (?:visit|service call|estimate|diagnostic)"
    r"|\bwon'?t be charged\b|\bdoesn'?t cost\b|\$\s?\d|\b\d+ dollars\b",
    re.IGNORECASE,
)
# Carbon monoxide talk. Only the server's check question and safety lines raise it; on a call with
# no safety signal the model's own is dropped (eval round 4, no-heat-gas-stove).
CARBON_MONOXIDE = re.compile(r"\bcarbon monoxide\b|\bco (?:alarm|detector)\b", re.IGNORECASE)
# A priority claim. Only the server's own Urgent line may say it (eval round 4).
# The model saying it raised the call itself ("I've marked it as a priority", "I'll put you first")
# is a claim too (eval run 20261104-173249, adds-fact-after-wait). A sentence saying it can't is kept.
CLAIMS_PRIORITY = re.compile(
    r"\btop[- ]priority\b|\bmost urgent\b|\bhighest[- ]priority\b"
    r"|\b(?:i|we)(?:'ve| have|'ll| will|'m| am)\s+(?:now\s+|also\s+)?"
    r"(?:mark(?:ed|ing)?|put(?:ting)?|flag(?:ged|ging)?|list(?:ed|ing)?)\s+"
    r"(?:it|this|you|the call|your call|the job)\b[^.?!]*?\b(?:priority|urgent|an emergency|first)\b"
    r"|\b(?:i|we)(?:'ve| have|'ll| will|'m| am)\s+(?:mov(?:ed|ing)|bump(?:ed|ing)?)\s+(?:you|it|this) up\b",
    re.IGNORECASE,
)
# A promise of who the technician will be ("Mike will be the one coming out", "I'll make sure it's
# Mike"). The dispatcher decides; the agent only passes the request on (DECISIONS.md 085). "I'll pass
# that along" and "I can't promise who comes" are kept.
NAME = (
    r"(?!(?:Someone|Somebody|They|He|She|It|We|You|That|This|There|Our|Your|The|A|An|Anyone|"
    r"Summit|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Today|Tomorrow|Everyone|"
    r"One|Nobody|Whoever)\b)[A-Z][a-z]+"
)
# A dispatch word paired with a person's name ("I'll send Mike", "Mike's on his way", "You'll get
# Mike", "It'll be Mike", "I've put Mike on your job"), whatever the caller asked for: the
# dispatcher decides, so the agent never says who is coming (PR #135 re-check).
PROMISES_TECHNICIAN = re.compile(
    rf"\b{NAME} (?:will|'ll) be (?:the one|your technician|the technician|coming|there|out)\b"
    rf"|\b{NAME}(?:'s| is) (?:on (?:his|her|their|the) way|coming|heading|the one)\b"
    rf"|\b{NAME} (?:will|'ll|can|is going to) (?:take care of|come|head|stop by|be by)\b"
    rf"|\b(?i:send|sending|you'll get|you will get|it'll be|it will be|i'll have|we'll have)\s+{NAME}\b"
    r"|\b(?i:i|we)(?:'ll| will)\s+(?:make sure|ensure|guarantee|see to it)\s+(?:that\s+)?"
    rf"(?:it'?s {NAME}|it is {NAME}|you get {NAME}|{NAME} (?:comes|is the one))\b"
    rf"|\b(?i:i|we)(?:'ve| have)\s+(?:assigned|booked|scheduled|put|got)\s+{NAME}\b",
)
# A sentence that passes a technician request on, or says it can't be promised: the one way the
# agent may name the technician the caller asked for.
PASSES_ON = re.compile(
    r"\bpass(?:ing)? (?:that|it|this|the request)? ?(?:along|on)\b"
    r"|\blet (?:the|our) team know\b"
    r"|\b(?:can'?t|cannot|can not|won'?t be able to|not able to|unable to|no way to) "
    r"(?:promise|guarantee)\b|\bno (?:promise|guarantee)s?\b",
    re.IGNORECASE,
)
# Gas or carbon monoxide advice to leave or call 911. Only the server's safety lines say it; on
# a call with no safety signal the model's own is dropped (eval round 4, gas-worry-no-smell).
HAZARD = re.compile(r"\bgas\b|\bsmell|\bcarbon monoxide\b|\bfumes\b|\bco alarm\b", re.IGNORECASE)
EVACUATE = re.compile(
    r"\b(?:get|step|head|go)\s+(?:everyone\s+)?out(?:side)?\b|\bleave the (?:house|home|apartment)\b"
    r"|\b911\b|\bgas company\b|\bevacuat",
    re.IGNORECASE,
)
ORDINAL = re.compile(r"^\d+(?:st|nd|rd|th)$")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# A question that only asks about the sentence before it: dropped with a dropped restatement, so
# the caller never hears a bare "Is that right?" (eval round 3 run 20261104-031113).
CONFIRMS_ONLY = re.compile(
    r"\s*(?:is that (?:right|correct)|(?:is )?that (?:right|correct)|correct|right)\s*\?\s*",
    re.IGNORECASE,
)
STREET_WORDS = {
    "street", "st", "avenue", "ave", "road", "rd", "boulevard", "blvd", "place", "pl", "lane",
    "ln", "court", "ct", "drive", "dr", "parkway", "pkwy", "terrace", "way", "broadway",
}
DIGIT_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


def sentences(text: str) -> list[str]:
    return [s for s in SENTENCE_END.split(text) if s.strip()]


def caller_digits(text: str) -> tuple[set[str], set[str]]:
    """The ZIPs and phone numbers in a caller's line, from digits said one by one ("one one two
    three four") or written as digits, as the transcription gives them."""
    zips, phones = set(), set()
    for digits, after in _digit_runs(text):
        first = after[0] if after else ""
        if ORDINAL.match(first) or first in STREET_WORDS:
            continue  # a house number before its street, "one eight eight 10 64th avenue"
        if len(digits) == 5:
            zips.add(digits)
        elif len(digits) == 10:
            phones.add(digits)
        elif len(digits) == 11 and digits.startswith("1"):
            phones.add(digits[1:])
    return zips, phones


def caller_streets(text: str) -> set[tuple[str, str]]:
    """House numbers the caller said with the first word of the street name, when what follows
    reads as a street ("one eight five seven 77th street" gives ("1857", "77th")), so "64 degrees"
    is not an address."""
    streets = set()
    for digits, after in _digit_runs(text):
        first, second = (after + ["", ""])[:2]
        if 1 <= len(digits) <= 5 and first and (
            ORDINAL.match(first) or first in STREET_WORDS or second in STREET_WORDS
        ):
            streets.add((digits, first))
    return streets


def _digit_runs(text: str) -> list[tuple[str, list[str]]]:
    """Each run of digits in a caller's line, with the two words that follow it. An ordinal
    ("77th") is a word here, so a street name ends a run."""
    tokens = re.findall(r"\d+(?:st|nd|rd|th)\b|[a-z]+|\d+", text.lower())
    runs, run = [], ""
    for i, token in enumerate(tokens):
        if token.isdigit():
            run += token
        elif token in DIGIT_WORDS and not ORDINAL.match(token):
            run += DIGIT_WORDS[token]
        else:
            if run:
                runs.append((run, tokens[i : i + 2]))
            run = ""
    if run:
        runs.append((run, []))
    return runs


class SpokenReply:
    """What of the model's reply the caller hears, kept the same in every voice layer and the eval
    harness. The caller hears the address and number read back once, in the server's sentence
    (eval round 3):

    - Some tool results are spoken by the voice layer itself, word for word: the read-back
      sentence, and on a safety call the sign-off end_call returns. In a reply that calls one of
      those, the text the model wrote first is dropped: no lead-in, no second goodbye.
    - In any reply, a sentence announcing the read-back is dropped, and so is a sentence that
      restates the ZIP or phone number the caller gave. The rest of the reply is kept; a reply
      with nothing left is silent.

    A streaming voice layer can only drop text it hasn't spoken yet. It passes each sentence
    through `sentence` as it completes, and while `holding` it keeps the whole reply until it
    ends. That is from the first in-area ZIP check, when a read-back can come, until the
    read-back is confirmed, and for the whole of a safety call."""

    def __init__(self) -> None:
        self.address_checked = False
        self.read_back_confirmed = False
        self.safety_call = False
        self.zips: set[str] = set()
        self.phones: set[str] = set()
        self.streets: set[tuple[str, str]] = set()
        self.urgent = False
        self.urgent_spoken = False
        self.advised = False
        self._dropped_restatement = False
        self._asked = False
        # A follow-up was asked for since the caller last spoke: at most one a turn.
        self._followed_up = False
        # Technicians the caller asked for by name (DECISIONS.md 085).
        self.technicians: set[str] = set()

    def holding(self) -> bool:
        return self.safety_call or (self.address_checked and not self.read_back_confirmed)

    def heard(self, safety: dict | None, caller_text: str = "", hints: list[str] | None = None) -> None:
        """A caller line and its turn result: the safety action the server returned, if any, and
        its hints (a technician the caller asked for by name)."""
        for hint in hints or []:
            if hint.startswith("technician:"):
                self.technicians.add(hint.removeprefix("technician:"))
        zips, phones = caller_digits(caller_text)
        self.zips |= zips
        self.phones |= phones
        self.streets |= caller_streets(caller_text)
        self._followed_up = False
        action = (safety or {}).get("action")
        if action in ("speak_lines", "speak_variant"):
            self.safety_call = True
        elif action == "cleared":
            self.safety_call = False

    def tool_done(self, name: str, envelope: dict) -> None:
        data = envelope.get("data") or {}
        if name in ("check_service_area", "find_address") and envelope.get("ok"):
            self.address_checked = True
        if name == "find_slots" and (data.get("priority") or {}).get("level") == "Urgent":
            self.urgent = True
        if data.get("spoken_first"):
            # The server's own line was spoken; the model's version of it is never said too.
            self.urgent_spoken = True
        if name == "read_back" and data.get("confirmed"):
            self.read_back_confirmed = True
            # Confirmed details are said once, in the server's sentence: never again by the model.
            address = data.get("address") or {}
            if address.get("zip"):
                self.zips.add(address["zip"])
            if data.get("phone"):
                self.phones.add(re.sub(r"\D", "", data["phone"]))
            street = re.match(r"(\d[\d-]*)\s+(\S+)", address.get("street") or "")
            if street:
                self.streets.add((street.group(1), street.group(2).lower()))

    def server_speaks(self, name: str, args: dict) -> bool:
        """Decided from the call alone, before its result: a streaming layer must choose then."""
        if name == "read_back":
            return not args.get("confirmed")
        return name == "end_call" and self.safety_call

    def start_reply(self) -> None:
        self._dropped_restatement = False
        self._asked = False

    def sentence(self, sentence: str) -> str:
        """One sentence of the model's text, in order within a reply, or "" when the caller
        shouldn't hear it."""
        after_restatement, self._dropped_restatement = self._dropped_restatement, False
        if ANNOUNCES_READ_BACK.search(sentence) or DESCRIBES_TOOLS.search(sentence):
            return ""
        if TOOL_TALK.search(sentence) or ASKS_TO_CONFIRM.search(sentence) or COACHING.search(sentence):
            return ""
        if INTERNAL_WORD.search(sentence) or CLAIMS_FEE.search(sentence):
            return ""
        if EXPLAINS_CALENDAR.search(sentence):
            return ""
        promised = PROMISES_TECHNICIAN.search(sentence) and not PASSES_ON.search(sentence)
        if promised or self._names_technician(sentence):
            return ""
        if self.safety_call and EVACUATE.search(sentence):
            # The caller stays in control: the agent's own recommendation to stay out or call 911
            # is said once on the call, after the server's lines (eval run 20261104-052332,
            # co-alarm-clear).
            if self.advised:
                return ""
            self.advised = True
        if not self.safety_call and CARBON_MONOXIDE.search(sentence):
            return ""
        if (not self.urgent or self.urgent_spoken) and CLAIMS_PRIORITY.search(sentence):
            return ""
        if not self.safety_call and HAZARD.search(sentence) and EVACUATE.search(sentence):
            return ""
        if after_restatement and CONFIRMS_ONLY.fullmatch(sentence):
            return ""
        if self._restates(sentence):
            self._dropped_restatement = True
            return ""
        # One question a reply: a second separate question waits for the caller's answer to the
        # first (eval round 4). A statement after it, such as "No worries if not.", stays.
        if sentence.rstrip().endswith("?"):
            if self._asked:
                return ""
            self._asked = True
        return sentence

    def _names_technician(self, sentence: str) -> bool:
        """A sentence naming a technician the caller asked for is a promise, whatever its words
        ("I'll send Mike", "It'll be Mike"), unless it passes the request on or says it can't be
        promised (PR #135 review)."""
        named = any(
            re.search(rf"\b{re.escape(name)}\b", sentence, re.IGNORECASE)
            for name in self.technicians
        )
        return named and not PASSES_ON.search(sentence)

    def _restates(self, sentence: str) -> bool:
        """The sentence says back the caller's ZIP, phone number or street address."""
        digits = re.sub(r"\D", "", sentence)
        lowered = sentence.lower()
        return (
            any(re.search(rf"\b{z}\b", sentence) for z in self.zips)
            or any(p in digits for p in self.phones)
            or any(
                re.search(rf"\b{re.escape(number)}\b", sentence)
                and re.search(rf"\b{re.escape(word)}\b", lowered)
                for number, word in self.streets
            )
        )

    def dead_end(self, raw: str, kept: str, calls: list[tuple[str, dict]]) -> str | None:
        """A reply that would leave the caller waiting in silence, and why: it called no tool yet
        promised to do something ("promised"), the caller heard nothing of it ("silent"), or it
        only acknowledged them ("stalled"). None when the reply calls a tool, ends on a question,
        or says something of substance; and after one follow-up since the caller last spoke, so a
        model that keeps failing can't loop."""
        if calls or self._followed_up or kept.rstrip().endswith("?"):
            return None
        if any(PROMISES_ACTION.search(s) for s in sentences(raw)):
            reason = "promised"
        elif not kept.strip():
            reason = "silent"
        elif all(ONLY_ACKNOWLEDGES.fullmatch(s.strip()) for s in sentences(kept)):
            reason = "stalled"
        else:
            return None
        self._followed_up = True
        return reason

    def text(self, text: str, calls: list[tuple[str, dict]]) -> str:
        """The model's text for one reply, given the (name, arguments) of the tools it called."""
        if any(self.server_speaks(n, a) for n, a in calls):
            return ""
        self.start_reply()
        return " ".join(kept for s in sentences(text) if (kept := self.sentence(s)))


def spoken_first(envelope: dict) -> str | None:
    """A sentence the voice layer speaks from a tool result word for word before the model's
    reply, which still follows: the top-priority line from find_slots (spec S-21a)."""
    return (envelope.get("data") or {}).get("spoken_first") or None


def for_model(envelope: dict) -> dict:
    """The tool result as the model sees it: a sentence the voice layer already spoke first is
    taken out of `say`, so the model doesn't say it again (the top-priority line)."""
    first = spoken_first(envelope)
    say = envelope.get("say") or ""
    if not first or not say.startswith(first):
        return envelope
    return {**envelope, "say": say[len(first):].strip()}


def spoken_by_server(name: str, envelope: dict) -> str | None:
    """The sentence a voice layer speaks itself from a tool result, word for word, with no model
    step after it: a safety refusal, the read-back sentence, and the sign-off end_call returns."""
    say = envelope.get("say") or ""
    if not say:
        return None
    if envelope.get("refused") == "gate_safety":
        return say
    # The caller's ZIP against the address the lookup found: the server's own question, said as
    # written, since it names both ZIPs, which a restatement check would otherwise drop.
    if envelope.get("refused") == "zip_mismatch":
        return say
    # The address lookup's questions (which one, spell it, the ZIP), as written, so the caller
    # hears one plain question rather than the model's account of the lookup.
    if name == "find_address" and envelope.get("refused") in (
        "address_several", "address_not_found", "address_lookup_down"
    ):
        return say
    if name == "read_back" and (envelope.get("ok") or envelope.get("refused") == "gate_read_back"):
        return say
    # The name and address check, which account on a shared number, and the moved-out address:
    # the server's own questions, as written, so the agent never reads a customer's details out
    # for a yes (issue #128).
    if envelope.get("refused") in (
        "identity_check", "identity_mismatch", "account_which", "which_old_address",
        "location_superseded",
    ):
        return say
    if name == "end_call" and envelope.get("ok"):
        return say
    # A booking the field service system couldn't take: one honest line, as written (spec S-48).
    if name == "book_appointment" and envelope.get("refused") == "fsm_unavailable":
        return say
    # A booking, move or cancel that went through: its confirmation is the whole turn, so no
    # second model request runs after the slowest tool on the call (eval round 4).
    if name in (
        "book_appointment", "reschedule_appointment", "cancel_appointment", "record_move"
    ) and envelope.get("ok"):
        return say
    return None


def job_types(tenant_dir: Path) -> list[str]:
    """Active job type codes from the tenant's config.yaml."""
    config = yaml.safe_load((tenant_dir / "config.yaml").read_text())
    return [j["code"] for j in config["job_types"] if j["active"]]


def _inline(schema: dict) -> dict:
    """Resolve $ref into the schema itself and drop titles: some providers refuse $defs, and titles
    only add tokens."""
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(copy.deepcopy(defs[node["$ref"].rsplit("/", 1)[1]]))
            return {k: walk(v) for k, v in node.items() if k not in ("title", "$defs")}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)
