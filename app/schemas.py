"""Request bodies. Every tool argument is typed and length-limited here; anything else is refused
before a handler runs (docs/architecture.md, checked arguments)."""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .settings import NOTE_MAX_CHARS
from .text import join_spelled

Priority = Literal["Low", "Normal", "High", "Urgent"]
Zip = Field(pattern=r"^\d{5}$")


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CallRef(Body):
    call_id: str = Field(min_length=1, max_length=100)


class Address(Body):
    street: str = Field(min_length=1, max_length=120)
    unit: str = Field("", max_length=20)
    city: str = Field("", max_length=60)
    zip: str = Zip
    spelled: str = Field(
        "",
        max_length=120,
        description='The street name as the caller spelled it, letter by letter, such as "H, O, U, '
        'S, T, O, N". The server joins the letters into the street name.',
    )

    @model_validator(mode="after")
    def join_letters(self):
        # Letters said one by one inside the street become the word. `spelled` is applied by the
        # tool, which can check the caller spelled it (tools._with_spelling).
        self.street = join_spelled(self.street)
        return self


# ---- lifecycle ---------------------------------------------------------------------------------


class StartIn(CallRef):
    caller_id: str = Field(max_length=40, description="The other party's number, as delivered")
    dialed_number: str = Field(max_length=40, description="Our number: the one the caller dialed")
    direction: Literal["inbound", "outbound"] = "inbound"
    flow: Literal["", "server_led"] = Field(
        "",
        description="server_led: this call's tools run the server-led flow (app/flow.py), as for "
        "the socket agent; empty: as today. Set once, at the start",
    )


class TurnIn(Body):
    speaker: Literal["caller", "agent"]
    text: str = Field(max_length=4000)
    replaces_last: bool = Field(
        False,
        description="A caller line the voice layer already sent in part, now whole: it replaces "
        "the call's last caller line instead of adding one",
    )
    timings: dict[str, float] = Field(
        default_factory=dict, description="Stage timings in ms, docs/latency.md"
    )


class EndIn(Body):
    reason: Literal[
        "caller_hangup", "agent_end", "dropped", "transfer", "error", "stepped_away"
    ] = "caller_hangup"


class TransferIn(Body):
    """The voice layer asks, right after a person flag, whether the call goes to a person now."""

    flag_id: int
    callback_id: int | None = None


class TransferDoneIn(Body):
    outcome: Literal["connected", "no_answer", "not_accepted", "failed"]


class HandoffDoneIn(Body):
    """What happened to a cold handoff's SIP transfer (app/transfer.py finish_handoff)."""

    outcome: Literal["handed_off", "failed"]


class RecordingIn(Body):
    """The voice layer started recording this call into the bucket under `object_key`."""

    egress_id: str = Field(min_length=1, max_length=80)
    object_key: str = Field(min_length=1, max_length=200)


class HeartbeatIn(Body):
    worker: str = Field(min_length=1, max_length=60)


class OutboundClaimIn(Body):
    worker: str = Field(min_length=1, max_length=60)


class OutboundDoneIn(Body):
    answered: bool = Field(description="Someone picked up and the call went ahead")
    voicemail: bool = Field(False, description="Not answered, and a voicemail was left")


# ---- tools -------------------------------------------------------------------------------------


class CustomerAddress(Body):
    """An address to look a customer up by. The ZIP is optional: with none, the server uses the one
    find_address found for that street on this call, so the model never has to guess one (#175,
    Sonnet sent 00000 and then apologized to the caller for a placeholder)."""

    street: str = Field(min_length=1, max_length=120)
    unit: str = Field("", max_length=20)
    city: str = Field("", max_length=60)
    zip: str = Field(
        "",
        pattern=r"^(\d{5})?$",
        description="Only a ZIP the caller said or find_address returned; otherwise leave it empty",
    )
    spelled: str = Field("", max_length=120)

    @model_validator(mode="after")
    def join_letters(self):
        self.street = join_spelled(self.street)
        return self


class FindCustomerIn(CallRef):
    phone: str | None = Field(None, max_length=40)
    address: CustomerAddress | None = None

    @model_validator(mode="after")
    def one_of(self):
        if not self.phone and not self.address:
            raise ValueError("give a phone number or an address")
        return self


class HistoryIn(CallRef):
    customer_id: int
    what: Literal["jobs", "calls", "equipment"]
    months_back: int = Field(24, ge=1, le=120)


class ServiceAreaIn(CallRef):
    zip: str = Zip


