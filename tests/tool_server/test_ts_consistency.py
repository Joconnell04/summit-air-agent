"""A booking that doesn't match the confirmed read-back is flagged Urgent for a person when the
call ends, and shows on the owner view (app/consistency.py)."""

import json

from toolkit import book, first_open, ready_to_book, sign_in


def flags(agent, call):
    with agent.db() as db:
        return [r[0] for r in db.execute(
            "SELECT reason FROM flags WHERE call_id = ? AND priority = 'Urgent'", (call,))]


def test_a_matching_booking_raises_nothing(agent):
    call = ready_to_book(agent)
    assert book(agent, call, first_open(agent, call))["ok"]
    agent.end(call)
    assert not [f for f in flags(agent, call) if "read-back" in f]


def test_a_booking_at_another_door_is_flagged_and_shown_to_the_owner(agent):
    call = ready_to_book(agent)
    assert book(agent, call, first_open(agent, call))["ok"]
    # Corrupt the booked job as if a bug had sent another unit and number to ServiceTitan.
    with agent.db() as db:
        row = db.execute("SELECT id, request_json FROM bookings WHERE call_id = ?", (call,)).fetchone()
        req = json.loads(row["request_json"])
        req["address"]["unit"] = "9F"
        req["phone"] = "2125550000"
        db.execute("UPDATE bookings SET request_json = ? WHERE id = ?", (json.dumps(req), row["id"]))
    agent.end(call)
    found = [f for f in flags(agent, call) if f.startswith("Booking doesn't match the read-back")]
    assert found and "booked unit 9F" in found[0] and "booked phone ending 0000" in found[0]
    sign_in(agent)
    assert "Booking doesn&#39;t match the read-back" in agent.client.get("/owner").text or \
        "Booking doesn't match the read-back" in agent.client.get("/owner").text
