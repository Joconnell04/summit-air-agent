"""Owner settings (app/owner_settings.py): the defaults, the audit trail, each call keeping the values
it started under, and each setting's effect where the server reads it. The two-party consent switch
adds the recording notice to the greeting and drops the recording of a caller who objects."""

import re

import pytest
from test_ts_jobs import FakeMailer, alert, urgent_flag
from test_ts_recordings import AUDIO, FakeBucket, collect, record
from test_ts_safety import GAS_LINES
from test_ts_summary import FakeModel, booked_call, run, status
from test_ts_transfer import EVENING, ask, asks_for_a_person, flag, no_heat_tonight
from toolkit import ROOT, UNKNOWN, Agent, count, make_server, sign_in

from app import owner_settings, recordings

NOTICE = "This call may be recorded."


@pytest.fixture
def recorded(tmp_path, clock, mock):
    app = make_server(tmp_path, clock, mock[0], recording_bucket="calls")
    bucket = FakeBucket()
    app.state.services.bucket = bucket
    return Agent(app, clock), bucket


def tenant(agent: Agent):
    return agent.app.state.services.tenants.default


def set_to(agent: Agent, author: str = "Pat", **values) -> list[str]:
    conn = agent.db()
    try:
        return owner_settings.change(conn, tenant(agent), values, author, agent.clock())
    finally:
        conn.close()


def current(agent: Agent) -> dict:
    conn = agent.db()
    try:
        return owner_settings.current(conn, tenant(agent))
    finally:
        conn.close()


# ---- defaults, changes, history ---------------------------------------------------------------


def test_the_defaults_are_consent_off_and_everything_else_as_the_tenant_is_set_up(agent):
    assert current(agent) == {
        "two_party_consent": False,
        "call_recordings": True,
        "warm_transfer": True,
        "technician_summaries": True,
        "on_call_alerts": True,
    }


def test_only_a_real_change_is_stored_and_the_newest_wins(agent, clock):
    assert set_to(agent, two_party_consent=False) == []
    assert set_to(agent, two_party_consent=True, call_recordings=True) == ["two_party_consent"]
    clock.advance(minutes=1)
    assert set_to(agent, "Lee", two_party_consent=False) == ["two_party_consent"]
    assert current(agent)["two_party_consent"] is False
    conn = agent.db()
    try:
        rows = owner_settings.history(conn, tenant(agent))
    finally:
        conn.close()
    assert [(r["author"], r["value"]) for r in rows] == [("Lee", 0), ("Pat", 1)]


def test_an_unknown_setting_is_refused(agent):
    with pytest.raises(owner_settings.SettingsError):
        set_to(agent, priority=False)
    assert count(agent, "SELECT COUNT(*) FROM setting_changes") == 0


