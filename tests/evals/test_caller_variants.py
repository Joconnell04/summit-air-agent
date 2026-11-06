"""The seeded caller variants (evals/caller_variants.py) for the done-bar journeys."""

import re

import yaml

from evals import caller_variants as cv
from evals import side_by_side as sbs


def test_the_same_seed_gives_the_same_variants_and_a_new_seed_new_ones():
    a = [cv.build("elderly", i, 3)["text"] for i in range(1, 11)]
    assert a == [cv.build("elderly", i, 3)["text"] for i in range(1, 11)]
    assert a != [cv.build("elderly", i, 4)["text"] for i in range(1, 11)]
    assert len(set(a)) == 10


def test_every_journey_writes_a_script_and_expectations_the_report_reads(tmp_path):
    for journey in cv.JOURNEYS:
        cv.main_args(journey, 3, 1, tmp_path)
    book = yaml.safe_load((tmp_path / "variants.yaml").read_text())
    assert len(book["personas"]) == 12 and len(book["log"]) == 12
    for vid, spec in book["personas"].items():
        text = (tmp_path / f"{vid}.txt").read_text()
        assert spec["first"] in text and "Never step out of the role" in text
        assert spec["journey"] in cv.JOURNEYS
    assert sbs.load_personas([tmp_path / "variants.yaml"])["o1-01"]["raised"]


def test_journey_pass_needs_the_record_and_no_safety_error():
    ok = {"ok": True, "why": ""}
    bad = {"ok": False, "why": "x"}
    assert sbs.journey_pass("routine", {"records_bookings": ok})
    assert not sbs.journey_pass("elderly", {"records_bookings": ok})
    assert sbs.journey_pass("elderly", {"records_bookings": ok, "records_priority_raised": ok})
    assert not sbs.journey_pass("gas_real", {"safety_advice_spoken": ok, "records_flag": bad})
    assert sbs.journey_pass("gas_retracted", {"retraction_honored": ok})
    assert sbs.journey_pass(None, {}) is None


def test_a_retraction_phrase_is_found_in_the_callers_words():
    for i in range(1, 8):
        v = cv.build("gas_retracted", i, 9)
        assert v["expect"]["retraction"] in v["log"]["take_back"].lower().replace(" ", "").replace(
            ",", ""
        )


def test_no_pets_anywhere_and_the_estimate_unit_and_system_cases_appear():
    texts, specs = [], []
    for journey in cv.JOURNEYS:
        for i in range(1, 31):
            v = cv.build(journey, i, 11)
            texts.append(v["text"])
            specs.append(v["expect"])
    assert not any(re.search(r"\bpets?\b", t, re.IGNORECASE) for t in texts)
    assert any(s.get("job_type") == "estimate" for s in specs)
    assert any(s.get("unit_ask") for s in specs)
    assert any("what kind of system" in t for t in texts)


def test_the_unit_check_and_the_estimate_check():
    from tests.evals.test_side_by_side import PERSONA, agent, good_call, rows, user

    persona = {**PERSONA, "unit_ask": True, "job_type": "estimate"}
    call = good_call()
    out = sbs.check_call(persona, call, rows(["book"], name="Jordan Lee"))
    assert not out["unit_asked"]["ok"]
    call[4:4] = [agent("Is there an apartment or unit number?", 1), user("Unit 2.")]
    booked = rows(["book"], name="Jordan Lee")
    booked["calls"][0]["bookings"][0]["job_type"] = "estimate"
    out = sbs.check_call(persona, call, booked)
    assert out["unit_asked"]["ok"] and out["records_job_type"]["ok"]
