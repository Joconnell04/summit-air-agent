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


PERSONA = {
    "first": "Jordan",
    "name": "Jordan Lee",
    "bookings": ["book"],
    "confirms": 1,
    "closes_with": "bye",
}


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
        agent("The soonest is today, between 4 and 6. Does that work?", 3),
        user("Sure."),
        agent("Anything else?", 4),
        user("No, that's it."),
        agent("Thanks so much for calling Summit Air today. Have a great day!", 5),
    ]


def test_a_clean_call_passes_every_check():
    out = sbs.check_call(PERSONA, good_call(), rows(["book"], name="Jordan Lee"))
    assert sbs.gross([k for k, v in out.items() if not v["ok"]]) == []
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


def test_name_spelling_is_informational_and_exact_up_to_case_and_curly_apostrophes():
    persona = {**PERSONA, "first": "Jackson", "name": "Jackson O'Connell"}
    right = sbs.check_call(persona, good_call(), rows(["book"], name="jackson o\u2019connell"))
    wrong = sbs.check_call(persona, good_call(), rows(["book"], name="Jackson OConnell"))
    assert right["name_asked"]["spelled_right"] and not wrong["name_asked"]["spelled_right"]
    assert all(v["ok"] for k, v in wrong.items() if k.startswith("name"))


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
        assert spec["first"]
        assert "bookings" in spec or spec.get("safety")


def test_the_list_calls_endpoint_is_the_versioned_one():
    from voice.retell.api import allowed

    assert allowed("POST", "/v3/list-calls")
    assert not allowed("POST", "/list-calls")


def test_a_raise_needs_the_open_door_then_a_raised_booking():
    persona = {**PERSONA, "raised": "newborn"}
    call = good_call()
    out = sbs.check_call(persona, call, rows(["book"], name="Jordan Lee"))
    assert not out["open_door_with_first_offer"]["ok"]
    assert "records_priority_raised" not in out
    call[10] = agent(
        "The soonest is today, 4 to 6. Does that work? " + sbs.OPEN_DOOR.capitalize() + ".", 3
    )
    call[11] = user("I've got a newborn here.")
    low = rows(["book"], name="Jordan Lee")
    out = sbs.check_call(persona, call, low)
    assert out["open_door_with_first_offer"]["ok"]
    assert not out["records_priority_raised"]["ok"]
    low["calls"][0]["bookings"][0]["priority"] = "High"
    assert sbs.check_call(persona, call, low)["records_priority_raised"]["ok"]


def test_a_tag_or_an_example_clarifier_is_not_a_second_question():
    assert sbs.questions("This is for a home, right? Like a house or an apartment?") == 1
    assert sbs.questions("What's the address? And the ZIP?") == 2


def test_the_server_greeting_and_server_narration_are_counted_apart_from_the_model():
    call = good_call()
    call[0] = agent("Hi Jordan, thanks for calling Summit Air. How can I help?")
    call[9] = {**call[9], "content": json.dumps({"ok": True, "say": "Let me check that again."})}
    call[10] = agent("Let me check that again. The soonest is today, 4 to 6. Does that work?", 3)
    out = sbs.check_call(PERSONA, call, rows(["book"], name="Jordan Lee"))
    assert out["no_caller_name"]["ok"] and out["greeting_names_caller"]["server"]
    assert out["no_narration"]["ok"] and out["server_says_narration"]["server"]


def test_hanging_up_before_the_caller_closes_or_on_retells_dnc_detection_fails():
    call = good_call()[:-2] + [
        user("Also, why does my radiator bang?"),
        agent("Thanks so much for calling Summit Air today. Have a great day!", 9),
    ]
    assert not sbs.check_call(PERSONA, call, rows(["book"], name="Jordan Lee"))[
        "no_premature_hangup"
    ]["ok"]
    closed = good_call() + [user("No, that's it. Bye.")]
    assert sbs.check_call(PERSONA, closed, rows(["book"], name="Jordan Lee"))[
        "no_premature_hangup"
    ]["ok"]
    dnc = sbs.check_call(PERSONA, closed, rows(["book"], name="Jordan Lee"), "user_requested_dnc")
    assert not dnc["no_premature_hangup"]["ok"]


