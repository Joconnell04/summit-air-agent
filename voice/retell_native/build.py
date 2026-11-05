"""Retell, native: the agent built the way Retell intends, as a second Retell entry beside the
baseline (voice/retell/), which stays untouched. Pure functions of their inputs; push.py sends them.

What differs from the baseline, and why (voice/retell_native/README.md):
- A one-page prompt (prompts/stripped.md) holds who the agent is, the job, the steps and the safety
  lines word for word. Facts a caller may ask about live in a Retell knowledge base built from the
  tenant's knowledge files and config, so nothing is in both.
- Retell's own End Call (it waits for the speech to finish) and Call Transfer to the office.
- Our tool server behind scoped custom functions only: lookups, the read-back, bookings, changes to
  a booking, a callback and a lead.
- Retell's Agent Handbook behaviours, the tenant's pronunciations and keywords, and the listening
  settings tested after live call 3 (voice/retell/build.py SPEECH).
"""

import hashlib
import json
from pathlib import Path

import yaml

from prompts.assemble import PROMPTS, STRIPPED
from voice.names import Names
from voice.retell import build as baseline

AGENT_NAME = "Summit Air (Retell native)"
# The copy changes are tried on before they reach the agent in Jackson's slate.
NEXT_NAME = "Summit Air (Retell native, next)"
# What the bridge's inbound webhook fills in per call (voice/retell/conversation.py).
CALL_CONTEXT = "{{call_context}}"
END_CALL_LINE = "- `end_call` once the caller is done. The server decides whether the line drops."

# The Agent Handbook (create-agent `handbook_config`, SOURCE:
# https://docs.retellai.com/api-references/create-agent.md, read 2026-11-05). NATO spelling stays off:
# "B as in Bravo" on every street name sounds robotic to a homeowner, and echo verification already
# repeats the details back. The professional preset is off because the conversational one replaces it.
HANDBOOK = {
    "default_personality": False,
    "conversational_personality": True,
    "natural_filler_words": True,
    "high_empathy": True,
    "echo_verification": True,
    "nato_phonetic_alphabet": False,
    "speech_normalization": True,
    "smart_matching": True,
    "ai_disclosure": True,
    "scope_boundaries": True,
}

# Words the ears should expect beyond the baseline's list and the tenant's street names.
EXTRA_KEYWORDS = ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island", "tune-up"]

# The custom functions, scoped to what a booking call needs. Each description says when to call it
# and when not to; `wait` is said while a slow one runs (Retell's speak-during-execution).
FUNCTIONS = {
    "find_customer": {
        "description": "Look up who is calling by the phone number they give or an address. Call it "
        "only when the caller gives a different number or an address and the call context shows "
        "no matching customer. Don't call it for the number they're calling from; that lookup has "
        "already been done.",
    },
    "get_customer_history": {
        "description": "Older history for a customer already matched on this call: past visits, "
        "past calls or equipment. Only when the caller asks about something earlier than what the "
        "call context shows.",
    },
    "check_service_area": {
        "description": "Check the caller's five-digit ZIP against the service area. Call it as soon "
        "as the caller says a ZIP. Never guess a ZIP from the street or the borough; ask for it, or "
        "use find_address.",
        "wait": "Say a short natural line such as \"One sec, let me check that.\"",
    },
    "find_address": {
        "description": "Find the ZIP for a house number and street when the caller doesn't know the "
        "ZIP. Pass the street exactly as they said it, and the borough only if they named one. "
        "Don't call it when they already gave a ZIP; use check_service_area.",
        "wait": "Say a short natural line such as \"Let me look that address up.\"",
    },
    "read_back": {
        "description": "Before any booking, confirm the address and the best phone number. The first "
        "call returns a sentence to say; say it and wait. The moment the caller says yes, call it "
        "again with confirmed set to true, before anything else. Don't book until it has returned "
        "ok with confirmed.",
    },
    "find_slots": {
        "description": "Find an opening for the job type. Without `day` it returns one opening to "
        "offer; offer it as one plain choice. Pass the day the caller asked for when they want a "
        "different one. Never offer a window it didn't return.",
        "wait": "Say a short natural line such as \"Let me see what's open.\"",
    },
    "book_appointment": {
        "description": "Book the window the caller chose, after read_back is confirmed. Its sentence "
        "is the confirmation; say it as written. Don't call it twice for the same window.",
        "wait": "Say a short natural line such as \"Booking that now.\"",
    },
    "reschedule_appointment": {
        "description": "Move a visit this caller already has to a window they chose from find_slots, "
        "with their reason. Only for a booking id from this call's context or a lookup.",
        "wait": "Say a short natural line such as \"Moving that for you now.\"",
    },
    "cancel_appointment": {
        "description": "Cancel a visit this caller already has, after they clearly say yes to "
        "cancelling it. Only for a booking id from this call's context or a lookup.",
        "wait": "Say a short natural line such as \"Okay, cancelling that now.\"",
    },
    "record_move": {
        "description": "For a caller on file who has moved house: first check the new address "
        "(find_address, or check_service_area with its ZIP) and read it back, then call this to "
        "move their account and open visits to it.",
        "wait": "Say a short natural line such as \"Updating your address now.\"",
    },
    "flag_person": {
        "description": "Ask someone from the office to call the caller back: a callback they want, a "
        "complaint, anything the tools refused, or anything you can't handle. Pass the time they "
        "asked for. Don't use it instead of booking when a booking is possible.",
    },
    "record_lead": {
        "description": "Save a lead: a quote for a new system or install, a membership sign-up, or "
        "a business. Someone calls them back; never quote a price.",
    },
}