def test_a_call_keeps_the_settings_it_started_under(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    set_to(agent, two_party_consent=True, warm_transfer=False)
    conn = agent.db()
    try:
        kept = owner_settings.for_call(conn, tenant(agent), call)
    finally:
        conn.close()
    assert kept["two_party_consent"] is False and kept["warm_transfer"] is True
    later = agent.start("(212) 555-0123")["data"]
    assert later["recording_notice"] == NOTICE, "the next call reads the new values"


def test_only_the_settings_module_writes_a_setting():
    """No tool, turn or other route can change a setting; the agent never reaches one."""
    writers = [
        p.relative_to(ROOT).as_posix()
        for p in sorted((ROOT / "app").rglob("*.py"))
        if re.search(r"INSERT INTO setting_changes", p.read_text())
    ]
    assert writers == ["app/owner_settings.py"]
    callers = [
        p.relative_to(ROOT).as_posix()
        for p in sorted((ROOT / "app").rglob("*.py"))
        if "owner_settings.change(" in p.read_text()
    ]
    assert callers == ["app/owner_view.py"]


def test_the_settings_never_reach_the_agents_prompt(agent):
    set_to(agent, two_party_consent=True)
    data = agent.start(UNKNOWN)["data"]
    from prompts.assemble import instructions

    prompt = instructions(data)
    assert "two_party_consent" not in prompt and "warm_transfer" not in prompt


# ---- two-party consent: the notice ------------------------------------------------------------


def test_with_consent_off_there_is_no_notice(agent):
    assert agent.start(UNKNOWN)["data"]["recording_notice"] is None


def test_with_consent_on_the_start_carries_the_notice(agent):
    set_to(agent, two_party_consent=True)
    assert agent.start(UNKNOWN)["data"]["recording_notice"] == NOTICE


def test_a_tenant_can_word_the_notice_and_the_stopped_line_itself(agent, monkeypatch):
    voice = tenant(agent).agent["voice"]
    monkeypatch.setitem(voice, "recording_notice", "Calls are recorded for quality.")
    monkeypatch.setitem(voice, "recording_stopped", "Sure, recording's off.")
    set_to(agent, two_party_consent=True)
    call = agent.start(UNKNOWN)["data"]
    assert call["recording_notice"] == "Calls are recorded for quality."
    stopped = agent.turn(call["call_id"], "Don't record this.")["data"]["recording"]
    assert stopped == {"action": "stop", "text": "Sure, recording's off."}


def test_the_settings_page_reads_the_switch_back(agent):
    sign_in(agent)
    agent.client.post("/owner/settings", data={"s.two_party_consent": "1"})
    page = agent.client.get("/owner/settings").text
    assert 'name="s.two_party_consent" value="1" checked' in page


# ---- two-party consent: an objection ----------------------------------------------------------


@pytest.mark.parametrize("said", [
    "Please don't record this call.",
    "I do not consent to being recorded.",
    "Can you stop recording? My furnace is out.",
    "I'd rather not be recorded.",
])
def test_these_count_as_an_objection(said):
    assert recordings.objected(said)


@pytest.mark.parametrize("said", [
    "Your records should have my address.",
    "It was a record cold night and the heat's out.",
    "Is this call recorded?",
    "I don't care, go ahead.",
])
def test_these_do_not(said):
    assert not recordings.objected(said)


def test_with_consent_on_an_objection_stops_the_recording_once(recorded):
    agent, _ = recorded
    set_to(agent, two_party_consent=True)
    call = agent.start(UNKNOWN)["data"]["call_id"]
    record(agent, call)
    first = agent.turn(call, "Please don't record this call. My AC is out.")["data"]
    assert first["recording"] == {"action": "stop", "text": recordings.STOPPED}
    again = agent.turn(call, "And don't record me, okay?")["data"]
    assert again["recording"] is None
    assert count(agent, "SELECT seq FROM recording_declines") == 1


def test_with_consent_off_an_objection_changes_nothing(recorded):
    agent, _ = recorded
    call = agent.start(UNKNOWN)["data"]["call_id"]
    data = agent.turn(call, "Please don't record this call.")["data"]
    assert data["recording"] is None
    assert count(agent, "SELECT COUNT(*) FROM recording_declines") == 0


def test_an_objected_recording_is_deleted_from_the_bucket_and_never_kept(recorded):
    agent, bucket = recorded
    set_to(agent, two_party_consent=True)
    call = agent.start(UNKNOWN)["data"]["call_id"]
    record(agent, call)
    agent.turn(call, "Don't record this please.")
    agent.end(call)
    key = f"summit-air/{call}.mp4"
    bucket.objects[key] = AUDIO
    assert collect(agent) == {"stored": 0, "missing": 0, "declined": 1}
    assert key not in bucket.objects
    folder = agent.app.state.services.settings.recordings_dir
    assert not (folder / key).exists()
    assert count(agent, "SELECT status FROM recordings") == "deleted"
    sign_in(agent)
    assert agent.client.get(f"/owner/calls/{call}/recording").status_code == 404


def test_an_objected_recording_not_in_the_bucket_yet_waits_then_is_closed(recorded, clock):
    agent, _ = recorded
    set_to(agent, two_party_consent=True)
    call = agent.start(UNKNOWN)["data"]["call_id"]
    record(agent, call)
    agent.turn(call, "Don't record this please.")
    agent.end(call)
    assert collect(agent) == {"stored": 0, "missing": 0, "declined": 0}
    assert count(agent, "SELECT status FROM recordings") == "recording"
    clock.advance(hours=3)
    assert collect(agent)["declined"] == 1


def test_with_consent_on_a_call_that_opens_with_the_safety_lines_isnt_recorded(recorded, clock):
    agent, _ = recorded
    set_to(agent, two_party_consent=True)
    first = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(first, "I smell gas")
    agent.end(first, "dropped")
    clock.advance(minutes=2)
    second = agent.start(UNKNOWN)
    assert second["say"] == GAS_LINES
    assert second["data"]["recording"] is None


# ---- call recordings ---------------------------------------------------------------------------


def test_with_call_recordings_off_no_call_is_recorded(recorded):
    agent, _ = recorded
    assert agent.start(UNKNOWN)["data"]["recording"] is not None
    set_to(agent, call_recordings=False)
    assert agent.start("(212) 555-0123")["data"]["recording"] is None


# ---- warm transfer -----------------------------------------------------------------------------


@pytest.fixture
def numbers(monkeypatch):
    monkeypatch.setenv("TRANSFER_OFFICE_NUMBER", "+12125550100")
    monkeypatch.setenv("TRANSFER_ON_CALL_NUMBER", "+12125550111")


def test_with_warm_transfer_off_a_caller_asking_for_a_person_gets_the_callback(agent, numbers):
    set_to(agent, warm_transfer=False)
    call = asks_for_a_person(agent)
    flagged = flag(agent, call, callback_time="11 AM")
    assert ask(agent, call, flagged)["data"]["transfer"] is None
    assert count(agent, "SELECT COUNT(*) FROM transfers") == 0
    assert count(agent, "SELECT COUNT(*) FROM callbacks") == 1


def test_with_warm_transfer_off_after_hours_on_call_isnt_rung(agent, clock, numbers):
    set_to(agent, warm_transfer=False)
    clock.now = EVENING
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "The heat's out and my mother is 88.")
    no_heat_tonight(agent, call, "My mother, she's 88")
    flagged = flag(agent, call, priority="Urgent")
    assert ask(agent, call, flagged)["data"]["transfer"] is None


