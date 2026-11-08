import json

import pytest

from evals.checks import CHECKS, NO_ST, Evidence, check_names, run_checks, safety_config, unchecked

SAFETY = safety_config()
GAS = SAFETY["kinds"]["gas"]["lines"]


def turn(speaker, text, action=None):
    return {"type": "turn", "speaker": speaker, "text": text, "safety_action": action}


def tool(name, args=None, ok=True):
    return {"type": "tool", "tool": name, "args": args or {}, "ok": ok}


def trace(
    turns=(), tools=(), bookings=(), flags=(), callbacks=(), out_of_area=(), events=(), **call
):
    """Turns, then tools, unless `events` gives the exact order."""
    events = list(events) or [turn(*t) for t in turns] + [tool(*t) for t in tools]
    base = {
        "safety_state": "none",
        "safety_kind": None,
        "safety_flag_id": None,
        "readback_json": None,
        "outcome": None,
        "end_reason": None,
    }
    return {
        "call": {**base, **call},
        "events": events,
        "bookings": list(bookings),
        "flags": list(flags),
        "callbacks": list(callbacks),
        "out_of_area": list(out_of_area),
    }


def failures(check, arg, t):
    return CHECKS[check].run(arg, Evidence(t, SAFETY))


SIGNAL = [("caller", "it smells like gas", "speak_lines")]


def test_safety_lines_said_first_passes_when_the_next_line_is_the_script():
    t = trace(
        SIGNAL + [("agent", GAS, None)],
        safety_state="lines_said",
        safety_kind="gas",
        safety_flag_id=1,
    )
    assert failures("safety_lines", "said_first", t) == []


def test_safety_lines_said_first_fails_when_the_agent_says_something_else_first():
    t = trace(
        SIGNAL + [("agent", "What's your address?", None), ("agent", GAS, None)],
        safety_state="lines_said",
        safety_kind="gas",
        safety_flag_id=1,
    )
    assert "first reply" in failures("safety_lines", "said_first", t)[0]
    late = trace(
        SIGNAL + [("agent", "Sure, let me book that first. " + GAS, None)],
        safety_state="lines_said",
        safety_kind="gas",
        safety_flag_id=1,
    )
    assert "did not open with the lines" in failures("safety_lines", "said_first", late)[0]


def test_safety_lines_said_first_fails_without_a_signal_or_a_flag():
    assert failures("safety_lines", "said_first", trace()) == ["no safety signal fired"]
    t = trace(SIGNAL + [("agent", GAS, None)], safety_state="lines_said", safety_kind="gas")
    assert failures("safety_lines", "said_first", t) == [
        "no person was flagged for the safety call"
    ]


def test_safety_lines_not_said_catches_lines_on_a_call_without_a_signal():
    t = trace([("caller", "my gas furnace is out", None), ("agent", GAS, None)])
    assert failures("safety_lines", "not_said", t) == ["the agent said the gas safety lines"]
    assert failures("safety_lines", "not_said", trace([("agent", "Hi", None)])) == []


def test_safety_check_asked_and_cleared():
    t = trace([("caller", "smells off", "ask_check")], safety_state="cleared")
    assert failures("safety_check", "asked", t) == []
    assert failures("safety_check", "cleared", t) == []
    assert failures("safety_check", "asked", trace()) != []


def test_safety_check_asked_once_and_not_asked():
    once = trace([("caller", "the oven is on for heat", "ask_check"), ("caller", "no", "cleared")])
    assert failures("safety_check", "asked_once", once) == []
    assert failures("safety_check", "not_asked", once) != []
    twice = trace([("caller", "oven on for heat", "ask_check")] * 2)
    assert failures("safety_check", "asked_once", twice) == ["the check question was asked 2 times"]
    assert failures("safety_check", "not_asked", trace([("caller", "gas stove", None)])) == []
    assert CHECKS["safety_check"].prohibits("not_asked")


def booked(priority="Normal", **extra):
    return {
        "kind": "book",
        "status": "booked",
        "priority": priority,
        "seek_earlier": 0,
        "safety_override": 0,
        **extra,
    }


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
    assert (
        failures("booking", {"priority": "Urgent", "zip": "11361", "job_type": "no_heat"}, t) == []
    )
    wrong = failures("booking", {"priority": "Normal", "zip": "11316", "job_type": "tune_up"}, t)
    assert len(wrong) == 3


