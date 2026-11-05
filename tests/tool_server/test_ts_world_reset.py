"""Resetting the live world between calls (scripts/reset_live_world.py): after the mock is reseeded,
nothing the tool server held from the old world reaches a new call."""

from toolkit import UNKNOWN, in_area_address, ready_to_book

from scripts.reset_live_world import clear_tool_server


def test_a_call_after_a_reset_never_picks_up_one_from_before_it(agent, clock, tmp_path):
    first = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(first, "Hi, the heat's out.")
    agent.end(first, "dropped")
    clock.advance(minutes=1)
    cleared = clear_tool_server(agent.app.state.services.settings.db_path, clock.now)
    assert cleared["resets"] == 1
    clock.advance(minutes=1)
    assert agent.start(UNKNOWN)["data"]["linked_call"] is None


def test_the_reset_forgets_old_world_ids_and_stops_queued_work(agent, clock):
    call = ready_to_book(agent)
    conn = agent.db()
    conn.execute(
        "INSERT INTO call_customers (call_id, customer_id, matched_by, customer_type) "
        "VALUES (?, 301, 'phone', 'Residential')",
        (call,),
    )
    conn.commit()
    conn.close()
    cleared = clear_tool_server(agent.app.state.services.settings.db_path, clock.now)
    assert cleared["call_customers"] == 1
    conn = agent.db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM call_customers").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 1, "history stays"
    finally:
        conn.close()


def test_a_customer_servicetitan_no_longer_has_is_not_the_callers_account(agent):
    """call-_+16466240418_ECUCgf3cyyQP: the call carried customer 301 from the old world, the name
    question was skipped, and the new customer went to ServiceTitan with no name (a 400)."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    conn = agent.db()
    conn.execute(
        "INSERT INTO call_customers (call_id, customer_id, matched_by, customer_type) "
        "VALUES (?, 999999, 'phone', 'Residential')",
        (call,),
    )
    conn.commit()
    conn.close()
    confirmed = agent.confirm_read_back(call, in_area_address())
    assert confirmed["say"] == "Thanks. And what name should I put the visit under?"
