"""Request bodies. Every tool argument is typed and length-limited here; anything else is refused
before a handler runs (docs/architecture.md, checked arguments)."""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .settings import NOTE_MAX_CHARS
from .text import join_spelled, with_spelled_name

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
        # Letters said one by one become the name, so the read-back says the word, not the letters.
        street = with_spelled_name(self.street, self.spelled) if self.spelled else self.street
        self.street = join_spelled(street)
        self.spelled = ""
        return self


# ---- lifecycle ---------------------------------------------------------------------------------


class StartIn(CallRef):
    caller_id: str = Field(max_length=40, description="The other party's number, as delivered")
    dialed_number: str = Field(max_length=40, description="Our number: the one the caller dialed")
    direction: Literal["inbound", "outbound"] = "inbound"


class TurnIn(Body):
    speaker: Literal["caller", "agent"]
    text: str = Field(max_length=4000)
    timings: dict[str, float] = Field(
        default_factory=dict, description="Stage timings in ms, docs/latency.md"
    )


class EndIn(Body):
    reason: Literal["caller_hangup", "agent_end", "dropped", "transfer", "error"] = "caller_hangup"


class HeartbeatIn(Body):
    worker: str = Field(min_length=1, max_length=60)


class OutboundClaimIn(Body):
    worker: str = Field(min_length=1, max_length=60)


class OutboundDoneIn(Body):
    answered: bool = Field(description="Someone picked up and the call went ahead")
    voicemail: bool = Field(False, description="Not answered, and a voicemail was left")


# ---- tools -------------------------------------------------------------------------------------


class FindCustomerIn(CallRef):
    phone: str | None = Field(None, max_length=40)
    address: Address | None = None

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


class Facts(Body):
    """What the agent has heard that bears on priority. The server also reads every caller line."""

    issue: str = Field("", max_length=300)
    who_home: str = Field("", max_length=300)
    inside_temp_f: float | None = Field(None, ge=0, le=130)
    temp_falling_fast: bool = False


class FindSlotsIn(CallRef):
    job_type: str = Field(max_length=40)
    priority: Priority | None = Field(None, description="The agent's view; logged, not used")
    preferred_days: list[str] = Field(default_factory=list, max_length=7)
    time_of_day: Literal["morning", "afternoon", "evening"] | None = None
    day: dt.date | None = Field(None, description="Ask for one day's exact windows")
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
    since: str = Field(min_length=1, max_length=100)
    tried: str = Field(min_length=1, max_length=300)
    who_home: str = Field(min_length=1, max_length=120)
    approver: str = Field(min_length=1, max_length=120)
    system_type: str = Field("", max_length=120)
    arrival_notes: str = Field("", max_length=300)
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
    reason: str = Field(min_length=3, max_length=200)


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
    callback_day: Literal["today", "tomorrow"] = Field(
        "today",
        description="The day the caller asked for. The times above are on that day; a bare hour "
        'such as "2" means 2 PM.',
    )

    @model_validator(mode="after")
    def window_or_time(self):
        window = self.callback_after or self.callback_before
        if window and (self.callback_time or self.callback_in_minutes is not None):
            raise ValueError("give callback_after or callback_before, or a callback time, not both")
        return self


class FlagIn(CallRef, CallbackTime):
    reason: str = Field(min_length=3, max_length=300)
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
    pass


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
