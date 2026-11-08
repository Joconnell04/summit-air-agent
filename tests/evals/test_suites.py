import pytest

from evals.scenario import Scenario
from evals.suites import SuiteError, load_suites, matches, parse_tags, select


def sc(id, flow, difficulty, tags=()):
    return Scenario.model_validate(
        {
            "id": id,
            "flow": flow,
            "difficulty": difficulty,
            "journeys": ["J1"],
            "tags": list(tags),
            "persona": "p",
            "opening": "o",
            "must": [{"booking": "none"}],
        }
    )


POOL = [
    sc("a", ["safety"], "easy", ["smoke"]),
    sc("b", ["safety"], "rigorous"),
    sc("c", ["booking"], "rigorous"),
    sc("d", ["booking"], "adversarial"),
]


def ids(found):
    return [s.id for s in found[0]]


def test_same_prefix_is_any_of_and_different_prefixes_are_all_of():
    assert ids(select(POOL, tags=["flow:safety", "flow:booking"])) == ["a", "b", "c", "d"]
    assert ids(select(POOL, tags=["flow:safety", "difficulty:rigorous"])) == ["b"]


def test_named_suites_filter_by_their_tags_and_set_samples():
    assert ids(select(POOL, "smoke")) == ["a"]
    assert ids(select(POOL, "rigorous")) == ["b", "c", "d"]
    chosen, samples = select(POOL, "full")
    assert len(chosen) == 4 and samples == 3


def test_a_suite_and_tags_combine():
    assert ids(select(POOL, "rigorous", ["flow:booking"])) == ["c", "d"]


def test_unknown_suite_and_empty_selection_are_errors():
    with pytest.raises(SuiteError, match="no suite"):
        select(POOL, "nightly")
    with pytest.raises(SuiteError, match="no scenario"):
        select(POOL, tags=["flow:spam"])


def test_parse_tags_and_bare_tags():
    assert parse_tags(" flow:safety, ,smoke ") == ["flow:safety", "smoke"]
    assert matches({"smoke", "flow:x"}, ["smoke"])
    assert not matches({"flow:x"}, ["smoke"])


def test_suites_file_has_the_five_suites():
    assert set(load_suites()) == {"smoke", "standard", "rigorous", "full", "demo"}


def test_the_demo_suite_has_three_scenarios_for_each_user_story():
    """DECISIONS.md 145: each user story in the assignment, three existing scenarios, two samples."""
    from evals.scenario import load_all
    from evals.suites import select

    chosen, samples = select(load_all(), "demo")
    assert len(chosen) == 18 and samples == 2


def test_a_scenario_gets_24_caller_turns_unless_it_sets_its_own():
    """DECISIONS.md 146: demo calls that booked ran out of 16 turns in the membership wrap-up."""
    from evals.scenario import load_all

    by_id = {s.id: s for s in load_all()}
    assert by_id["no-cooling-standard"].max_turns == 24
    assert by_id["angry-caller-demands-person"].max_turns == 8
