"""The fixed responses on the bench's Rules page (exploration 018, option A): what the agent says
word for word and the server's hard rules, one row per trigger, by topic.

The rows come from the rulebook (app/rulebook.py), so a new line in agent.yaml or a new server
check shows up here by itself. This module only sorts them: which topic a line or check belongs
to, which lines are variants of one response, and the trigger in plain words. A test fails when a
line or check has no topic, so nothing drops off the page.
"""

from dataclasses import dataclass, field

from . import rulebook, rules
from .tenant import Tenant

TOPICS = {
    "safety": "Safety",
    "greeting": "Greeting",
    "drops": "Drops and outages",
    "who": "Who's calling",
    "address": "Address and area",
    "booking": "Booking",
    "person": "Passing to a person",
    "closing": "Closing",
}

# A line's topic, by the first prefix of its key that matches.
LINE_TOPICS = (
    ("safety.", "safety"),
    ("greeting_linked", "drops"),
    ("greeting", "greeting"),
    ("callback_opening", "greeting"),
    ("recording", "greeting"),
    ("earlier_slot_offer", "greeting"),
    ("outbound_voicemail", "greeting"),
    ("repeated_drops", "drops"),
    ("still_here", "drops"),
    ("stepped_away", "drops"),
    ("server_down", "drops"),
    ("fallback", "drops"),
    ("at_capacity", "drops"),
    ("bridge_", "drops"),
    ("identity_", "who"),
    ("move_", "who"),
    ("heads_up", "who"),
    ("commercial", "who"),
    ("not_offered", "who"),
    ("do_not_call", "who"),
    ("card_stop", "who"),
    ("billing_callback", "who"),
    ("account_", "who"),
    ("read_back", "address"),
    ("zip_", "address"),
    ("address_", "address"),
    ("out_of_area", "address"),
    ("moved_from_address", "address"),
    ("location_superseded", "address"),
    ("flag_stored", "person"),
    ("transfer_", "person"),
    ("handoff_now", "person"),
    ("lead_stored", "person"),
    ("callback_", "person"),
    ("limit", "person"),
    ("anything_else_first", "closing"),
    ("after_confirmation", "closing"),
    ("goodbye", "closing"),
    ("", "booking"),
)

# Lines that are variants of one response share a row: the first one found is shown and the rest
# are in its tooltip. A line the owner can reword is always its own row.
FAMILIES = {
    "greeting_linked": "greeting_linked",
    "identity_check": "identity_check",
    "move_": "move",
    "callback_by": "callback_time",
    "callback_at": "callback_time",
    "callback_after": "callback_time",
    "callback_before": "callback_time",
    "callback_between": "callback_time",
    "callback_asap": "callback_time",
    "bridge_": "bridge",
    "flag_stored": "flag_stored",
    "do_not_call": "do_not_call",
    "approval_callback": "approval_callback",
    "fsm_unavailable": "fsm_unavailable",
    "read_back": "read_back",
}

# What sets each response off, in plain words. A line with no entry shows its rulebook name.
WHEN = {
    "greeting_unknown": "A call is answered",
    "greeting_known": "A known caller's call is answered",
    "callback_opening": "We call a caller back",
    "callback_opening_unknown": "We call back without a name",
    "recording_notice": "Recording needs the caller's consent",
    "recording_stopped": "The caller asks not to be recorded",
    "earlier_slot_offer": "An earlier opening comes up",
    "outbound_voicemail": "Nobody answers our call",
    "greeting_linked": "A caller rings back after being cut off",
    "repeated_drops": "The call has dropped more than once",
    "still_here": "The caller goes quiet",
    "stepped_away": "The caller has been away too long",
    "server_down": "Our system has trouble mid-call",
    "fallback": "The phones are down",
    "at_capacity": "Every line is busy",
    "bridge": "A lookup takes a moment",
    "identity_check": "A change is asked for from an unknown number",
    "identity_mismatch": "The name and address don't match",
    "move": "The caller says they moved",
    "heads_up": "A change came from another number",
    "commercial": "The caller describes a business",
    "not_offered": "The caller asks for work we don't do",
    "do_not_call": "The caller asks not to be called",
    "card_stop": "The caller starts reading a card number",
    "billing_callback": "The caller disputes a charge",
    "account_choice": "A new address could go on two accounts",
    "account_name": "A new account needs a name",
    "account_which": "The number is on more than one account",
    "read_back": "Before any booking",
    "zip_mismatch": "The ZIP doesn't match the street",
    "address_which": "The street is in more than one place",
    "address_not_found": "The street can't be found",
    "address_ask_zip": "The ZIP isn't known yet",
    "out_of_area": "The address is outside the area",
    "moved_from_address": "A booking is for an address they left",
    "location_superseded": "A visit is still at their old address",
    "flag_stored": "The team needs to call the caller",
    "transfer_handoff": "A caller is put through",
    "transfer_no_answer": "Nobody picks up the transfer",
    "transfer_prompt": "Someone answers a transfer",
    "transfer_summary": "They take the transfer",
    "handoff_now": "The caller goes straight to a person",
    "lead_stored": "The caller wants to hear about a service",
    "callback_time": "A callback time is said",
    "callback_outside_hours": "A callback is asked for outside calling hours",
    "limit": "The call reaches its limit",
    "anything_else_first": "Before ending a booked call",
    "after_confirmation": "A booking is confirmed",
    "goodbye": "The call ends",
    "fsm_unavailable": "ServiceTitan can't take a booking",
    "approval_callback": "It's unclear who can approve the work",
    "slot_taken": "The window fills while booking",
    "visit_passed": "An open visit's window has passed",
    "urgent_priority": "The call is marked urgent",
    "next_unit": "Booking a building's next unit",
    "arrival_no_fit": "The caller wants an exact arrival time",
    "return_visit_review": "We were just out at the home",
    "seek_earlier_note": "The caller wants an earlier opening",
    "safety.booking_note": "A visit is booked on a safety call",
    "safety.sign_off": "A safety call ends",
}

