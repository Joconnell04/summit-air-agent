"""Emergency dispatch (DECISIONS.md 123): a no-heat or no-cooling call the server scores High or
Urgent books the soonest technician at the read-back, with no windows offered, and the dispatch
sentence is the read-back."""

from itertools import pairwise

from toolkit import TENANT_DIR, UNKNOWN, book, in_area_address

from app.tenant import load_agent

LINES = load_agent(TENANT_DIR)[0]["lines"]


def _start_no_heat(agent, caller_id=UNKNOWN, said="Hi, my heat's out and my mother is 82, she's here with me."):
    call = agent.start(caller_id)["data"]["call_id"]
    agent.turn(call, said)
    agent.turn(call, "My name is Pat Rivera.")
    return call


def _dispatch(agent, call, address=None, job_type="no_heat", phone="caller_id"):
    address = address or in_area_address()
    agent.turn(call, f"It's {address['street']}, {address['zip']}.")
    return agent.tool(
        "read_back", call, address=address, phone=phone, job_type=job_type
    )


def test_a_high_priority_no_heat_call_is_dispatched_at_the_read_back(agent):
    call = _start_no_heat(agent)
    sent = _dispatch(agent, call)
    assert sent["ok"], sent
    data = sent["data"]
    assert data["dispatched"] and data["confirmed"]
    assert data["priority"]["level"] in ("High", "Urgent")
    # The sentence is the read-back: the address, the number by its last four, and a question.
    say = sent["say"]
    assert "right away" in say and "one oh one Union St" in say
    assert "ending in zero nine nine nine" in say
    assert say.endswith("Is that the right address?")
    assert "within the next hour" in say or "by " in say
    assert "Will someone be home" in data["next"] and "add_note" in data["next"]
    with agent.db() as db:
        row = db.execute(
            "SELECT status, job_type, priority FROM bookings WHERE call_id = ? AND kind = 'book'",
            (call,),
        ).fetchone()
        assert row["status"] == "booked" and row["job_type"] == "no_heat"
        assert row["priority"] in ("High", "Urgent")
        assert db.execute("SELECT COUNT(*) FROM dispatches WHERE call_id = ?", (call,)).fetchone()[0] == 1


def test_the_soonest_window_is_dispatched(agent):
    call = _start_no_heat(agent)
    soonest = agent.tool("find_slots", call, job_type="no_heat")["data"]["first_urgent"]
    sent = _dispatch(agent, call)
    assert sent["data"]["window"]["date"] == soonest["date"]
    assert sent["data"]["window"]["window"] == soonest["window"]


def test_the_callers_yes_confirms_with_nothing_more_to_ask(agent):
    call = _start_no_heat(agent)
    address = in_area_address()
    sent = _dispatch(agent, call, address)
    agent.say(call, sent["say"])
    agent.turn(call, "Yes, that's right.")
    yes = agent.tool("read_back", call, address=address, phone="caller_id", confirmed=True)
    assert yes["ok"], yes
    assert yes["data"]["dispatched"] and yes["say"] == ""
    assert "Will someone be home" in yes["data"]["next"]
    # Times asked for anyway are not offered, and a booking tried anyway books nothing more.
    found = agent.tool("find_slots", call, job_type="no_heat")
    assert found["data"]["dispatched"] and found["say"] == ""
    slot = {"date": sent["data"]["window"]["date"], "window": sent["data"]["window"]["window"]}
    again = book(agent, call, slot, job_type="no_heat")
    assert again.get("refused") == "already_booked", again


def test_a_corrected_unit_moves_the_dispatch(agent):
    call = _start_no_heat(agent)
    address = in_area_address()
    first = _dispatch(agent, call, address)
    first_job = first["data"]["booking_id"]
    agent.say(call, first["say"])
    agent.turn(call, "It's unit 2.")
    moved = agent.tool(
        "read_back", call, address={**address, "unit": "2"}, phone="caller_id", job_type="no_heat"
    )
    assert moved["ok"] and moved["data"]["dispatched"], moved
    assert moved["data"]["booking_id"] != first_job
    assert "unit two" in moved["say"]
    with agent.db() as db:
        live = db.execute(
            "SELECT COUNT(*) FROM bookings WHERE call_id = ? AND kind = 'book' AND status = 'booked'",
            (call,),
        ).fetchone()[0]
        assert live == 1