PROMPT_ADDITIONS = """## Intake, briefly

A tune-up needs no problem and no start date: the reason is the yearly service. Ask how cold or
warm it is only on a no-heat or no-cooling call. Once a detail is said, don't ask for it again;
`read_back` is the one confirmation of the address and number, so don't read them back yourself
first. When the caller picks a window, book it.

Everything you write is spoken aloud. Never write brackets, stage directions or notes to yourself,
and never mention the server, tools, systems or confirmations; just say the next thing to the caller.

## Facts and ending

Facts about the company (hours, areas served, what a visit or the membership covers, payment) are in
your knowledge base. If something isn't there, don't guess: say someone from the office can call
back, and offer `flag_person`.

`end_call` is how you hang up. Say goodbye first, then call it. On a call with a safety signal,
use the sign-off above before ending. `transfer_to_office` puts the caller through to the office;
use it only when the caller asks for a person right now, during office hours."""


# Letter and spelling questions ("how many e's in seventeen?") go to one function the bridge answers
# itself (voice/retell/letters.py). On the native agent's gate call the model counted three e's in
# "seventeen", and the transcript wrote the number as "17", so it first answered "None." for the
# digits. Retell's Code tool was tried first: it takes no arguments, so it needed an extraction
# step, and the model described the question aloud before that step on every first turn.
LETTERS_PROMPT = """## Letters and spelling

For any question about the letters in a word (how many of a letter, or how a word is spelled), call
`count_letters` before you say anything, then answer from its result in one short sentence. A
number the caller says, such as "17", is the word written out ("seventeen"). To spell a word aloud,
put its letters alone inside spell tags, with no dashes or spaces.

Example. Caller: "How many e's are in 17?" You call count_letters with word "seventeen" and letter
"e", writing no text before the call, and then say only: "Seventeen has four e's. What can I help
you with today?" """


def letter_tools(base_url: str) -> list[dict]:
    return [{
        "type": "custom", "name": "count_letters",
        "description": "Count a letter in a word, and get the word's spelling. Call it for any "
        "question about the letters in a word.",
        "url": f"{base_url}/retell/letters", "method": "POST",
        "parameters": {"type": "object", "required": ["word"], "properties": {
            "word": {"type": "string",
                     "description": "The word written out in letters. A number the caller "
                     "said is its word: 17 is seventeen."},
            "letter": {"type": "string",
                       "description": "The one letter to count; leave it out to get the spelling."},
        }},
        "args_at_root": False, "speak_during_execution": False, "speak_after_execution": True,
        "timeout_ms": 5000, "max_retry": 1,
    }]


def page(tenant_dir: Path, letters: bool = False) -> str:
    """prompts/stripped.md with the tenant's name, safety lines and sign-off word for word, as
    prompts/assemble.py builds it, then the native additions and this call's context."""
    agent = yaml.safe_load((tenant_dir / "agent.yaml").read_text())
    company = yaml.safe_load((tenant_dir / "config.yaml").read_text())["company"]["name"]
    safety = agent["safety"]
    lines = "\n".join(
        f"- {kind}: \"{' '.join(spec['lines'].split())}\"" for kind, spec in safety["kinds"].items()
    )
    text = (
        (PROMPTS / STRIPPED).read_text().strip()
        .replace("{company}", company)
        .replace("{safety_lines}", lines)
        .replace("{sign_off}", f"\"{' '.join(safety['sign_off'].split())}\"")
        # Here end_call is Retell's own: it hangs up once the agent has finished speaking.
        .replace(END_CALL_LINE, "- `end_call` after your goodbye. It hangs up once you've finished "
                 "speaking.")
    )
    extra = f"\n\n{LETTERS_PROMPT}" if letters else ""
    return f"{text}\n\n{PROMPT_ADDITIONS}{extra}\n\n{CALL_CONTEXT}"


def clock(hhmm: str) -> str:
    """"08:00" -> "8 AM", "20:30" -> "8:30 PM"."""
    hour, minute = (int(x) for x in hhmm.split(":"))
    suffix = "AM" if hour < 12 else "PM"
    hour = hour % 12 or 12
    return f"{hour}:{minute:02d} {suffix}" if minute else f"{hour} {suffix}"