# Each server check by its rulebook title: its topic, what sets it off in plain words, and the
# line it says when its refusal code doesn't name one (GATE_LINES covers the codes).
GATES = {
    'Looks the caller up by their number before the agent speaks': (
        'greeting', 'A call comes in', None,
    ),
    'A dropped safety call picks up where it stopped': (
        'safety', 'A safety caller rings back', None,
    ),
    'Listens for a safety emergency on every caller line': (
        'safety', 'Every time the caller speaks', None,
    ),
    "Speaks the signed-off safety lines itself, never the agent's own": (
        'safety', 'The caller describes an emergency', None,
    ),
    'Marks a caller who describes a business, such as a store, as commercial': (
        'who', 'The caller describes a business', 'commercial',
    ),
    'Stops calling anyone who asks not to be called': (
        'who', 'The caller asks not to be called', 'do_not_call',
    ),
    "Says once that the company doesn't do work it doesn't, and keeps it as a lead": (
        'who', "The caller asks for work we don't do", 'not_offered',
    ),
    'Passes on a request for a technician by name, and never promises one': (
        'person', 'The caller asks for a technician by name', None,
    ),
    'Sets priority from what the caller says, and only ever raises it': (
        'booking', 'The caller describes the problem', None,
    ),
    'Two bookings for different jobs, one change, one cancel, then a person': (
        'person', 'The call asks for more than it can do', None,
    ),
    'Never books one visit twice; a second at one address is a different job': (
        'booking', 'A second visit is asked for at one address', None,
    ),
    "Can't delete records or look at another customer's": (
        'who', "Anyone asks for another customer's records", None,
    ),
    "Can't place a call to 911 or anyone outside the company's callbacks": (
        'person', 'A call out is asked for', None,
    ),
    'Puts a caller through to a person only when the server says so': (
        'person', 'The caller asks for a person', None,
    ),
    'Never puts a safety call through to a person': (
        'safety', 'A safety caller asks for a person', None,
    ),
    'Every callback has a named time inside calling hours': (
        'person', 'A callback is promised', None,
    ),
    'Holds any booking until the safety lines are said and a person is flagged': (
        'safety', 'A safety caller wants a visit', None,
    ),
    'Holds any booking until the address and phone are read back and confirmed': (
        'address', 'Before any booking', None,
    ),
    "Reads the address and phone back once; confirmed details aren't read again": (
        'address', 'The details are already confirmed', None,
    ),
    'A detail the caller changes after confirming is read back again before booking': (
        'address', 'The caller changes a confirmed detail', None,
    ),
    'Asks about an open visit whose window has passed, never calls it upcoming': (
        'booking', "An open visit's window has passed", 'visit_passed',
    ),
    "Won't book outside the service area": (
        'address', 'The address is outside the area', None,
    ),
    'Checks the area and books only on a ZIP the caller said or one on their record': (
        'address', 'A ZIP is needed to book', None,
    ),
    "Looks up a ZIP only for a street the caller said, and asks when it can't tell": (
        'address', 'The caller gives a street', None,
    ),
    "Won't book on a ZIP that disagrees with the caller's street until they settle it": (
        'address', "The ZIP doesn't match the street", None,
    ),
    "Won't book a commercial customer": (
        'who', 'A business wants a visit', None,
    ),
    "When who can approve isn't settled, a booking gets a call before dispatch": (
        'booking', "It's unclear who can approve the work", 'approval_callback',
    ),
    'Books only openings ServiceTitan offered, and never the same one twice': (
        'booking', 'The caller picks a time', None,
    ),
    "Changes only the caller's own visits": (
        'booking', 'The caller asks to change a visit', None,
    ),
    'Asks whose account a new address goes on before booking it': (
        'who', 'A new address is given', None,
    ),
    'A number on two accounts books under the name the caller says': (
        'who', 'The number is on more than one account', None,
    ),
    'Changes a visit or account from an unknown number only after a name and address': (
        'who', 'A change is asked for from an unknown number', None,
    ),
    'A caller who moved gets the new address, and no visit goes to the old one': (
        'who', 'The caller says they moved', None,
    ),
    'After hours, books a tonight visit only once the on-call technician is alerted': (
        'booking', 'An urgent visit is booked after hours', None,
    ),
    'When ServiceTitan is down, keeps the booking and sets a callback': (
        'booking', "ServiceTitan can't take a booking", None,
    ),
    "Asks if there's anything else before hanging up on a booked call": (
        'closing', 'Before ending a booked call', None,
    ),
    'On a safety call, signs off instead of hanging up': (
        'safety', 'A safety call ends', 'safety.sign_off',
    ),
    'A dropped safety call gets an urgent flag and a quick callback': (
        'safety', 'A safety call drops', None,
    ),
    'Calls back only to the caller, once, and never to a do-not-call number': (
        'person', 'A callback is placed', None,
    ),
    'Retries a queued booking until ServiceTitan takes it': (
        'booking', 'A booking waits for ServiceTitan', None,
    ),
}