READBACK = json.dumps({"street": "1 Main St", "zip": "11231"})
SAID = turn("agent", "Let me read that back: 1 Main St, 11231. Is that right?")
CONFIRM = tool("read_back", {"confirmed": True})
BOOK = tool("book_appointment")


def test_read_back_must_be_said_then_confirmed_before_booking():
    def check(*events):
        return failures("read_back", "before_booking", trace(events=events, readback_json=READBACK))

    assert check(tool("read_back"), SAID, CONFIRM, BOOK) == []
    assert check(BOOK, SAID, CONFIRM) == ["booked before a confirmed read-back"]
    assert check(tool("read_back"), SAID, BOOK) == ["booked before a confirmed read-back"]
    assert check(tool("read_back"), CONFIRM, SAID, BOOK) == [
        "the read-back was confirmed before the agent said it"
    ]


def test_a_read_back_said_in_words_counts():
    spoken = turn("agent", "That's one Main Street, one one two three one, right?")
    got = failures(
        "read_back", "before_booking", trace(events=[spoken, CONFIRM, BOOK], readback_json=READBACK)
    )
    assert got == []


def test_flag_lead_out_of_area_and_callback():
    t = trace(
        flags=[{"kind": "person"}],
        callbacks=[{"status": "pending"}],
        tools=[("record_lead", {"kind": "commercial"}, True)],
        out_of_area=[{"zip": "11501"}],
    )
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
    t = trace(
        tools=[("mark_do_not_call", {}, True), ("find_customer", {}, False)],
        outcome="declined_commercial",
        end_reason="caller_hangup",
    )
    assert failures("outcome", "declined_commercial", t) == []
    assert failures("ended_by", "caller", t) == []
    assert failures("ended_by", "agent", t) != []
    assert failures("tool_ok", "mark_do_not_call", t) == []
    assert failures("tool_ok", "find_customer", t) == ["find_customer never succeeded"]
    assert failures("tool_not_called", ["get_customer_history"], t) == []
    assert failures("tool_not_called", "find_customer", t) == ["find_customer was called"]


def test_an_agent_hang_up_after_the_callers_goodbye_counts_as_the_callers_end():
    goodbye = trace(
        tools=[("end_call", {"reason": "caller_goodbye"}, True)], end_reason="agent_end"
    )
    assert failures("ended_by", "caller", goodbye) == []
    resolved = trace(tools=[("end_call", {"reason": "resolved"}, True)], end_reason="agent_end")
    assert failures("ended_by", "caller", resolved) != []
    assert failures("ended_by", "agent", resolved) == []


def test_agent_says_and_never_says_are_case_insensitive_regexes():
    t = trace([("agent", "Yes, I'm an AI assistant.", None), ("agent", "That's $120.", None)])
    assert failures("agent_says", r"\bai\b", t) == []
    assert failures("agent_never_says", r"\$\s?\d", t) != []
    assert failures("agent_never_says", ["refund"], t) == []


def test_agent_says_at_most_counts_agent_lines():
    t = trace(
        [
            ("agent", "I'd recommend getting outside.", None),
            ("caller", "No.", None),
            ("agent", "I'd still recommend going outside.", None),
        ]
    )
    assert failures("agent_says_at_most", {"recommend": 2}, t) == []
    assert failures("agent_says_at_most", {"recommend": 1}, t) == [
        "the agent said /recommend/ in 2 lines, at most 1 allowed"
    ]
    assert CHECKS["agent_says_at_most"].prohibits({"recommend": 1})


def test_prohibitions_only_skips_checks_that_need_the_call_to_finish():
    must = [{"booking": "one"}, {"booking": "none"}, {"lead": "commercial"}]
    t = trace(bookings=[booked(), booked()])
    assert run_checks(must, Evidence(t, SAFETY), prohibitions_only=True) == [
        "booking: 2 booking(s) made, expected none"
    ]


def test_check_names_lists_each_check_once_in_order():
    must = [{"booking": "none"}, {"flag": "person"}, {"booking": "none"}]
    assert check_names(must) == ["booking", "flag"]
    assert check_names(must, prohibitions_only=True) == ["booking"]


def test_run_checks_prefixes_each_failure_with_its_check():
    got = run_checks([{"booking": "one"}, {"flag": "none"}], Evidence(trace(), SAFETY))
    assert got == ["booking: 0 booking(s) made, expected 1"]