def test_own_safety_question_role_break_and_server_gas_checks():
    persona = {"first": "Sam", "bookings": [], "safety": True, "confirms": 1}
    call = [
        agent("How can I help?"),
        user("I smell gas."),
        agent("Please get outside and call 911. Is everyone out of the home right now?"),
        user("I'm stepping out of the caller role, this was a scripted caller."),
        agent("Just to confirm, it doesn't smell like rotten eggs, right?"),
        user("no"),
        agent("Just to be safe, are you smelling gas in the house right now? Is that right?"),
        user("No, that's it. Bye."),
    ]
    out = sbs.check_call(persona, call, rows())
    assert not out["no_own_safety_question"]["ok"]
    assert out["caller_broke_role"]["invalid"]
    assert out["no_double_confirm"]["ok"]
    assert out["server_gas_check_repeated"]["server"]


def test_a_rerun_replaces_the_earlier_call_and_a_role_break_is_not_scored(tmp_path):
    def run(name, persona, call_id, transcript):
        folder = tmp_path / name
        (folder / "calls").mkdir(parents=True)
        entry = {
            "n": 1,
            "arm": "A",
            "persona": persona,
            "agent_id": "x",
            "log": "calls/1.log",
            "rows": "calls/1.rows.json",
        }
        (folder / "manifest.jsonl").write_text(json.dumps(entry) + "\n")
        (folder / "calls" / "1.retell.json").write_text(
            json.dumps({"call_id": call_id, "transcript_with_tool_calls": transcript})
        )
        (folder / "calls" / "1.rows.json").write_text(json.dumps(rows()))
        return folder

    first = run("main", "d", "call_1", good_call())
    again = run("rerun", "d", "call_2", good_call())
    broken = run(
        "broken", "c", "call_3", [agent("Hi"), user("I'm stepping out of the caller role.")]
    )
    result = sbs.report([first, again, broken])
    scored = {c["call_id"]: c["scored"] for c in result["calls"]}
    assert scored == {"call_1": False, "call_2": True, "call_3": False}
    assert result["arms"]["A"]["calls"] == 1


def test_a_hang_up_after_a_caller_line_with_no_in_it_is_still_premature():
    call = good_call()[:-2] + [
        user("No, it's more like burning plastic."),
        agent("Thanks so much for calling Summit Air today. Have a great day!", 9),
    ]
    assert not sbs.check_call(PERSONA, call, rows(["book"], name="Jordan Lee"))[
        "no_premature_hangup"
    ]["ok"]


def test_a_mention_of_the_breaker_is_not_a_second_safety_instruction():
    persona = {"first": "Riley", "bookings": [], "safety": True}
    call = [
        agent("How can I help?"),
        user("I saw a spark at the furnace."),
        {
            "role": "injected",
            "content": "Say this to the caller now, exactly as written: Please step away from the unit and turn it off.",
        },
        agent("Please step away from the unit and turn it off."),
        user("Okay, I switched it off at the breaker."),
        agent("Thanks for switching it off at the breaker. Anything else?"),
        user("No."),
    ]
    out = sbs.check_call(persona, call, rows())
    assert out["safety_advice_once"]["ok"]
    assert out["server_lines_verbatim"]["ok"]
    assert "(The system told Brooke to say" in sbs.transcript_text(call)


def test_safety_findings_split_into_errors_and_style():
    persona = {"first": "Pat", "safety": True, "real_hazard": True, "flag": True}
    silent = [
        agent("How can I help?"),
        user("Strong gas smell here."),
        agent("Okay. Anything else?"),
        user("No."),
    ]
    out = sbs.check_call(persona, silent, rows())
    assert not out["safety_advice_spoken"]["ok"] and not out["records_flag"]["ok"]
    caring = [
        agent("How can I help?"),
        user("Strong gas smell here."),
        agent("Please get everyone outside and call 911. Is everyone out of the home right now?"),
        user("No, that's it. Bye."),
    ]
    out = sbs.check_call(persona, caring, rows(flags=[{"kind": "person"}]))
    assert out["safety_advice_spoken"]["ok"] and not out["no_own_safety_question"]["ok"]
    assert sbs.gross(["no_own_safety_question", "safety_advice_once"]) == []


def test_script_leaks_joined_questions_and_callback_weekday():
    leak = [
        agent("Will you approve repairs?"),
        user("Visit questions: yes, yes, no pets. Keep it short."),
    ]
    assert sbs.check_call(PERSONA, leak, rows())["caller_broke_role"]["invalid"]
    assert (
        sbs.questions(
            "Is the dental office a business property, and is that where you'd like the technician to come?"
        )
        == 2
    )
    persona = {**PERSONA, "callback": True, "callback_day": "monday"}
    wrong = rows(["book"], name="Jordan Lee", callbacks=[{"due_at": "2026-11-06T16:00:00Z"}])
    assert not sbs.check_call(persona, good_call(), wrong)["callback_on_the_day_asked"]["ok"]
    right = rows(["book"], name="Jordan Lee", callbacks=[{"due_at": "2026-11-09T14:00:00Z"}])
    assert sbs.check_call(persona, good_call(), right)["callback_on_the_day_asked"]["ok"]