class FindAddressIn(CallRef):
    street: str = Field(
        min_length=1,
        max_length=120,
        description='The house number and street as the caller said them, such as "248 Duffield '
        'St" or "41-12 Bell Blvd"',
    )
    borough: str = Field(
        "",
        max_length=60,
        description="The borough or neighborhood, only if the caller said one",
    )
    spelled: str = Field(
        "",
        max_length=120,
        description="The street name as the caller spelled it, letter by letter",
    )

    @model_validator(mode="after")
    def join_letters(self):
        self.street = join_spelled(self.street)
        return self


class Facts(Body):
    """What the agent has heard that bears on priority. The server sets priority from the caller's
    own lines; `who_home`, `issue` and `since` raise nothing, and `inside_temp_f` counts only when
    a caller line carries that number (DECISIONS.md 072)."""

    issue: str = Field("", max_length=300)
    # Since when, as the agent heard it: the Retell agent passes it here, and it was refused as an
    # unknown field (Retell eval suite round 1, 16 cases).
    since: str = Field("", max_length=120)
    who_home: str = Field("", max_length=300)
    inside_temp_f: float | None = Field(None, ge=0, le=130)
    temp_falling_fast: bool = False


class FindSlotsIn(CallRef):
    job_type: str = Field(max_length=40)
    priority: Priority | None = Field(None, description="The agent's view; logged, not used")
    preferred_days: list[str] = Field(default_factory=list, max_length=7)
    time_of_day: Literal["morning", "afternoon", "evening"] | None = None
    moving_a_visit: bool = Field(
        False,
        description="True when finding a new time for a visit the caller already has, before "
        "reschedule_appointment",
    )
    window_minutes: int | None = Field(
        None,
        ge=30,
        le=720,
        multiple_of=30,
        description="How long a window the caller wants, in minutes, when they name one (\"an "
        "hour\", \"a 3-hour window\"); left out, the company's usual length",
    )
    day: dt.date | str | None = Field(
        None,
        description="Ask for one day's exact windows: a date, or today, tomorrow or a weekday name",
    )
    arrive_after: str | None = Field(
        None,
        max_length=20,
        description='The caller can only have the technician arrive after this local time, such '
        'as "8" or "1 PM". Only what the caller said; a bare hour such as "2" means 2 PM.',
    )
    arrive_before: str | None = Field(
        None,
        max_length=20,
        description='The caller needs the technician to arrive before this local time, such as '
        '"10" or "2". Only what the caller said; "8 to 2" is arrive_after "8" and arrive_before "2".',
    )
    facts: Facts = Field(default_factory=Facts)


class ReadBackIn(CallRef):
    address: Address
    phone: str = Field(
        max_length=40,
        description="Ten digits, caller_id for the number calling, or the last four digits of a "
        "number on file",
    )
    phone_for: str = Field(
        "",
        max_length=60,
        description='Who the number reaches when it isn\'t the caller, as the caller said it, such as '
        '"my daughter Ana". Always set when the caller says the number is someone else\'s; empty '
        "for the caller's own number.",
    )
    name: str = Field(
        "",
        max_length=80,
        description="The name the visit goes under, when the caller already gave it; the server "
        "then doesn't ask for it after the read-back",
    )
    job_type: str | None = Field(
        None,
        max_length=40,
        description="The job type of the visit, when the caller has said the problem. For no heat "
        "or no cooling the server may dispatch the soonest technician here and say so",
    )
    confirmed: bool = False


class Slot(Body):
    date: dt.date
    window: str = Field(
        max_length=40,
        description="The window code find_slots returned (such as morning), not its spoken label",
    )


class JobDetails(Body):
    """The intake every booking carries (spec S-13). Address and phone come from the read-back."""

    issue: str = Field(min_length=1, max_length=300)
    # Needed for a repair; an estimate visit for a new system needs only the issue (Jackson's call
    # call_2fb4e053ba8b76b57c348845990). book_appointment checks them by job type.
    since: str = Field("", max_length=100)
    tried: str = Field("", max_length=300)
    who_home: str = Field("", max_length=120)
    approver: str = Field("", max_length=120)
    # Who'll let the technician in and any gate code or buzzer come from one answer to one question
    # (prompts/spine.md); the first goes in who_home, the second in arrival_notes.
    system_type: str = Field("", max_length=120)
    arrival_notes: str = Field("", max_length=300)
    also_noted: str = Field(
        "",
        max_length=300,
        description="The caller's answer to the catch-all (\"Anything else the technician should "
        "know, like water leaking or it turning on and off a lot?\"), as they said it",
    )
    name: str = Field(
        "",
        max_length=80,
        description="Needed for a caller not on file, and for the account holder at a new "
        "address booked with new_address_account own",
    )
    inside_temp_f: float | None = Field(None, ge=0, le=130)


