"""Every rule the agent follows, by call stage, for the owner view's Rules page (exploration 009,
option A).

The rows are built each time from what the agent actually runs on: the tenant's agent.yaml and
config.yaml, the prompt files, and the owner's live version (app/rules.py). Only the server checks
need words written here, because they are code; a test fails when a refusal the tools can raise
or a must-pass spec line has no row, and another when agent.yaml gains a section this module does
not show. Each row says how the rule is kept (a server check, exact wording, a company setting,
the agent's guidance or the owner's own guidance) and whether the owner can change it.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from . import rules
from .tenant import DAY_NAMES, Tenant

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"

KINDS = {
    "server": "Server check",
    "wording": "Exact wording",
    "setting": "Company setting",
    "guidance": "Agent guidance",
    "house": "Your guidance",
}
WHY_LOCKED = {
    "server": "Enforced in code on every call, whatever the agent is told.",
    "wording": "Said word for word by the server. Changed by engineers.",
    "setting": "A company setting. Changed by engineers, then tested.",
    "guidance": "Part of the agent's instructions. Changed by engineers, then tested.",
}


@dataclass(frozen=True)
class Rule:
    id: str
    stage: str
    kind: str
    title: str
    detail: str = ""
    editable: bool = False
    why: str = ""
    spec: tuple[str, ...] = ()

    @property
    def kind_label(self) -> str:
        return KINDS[self.kind]


# The server's own checks. Code, so their words live here; refusals lists every refusal code a
# check raises, so the completeness test can match them against app/tools.py.
GATES = (
    ("before_hello", "Looks the caller up by their number before the agent speaks", (), ()),
    ("before_hello", "A dropped safety call picks up where it stopped", (), ("S-18",)),
    ("whole_call", "Listens for a safety emergency on every caller line", (), ("S-17",)),
    ("whole_call", "Speaks the signed-off safety lines itself, never the agent's own", (),
     ("S-17",)),
    ("whole_call", "Marks a caller who describes a business, such as a store, as commercial", (),
     ("S-05",)),
    ("whole_call", "Stops calling anyone who asks not to be called", (), ()),
    ("whole_call", "Says once that the company doesn't do work it doesn't, and keeps it as a lead",
     (), ()),
    ("whole_call", "Passes on a request for a technician by name, and never promises one", (),
     ()),
    ("whole_call", "Sets priority from what the caller says, and only ever raises it", (), ()),
    ("whole_call", "Two bookings for different jobs, one change, one cancel, then a person",
     ("limit",), ()),
    ("booking", "Never books one visit twice; a second at one address is a different job",
     ("already_booked", "second_job"), ()),
    ("whole_call", "Can't delete records or look at another customer's", ("not_owner",),
     ("S-39",)),
    ("whole_call", "Can't place a call to 911 or anyone outside the company's callbacks", (),
     ("S-24",)),
    ("whole_call", "Puts a caller through to a person only when the server says so",
     (), ()),
    ("whole_call", "Never puts a safety call through to a person", (), ()),
    ("whole_call", "Every callback has a named time inside calling hours",
     ("invalid_callback_time",), ()),
    ("booking", "Holds any booking until the safety lines are said and a person is flagged",
     ("gate_safety",), ("S-18",)),
    ("booking", "Holds any booking until the address and phone are read back and confirmed",
     ("gate_read_back", "invalid_phone"), ("S-26",)),
    ("booking", "Reads the address and phone back once; confirmed details aren't read again",
     ("read_back_confirmed",), ("S-26",)),
    ("booking", "A detail the caller changes after confirming is read back again before booking",
     (), ("S-26",)),
    ("booking", "Asks about an open visit whose window has passed, never calls it upcoming",
     ("open_job_passed",), ()),
    ("booking", "Won't book outside the service area", ("out_of_area",), ("S-28",)),
    ("booking", "Checks the area and books only on a ZIP the caller said or one on their record",
     ("zip_not_heard",), ()),
    ("booking", "Looks up a ZIP only for a street the caller said, and asks when it can't tell",
     ("address_not_heard", "address_several", "address_not_found", "address_lookup_down",
      "zip_asked"),
     ("S-13a",)),
    ("booking", "Won't book on a ZIP that disagrees with the caller's street until they settle it",
     ("zip_mismatch",), ("S-13a",)),
    ("booking", "Won't book a commercial customer", ("customer_type",), ("S-05",)),
    ("booking", "When who can approve isn't settled, a booking gets a call before dispatch", (),
     ("S-07a",)),
    ("booking", "Books only openings ServiceTitan offered, and never the same one twice",
     ("invalid_slot", "slot_taken"), ("S-27",)),
    ("booking", "Changes only the caller's own visits", ("invalid_state", "open_job"), ()),
    ("booking", "Asks whose account a new address goes on before booking it",
     ("account_choice",), ("S-31a",)),
    ("booking", "A number on two accounts books under the name the caller says",
     ("account_which",), ()),
    ("whole_call", "Changes a visit or account from an unknown number only after a name and address",
     ("identity_check", "identity_mismatch"), ()),
    ("whole_call", "A caller who moved gets the new address, and no visit goes to the old one",
     ("move_not_said", "which_old_address", "same_address", "location_superseded", "move_first"),
     ()),
    ("booking", "After hours, books a tonight visit only once the on-call technician is alerted",
     ("on_call_first",), ()),
    ("booking", "When ServiceTitan is down, keeps the booking and sets a callback",
     ("fsm_unavailable", "fsm_rejected"), ()),
    ("closing", "Asks if there's anything else before hanging up on a booked call",
     ("anything_else_first",), ()),
    ("closing", "On a safety call, signs off instead of hanging up", (), ("S-19",)),
    ("after_call", "A dropped safety call gets an urgent flag and a quick callback", (),
     ("S-18",)),
    ("after_call", "Calls back only to the caller, once, and never to a do-not-call number", (),
     ()),
    ("after_call", "Retries a queued booking until ServiceTitan takes it", (), ()),
)
# Refusals that are about the request, not a rule an owner would look for.
NOT_RULES = {"invalid_arguments", "unknown_call", "call_ended"}

# Wording lines: where in the call each is said, and a short name. A new line in agent.yaml shows
# under Whole call with its key as the name until it is added here.
LINE_STAGES = {
    "greeting_known": ("opening", "Greeting, known caller"),
    "greeting_unknown": ("opening", "Greeting"),
    "zip_mismatch": ("booking", "ZIP mismatch"),
    "address_which": ("booking", "Which address"),
    "address_not_found": ("booking", "Street not found"),
    "address_ask_zip": ("booking", "Ask for the ZIP"),
    "recording_notice": ("opening", "Recording notice, two-party consent"),
    "recording_stopped": ("whole_call", "Recording stopped, two-party consent"),
    "callback_opening": ("opening", "Callback opening"),
    "callback_opening_unknown": ("opening", "Callback, no name"),
    "earlier_slot_offer": ("opening", "Earlier opening offer"),
    "repeated_drops": ("opening", "After dropped calls"),
    "commercial": ("whole_call", "Commercial decline"),
    "approval_callback": ("booking", "Approval call before dispatch"),
    "approval_callback_late": ("booking", "Approval call, job held"),
    "do_not_call": ("whole_call", "Do not call"),
    "do_not_call_with_callback": ("whole_call", "Do not call, callback off"),
    "flag_stored": ("whole_call", "Team will call"),
    "flag_stored_urgent": ("whole_call", "Team will call, urgent"),
    "flag_stored_no_callback": ("whole_call", "Team told"),
    "transfer_handoff": ("whole_call", "Putting you through"),
    "handoff_now": ("whole_call", "Sent straight to a person"),
    "transfer_no_answer": ("whole_call", "Nobody picked up"),
    "transfer_prompt": ("whole_call", "To the person answering"),
    "transfer_summary": ("whole_call", "Summary once they take it"),
    "lead_stored": ("whole_call", "Lead saved"),
    "callback_by": ("whole_call", "Callback by a time"),
    "callback_after": ("whole_call", "Callback after a time"),
    "callback_before": ("whole_call", "Callback before a time"),
    "callback_between": ("whole_call", "Callback between times"),
    "callback_outside_hours": ("whole_call", "Outside calling hours"),
    "limit": ("whole_call", "Call limit reached"),
    "bridge_check": ("whole_call", "While checking"),
    "bridge_sec": ("whole_call", "While checking, short"),
    "bridge_look": ("whole_call", "While looking"),
    "server_down": ("whole_call", "System trouble"),
    "read_back": ("booking", "Read-back"),
    "read_back_for": ("booking", "Read-back, someone else's number"),
    "account_which": ("booking", "Which account on the number"),
    "identity_check": ("whole_call", "Name and address check"),
    "identity_check_name_only": ("whole_call", "Name and address check, name only"),
    "identity_check_address_only": ("whole_call", "Name and address check, address only"),
    "identity_check_address": ("whole_call", "Name and address check, address"),
    "identity_check_name": ("whole_call", "Name and address check, name"),
    "identity_check_again": ("whole_call", "Name and address check, again"),
    "identity_mismatch": ("whole_call", "Name and address didn't match"),
    "flag_stored_on_file": ("whole_call", "Team will call the number on file"),
    "move_which": ("whole_call", "Moved, which old address"),
    "move_done": ("whole_call", "Moved, address updated"),
    "move_visit_moved": ("whole_call", "Moved, visit moved"),
    "move_visit_moved_when": ("whole_call", "Moved, visit moved, with its day"),
    "move_out_of_area": ("whole_call", "Moved outside the area"),
    "move_visit_cancelled": ("whole_call", "Moved, old visit cancelled"),
    "move_visit_person": ("whole_call", "Moved, technician already out"),
    "location_superseded": ("booking", "Visit at the old address"),
    "moved_from_address": ("booking", "Booking at the old address"),
    "heads_up": ("after_call", "Heads-up to the number on file"),
    "out_of_area": ("booking", "Outside the area"),
    "not_offered": ("whole_call", "Work we don't do"),
    "visit_passed": ("booking", "Visit window passed"),
    "next_unit": ("booking", "Next unit"),
    "slot_taken": ("booking", "Window just filled"),
    "urgent_priority": ("booking", "Marked urgent"),
    "fsm_unavailable": ("booking", "Can't book now"),
    "fsm_unavailable_no_callback": ("booking", "Can't book, noted"),
    "anything_else_first": ("closing", "Anything else"),
    "goodbye": ("closing", "Goodbye"),
    "fallback": ("before_hello", "Phones down"),
    "at_capacity": ("before_hello", "All lines busy"),
    "outbound_voicemail": ("after_call", "Voicemail we leave"),
}
WORDING_WHY = {
    "read_back": rules.LOCKED["read_back"],
    "commercial": "Declines a business the same way every time. Changed by engineers.",
}

# Prompt items whose lead says where in the call they apply; everything else is Whole call.
PROMPT_STAGES = {
    "The caller has already heard the greeting": "opening",
    "Listen first": "opening",
    "Contact number": "booking",
    "Confirm the address and number before booking": "booking",
    "Offer times like a person": "booking",
    "Book, then confirm out loud": "booking",
    "End": "closing",
}
# agent.yaml sections that hold no rule an owner would read: numbers, connections, plumbing.
NOT_SHOWN = {"extends", "phone_numbers", "fsm", "weather", "owner_rules"}
NOT_SHOWN_PROMPTS = {"tool-use.md"}


def _plain(text: str, tenant: Tenant) -> str:
    """Markdown and template fields as an owner reads them: {time} as [time], code as words."""
    text = text.replace("{company}", tenant.name)
    text = re.sub(r"`([^`]+)`", lambda m: m.group(1).replace("_", " "), text)
    text = text.replace("**", "")
    text = re.sub(r"\{(\w+)\}", lambda m: "[" + m.group(1).replace("_", " ") + "]", text)
    return " ".join(text.split())


def _gates() -> list[Rule]:
    return [
        Rule(f"server.{n}", stage, "server", title, why=WHY_LOCKED["server"], spec=spec)
        for n, (stage, title, _codes, spec) in enumerate(GATES, 1)
    ]


def gate_codes() -> set[str]:
    return {c for _s, _t, codes, _spec in GATES for c in codes}


def _wording(tenant: Tenant, edits: dict[str, str]) -> list[Rule]:
    found = []
    spoken = {
        **{k: v for k, v in tenant.agent["voice"].items() if isinstance(v, str)},
        **tenant.agent["voice"].get("clips", {}),
        **tenant.lines,
        **edits,
    }
    for key, text in spoken.items():
        stage, title = LINE_STAGES.get(key, ("whole_call", key.replace("_", " ").capitalize()))
        editable = key in rules.EDITABLE_LINES
        found.append(
            Rule(
                f"line.{key}",
                stage,
                "wording",
                title,
                _plain(text, tenant),
                editable,
                "" if editable else WORDING_WHY.get(key, WHY_LOCKED["wording"]),
            )
        )
    safety = tenant.safety
    why = rules.LOCKED["safety"]
    for kind, cfg in safety["kinds"].items():
        label = cfg["label"].capitalize()
        found.append(Rule(f"safety.{kind}.lines", "whole_call", "wording", f"{label}: safety lines",
                          _plain(cfg["lines"], tenant), why=why, spec=("S-17",)))
        found.append(Rule(f"safety.{kind}.variants", "whole_call", "wording",
                          f"{label}: said again", " / ".join(cfg.get("variants", [])), why=why))
    for kind, cfg in safety.get("vague", {}).items():
        label = safety["kinds"][kind]["label"].capitalize()
        found.append(Rule(f"safety.{kind}.question", "whole_call", "wording",
                          f"{label}: check question", cfg["question"], why=why))
        if cfg.get("confirm_question"):
            found.append(Rule(f"safety.{kind}.confirm_question", "whole_call", "wording",
                              f"{label}: confirm question", cfg["confirm_question"], why=why))
    if safety.get("booking_note"):
        found.append(Rule("safety.booking_note", "booking", "wording", "Visit on a safety call",
                          _plain(safety["booking_note"], tenant), why=why, spec=("S-18",)))
    found.append(Rule("safety.sign_off", "closing", "wording", "Safety sign-off",
                      _plain(safety["sign_off"], tenant), why=why, spec=("S-19",)))
    return found


def _few(items: list[str], n: int = 4) -> str:
    shown = ", ".join(items[:n])
    return f"{shown} and {len(items) - n} more" if len(items) > n else shown


def _clock(value: str) -> str:
    hour, minute = (int(x) for x in value.split(":"))
    suffix = "AM" if hour < 12 else "PM"
    hour = hour % 12 or 12
    return f"{hour} {suffix}" if minute == 0 else f"{hour}:{minute:02d} {suffix}"


def _hours(tenant: Tenant) -> str:
    parts = []
    for day in DAY_NAMES:
        span = tenant.hours.get(day)
        parts.append(
            f"{day.capitalize()} {_clock(span[0])} to {_clock(span[1])}" if span else
            f"{day.capitalize()} closed"
        )
    return ", ".join(parts)


def _settings(tenant: Tenant) -> list[Rule]:
    why = WHY_LOCKED["setting"]
    agent = tenant.agent
    found = [
        Rule("setting.hours", "before_hello", "setting", "Office hours", _hours(tenant), why=why),
        Rule("setting.repeat_callers", "before_hello", "setting", "Repeat callers",
             f"A call back within {tenant.repeat['link_within_minutes']} minutes, or later the "
             "same day after a dropped call, picks up the last one and checks it's the same "
             f"person; after {tenant.repeat['drops_before_callback_offer']} dropped calls the "
             "agent offers a callback", why=why),
    ]
    if tenant.pace:
        p = tenant.pace
        found.append(Rule(
            "setting.pace", "whole_call", "setting", "Speaking pace",
            f"Slower ({p['older_adult_speed']} of normal) for a caller who says they're elderly "
            f"or {p['older_adult_age']} or over; a step slower or faster when the caller asks, "
            f"kept for their next call; a step slower after \"sorry, what?\" "
            f"{p['repeats_before_slower']} times; the address and number in a read-back a step "
            "slower than the rest; the address slower again when that's all they ask", why=why))
    declined = tenant.services.get("not_offered", {})
    if declined:
        found.append(Rule(
            "setting.services", "whole_call", "setting", "Work we don't do",
            "; ".join(f"{v['what']} (suggests {v['suggest']})" for v in declined.values()),
            why=why))
    labels = {code: j["name"] for code, j in tenant.job_types.items()}
    for job, brackets in tenant.priority["brackets"].items():
        for b in brackets:
            found.append(Rule(f"setting.priority.{job}.{b['id']}", "whole_call", "setting",
                              f"{labels.get(job, job)}: {b['reason']}", f"Starts at {b['level']}",
                              why=rules.LOCKED["priority"]))
    for r in tenant.priority["rules"]:
        found.append(Rule(f"setting.priority.rule.{r['id']}", "whole_call", "setting",
                          r["reason"].capitalize(),
                          f"Raises to {r['raise_to']}" + (
                              f" when the caller says {_few(r['phrases'])}" if r.get("phrases")
                              else ""
                          ), why=rules.LOCKED["priority"]))
    combine = tenant.priority.get("combine")
    if combine:
        found.append(Rule("setting.priority.combine", "whole_call", "setting",
                          combine["reason"].capitalize(),
                          f"{combine['groups']} together raise to {combine['raise_to']}",
                          why=rules.LOCKED["priority"]))
    for kind, cfg in tenant.safety["kinds"].items():
        found.append(Rule(f"setting.safety.{kind}", "whole_call", "setting",
                          f"{cfg['label'].capitalize()}: words that start the script",
                          _few(cfg["clear"]), why=rules.LOCKED["safety"]))
    for kind, cfg in tenant.safety.get("vague", {}).items():
        label = tenant.safety["kinds"][kind]["label"]
        found.append(Rule(f"setting.safety.{kind}.vague", "whole_call", "setting",
                          f"Words that ask about {label}", _few(cfg["phrases"]),
                          why=rules.LOCKED["safety"]))
        if cfg.get("confirm_phrases"):
            found.append(Rule(f"setting.safety.{kind}.confirm", "whole_call", "setting",
                              f"Words that ask the lighter {label} question",
                              _few(cfg["confirm_phrases"]), why=rules.LOCKED["safety"]))
    found.append(Rule("setting.commercial", "whole_call", "setting", "Business words",
                      _few(tenant.commercial["owned"] + tenant.commercial["phrases"]), why=why))
    approval = tenant.approval
    if approval["unclear_phrases"]:
        found.append(Rule("setting.approval", "booking", "setting", "Approval not settled",
                          f"When the caller says {_few(approval['unclear_phrases'])}, a booking "
                          f"gets a call {approval['callback_hours_before_window']} hours before the "
                          "window", why=why))
    fees = [k.replace("_", " ") for k, v in tenant.fees.items() if v is not None]
    found.append(Rule("setting.fees", "whole_call", "setting", "Fees the agent may say",
                      _few(fees) if fees else "None, so the agent never quotes a fee", why=why))
    zips = sorted(tenant.service_area)
    zones = sorted({zone["name"] for zone in tenant.service_area.values()})
    found.append(Rule("setting.service_area", "booking", "setting", "Service area",
                      f"{len(zips)} ZIP codes in {_few(zones, 3)}", why=why, spec=("S-28",)))
    found.append(Rule("setting.windows", "booking", "setting", "Arrival windows",
                      ", ".join(("After hours " if w.after_hours else "")
                                + f"{_clock(w.start.strftime('%H:%M'))} to "
                                + _clock(w.end.strftime("%H:%M")) for w in tenant.windows),
                      why=why))
    holds = tenant.urgent_hold.get("per_day", {})
    weekday = holds.get("mon", {})
    if weekday:
        found.append(Rule("setting.urgent_hold", "booking", "setting", "Technicians held for urgent",
                          f"{max(weekday.values())} per weekday window", why=why))
    cut = tenant.after_hours_cutoffs
    found.append(Rule("setting.after_hours", "booking", "setting", "No heat after hours",
                      f"Tonight if it will drop below {cut['tonight_below_f']} F; first morning "
                      f"slot if it stays above {cut['morning_above_f']} F", why=why))
    cb = tenant.callbacks
    found += [
        Rule("setting.callbacks.fsm_down", "booking", "setting", "When booking is down",
             f"Someone calls back within {cb['fsm_down_minutes']} minutes", why=why),
        Rule("setting.callbacks.hours", "after_call", "setting", "Calling hours",
             f"{_clock(cb['outbound_hours'][0])} to {_clock(cb['outbound_hours'][1])}, "
             f"{cb['max_attempts']} try", why=why),
        Rule("setting.callbacks.dropped_safety", "after_call", "setting",
             "Dropped safety call", f"Called back within {cb['dropped_safety_minutes']} minutes",
             why=why),
        Rule("setting.on_call_alerts", "after_call", "setting", "On-call alerts",
             f"{_few(agent['on_call_alerts']['priorities'])} flags after hours are emailed",
             why=why),
        Rule("setting.retention", "after_call", "setting", "Records kept",
             f"Transcripts{' and recordings' if tenant.retention.get('record_calls') else ''} "
             f"{tenant.retention['transcript_days']} days, calls "
             f"{tenant.retention['call_record_days']} days", why=why),
    ]
    if tenant.transfer:
        cfg = tenant.transfer
        found.append(Rule("setting.transfer", "whole_call", "setting", "Putting a caller through",
                          "The office in hours when the caller asks for a person; the on-call "
                          "technician after hours on an urgent call. Rings "
                          f"{cfg['ring_seconds']} s, then someone calls back within "
                          f"{cfg['fallback_callback_minutes']} minutes", why=why))
    return found


# The agent.yaml sections each part above reads, for the completeness test.
SHOWN = {
    "safety", "commercial", "approval", "priority", "after_hours", "callbacks", "on_call_alerts",
    "repeat_callers", "retention", "lines", "voice", "transfer", "pace", "services",
}


def _items(path: Path) -> list[tuple[str, str]]:
    """(heading, text) for each paragraph and top-level list item in a prompt file. Indented lines
    belong to the item above them."""
    heading, items, current = "", [], None
    for raw in path.read_text().splitlines():
        line = raw.rstrip()
        if line.startswith("#"):
            heading, current = line.lstrip("#").strip(), None
            continue
        if not line.strip():
            current = None
            continue
        top = re.match(r"^(?:- |\d+\. )(.*)", line)
        if top or current is None:
            items.append([heading, top.group(1) if top else line.strip()])
            current = items[-1]
        else:
            current[1] += " " + line.strip()
    return [(h, t) for h, t in items]


def _split(text: str) -> tuple[str, str]:
    """A bold lead or the first sentence as the title, the rest as the detail."""
    bold = re.match(r"^\*\*(.+?)\*\*\s*(.*)", text)
    if bold:
        return bold.group(1).rstrip(".:"), bold.group(2)
    first = re.match(r"^(.+?[.:])(?:\s+(.*)|$)", text)
    if first:
        return first.group(1).rstrip(".:"), first.group(2) or ""
    return text, ""


def _guidance(tenant: Tenant) -> list[Rule]:
    # Imported here: prompts.assemble imports app.tools, which the app imports first.
    from prompts.assemble import PARTS

    found = []
    # tool-use.md is how the agent reads the tools' answers; the checks it points to are listed
    # as server checks, so its own lines would only repeat them in tool names.
    for part in (p for p in PARTS if p not in NOT_SHOWN_PROMPTS):
        for n, (_heading, text) in enumerate(_items(PROMPTS / part), 1):
            title, detail = _split(text)
            stage = next(
                (s for lead, s in PROMPT_STAGES.items() if title.startswith(lead)), "whole_call"
            )
            found.append(Rule(f"prompt.{Path(part).stem}.{n}", stage, "guidance",
                              _plain(title, tenant), _plain(detail, tenant),
                              why=WHY_LOCKED["guidance"]))
    return found


def _house(content: dict) -> list[Rule]:
    return [
        Rule(f"house.{stage}.{n}", stage, "house", text, editable=True)
        for stage, items in content.get("guidance", {}).items()
        for n, text in enumerate(items, 1)
    ]


def rulebook(tenant: Tenant, content: dict | None = None) -> list[Rule]:
    """Every rule, from the files the agent runs on and the owner's live content (app/rules.py)."""
    content = rules.applied(tenant, content)
    return (
        _gates() + _house(content) + _wording(tenant, content["lines"]) + _settings(tenant)
        + _guidance(tenant)
    )


def by_stage(found: list[Rule]) -> list[tuple[str, str, list[Rule]]]:
    """(stage, name, rules) in call order. The owner's rows and editable ones come first in each."""
    order = {"house": 0, "server": 1, "wording": 2, "setting": 3, "guidance": 4}
    return [
        (stage, name, sorted((r for r in found if r.stage == stage),
                             key=lambda r: (not r.editable, order[r.kind])))
        for stage, name in rules.STAGES.items()
    ]