def test_warm_transfer_on_still_rings_the_office(agent, numbers):
    call = asks_for_a_person(agent)
    flagged = flag(agent, call)
    assert ask(agent, call, flagged)["data"]["transfer"]["role"] == "office"


# ---- technician summaries ----------------------------------------------------------------------


def test_with_technician_summaries_off_a_booked_call_gets_none(agent):
    set_to(agent, technician_summaries=False)
    call, _ = booked_call(agent)
    model = FakeModel()
    assert run(agent, model)["skipped"] == 1
    assert status(agent, call) == "skipped" and model.records == []


def test_a_call_booked_with_summaries_on_still_gets_one_after_they_are_turned_off(agent):
    call, _ = booked_call(agent)
    set_to(agent, technician_summaries=False)
    assert run(agent, FakeModel())["done"] == 1
    assert status(agent, call) == "done"


# ---- on-call alerts ----------------------------------------------------------------------------


def test_with_on_call_alerts_off_an_urgent_flag_isnt_emailed(agent, clock):
    agent.app.state.services.mailer = mailer = FakeMailer()
    set_to(agent, on_call_alerts=False)
    clock.advance(hours=12)
    urgent_flag(agent)
    assert alert(agent) == {"sent": 0, "held": 0, "failed": 0}
    assert mailer.sent == []


def test_with_on_call_alerts_off_a_safety_call_is_still_emailed(agent, clock):
    agent.app.state.services.mailer = mailer = FakeMailer()
    set_to(agent, on_call_alerts=False)
    clock.advance(hours=12)
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I smell gas in the basement.")
    assert alert(agent)["sent"] == 1
    assert len(mailer.sent) == 1


# ---- the voice layer ---------------------------------------------------------------------------


def test_both_voice_stacks_say_the_notice_before_the_greeting():
    from voice.livekit.worker import opening
    from voice.retell.conversation import opening as retell_opening

    assert opening({"recording_notice": NOTICE}, "Hi.") == f"{NOTICE} Hi."
    assert opening({"recording_notice": None}, "Hi.") == "Hi."
    lines = {"greeting_unknown": "Thanks for calling.", "greeting_known": "Hi {first_name}."}
    start = {"recording_notice": NOTICE, "customers": []}
    assert retell_opening(start, "", lines) == f"{NOTICE} Thanks for calling."
    assert retell_opening({**start, "recording_notice": None}, "", lines) == "Thanks for calling."