# A check whose refusal says a line other than the one named by its code.
GATE_LINES = {
    "customer_type": "commercial",
    "invalid_callback_time": "callback_outside_hours",
    "move_not_said": "move",
    "move_unverified": "move",
    "gate_read_back": "read_back",
    "read_back_confirmed": "read_back",
}


@dataclass
class Fixed:
    when: str
    line: str = ""
    variants: list[str] = field(default_factory=list)
    # The line's key when the owner may reword it (rules.EDITABLE_LINES), shown as a field.
    editable: str = ""
    rule: bool = False
    # For a server check that says no line: what it does, in its rulebook words.
    rule_text: str = ""


def topic_of(key: str) -> str:
    return next(topic for prefix, topic in LINE_TOPICS if key.startswith(prefix))


def family_of(key: str) -> str:
    """The row a line shares with its variants. Each kind of safety emergency (its lines, what is
    said again and its check questions) is one row, whatever kinds the tenant has."""
    if key in rules.EDITABLE_LINES:
        return key
    parts = key.split(".")
    if parts[0] == "safety" and len(parts) == 3:
        return f"safety.{parts[1]}"
    return next((f for prefix, f in FAMILIES.items() if key.startswith(prefix)), key)


def _when(tenant: Tenant, family: str, title: str) -> str:
    kind = family.removeprefix("safety.")
    if kind in tenant.safety["kinds"]:
        return f"{tenant.safety['kinds'][kind]['label'].capitalize()} on the call"
    return WHEN.get(family, title)


def responses(tenant: Tenant, content: dict | None = None) -> list[tuple[str, str, list[Fixed]]]:
    """(topic, name, rows) in TOPICS order. In each topic the server's hard rules come first, each
    with the line it says when there is one; then every other line, one row per trigger."""
    lines: dict[str, Fixed] = {}
    topics: dict[str, str] = {}
    for r in rulebook.rulebook(tenant, content):
        if r.kind != "wording":
            continue
        key = r.id.removeprefix("line.")
        family = family_of(key)
        if family in lines:
            lines[family].variants.append(r.detail)
            continue
        lines[family] = Fixed(
            _when(tenant, family, r.title), r.detail,
            editable=key if key in rules.EDITABLE_LINES else "",
        )
        topics[family] = topic_of(key)

    found: dict[str, list[Fixed]] = {t: [] for t in TOPICS}
    # A line is said on one row only: the first check that holds on it takes it, and a later
    # check on the same line (the read-back) is listed with what it does instead.
    shown: set[str] = set()
    for _stage, title, codes, _spec in rulebook.GATES:
        topic, when, line = GATES[title]
        linked = [
            f for f in ([line] if line else []) + [family_of(GATE_LINES.get(c, c)) for c in codes]
            if f in lines and f not in shown
        ]
        said = lines[linked[0]] if linked else None
        found[topic].append(Fixed(
            when, said.line if said else "", said.variants if said else [],
            said.editable if said else "", rule=True, rule_text="" if said else title,
        ))
        if said:
            shown.add(linked[0])
    for family, row in lines.items():
        if family not in shown:
            found[topics[family]].append(row)
    return [(t, TOPICS[t], found[t]) for t in TOPICS]
