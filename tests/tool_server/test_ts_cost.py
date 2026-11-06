"""The Results page's cost estimate (app/cost.py): inputs suggested from the company's own recent
calls, the month's sum, and the panel on the page."""

from datetime import timedelta

from toolkit import UNKNOWN, sign_in

from app import cost


def tenant(agent):
    return agent.app.state.services.tenants.default


def suggested(agent):
    conn = agent.db()
    try:
        return cost.recommended(conn, tenant(agent), agent.clock())
    finally:
        conn.close()


def call_for(agent, clock, minutes: float) -> str:
    call = agent.start(UNKNOWN)["data"]["call_id"]
    clock.now += timedelta(minutes=minutes)
    agent.client.post(f"/calls/{call}/end", json={"reason": "caller_hangup"})
    return call


def test_with_no_calls_the_suggestions_are_the_measured_defaults(agent):
    s = suggested(agent)
    assert not s["from_calls"]
    assert s["minutes_per_call"] == cost.DEFAULTS["minutes_per_call"]
    assert s["voice_per_minute"] == cost.VOICE_PER_MINUTE


def test_the_suggested_length_and_volume_come_from_recent_calls(agent, clock):
    for minutes in (2, 4):
        call_for(agent, clock, minutes)
    s = suggested(agent)
    assert s["from_calls"] and s["calls"] == 2
    assert s["minutes_per_call"] == 3.0
    # Two calls inside one day is about 60 a month, shown at the slider's floor of 100.
    assert s["calls_per_month"] == 100
    assert s["booked_share"] == 0


def test_a_hang_up_in_the_first_seconds_does_not_count_toward_length(agent, clock):
    call_for(agent, clock, 3)
    call_for(agent, clock, 0.02)
    assert suggested(agent)["calls"] == 1


def test_the_month_is_minutes_at_the_voice_rate_plus_the_model_plus_fixed():
    m = cost.monthly(1000, 2.5, 0.5, 0.1, 20, model_per_call=0.01)
    assert round(m["per_call"], 4) == 0.26
    assert round(m["total"], 2) == 280.0
    assert round(m["per_booked_job"], 2) == 0.56
    assert round(m["per_year"], 2) == 3360.0


def test_no_bookings_gives_no_cost_per_booked_job():
    assert cost.monthly(500, 2, 0, 0.1, 0)["per_booked_job"] is None


def test_the_results_page_shows_the_estimate_with_its_suggestions(agent, clock):
    call_for(agent, clock, 2)
    sign_in(agent)
    page = agent.client.get("/owner/results").text
    assert "Cost estimate" in page and 'id="cost-data"' in page
    assert "suggested from your last 1 calls" in page or "suggested from your last 1 call" in page
    for name in ("calls_per_month", "minutes_per_call", "booked_share", "voice_per_minute", "fixed_monthly"):
        assert f'name="{name}"' in page
    assert "Use suggested" in page
