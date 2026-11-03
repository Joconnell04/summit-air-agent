import json

import pytest

from evals.checks import CHECKS, Evidence, run_checks, safety_config

SAFETY = safety_config()
GAS = SAFETY["kinds"]["gas"]["lines"]


def trace(turns=(), tools=(), bookings=(), flags=(), callbacks=(), out_of_area=(), **call):
    events = [
        {"type": "turn", "speaker": s, "text": t, "safety_action": a} for s, t, a in turns
    ] + [
        {"type": "tool", "tool": n, "args": a, "ok": ok} for n, a, ok in tools
    ]
    base = {"safety_state": "none", "safety_kind": None, "safety_flag_id": None,
            "readback_json": None, "outcome": None, "end_reason": None}
    return {"call": {**base, **call}, "events": events, "bookings": list(bookings),
            "flags": list(flags), "callbacks": list(callbacks), "out_of_area": list(out_of_area)}


def failures(check, arg, t):
    return CHECKS[check].run(arg, Evidence(t, SAFETY))


SIGNAL = [("caller", "it smells like gas", "speak_lines")]


def test_safety_lines_said_first_passes_when_the_next_line_is_the_script():
    t = trace(SIGNAL + [("agent", GAS, None)], safety_state="lines_said", safety_kind="gas",
              safety_flag_id=1)
    assert failures("safety_lines", "said_first", t) == []


def test_safety_lines_said_first_fails_when_the_agent_says_something_else_first():
    t = trace(SIGNAL + [("agent", "What's your address?", None), ("agent", GAS, None)],
              safety_state="lines_said", safety_kind="gas", safety_flag_id=1)
    assert "first reply" in failures("safety_lines", "said_first", t)[0]


def test_safety_lines_said_first_fails_without_a_signal_or_a_flag():
    assert failures("safety_lines", "said_first", trace()) == ["no safety signal fired"]
    t = trace(SIGNAL + [("agent", GAS, None)], safety_state="lines_said", safety_kind="gas")
    assert failures("safety_lines", "said_first", t) == ["no person was flagged for the safety call"]


def test_safety_lines_not_said_catches_lines_on_a_call_without_a_signal():
    t = trace([("caller", "my gas furnace is out", None), ("agent", GAS, None)])
    assert failures("safety_lines", "not_said", t) == ["the agent said the gas safety lines"]
    assert failures("safety_lines", "not_said", trace([("agent", "Hi", None)])) == []


def test_safety_check_asked_and_cleared():
    t = trace([("caller", "smells off", "ask_check")], safety_state="cleared")
    assert failures("safety_check", "asked", t) == []
    assert failures("safety_check", "cleared", t) == []
    assert failures("safety_check", "asked", trace()) != []


def booked(priority="Normal", **extra):
    return {"kind": "book", "status": "booked", "priority": priority, "seek_earlier": 0,
            "safety_override": 0, **extra}


def test_booking_counts_only_live_bookings():
    failed = {"kind": "book", "status": "failed"}
    assert failures("booking", "none", trace(bookings=[failed])) == []
    assert failures("booking", "one", trace(bookings=[booked()])) == []
    assert "2 booking(s)" in failures("booking", "one", trace(bookings=[booked(), booked()]))[0]


def test_booking_checks_priority_zip_and_job_type():
    t = trace(
        bookings=[booked("Urgent")],
        tools=[("book_appointment", {"job_type": "no_heat"}, True)],
        readback_json=json.dumps({"street": "1 Main St", "zip": "11361"}),
    )
    assert failures("booking", {"priority": "Urgent", "zip": "11361", "job_type": "no_heat"},
                    t) == []
    wrong = failures("booking", {"priority": "Normal", "zip": "11316", "job_type": "tune_up"}, t)
    assert len(wrong) == 3


def test_read_back_must_be_confirmed_before_booking():
    early = trace(tools=[("book_appointment", {}, True), ("read_back", {"confirmed": True}, True)])
    assert failures("read_back", "before_booking", early) == ["booked before a confirmed read-back"]
    asked_only = trace(tools=[("read_back", {}, True), ("book_appointment", {}, True)])
    assert failures("read_back", "before_booking", asked_only) != []
    good = trace(tools=[("read_back", {"confirmed": True}, True), ("book_appointment", {}, True)])
    assert failures("read_back", "before_booking", good) == []


def test_flag_lead_out_of_area_and_callback():
    t = trace(flags=[{"kind": "person"}], callbacks=[{"status": "pending"}],
              tools=[("record_lead", {"kind": "commercial"}, True)],
              out_of_area=[{"zip": "11501"}])
    assert failures("flag", "person", t) == []
    assert failures("flag", "none", t) != []
    assert failures("lead", "commercial", t) == []
    assert failures("lead", "install", t) == ["no install lead recorded"]
    assert failures("out_of_area", "11501", t) == []
    assert failures("out_of_area", "recorded", trace()) != []
    assert failures("callback", "promised", t) == []
    assert failures("callback", "none", t) != []


def test_no_changes_fails_on_any_write():
    assert failures("no_changes", True, trace(tools=[("add_note", {}, True)])) == []
    t = trace(tools=[("cancel_appointment", {}, True)], bookings=[{"kind": "cancel"}])
    assert failures("no_changes", True, t) != []


def test_outcome_ended_by_and_tool_checks():
    t = trace(tools=[("mark_do_not_call", {}, True), ("find_customer", {}, False)],
              outcome="declined_commercial", end_reason="caller_hangup")
    assert failures("outcome", "declined_commercial", t) == []
    assert failures("ended_by", "caller", t) == []
    assert failures("ended_by", "agent", t) != []
    assert failures("tool_ok", "mark_do_not_call", t) == []
    assert failures("tool_ok", "find_customer", t) == ["find_customer never succeeded"]
    assert failures("tool_not_called", ["get_customer_history"], t) == []
    assert failures("tool_not_called", "find_customer", t) == ["find_customer was called"]


def test_agent_says_and_never_says_are_case_insensitive_regexes():
    t = trace([("agent", "Yes, I'm an AI assistant.", None), ("agent", "That's $120.", None)])
    assert failures("agent_says", r"\bai\b", t) == []
    assert failures("agent_never_says", r"\$\s?\d", t) != []
    assert failures("agent_never_says", ["refund"], t) == []


def test_run_checks_prefixes_each_failure_with_its_check():
    got = run_checks([{"booking": "one"}, {"flag": "none"}], Evidence(trace(), SAFETY))
    assert got == ["booking: 0 booking(s) made, expected 1"]


@pytest.mark.parametrize(
    "check, arg",
    [("safety_lines", "maybe"), ("booking", {"colour": "red"}), ("out_of_area", "Queens"),
     ("agent_says", "("), ("tool_ok", 3), ("no_changes", False)],
)
def test_bad_arguments_are_rejected(check, arg):
    with pytest.raises(ValueError):
        CHECKS[check].validate(arg)
