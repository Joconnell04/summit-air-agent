"""The side-by-side eval's gross-error checks (evals/side_by_side.py) on small made-up calls."""

import json

import yaml

from evals import side_by_side as sbs


def agent(text, response_id=0):
    return {"role": "agent", "content": text, "metadata": {"response_id": response_id}}


def user(text):
    return {"role": "user", "content": text}


def tool(name, tid, say=None, args="{}"):
    body = {"ok": True, **({"say": say} if say else {})}
    return [
        {"role": "tool_call_invocation", "tool_call_id": tid, "name": name, "arguments": args},
        {"role": "tool_call_result", "tool_call_id": tid, "content": json.dumps(body)},
    ]


def rows(bookings=(), name=None, dnc=(), linked=None, **tables):
    call = {
        "call": {"id": "retell_x", "linked_call_id": linked},
        "bookings": [{"kind": k, "status": "booked", "priority_reasons": ""} for k in bookings],
        "booking_names": [{"name": name}] if name else [],
        "call_names": [],
        "flags": [],
        "callbacks": [],
        "leads": [],
        **tables,
    }
    return {"calls": [call], "do_not_call": list(dnc)}


PERSONA = {"first": "Jordan", "name": "Jordan Lee", "bookings": ["book"], "confirms": 1}


def good_call():
    return [
        agent("Hi, thanks for calling Summit Air. How can I help?"),
        user("Hi, my heat's not working."),
        agent("I'm sorry to hear that. Can I get your first and last name?"),
        user("Jordan Lee."),
        *tool("read_back", "t1", say="I have one ninety-nine East Fourth Street. Is that right?"),
        agent("I have one ninety-nine East Fourth Street. Is that right?", 2),
        user("Yes."),
        *tool("find_slots", "t2", say="The soonest is today, 4 to 6."),
        agent("The soonest is today, Thursday, between 4 and 6. Does that work?", 3),
        user("Sure."),
        agent("Thanks so much for calling Summit Air today. Have a great day!", 4),
    ]


def test_a_clean_call_passes_every_check():
    out = sbs.check_call(PERSONA, good_call(), rows(["book"], name="Jordan Lee"))
    assert [k for k, v in out.items() if not v["ok"]] == []
    assert out["name_asked"]["full_name"] is True


def test_two_questions_name_and_narration_fail():
    call = good_call()
    call[2] = agent("Thanks, Jordan. Let me check that. What's the address? And the ZIP?")
    out = sbs.check_call(PERSONA, call, rows(["book"], name="Jordan Lee"))
    assert not out["one_question_per_turn"]["ok"]
    assert not out["no_caller_name"]["ok"]
    assert not out["no_narration"]["ok"]


def test_a_reworded_server_line_fails_but_a_dated_slot_offer_does_not():
    call = good_call()
    call[6] = agent("So that's East Fourth, right?", 2)
    out = sbs.check_call(PERSONA, call, rows(["book"], name="Jordan Lee"))
    assert not out["server_lines_verbatim"]["ok"]
    assert "read_back" in out["server_lines_verbatim"]["why"]


def test_slots_before_read_back_fails():
    call = good_call()
    read_back, slots = call[4:8], call[8:12]
    call = call[:4] + slots + read_back + call[12:]
    out = sbs.check_call(PERSONA, call, rows(["book"], name="Jordan Lee"))
    assert not out["read_back_before_slots"]["ok"]


def test_name_spelling_is_exact_up_to_case_and_curly_apostrophes():
    persona = {**PERSONA, "first": "Jackson", "name": "Jackson O'Connell"}
    assert sbs.check_call(persona, good_call(), rows(["book"], name="jackson o’connell"))[
        "name_spelled_right"
    ]["ok"]
    assert not sbs.check_call(persona, good_call(), rows(["book"], name="Jackson OConnell"))[
        "name_spelled_right"
    ]["ok"]


def test_safety_said_twice_and_after_a_retraction_fails():
    persona = {"first": "Sam", "bookings": [], "safety": True, "retraction": "nevermind"}
    call = [
        agent("How can I help?"),
        user("I think I smell gas."),
        agent("Please leave the house and call nine one one from outside."),
        user("wait never mind, it's just the stove."),
        agent("Okay. Still, please leave the house and call 911."),
        user("no."),
    ]
    out = sbs.check_call(persona, call, rows())
    assert not out["safety_advice_once"]["ok"]
    assert not out["retraction_honored"]["ok"]


def test_hanging_up_on_no_thanks_fails():
    persona = {"first": "Jordan", "bookings": [], "no_thanks": "no thanks"}
    call = [
        agent("Want to hear about the plan?"),
        user("no thanks"),
        agent("Thanks so much for calling Summit Air today. Have a great day!"),
    ]
    assert not sbs.check_call(persona, call, rows())["no_hangup_on_no_thanks"]["ok"]
    call.append(user("Also, a bill question."))
    assert sbs.check_call(persona, call, rows())["no_hangup_on_no_thanks"]["ok"]


def test_carried_over_visit_dnc_and_missing_records_fail():
    call = good_call()
    call[2] = agent("You already have a visit on Saturday. Can I get your name?")
    persona = {**PERSONA, "callback": True, "dnc_undo": True}
    out = sbs.check_call(persona, call, rows([], dnc=[{"phone_key": "x"}], linked="retell_old"))
    assert not out["nothing_carried_over"]["ok"]
    assert not out["records_bookings"]["ok"]
    assert not out["records_callback"]["ok"]
    assert not out["records_dnc_undone"]["ok"]


def test_every_persona_has_expectations_and_a_script():
    personas = yaml.safe_load(sbs.PERSONAS.read_text())["personas"]
    folder = sbs.PERSONAS.parent / "personas"
    assert sorted(personas) == sorted(p.stem for p in folder.glob("*.txt"))
    for spec in personas.values():
        assert spec["first"] and "bookings" in spec


def test_the_list_calls_endpoint_is_the_versioned_one():
    from voice.retell.api import allowed

    assert allowed("POST", "/v3/list-calls")
    assert not allowed("POST", "/list-calls")
