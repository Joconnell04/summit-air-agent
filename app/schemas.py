"""Request bodies. Every tool argument is typed and length-limited here; anything else is refused
before a handler runs (docs/architecture.md, checked arguments)."""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .settings import NOTE_MAX_CHARS

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
    window: str = Field(max_length=40)


class JobDetails(Body):
    """The intake every booking carries (spec S-13). Address and phone come from the read-back."""

    issue: str = Field(min_length=1, max_length=300)
    since: str = Field(min_length=1, max_length=100)
    tried: str = Field(min_length=1, max_length=300)
    who_home: str = Field(min_length=1, max_length=120)
    approver: str = Field(min_length=1, max_length=120)
    system_type: str = Field("", max_length=120)
    arrival_notes: str = Field("", max_length=300)
    name: str = Field("", max_length=80, description="Needed only for a caller not on file")
    inside_temp_f: float | None = Field(None, ge=0, le=130)


class BookIn(CallRef):
    job_type: str = Field(max_length=40)
    slot: Slot
    details: JobDetails
    priority: Priority | None = Field(None, description="The agent's view; logged, not used")


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