class BookIn(CallRef):
    job_type: str = Field(max_length=40)
    slot: Slot
    details: JobDetails
    priority: Priority | None = Field(None, description="The agent's view; logged, not used")
    separate_problem: bool = Field(
        False,
        description="True only when the caller has an open visit at this address and confirmed "
        "this is a different problem that needs its own visit. To move a visit, use "
        "reschedule_appointment.",
    )
    new_address_account: Literal["own", "caller"] | None = Field(
        None,
        description="Only when a caller on file books an address that isn't theirs on file: own "
        "puts the address on its own account, in details.name, with the caller as a contact (the "
        "usual choice, such as a parent's house); caller adds it to the caller's own account (they "
        "moved, or it's theirs). Ask the caller which.",
    )


class RescheduleIn(CallRef):
    booking_id: int
    slot: Slot
    reason: str = Field(min_length=1, max_length=200)


class CancelIn(CallRef):
    booking_id: int
    # Optional: a caller who won't say why still gets the cancel (issue #149).
    reason: str = Field(
        "", max_length=200, description="Why, if the caller said; leave it out if they didn't"
    )


class MoveIn(CallRef):
    customer_id: int | None = Field(
        None, description="The account that moved; leave it out when one customer is matched"
    )
    from_location_id: int | None = Field(
        None,
        description="The location they moved out of, only when the account has more than one "
        "and the caller said which",
    )


class CallbackTime(Body):
    callback_time: str | None = Field(
        None, max_length=40, description='A local time such as "4:30 PM", or ISO with an offset'
    )
    callback_in_minutes: int | None = Field(None, ge=1, le=10080)
    callback_after: str | None = Field(
        None,
        max_length=40,
        description='The caller asked to be called after this local time today, such as "5 PM". '
        "Only what the caller said; use it instead of callback_time.",
    )
    callback_before: str | None = Field(
        None,
        max_length=40,
        description='The caller asked to be called before this local time today, such as "3 PM". '
        "Only what the caller said; use it instead of callback_time.",
    )
    callback_day: Literal[
        "today", "tomorrow", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
        "sunday",
    ] = Field(
        "today",
        description='The day the caller asked for: "today", "tomorrow", or the weekday they named '
        '("friday" for "Friday at 2"), the next one coming. The times above are on that day; a bare '
        'hour such as "2" means 2 PM.',
    )

    @model_validator(mode="after")
    def window_or_time(self):
        window = self.callback_after or self.callback_before
        if window and (self.callback_time or self.callback_in_minutes is not None):
            raise ValueError("give callback_after or callback_before, or a callback time, not both")
        return self


FLAG_REASON_CHARS = 600


class FlagIn(CallRef, CallbackTime):
    # A long reason is cut, never refused: Retell call call_f0846787038f654309219adb1e8 lost its
    # flag to a 300-character limit.
    reason: str = Field(min_length=3, max_length=20000)

    @field_validator("reason")
    @classmethod
    def _cut(cls, value: str) -> str:
        return value if len(value) <= FLAG_REASON_CHARS else value[: FLAG_REASON_CHARS - 3] + "..."
    priority: Literal["Normal", "High", "Urgent"] = "Normal"
    system_calls_back: bool = Field(
        False,
        description="True when the caller only asked to be called back (after dropped calls, or a "
        "plain 'call me back'): the system dials them at that time (journey 12). False when a "
        "person has to handle it.",
    )


class LeadDetails(CallbackTime):
    contact_name: str = Field(min_length=1, max_length=80)
    company: str = Field("", max_length=120)
    need: str = Field(min_length=3, max_length=300)


class LeadIn(CallRef):
    kind: Literal["commercial", "membership", "install"]
    details: LeadDetails


class NoteIn(CallRef):
    text: str = Field(min_length=1, max_length=NOTE_MAX_CHARS)
    attach_to: Literal["customer", "job"] = "customer"
    customer_id: int | None = None


class DoNotCallIn(CallRef):
    confirmed: bool = Field(
        False, description="True once the caller said yes to the confirm question it returned"
    )
    undo: bool = Field(False, description="True when the caller wants to be called again after all")


class LookupIn(CallRef):
    question: str = Field(min_length=1, max_length=300)


class EndCallIn(CallRef):
    reason: Literal[
        "resolved",
        "caller_goodbye",
        "wrong_number",
        "spam",
        "not_a_customer",
        "declined",
        "other",
    ]