def test_a_routine_job_type_reads_back_as_usual(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I'd like a tune-up.")
    sent = _dispatch(agent, call, job_type="tune_up")
    assert sent["ok"] and not sent["data"].get("dispatched"), sent
    assert sent["data"]["confirmed"] is False


def test_a_gas_call_never_dispatches(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "My heat's out and I smell gas.")
    sent = _dispatch(agent, call)
    assert not (sent["data"] or {}).get("dispatched"), sent


def test_after_hours_the_on_call_technician_calls_first(agent, clock):
    clock.advance(hours=13)  # 10 PM in New York
    call = _start_no_heat(agent, said="My heat is out, it's 50 degrees inside and my mother is 82.")
    sent = _dispatch(agent, call)
    assert sent["ok"] and sent["data"]["dispatched"], sent
    assert "Our on-call technician will call you within twenty minutes" in sent["say"]
    with agent.db() as db:
        urgent = db.execute(
            "SELECT 1 FROM flags WHERE call_id = ? AND kind = 'person' AND priority = 'Urgent'",
            (call,),
        ).fetchone()
        assert urgent


def _fsm(agent):
    return agent.app.state.services.fsm["summit-air"]


def test_the_visit_booked_with_the_old_unit_is_cancelled(agent):
    call = _start_no_heat(agent)
    address = in_area_address()
    first = _dispatch(agent, call, address)
    agent.say(call, first["say"])
    agent.turn(call, "It's unit 2.")
    moved = agent.tool(
        "read_back", call, address={**address, "unit": "2"}, phone="caller_id", job_type="no_heat"
    )
    assert moved["data"]["dispatched"], moved
    assert _fsm(agent).job(first["data"]["booking_id"])["jobStatus"] == "Canceled"
    assert _fsm(agent).job(moved["data"]["booking_id"])["jobStatus"] == "Scheduled"


def test_a_corrected_number_books_again_at_the_same_address(agent):
    call = _start_no_heat(agent)
    address = in_area_address()
    first = _dispatch(agent, call, address)
    agent.say(call, first["say"])
    agent.turn(call, "Actually call me on 718 555 0142.")
    moved = agent.tool(
        "read_back", call, address=address, phone="7185550142", job_type="no_heat"
    )
    assert moved["data"]["dispatched"], moved
    assert "ending in zero one four two" in moved["say"]


def test_facts_that_raise_the_priority_dispatch_at_find_slots(agent, monkeypatch):
    """A read-back below High reads back as usual; once the caller's facts raise it, find_slots
    dispatches instead of offering times."""
    from dataclasses import replace

    from app import tools

    real = tools._priority
    calm = {"on": True}

    def priority(ctx, job_type, inside, wait=True):
        found = real(ctx, job_type, inside, wait)
        if calm["on"]:
            return replace(found, priority=replace(found.priority, level="Normal"))
        return found

    monkeypatch.setattr(tools, "_priority", priority)
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "My heat's out.")
    address = in_area_address()
    asked = _dispatch(agent, call, address)
    assert not asked["data"].get("dispatched") and asked["data"]["confirmed"] is False
    agent.say(call, asked["say"])
    agent.turn(call, "Yes. And my mother is 82, she's here with me.")
    agent.tool("read_back", call, address=address, phone="caller_id", confirmed=True)
    calm["on"] = False
    found = agent.tool("find_slots", call, job_type="no_heat")
    assert found["data"]["dispatched"], found
    assert "Is that the right address?" in found["say"]


def test_a_dispatched_job_is_tagged_urgent_in_the_crm(agent):
    call = _start_no_heat(agent)
    sent = _dispatch(agent, call)
    job = _fsm(agent).job(sent["data"]["booking_id"])
    assert job["tagTypeIds"] == [804]


def test_a_high_raise_after_hours_goes_on_call_not_to_the_morning(agent, clock):
    """Jackson's midnight call: "super urgent", then "my elderly grandma's here", then "You send
    somebody now". It reached High and heard 8 AM, then a callback."""
    clock.advance(hours=15)  # midnight in New York
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "Hi, my heat's out, it's super urgent.")
    agent.turn(call, "My elderly grandma's here.")
    agent.turn(call, "You send somebody now.")
    sent = _dispatch(agent, call)
    assert sent["ok"] and sent["data"]["dispatched"], sent
    assert sent["data"]["on_call"] is True
    assert "Our on-call technician will call you within twenty minutes" in sent["say"]
    assert "8" not in sent["say"].split("minutes", 1)[1]
    with agent.db() as db:
        urgent = db.execute(
            "SELECT COUNT(*) FROM flags WHERE call_id = ? AND kind = 'person' AND priority = 'Urgent'",
            (call,),
        ).fetchone()[0]
        callback = db.execute("SELECT 1 FROM callbacks WHERE call_id = ?", (call,)).fetchone()
    assert urgent == 1 and callback


def test_the_read_back_starts_with_a_connector_never_twice_the_same(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I need a tune-up.")
    said = []
    for n in range(4):
        address = in_area_address(n + 1)
        agent.turn(call, f"It's {address['street']}, {address['zip']}.")
        asked = agent.tool("read_back", call, address=address, phone="caller_id")
        assert asked["say"].endswith(asked["data"]["sentence"])
        said.append(asked["say"][: -len(asked["data"]["sentence"])].strip())
    assert all(word in ("Got it.", "Perfect.", "Okay.") for word in said), said
    assert all(a != b for a, b in pairwise(said)), said