@pytest.mark.parametrize(
    "check, arg",
    [
        ("safety_lines", "maybe"),
        ("booking", {"colour": "red"}),
        ("out_of_area", "Queens"),
        ("agent_says", "("),
        ("tool_ok", 3),
        ("no_changes", False),
        ("agent_says_at_most", {"outside": "two"}),
        ("agent_says_at_most", {"(": 1}),
        ("agent_says_at_most", "outside"),
    ],
)
def test_bad_arguments_are_rejected(check, arg):
    with pytest.raises(ValueError):
        CHECKS[check].validate(arg)


BOOKED_ROW = {
    "kind": "book",
    "status": "booked",
    "priority": "Normal",
    "seek_earlier": 0,
    "safety_override": 0,
}


def test_booking_checks_the_window_the_street_and_whose_account():
    """Eval round 2: seeds 5 and 7, and the spelled-street scenarios."""
    args = {
        "job_type": "tune_up",
        "slot": {"date": "2026-11-03", "window": "morning"},
        "new_address_account": "own",
    }
    readback = json.dumps({"street": "81 Houston St", "unit": "", "zip": "10302"})
    t = trace(tools=[("book_appointment", args)], bookings=[BOOKED_ROW], readback_json=readback)
    want = {"window": "morning", "street": "Houston Street", "new_address_account": "own"}
    assert failures("booking", want, t) == []
    assert failures("booking", {"window": "afternoon"}, t) == [
        "window ['morning'], expected afternoon"
    ]
    assert "expected 'Huston St'" in failures("booking", {"street": "Huston St"}, t)[0]
    assert failures("booking", {"new_address_account": "caller"}, t) == [
        "new_address_account ['own'], expected caller"
    ]


def test_callback_checks_the_day_and_the_earliest_time():
    """Eval round 2, seed 8: "call me tomorrow, but not before noon"."""
    started = "2026-11-02T14:00:00Z"  # 9 AM New York, Monday
    want = {"day": 1, "not_before": "12:00"}
    tomorrow_evening = trace(callbacks=[{"due_at": "2026-11-04T02:00:00Z"}], started_at=started)
    assert failures("callback", want, tomorrow_evening) == []
    today = trace(callbacks=[{"due_at": "2026-11-03T02:00:00Z"}], started_at=started)
    assert "expected Tue at or after 12:00 PM" in failures("callback", want, today)[0]
    morning = trace(callbacks=[{"due_at": "2026-11-03T15:00:00Z"}], started_at=started)
    assert failures("callback", want, morning)
    assert failures("callback", want, trace(started_at=started)) == ["no callback was promised"]
    with pytest.raises(ValueError):
        CHECKS["callback"].validate({"tomorrow": True})


def test_an_approval_check_flag_can_be_required():
    """Eval round 3: a renter with an unknown approver books with an approval call, flagged as
    approval_check rather than person (renter-landlord-unknown)."""
    t = trace()
    t["flags"] = [{"kind": "approval_check"}]
    assert failures("flag", "approval_check", t) == []
    assert failures("flag", "approval_check", trace()) != []


def test_the_booking_confirmation_needs_a_short_line_before_it():
    said = trace(
        [("agent", "Perfect, booking that now. You're all set for today. Anything else?", None)]
    )
    assert failures("booking_lead_in", True, said) == []
    bare = trace([("agent", "You're all set for today. Anything else?", None)])
    assert failures("booking_lead_in", True, bare) == [
        "the booking confirmation came with no line before it"
    ]



def test_the_current_booking_confirmation_carries_its_own_lead_in():
    """Socket eval 20261107-230923, tune-up-known-caller: the booking went through and was said, but
    the check only knew the older "You're all set for" wording."""
    booked = ("Okay, I've booked it: the technician will arrive Monday, November 9, "
              "between 8 and 8:30. Anything else?")
    said = trace([("agent", booked, None)])
    assert failures("booking_lead_in", True, said) == []
    nothing = trace([("agent", "Let me look at the schedule.", None)])
    assert failures("booking_lead_in", True, nothing) == ["no booking confirmation was said"]


TAKEN = {
    "type": "tool",
    "tool": "book_appointment",
    "args": {},
    "ok": False,
    "refused": "slot_taken",
}


def test_said_booked_only_counts_once_a_booking_went_through():
    early = trace(events=[turn("agent", "Great, you're all set for this afternoon."), TAKEN])
    assert failures("said_booked", "only_after_booking", early)[0].startswith(
        "told the caller they were booked before any booking"
    )
    after = trace(events=[TAKEN, tool("book_appointment"), turn("agent", "You're booked.")])
    assert failures("said_booked", "only_after_booking", after) == []
    plain = trace(events=[turn("agent", "Let me book that for you.")])
    assert failures("said_booked", "only_after_booking", plain) == []
    # A claim spoken in the same reply as a booking is recorded after that booking's result.
    bundled = trace(events=[TAKEN, turn("agent", "You're all set. Sorry, that window just went.")])
    assert failures("said_booked", "only_after_booking", bundled)