def knowledge(tenant_dir: Path) -> list[dict]:
    """The knowledge base's texts: the tenant's knowledge files, plus hours, areas and visit types
    from config.yaml, which is what the tool server enforces. No prices: none are configured."""
    config = yaml.safe_load((tenant_dir / "config.yaml").read_text())
    texts = []
    for path in sorted((tenant_dir / "knowledge").glob("*.md")):
        if path.name in ("README.md", "sign-off.md"):
            continue
        body = path.read_text()
        if path.name == "faq.md":
            # Hours and areas come from config below; the areas line predates the five boroughs.
            # The fee line's note is for whoever edits the file, not for callers.
            body = "\n".join(line for line in body.splitlines() if not line.startswith(("- What areas do you cover", "- When are you open")))
            body = body.replace("Only say a fee if config.yaml sets one. It doesn't today, so: ", "")
        texts.append({"title": path.stem.replace("-", " ").capitalize(), "text": body.strip()})
    days = {d: f"{clock(o)} to {clock(c)}" for d, (o, c) in config["company"]["hours"].items()}
    hours = (f"open every day, {next(iter(days.values()))}, weekends included."
             if len(set(days.values())) == 1 else "; ".join(f"{d}: {h}" for d, h in days.items()))
    known = sorted(set(config["address_lookup"]["zip_boroughs"].values()))
    areas = [b for b in known if any(z["name"].startswith(b) for z in config["zones"])]
    visits = [f"{j['name']}: about {j['duration'] // 60} minutes on site" for j in config["job_types"]
              if j.get("active")]
    texts.append({"title": "Hours and areas", "text":
                  f"Hours: {hours} After hours, an on-call technician handles urgent no-heat and "
                  "no-cooling calls.\n"
                  f"Areas served: homes in {', '.join(areas[:-1])} and {areas[-1]}, New York City. "
                  "The agent checks the ZIP before booking.\n"
                  "Payment: the office handles payment; the agent never takes a card."})
    texts.append({"title": "Visit types", "text": "\n".join(visits) + "\nA tune-up is the yearly "
                  "maintenance visit for a furnace, boiler or AC that is working; a repair visit is "
                  "for one that isn't."})
    return texts


def knowledge_name(texts: list[dict]) -> str:
    """Named by its content, so a change makes a new base (Retell names are under 40 characters)."""
    digest = hashlib.sha256(json.dumps(texts, sort_keys=True).encode()).hexdigest()[:8]
    return f"summit-native-{digest}"


def functions(specs: list[dict], base_url: str, office_number: str, letters: bool = False) -> list[dict]:
    """The scoped custom functions to the bridge, then Retell's End Call and Call Transfer."""
    by_name = {s["name"]: s for s in specs}
    tools = []
    for tool in baseline.custom_tools([by_name[name] for name in FUNCTIONS], base_url):
        native = FUNCTIONS[tool["name"]]
        tool["description"] = native["description"]
        if "wait" in native:
            tool.update(speak_during_execution=True, execution_message_description=native["wait"],
                        execution_message_type="prompt")
        tools.append(tool)
    # Nothing is said during End Call: the goodbye is already said, and a second one doubled it
    # in the review chats.
    tools.append({"type": "end_call", "name": "end_call", "speak_during_execution": False,
                  "description": "Hang up, after you have said goodbye and the caller has nothing "
                  "else. Never in the middle of a safety call before the sign-off."})
    tools.append({"type": "transfer_call", "name": "transfer_to_office",
                  "description": "Put the caller through to the office, only when they ask for a "
                  "person right now during office hours. Tell them you're transferring first.",
                  "transfer_destination": {"type": "predefined", "number": office_number},
                  "transfer_option": {"type": "cold_transfer"},
                  "speak_during_execution": True,
                  "execution_message_description": "Tell them you're putting them through now.",
                  "execution_message_type": "prompt"})
    return tools + (letter_tools(base_url) if letters else [])


def llm_body(tenant_dir: Path, specs: list[dict], base_url: str, model_id: str, office_number: str,
             knowledge_base_id: str, letters: bool = False) -> dict:
    return {
        "model": baseline.retell_model(model_id),
        "model_temperature": baseline.TEMPERATURE,
        "general_prompt": page(tenant_dir, letters),
        "general_tools": functions(specs, base_url, office_number, letters),
        "begin_message": "{{greeting}}",
        "start_speaker": "agent",
        "knowledge_base_ids": [knowledge_base_id],
    }


def agent_body(llm_id: str, base_url: str, voice_id: str, voice_model: str, names: Names,
               name: str = AGENT_NAME) -> dict:
    body = baseline.agent_body(llm_id, base_url, voice_id, voice_model, names)
    body["agent_name"] = name
    body["boosted_keywords"] = list(dict.fromkeys(body["boosted_keywords"] + EXTRA_KEYWORDS))
    body["handbook_config"] = HANDBOOK
    # Speak the greeting the moment the call connects, and start every slate call clean.
    body["begin_message_delay_ms"] = 0
    body["contact_memory_config"] = {"enable_read": False, "enable_update": False}
    return body