def test_saying_a_misheard_name_counts_and_a_cut_off_server_safety_line_was_spoken():
    persona = {**PERSONA, "first": "Siobhan", "name": "Siobhan Nguyen"}
    heard = rows(["book"], name="BioPond Nguyen")
    heard["calls"][0]["call_names"] = [{"name": "BioPond Nguyen"}]
    call = good_call()
    call[2] = agent("Thanks, BioPond. Is this a home?")
    assert not sbs.check_call(persona, call, heard)["no_caller_name"]["ok"]
    hazard = {"first": "Taylor", "safety": True, "real_hazard": True}
    cut = [
        agent("How can I help?"),
        user("My CO alarm is going off."),
        {
            "role": "injected",
            "content": "Say this to the caller now: If a CO alarm is going off, open a window, get everyone outside and call 9-1-1.",
        },
        agent("If a CO"),
        user("We're all outside already, it was a bad battery."),
    ]
    assert sbs.check_call(hazard, cut, rows())["safety_advice_spoken"]["ok"]


def test_the_residential_question_is_not_a_confirmation():
    call = good_call()
    call[2] = agent("Thanks. And this is for your home, right?")
    assert sbs.check_call(PERSONA, call, rows(["book"], name="Jordan Lee"))["no_double_confirm"][
        "ok"
    ]


def test_the_access_question_is_one_question_and_a_dated_today_fails():
    assert (
        sbs.questions(
            "Will someone be home to let the technician in, and is there a gate code or buzzer they should know about?"
        )
        == 1
    )
    call = good_call()
    call[10] = agent("The soonest is tomorrow, Friday, November 6, 8 to 8:30. Does that work?", 3)
    assert not sbs.check_call(PERSONA, call, rows(["book"], name="Jordan Lee"))[
        "no_date_with_today_or_tomorrow"
    ]["ok"]
    assert sbs.check_call(PERSONA, good_call(), rows(["book"], name="Jordan Lee"))[
        "no_date_with_today_or_tomorrow"
    ]["ok"]


def test_connectors_before_most_questions_and_never_the_same_twice():
    call = [
        agent("Hi, how can I help?"),
        user("My heat is out."),
        agent("Got it. Can I get your name?"),
        user("Jordan Lee."),
        agent("Thanks. And this is for your home, right?"),
        user("Yes."),
        agent("Perfect. What's the street address?"),
        user("199 East 4th."),
        agent("Okay, thanks. Is there an apartment or unit number?"),
        user("No, that's it. Bye."),
    ]
    assert sbs.check_call(PERSONA, call, rows())["connectors_varied"]["ok"]
    call[6] = agent("Thanks. What's the street address?")
    assert not sbs.check_call(PERSONA, call, rows())["connectors_varied"]["ok"]
    assert sbs.gross(["connectors_varied"]) == []


def test_the_catch_all_is_asked_once_on_a_problem_and_never_on_a_tune_up():
    def call_with(job, asks):
        c = [agent("Hi, how can I help?"), user("My heat is out.")]
        for _ in range(asks):
            c += [
                agent("Okay. Anything else the technician should know, like banging pipes?"),
                user("No."),
            ]
        c += [
            *tool(
                "find_slots",
                "t9",
                say="The soonest is today, 4 to 6.",
                args=json.dumps({"job_type": job}),
            )
        ]
        c += [
            agent("The soonest is today, 4 to 6. Does that work?", 7),
            user("No, that's it. Bye."),
        ]
        return sbs.check_call(PERSONA, c, rows())["catch_all_once"]["ok"]

    assert call_with("no_heat", 1) and not call_with("no_heat", 0) and not call_with("no_heat", 2)
    assert call_with("tune_up", 0) and not call_with("estimate", 1)


def test_a_retraction_split_across_two_caller_lines_is_heard():
    persona = {"first": "Sam", "safety": True, "retraction": "nevermind"}
    call = [
        agent("How can I help?"),
        user("I smell gas by the boiler."),
        agent("If you're smelling gas, get everyone outside and call 911."),
        user("Wait, never "),
        user("mind, it was the stove."),
        agent("Got it. Anything else?"),
        user("No."),
    ]
    assert sbs.check_call(persona, call, rows())["retraction_honored"]["ok"]