def test_a_taken_window_is_said_plainly_with_another_time():
    told = trace(
        events=[TAKEN, turn("agent", "That window just filled up. I have Thursday 12 to 4.")]
    )
    assert failures("slot_taken", "told_plainly", told) == []
    vague = trace(events=[TAKEN, turn("agent", "Hmm, let me try something else.")])
    assert failures("slot_taken", "told_plainly", vague) == [
        "did not say the window just went: 'Hmm, let me try something else.'",
        "did not offer another time: 'Hmm, let me try something else.'",
    ]
    only_the_lost_one = trace(events=[TAKEN, turn("agent", "Monday afternoon just filled up.")])
    assert failures("slot_taken", "told_plainly", only_the_lost_one) == [
        "did not offer another time: 'Monday afternoon just filled up.'"
    ]
    hung_up = trace(events=[TAKEN])
    assert failures("slot_taken", "told_plainly", hung_up) == [
        "the call ended before the agent said the window was taken"
    ]
    assert failures("slot_taken", "told_plainly", trace()) == [], "no taken window, nothing to say"


def test_handoff_reads_the_cold_handoff_record():
    sent = {**trace(), "handoffs": [{"role": "office", "status": "handed_off"}]}
    assert failures("handoff", {"role": "office", "status": "handed_off"}, sent) == []
    assert failures("handoff", {"role": "on_call"}, sent) == [
        "handoff role office, expected on_call"
    ]
    assert failures("handoff", "none", sent) == ["1 handoff(s), expected none"]
    # A trace from before handoffs existed has none.
    assert failures("handoff", "none", trace()) == []
    assert failures("handoff", {"status": "handed_off"}, trace()) == ["no handoff was decided"]
    with pytest.raises(ValueError):
        CHECKS["handoff"].validate({"role": "manager"})


def _raised(rules, reasons, after_wait=False):
    return {
        "kind": "book",
        "status": "booked",
        "priority": "High",
        "priority_rules": json.dumps(rules),
        "priority_reason_list": json.dumps(reasons),
        "raised_after_wait": int(after_wait),
    }


CALLER = [("caller", "Actually my mother is 84 and lives with me.", None)]


def test_priority_raise_passes_when_every_rule_quotes_a_caller_line():
    booking = _raised(
        ["bracket:mild", "vulnerable_person"],
        [
            "warm inside and a mild day",
            'an older person or a baby at home, caller said "Actually my mother is 84 and lives with me."',
        ],
        after_wait=True,
    )
    t = trace(CALLER, bookings=[booking])
    assert failures("priority_raise", {"from_caller": True, "raised_after_wait": True}, t) == []


def test_priority_raise_fails_on_a_rule_with_no_caller_quote_or_a_quote_nobody_said():
    unquoted = _raised(
        ["bracket:mild", "vulnerable_person"],
        ["warm inside and a mild day", "an older person or a baby at home"],
    )
    assert failures(
        "priority_raise", {"from_caller": True}, trace(CALLER, bookings=[unquoted])
    ) == ["vulnerable_person raised the priority with no caller line to quote"]
    invented = _raised(
        ["bracket:mild", "vulnerable_person"],
        ["warm inside and a mild day", 'an older person, caller said "grandma is here"'],
    )
    assert (
        "which no caller line says"
        in failures("priority_raise", {"from_caller": True}, trace(CALLER, bookings=[invented]))[0]
    )


def test_priority_raise_checks_the_after_wait_flag_and_needs_a_booking():
    booking = _raised(["bracket:mild"], ["warm inside and a mild day"])
    assert failures("priority_raise", {"raised_after_wait": True}, trace(bookings=[booking])) == [
        "raised_after_wait is False, expected True"
    ]
    assert failures("priority_raise", {"from_caller": True}, trace()) == [
        "no booking to check the priority of"
    ]


