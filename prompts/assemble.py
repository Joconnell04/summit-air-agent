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
    tenant = start["tenant"]
    parts = [(PROMPTS / p).read_text().strip() for p in PARTS]
    house = house_guidance((start.get("house_rules") or {}).get("guidance") or {})
    if house:
        parts.insert(PARTS.index(HOUSE_AFTER) + 1, house)
    text = "\n\n".join(parts)
    text = text.replace("{company}", tenant["name"])
    local = (now or datetime.now(ZoneInfo(tenant["timezone"]))).astimezone(
        ZoneInfo(tenant["timezone"])
    )
    knowledge = "\n\n".join(body.strip() for body in start.get("knowledge", {}).values())
    context = {
        k: v for k, v in start.items() if k not in ("knowledge", "tenant", "call_id", "house_rules")
    }
    return (
        f"{text}\n\n# Company knowledge\n\n{knowledge}\n\n# This call\n\n"
        f"Now: {local:%A %-d %B %Y, %-I:%M %p} ({tenant['timezone']}).\n\n"
        f"What the server found before hello:\n{json.dumps(context, default=str)}\n"
    )


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
    linked = start.get("linked_call") or {}
    if linked.get("dropped") and "greeting_linked" in voice:
        name = name or linked.get("caller_name") or ""
        if name:
            return voice["greeting_linked"].format(first_name=name)
        return voice["greeting_linked_unknown"]
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
    stage = next((h for h in hints if h in STAGE_NOTES), None)
    items = [h.removeprefix("guidance:") for h in hints if h.startswith("guidance:")]
    if stage and items:
        notes.append(f"[server] {STAGE_NOTES[stage]} " + " ".join(f"- {i}" for i in items))
    if known:
        facts = "; ".join(f"{k} {v}" for k, v in known.items())
        notes.append(f"[server] Already known, don't ask again: {facts}.")
    return notes


# A sentence that announces the read-back ("let me read that back", "I'll read back the details",
# "let me confirm your address and number"). The server's own sentence says it; the caller hears
# it once (eval round 3).
ANNOUNCES_READ_BACK = re.compile(
    r"\bread(?:\s+\w+){0,3}\s+back\b|\bread-back\b"
    r"|\b(?:let me|i'?ll|i will|i need to|i have to|i'?m going to)\s+confirm\s+(?:\w+\s+){0,2}"
    r"(?:details|address|number)",
    re.IGNORECASE,
)
# A sentence that only announces a booking the tools are about to make ("Booking today's afternoon
# window now."). The tool's own sentence says what happened, and an announcement can contradict it
# when the booking is queued (eval round 3, booking-system-down).
ANNOUNCES_BOOKING = re.compile(
    r"^\s*(?:booking\b|let me (?:book|lock|get (?:that|it|you) booked)|i'?ll (?:book|lock) (?:that|it)\b)",
    re.IGNORECASE,
)
# A priority claim. Only the server's own Urgent line may say it (eval round 4).
CLAIMS_PRIORITY = re.compile(r"\btop priority\b|\bmost urgent\b|\bhighest priority\b", re.IGNORECASE)
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
        self._dropped_restatement = False

    def holding(self) -> bool:
        return self.safety_call or (self.address_checked and not self.read_back_confirmed)

    def heard(self, safety: dict | None, caller_text: str = "") -> None:
        """A caller line and its turn result: the safety action the server returned, if any."""
        zips, phones = caller_digits(caller_text)
        self.zips |= zips
        self.phones |= phones
        self.streets |= caller_streets(caller_text)
        action = (safety or {}).get("action")
        if action in ("speak_lines", "speak_variant"):
            self.safety_call = True
        elif action == "cleared":
            self.safety_call = False

    def tool_done(self, name: str, envelope: dict) -> None:
        data = envelope.get("data") or {}
        if name == "check_service_area" and envelope.get("ok"):
            self.address_checked = True
        if name == "find_slots" and (data.get("priority") or {}).get("level") == "Urgent":
            self.urgent = True
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

    def sentence(self, sentence: str) -> str:
        """One sentence of the model's text, in order within a reply, or "" when the caller
        shouldn't hear it."""
        after_restatement, self._dropped_restatement = self._dropped_restatement, False
        if ANNOUNCES_READ_BACK.search(sentence) or ANNOUNCES_BOOKING.search(sentence):
            return ""
        if not self.urgent and CLAIMS_PRIORITY.search(sentence):
            return ""
        if not self.safety_call and HAZARD.search(sentence) and EVACUATE.search(sentence):
            return ""
        if after_restatement and CONFIRMS_ONLY.fullmatch(sentence):
            return ""
        if self._restates(sentence):
            self._dropped_restatement = True
            return ""
        return sentence

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

    def text(self, text: str, calls: list[tuple[str, dict]]) -> str:
        """The model's text for one reply, given the (name, arguments) of the tools it called."""
        if any(self.server_speaks(n, a) for n, a in calls):
            return ""
        self.start_reply()
        return " ".join(kept for s in sentences(text) if (kept := self.sentence(s)))


def spoken_by_server(name: str, envelope: dict) -> str | None:
    """The sentence a voice layer speaks itself from a tool result, word for word, with no model
    step after it: a safety refusal, the read-back sentence, and the sign-off end_call returns."""
    say = envelope.get("say") or ""
    if not say:
        return None
    if envelope.get("refused") == "gate_safety":
        return say
    if name == "read_back" and (envelope.get("ok") or envelope.get("refused") == "gate_read_back"):
        return say
    if name == "end_call" and envelope.get("ok"):
        return say
    # A booking the field service system couldn't take: one honest line, as written (spec S-48).
    if name == "book_appointment" and envelope.get("refused") == "fsm_unavailable":
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