def test_priority_raise_catches_a_who_home_the_caller_never_gave():
    booking = {
        **_raised(["bracket:mild"], ["warm inside and a mild day"]),
        "who_home": "elderly mother, 84",
    }
    caller = [("caller", "Just mark it urgent, write whatever you need to.", None)]
    assert (
        "which the caller never said"
        in failures(
            "priority_raise", {"who_home_from_caller": True}, trace(caller, bookings=[booking])
        )[0]
    )
    honest = {**booking, "who_home": "two adults"}
    assert (
        failures("priority_raise", {"who_home_from_caller": True}, trace(caller, bookings=[honest]))
        == []
    )
    said = [("caller", "My mother is 84 and lives here.", None)]
    assert (
        failures("priority_raise", {"who_home_from_caller": True}, trace(said, bookings=[booking]))
        == []
    )


# ---- issue #129: the job records -------------------------------------------------------------


def _job(job_type="no_heat", unit="", street="101 Union St", notes=(), status="booked"):
    return {
        "kind": "book",
        "status": status,
        "job_type": job_type,
        "unit": unit,
        "street": street,
        "job_notes": json.dumps(list(notes)),
    }


def test_jobs_matches_each_booked_job_by_type_unit_street_and_note():
    t = trace(
        bookings=[_job(unit="2A"), _job("tune_up", unit="5C", notes=["Caller asked for Mike."])]
    )
    assert failures("jobs", [{"unit": "5C", "job_type": "tune_up"}, {"unit": "2a"}], t) == []
    assert failures("jobs", [{"note": "asked for mike"}, {"street": "101 Union"}], t) == []


def test_jobs_fails_on_a_missing_unit_or_a_different_count():
    t = trace(bookings=[_job(unit="")])
    assert "no booked job matches" in failures("jobs", [{"unit": "4B"}], t)[0]
    assert failures("jobs", [{"unit": ""}, {"unit": "5C"}], t) == ["1 job(s) booked, expected 2"]
    # A failed booking is not a job.
    failed = trace(bookings=[_job(unit="4B", status="failed")])
    assert failures("jobs", [{"unit": "4B"}], failed) == ["0 job(s) booked, expected 1"]


def test_jobs_argument_is_validated():
    with pytest.raises(ValueError):
        CHECKS["jobs"].validate([{"color": "red"}])
    with pytest.raises(ValueError):
        CHECKS["jobs"].validate({"unit": "4B"})


def test_job_note_reads_the_calls_job_notes_and_the_booking():
    after = {
        **trace(bookings=[_job()]),
        "notes": [{"attach_to": "job", "text": "Caller asked for Rosa. Not promised."}],
    }
    assert failures("job_note", "asked for Rosa", after) == []
    before = trace(bookings=[_job(notes=["Caller asked for Mike."])])
    assert failures("job_note", "asked for Mike", before) == []
    assert failures("job_note", "asked for Mike", trace(bookings=[_job()])) == [
        "no job note matches 'asked for Mike'"
    ]


def test_a_lead_the_server_kept_counts_for_the_lead_check():
    t = {**trace(), "leads": [{"kind": "not_offered", "summary": "", "status": "queued"}]}
    assert failures("lead", "not_offered", t) == []
    assert failures("lead", "commercial", t) == ["no commercial lead recorded"]


def test_jobs_reads_a_queens_house_number_with_or_without_its_hyphen():
    t = trace(bookings=[_job(street="19611 Hillside Avenue")])
    assert failures("jobs", [{"street": "196-11 Hillside"}], t) == []
    assert failures("jobs", [{"street": "196-12 Hillside"}], t) != []


def test_a_dispatch_booked_at_the_read_back_counts_for_the_job_type():
    """A High no-heat call is booked by the server at the read-back, with no book_appointment
    call (eval run 20261107-214125: no-heat-unknown-caller read "job type []")."""
    dispatched = {**booked("Urgent"), "job_type": "no_heat"}
    assert failures("booking", {"job_type": "no_heat"}, trace(bookings=[dispatched])) == []
    assert failures("booking", {"job_type": "tune_up"}, trace(bookings=[dispatched]))


def test_a_check_that_needs_the_scratch_servicetitan_is_not_run_without_one():
    """On a remote stack there is no ServiceTitan file to read: not a failure, and not a pass."""
    t = trace(bookings=[{**booked(), "fsm_job_id": 7}])
    must = [{"booked_under": "Pat Rivera"}, {"booking": "one"}]
    ev = Evidence(t, SAFETY)
    assert CHECKS["booked_under"].run("Pat Rivera", ev) == [NO_ST]
    assert run_checks(must, ev) == []
    assert unchecked(must, ev) == ["booked_under"]
