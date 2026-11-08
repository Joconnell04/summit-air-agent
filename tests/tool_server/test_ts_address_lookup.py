"""The address lookup (app/geocode.py, tools.find_address): a ZIP found for a house and street
the caller said counts as known; one place fills the ZIP, several ask which, none asks the caller
to spell the street, an outage asks for the ZIP, and a ZIP that disagrees with the lookup is
never booked until the caller settles it."""

import httpx
import pytest
import yaml
from toolkit import TENANT_DIR, UNKNOWN, Agent, book, count, first_open, make_server

from app import geocode
from app.geocode import Area, Found, Place

AREA = Area.from_config(yaml.safe_load((TENANT_DIR / "config.yaml").read_text())["address_lookup"])


def lookup(backends):
    """A geocoder with this tenant's borough names."""
    return geocode.Geocoder(backends, AREA.boroughs)

DUFFIELD = Place("248", "DUFFIELD STREET", "11201", "Brooklyn", "Downtown Brooklyn")
MAIN_BK = Place("1", "MAIN STREET", "11201", "Brooklyn", "DUMBO")
MAIN_MN = Place("1", "MAIN STREET", "10044", "Manhattan", "Roosevelt Island")
BROADWAY_MN = Place("100", "BROADWAY", "10005", "Manhattan", "Financial District")
# What a search engine returns: near matches with the exact one.
BOOK = {
    ("248", "duffield st"): [Place("16", "DUFFIELD STREET", "11201", "Brooklyn", ""), DUFFIELD],
    ("1", "main st"): [MAIN_BK, MAIN_MN, Place("1", "MAIN STREET NORTH", "10044", "Manhattan", "")],
    ("100", "broadway"): [BROADWAY_MN],
}


class Book:
    """A backend that answers from BOOK and counts what it was asked."""

    def __init__(self, book=BOOK):
        self.book = book
        self.asked: list[tuple[str, str]] = []

    def __call__(self, house, street):
        key = (geocode.house_key(house), geocode.street_match_key(street))
        self.asked.append(key)
        return list(self.book.get(key, []))


def down(house, street):
    raise httpx.ConnectError("connection refused")


@pytest.fixture
def answers():
    return Book()


@pytest.fixture
def located(tmp_path, clock, mock, answers):
    """An agent whose server has the address lookup."""
    return Agent(make_server(tmp_path, clock, mock[0], geocoder=lookup([("book", answers)])), clock)


def _address(street="248 Duffield St", zip_code="11201", city="Brooklyn"):
    return {"street": street, "unit": "", "city": city, "zip": zip_code}


# ---- the module ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "said, house, street",
    [
        ("248 Duffield St", "248", "Duffield St"),
        ("41-12 Bell Blvd", "41-12", "Bell Blvd"),
        ("41 - 12 Bell Blvd", "41-12", "Bell Blvd"),
        ("2214 Avenue U", "2214", "Avenue U"),
        # The agent passed the unit with the street (standard run 20261104-171717).
        ("1 Main Street, Apt 5F", "1", "Main Street"),
        ("1 Main St Apt 5F", "1", "Main St"),
        ("455 Court St #2R", "455", "Court St"),
        ("1 Main Street, Brooklyn", "1", "Main Street"),
    ],
)
def test_an_address_splits_into_house_and_street(said, house, street):
    assert geocode.split_address(said) == (house, street)


def test_no_house_number_is_no_address():
    assert geocode.split_address("Duffield Street") is None


@pytest.mark.parametrize(
    "a, b",
    [("East 19th St", "EAST 19 STREET"), ("34th Ave", "34 AVENUE"), ("Duffield Street", "duffield st")],
)
def test_the_street_compares_like_the_citys(a, b):
    assert geocode.street_match_key(a) == geocode.street_match_key(b)


def test_only_the_same_house_on_the_same_street_counts():
    found = geocode.pick(BOOK[("248", "duffield st")], "248", "Duffield St")
    assert found.status == "one" and found.places == (DUFFIELD,)


def test_the_same_address_in_two_boroughs_is_several_until_the_borough_is_given():
    candidates = BOOK[("1", "main st")]
    assert geocode.pick(candidates, "1", "Main St").status == "several"
    assert geocode.pick(candidates, "1", "Main St", "Brooklyn", boroughs=AREA.boroughs).places == (MAIN_BK,)
    assert geocode.pick(candidates, "1", "Main St", "dumbo", boroughs=AREA.boroughs).places == (MAIN_BK,)


def test_a_borough_with_no_such_address_is_none_and_a_neighborhood_only_narrows():
    candidates = BOOK[("1", "main st")]
    assert geocode.pick(candidates, "1", "Main St", "Queens", boroughs=AREA.boroughs).status == "none"
    assert geocode.pick(candidates, "1", "Main St", "Park Slope", boroughs=AREA.boroughs).status == "several"


def test_numbers_said_one_by_one_start_a_lookup():
    assert geocode.addresses_in("two four eight duffield street one one two zero one") == [
        ("248", "duffield street")
    ]
    assert geocode.addresses_in("it's 64 degrees in here") == []


def test_the_spoken_street_is_readable():
    assert DUFFIELD.spoken_street() == "248 Duffield Street"
    assert Place("77-10", "34 AVENUE", "11372", "Queens", "").spoken_street() == "77-10 34th Avenue"
    assert Place("2214", "AVENUE U", "11229", "Brooklyn", "").spoken_street() == "2214 Avenue U"


def test_a_failing_backend_falls_back_to_the_next(answers):
    geo = lookup([("geosearch", down), ("census", answers)])
    found = geo.find("248", "Duffield St")
    assert found.status == "one" and found.source == "census"


def test_no_backend_answering_is_unavailable_and_is_not_kept():
    calls = []

    def flaky(house, street):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow")
        return [DUFFIELD]

    geo = lookup([("geosearch", flaky)])
    assert geo.find("248", "Duffield St").status == "unavailable"
    assert geo.find("248", "Duffield St").status == "one"


def test_a_slow_answer_is_unavailable_to_a_short_wait():
    import threading

    release = threading.Event()

    def slow(house, street):
        release.wait(2)
        return [DUFFIELD]

    geo = lookup([("geosearch", slow)])
    assert geo.find("248", "Duffield St", wait_s=0.05) == Found("unavailable")
    release.set()


def test_answers_are_kept_per_house_and_street_whatever_the_borough(answers):
    geo = lookup([("book", answers)])
    geo.prefetch_line("It's 1 Main Street.")
    assert geo.find("1", "Main St", "Brooklyn").places == (MAIN_BK,)
    assert geo.find("1", "main street").status == "several"
    assert answers.asked == [("1", "main st")]


def test_the_census_answer_is_read_and_kept_to_the_city():
    body = {
        "result": {
            "addressMatches": [
                {"matchedAddress": "248 DUFFIELD ST, BROOKLYN, NY, 11201"},
                {"matchedAddress": "248 DUFFIELD ST, ALBANY, NY, 12202"},
            ]
        }
    }
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    places = geocode.census(http, AREA, "248", "Duffield St")
    assert [(p.house, p.zip, p.borough) for p in places] == [("248", "11201", "Brooklyn")]


def test_the_geosearch_answer_is_read():
    body = {
        "features": [
            {"properties": {"housenumber": "248", "street": "DUFFIELD STREET", "postalcode": "11201",
                            "borough": "Brooklyn", "neighbourhood": "Downtown Brooklyn"}},
            {"properties": {"name": "Brooklyn", "layer": "borough"}},
        ]
    }
    seen = {}

    def answer(request):
        seen["text"] = request.url.params["text"]
        return httpx.Response(200, json=body)

    http = httpx.Client(transport=httpx.MockTransport(answer))
    assert geocode.geosearch(http, AREA.geosearch_url, "248", "Duffield St") == [DUFFIELD]
    assert seen["text"] == "248 Duffield St"


# ---- find_address -------------------------------------------------------------------------


def test_one_place_fills_the_zip_and_the_read_back_confirms_it(located):
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "It's 248 Duffield St. I don't remember the zip, sorry.")
    found = located.tool("find_address", call, street="248 Duffield St")
    assert found["ok"], found
    assert found["data"]["zip"] == "11201"
    assert found["data"]["street"] == "248 Duffield Street"
    assert found["data"]["in_area"] is True
    asked = located.tool("read_back", call, address=_address("248 Duffield Street"), phone="caller_id")
    assert asked["ok"]
    assert asked["data"]["sentence"].startswith("So that's two forty-eight Duffield Street, and ")


def test_the_lookup_zip_passes_the_zip_gate_but_a_guess_does_not(located):
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "It's 248 Duffield St.")
    assert located.tool("check_service_area", call, zip="11201")["refused"] == "zip_not_heard"
    located.tool("find_address", call, street="248 Duffield St")
    assert located.tool("check_service_area", call, zip="11201")["ok"]


def test_a_street_the_caller_never_said_is_not_looked_up(located):
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "My heat's out, I'm in Brooklyn.")
    result = located.tool("find_address", call, street="248 Duffield St")
    assert result["refused"] == "address_not_heard"
    other = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(other, "It's 248 Smith Street.")
    assert located.tool("find_address", other, street="248 Duffield St")["refused"] == (
        "address_not_heard"
    )
    assert count(located, "SELECT COUNT(*) FROM address_lookups") == 0


def test_a_street_spelled_letter_by_letter_counts_as_said(located):
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "Two four eight, it's spelled D U F F I E L D, street.")
    found = located.tool("find_address", call, street="248 St", spelled="D, U, F, F, I, E, L, D")
    assert found["ok"], found
    assert found["data"]["zip"] == "11201"


def test_the_same_street_in_two_boroughs_asks_which(located):
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "1 Main Street.")
    asked = located.tool("find_address", call, street="1 Main St")
    assert asked["refused"] == "address_several"
    assert asked["say"] == "Is that the one in Brooklyn or Manhattan?"
    assert located.tool("check_service_area", call, zip="11201")["refused"] == "zip_not_heard"
    located.turn(call, "Brooklyn.")
    found = located.tool("find_address", call, street="1 Main St", borough="Brooklyn")
    assert found["ok"] and found["data"]["zip"] == "11201"


def test_no_match_asks_to_spell_then_for_the_borough(located):
    """Booking-first: with no borough said, the borough is asked before any ZIP (the visit is then
    booked unverified)."""
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "It's 52 Dufield Street.")
    first = located.tool("find_address", call, street="52 Dufield St")
    assert first["refused"] == "address_not_found"
    assert "spell" in first["say"]
    second = located.tool("find_address", call, street="52 Dufield St")
    assert second["refused"] == "address_not_found"
    assert second["say"] == "And which borough is that in?"


def test_an_outage_asks_for_the_borough_then_books_unverified(tmp_path, clock, mock):
    agent = Agent(make_server(tmp_path, clock, mock[0], geocoder=lookup([("down", down)])), clock)
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "248 Duffield Street.")
    result = agent.tool("find_address", call, street="248 Duffield St")
    assert result["refused"] == "address_lookup_down"
    assert result["say"] == "And which borough is that in?"
    agent.say(call, result["say"])
    agent.turn(call, "Brooklyn.")
    again = agent.tool("find_address", call, street="248 Duffield St", borough="Brooklyn")
    assert again["ok"] and again["data"]["verified"] is False and again["data"]["zip"] == "11201"


def test_the_tenant_config_turns_the_lookup_on():
    assert geocode.from_settings("", {"geosearch_url": "x"}) is None
    assert geocode.from_settings("geosearch", None) is None
    assert geocode.from_settings("geosearch", {"geosearch_url": "x"}) is not None
    with pytest.raises(ValueError):
        geocode.from_settings("google", {"geosearch_url": "x"})


def test_no_geocoder_asks_for_the_zip(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "248 Duffield Street.")
    assert agent.tool("find_address", call, street="248 Duffield St")["refused"] == (
        "address_lookup_down"
    )


def test_an_address_found_in_manhattan_is_in_the_area(located):
    """Manhattan was outside the area until Jackson's call on the Retell number (DECISIONS.md 105);
    an address the city lookup finds is outside it only past the five boroughs, which the lookup
    doesn't cover, so out-of-area is the ZIP check's (test_ts_calls.py)."""
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "100 Broadway, Manhattan.")
    result = located.tool("find_address", call, street="100 Broadway", borough="Manhattan")
    assert result["ok"] and result["data"]["in_area"], result
    assert count(located, "SELECT COUNT(*) FROM out_of_area") == 0


# ---- a ZIP that disagrees with the lookup ---------------------------------------------------


def test_a_mismatched_zip_is_read_against_the_lookup_and_not_booked(located):
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "248 Duffield St, 11205.")
    checked = located.tool("check_service_area", call, zip="11205")
    assert checked["refused"] == "zip_mismatch"
    assert checked["say"] == (
        "I have two forty-eight Duffield Street in Downtown Brooklyn as one one two zero one, not one one two "
        "zero five. Which one is right?"
    )
    asked = located.tool("read_back", call, address=_address(zip_code="11205"), phone="caller_id")
    assert asked["refused"] == "zip_mismatch"
    assert count(located, "SELECT COUNT(*) FROM calls WHERE readback_json IS NOT NULL") == 0


def test_the_caller_picking_the_lookup_zip_settles_it(located):
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "248 Duffield St, 11205.")
    located.tool("check_service_area", call, zip="11205")
    located.turn(call, "Oh, the first one, sorry.")
    assert located.tool("check_service_area", call, zip="11201")["ok"]
    located.confirm_read_back(call, _address(zip_code="11201"))


def test_the_caller_repeating_their_own_zip_settles_it(located):
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "248 Duffield St, 11205.")
    located.tool("check_service_area", call, zip="11205")
    located.turn(call, "Hmm.")
    assert located.tool("check_service_area", call, zip="11205")["refused"] == "zip_mismatch"
    located.turn(call, "No, it's definitely 11205.")
    assert located.tool("check_service_area", call, zip="11205")["ok"]


def test_a_lookup_that_comes_back_late_still_stops_the_booking(tmp_path, clock, mock, answers):
    """The read-back was confirmed on the caller's ZIP before the lookup answered; the booking
    is refused and the address goes back to the read-back."""
    import threading

    ready = threading.Event()

    def late(house, street):
        ready.wait(5)
        return answers(house, street)

    agent = Agent(make_server(tmp_path, clock, mock[0], geocoder=lookup([("late", late)])), clock)
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.confirm_read_back(call, _address(zip_code="11205"))
    ready.set()
    booked = book(agent, call, first_open(agent, call))
    assert booked["refused"] == "zip_mismatch"
    assert count(agent, "SELECT COUNT(*) FROM bookings") == 0
    assert count(agent, "SELECT COUNT(*) FROM calls WHERE readback_confirmed_at IS NOT NULL") == 0


def test_a_zip_on_the_callers_record_wins_over_the_lookup(tmp_path, clock, mock, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    # A lookup that disagrees with the ZIP on the caller's record (the seed's is 11249).
    bedford = Place("620", "BEDFORD AVENUE", "11211", "Brooklyn", "Williamsburg")
    geo = lookup([("book", Book({("620", "bedford ave"): [bedford]}))])
    agent = Agent(make_server(tmp_path, clock, mock[0], geocoder=geo), clock)
    call = agent.start(maria["phone"])["data"]["call_id"]
    agent.turn(call, "It's 620 Bedford Ave, 11249.")
    assert agent.tool("check_service_area", call, zip="11249")["ok"]


def test_without_a_lookup_the_callers_zip_stands(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "248 Duffield St, 11205.")
    assert agent.tool("check_service_area", call, zip="11205")["ok"]


# ---- from the first eval run of these scenarios ---------------------------------------------


def test_a_street_named_like_a_street_type_counts_as_said(located, answers):
    """ "455 Court Street": Court is the name, though "court" is a street type (eval run
    20261104-162045, renter-landlord-approves-books)."""
    answers.book[("455", "ct st")] = [Place("455", "COURT STREET", "11231", "Brooklyn", "Carroll Gardens")]
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "It's 455 Court Street, apartment 2R, Brooklyn.")
    found = located.tool("find_address", call, street="455 Court Street", borough="Brooklyn")
    assert found["ok"], found
    assert found["data"]["zip"] == "11231"


def test_a_borough_and_a_neighborhood_together_narrow_to_one(located):
    """ "Brooklyn, DUMBO" (eval run 20261104-162045, same-street-two-boroughs)."""
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "1 Main Street, 5F. Brooklyn, in DUMBO.")
    found = located.tool("find_address", call, street="1 Main Street", borough="Brooklyn, DUMBO")
    assert found["ok"] and found["data"]["zip"] == "11201"
    assert geocode.pick(BOOK[("1", "main st")], "1", "Main St", "DUMBO in Brooklyn",
                        boroughs=AREA.boroughs).places == (MAIN_BK,)


def test_find_address_asks_when_the_caller_gave_another_zip(located):
    """The agent looked the street up though the caller gave a ZIP; the lookup's ZIP isn't used
    quietly (eval run 20261104-162045, zip-mismatch-caller-settles)."""
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "it's two four eight duffield street brooklyn one one two oh five")
    asked = located.tool("find_address", call, street="248 Duffield Street", borough="Brooklyn")
    assert asked["refused"] == "zip_mismatch"
    located.turn(call, "oh you're right it's one one two oh one")
    found = located.tool("find_address", call, street="248 Duffield Street", borough="Brooklyn")
    assert found["ok"] and found["data"]["zip"] == "11201"
    located.confirm_read_back(call, _address("248 Duffield Street", "11201"))


def test_the_lookup_zip_is_told_to_the_agent_as_known(located):
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "248 Duffield Street, I don't know the zip.")
    located.tool("find_address", call, street="248 Duffield St")
    assert located.turn(call, "Apartment 3.")["data"]["known"]["ZIP"] == "11201"


def test_the_lookup_questions_are_spoken_as_written():
    from prompts.assemble import spoken_by_server

    for code in ("address_several", "address_not_found", "address_lookup_down"):
        envelope = {"ok": False, "say": "Is that the one in Brooklyn or Manhattan?", "refused": code}
        assert spoken_by_server("find_address", envelope) == envelope["say"]
    mismatch = {"ok": False, "say": "Which one is right?", "refused": "zip_mismatch"}
    assert spoken_by_server("check_service_area", mismatch) == "Which one is right?"


def test_a_zip_the_caller_gave_stands_when_the_lookup_cant_settle_one(tmp_path, clock, mock):
    """The agent looked up a street the caller gave with its ZIP and the lookup was down: the
    caller isn't asked again for the ZIP they just said (must-pass run 20261104-165525)."""
    agent = Agent(make_server(tmp_path, clock, mock[0], geocoder=lookup([("down", down)])), clock)
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "It's 71-20 Austin Street, Forest Hills, 11375.")
    found = agent.tool("find_address", call, street="71-20 Austin Street")
    assert found["ok"], found
    assert found["data"]["zip"] == "11375" and found["data"]["from_caller"] is True


def test_the_callers_zip_picks_one_of_several(located):
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "1 Main Street, 11201.")
    found = located.tool("find_address", call, street="1 Main St")
    assert found["ok"] and found["data"]["zip"] == "11201"


def test_numbers_run_together_are_not_a_zip_that_declines(tmp_path, clock, mock):
    """ "ten sixty two one oh six two park place" holds the run 21062, which declined a building
    in the area as out of area (eval run 20261104-174038, property-manager-books-building)."""
    agent = Agent(make_server(tmp_path, clock, mock[0], geocoder=lookup([("down", down)])), clock)
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "it's one oh six two park place brooklyn one one two one six")
    agent.turn(call, "not one oh six it's ten sixty two one oh six two park place p a r k place")
    found = agent.tool("find_address", call, street="1062 Park Place", borough="Brooklyn")
    assert found["ok"] and found["data"]["zip"] == "11216", found
    assert count(agent, "SELECT COUNT(*) FROM out_of_area") == 0


def test_a_zip_out_of_the_area_said_with_the_street_is_left_to_the_area_check(tmp_path, clock,
                                                                               mock):
    agent = Agent(make_server(tmp_path, clock, mock[0], geocoder=lookup([("down", down)])), clock)
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "It's 47 Willis Ave, Mineola, 11501.")
    found = agent.tool("find_address", call, street="47 Willis Ave")
    assert found["refused"] == "address_lookup_down"
    assert agent.tool("check_service_area", call, zip="11501")["refused"] == "out_of_area"


# ---- review of #125 -------------------------------------------------------------------------


def test_a_house_number_the_model_cut_short_is_handed_back_as_the_caller_said_it(located):
    """Demo eval 20261108-011049, no-cooling-standard: the caller said "two two nine oh Hylan
    Boulevard" (2290), the model looked up 229 four times, and each refusal only asked for the
    street address again. The refusal now names the number the caller said before that street."""
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "it's two two nine oh hylan boulevard staten island one oh three oh six")
    result = located.tool("find_address", call, street="229 Hylan Boulevard")
    assert result["refused"] == "address_not_heard"
    assert result["say"] == ""
    assert "2290 Hylan Boulevard" in result["next"]
    # A street the caller never put a number before still asks for the address.
    other = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(other, "My heat's out, I'm in Brooklyn.")
    plain = located.tool("find_address", other, street="248 Duffield St")
    assert plain["say"] == "What's the street address there?"


AVENUE_U = Place("5", "AVENUE U", "11223", "Brooklyn", "Gravesend")


def test_a_number_said_about_something_else_is_not_a_house_number(located, answers):
    answers.book[("5", "ave u")] = [AVENUE_U]
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "My furnace has been out for 5 days and I have two kids at home")
    result = located.tool("find_address", call, street="5 Avenue U")
    assert result["refused"] == "address_not_heard"
    assert located.tool("check_service_area", call, zip="11223")["refused"] == "zip_not_heard"


def test_a_street_word_inside_another_word_is_not_the_street(located, answers):
    answers.book[("455", "ct st")] = [Place("455", "COURT STREET", "11231", "Brooklyn", "")]
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "Unit 455, the electrician was here yesterday.")
    assert located.tool("find_address", call, street="455 Court St")["refused"] == (
        "address_not_heard"
    )


def test_the_number_and_street_must_come_together(located):
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "It's been 248 minutes on hold, I swear.")
    located.turn(call, "Anyway, we're near Duffield Street somewhere.")
    located.turn(call, "Not sure of the number.")
    assert located.tool("find_address", call, street="248 Duffield St")["refused"] == (
        "address_not_heard"
    )


def test_the_real_address_passes_however_it_was_said(located):
    for line in ("248 duffield st", "two four eight duffield street", "It's 248 Duffield St."):
        call = located.start(UNKNOWN)["data"]["call_id"]
        located.turn(call, line)
        assert located.tool("find_address", call, street="248 Duffield St")["ok"], line


def test_a_house_number_given_as_the_answer_to_the_agents_question_counts(located):
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "I'm on Duffield Street in Brooklyn.")
    located.say(call, "What's the house number?")
    located.turn(call, "it's two forty eight")
    assert located.tool("find_address", call, street="248 Duffield St")["ok"]


def test_a_bare_number_not_asked_for_does_not_count(located):
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "I'm on Duffield Street in Brooklyn.")
    located.say(call, "How long has it been out?")
    located.turn(call, "248")
    assert located.tool("find_address", call, street="248 Duffield St")["refused"] == (
        "address_not_heard"
    )


@pytest.mark.parametrize(
    "line",
    [
        "two forty eight duffield street",
        "two hundred forty eight duffield street",
        "two forty eight D U F F I E L D",
        "248 on Duffield, by the park",
    ],
)
def test_spoken_house_numbers_pass(located, line):
    """Re-check of #125: "forty" was dropped, so "two forty eight" read as 2, 8."""
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, line)
    assert located.tool("find_address", call, street="248 Duffield St")["ok"], line


def test_a_queens_pair_said_as_words_passes(located, answers):
    answers.book[("4241", "bell blvd")] = [Place("42-41", "BELL BOULEVARD", "11361", "Queens", "Bayside")]
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "forty two forty one bell boulevard")
    assert located.tool("find_address", call, street="42-41 Bell Blvd")["ok"]


@pytest.mark.parametrize(
    "line, street",
    [
        ("I live on Ocean Avenue, furnace out for 12 days", "12 Ocean Ave"),
        ("I called 2 times about Union Street", "2 Union St"),
        ("it's been out since 9 and I'm on Union", "9 Union St"),
        ("my zip is 11201 and I live on Union", "11201 Union St"),
        ("zip code 11201 Union Street", "11201 Union St"),
    ],
)
def test_a_number_not_right_before_the_street_is_not_its_house_number(located, line, street):
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, line)
    assert located.tool("find_address", call, street=street)["refused"] == "address_not_heard"


def test_a_floor_after_the_street_still_gets_the_mismatch_question(located):
    assert geocode.split_address("248 Duffield Street 3rd Floor") == ("248", "Duffield Street")
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "248 Duffield Street, 3rd floor, 11205.")
    address = _address("248 Duffield Street 3rd Floor", "11205")
    assert located.tool("read_back", call, address=address, phone="caller_id")["refused"] == (
        "zip_mismatch"
    )


def test_a_zip_said_before_the_street_is_asked_about_not_replaced(located):
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "I'm in 11205.")
    located.turn(call, "The address is 248 Duffield Street.")
    assert located.tool("find_address", call, street="248 Duffield St")["refused"] == "zip_mismatch"


# ---- Jackson's call on the Retell number (call_ca9bfcec0b968db42ac2f428bea) ---------------------

EAST_4TH = Place("199", "EAST 4 STREET", "10009", "Manhattan", "East Village")


@pytest.mark.parametrize(
    "said",
    [
        "One nine nine East fourth Street.",
        "one ninety nine east fourth street",
        "a hundred ninety nine east fourth street",
        "one hundred and ninety nine East 4th Street, unit 2",
        "It's 199 E 4th St, apartment 2.",
        "one nine nine east fourth street number 2",
    ],
)
@pytest.mark.parametrize("model_street", ["199 East Fourth Street", "199 East 4th Street", "199 E 4 St"])
def test_a_direction_and_an_ordinal_said_any_way_still_match(located, answers, said, model_street):
    """The caller said "One nine nine East fourth Street." and the model sent "199 East Fourth
    Street": refused address_not_heard three times, because "e" sat between the number and the
    street, and "4th" never matched "fourth"."""
    answers.book[("199", geocode.street_match_key("East 4 Street"))] = [EAST_4TH]
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, said)
    result = located.tool("find_address", call, street=model_street)
    assert result["refused"] != "address_not_heard", result


@pytest.mark.parametrize("lines", [
    ["$199.", "East 4th Street, Unit 2."],
    ["$199 East 4th Street, unit 2."],
    ["It's #199 East 4th Street."],
])
def test_a_house_number_written_as_money_or_split_from_its_street_still_counts(
    located, answers, lines
):
    """Retell's speech-to-text wrote the spoken "199" as "$199." and put the street in the next
    line (calls call_926427e1d1f2a80cb78dc6a4e04 and call_b1fa369abb81ba74c1486c1fd67): refused
    address_not_heard."""
    answers.book[("199", geocode.street_match_key("East 4 Street"))] = [EAST_4TH]
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.say(call, "What's the street address, including any apartment or unit number?")
    for line in lines:
        located.turn(call, line)
    result = located.tool("find_address", call, street="199 East 4th Street")
    assert result["ok"], result


def test_partial_lines_the_voice_layer_logs_dont_break_the_number_and_the_street(located, answers):
    """Retell call call_feed25cb0e5df1017695f9dc210 logged "One", "$199.", then "East", "East 4th,
    apartment 2.", and the model passed "199 East 4th Street, apartment 2": refused."""
    answers.book[("199", geocode.street_match_key("East 4 Street"))] = [EAST_4TH]
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.say(call, "What's the street address for the visit?")
    located.turn(call, "One")
    located.turn(call, "$199.")
    located.say(call, "Got it, and what street is that on?")
    located.turn(call, "East")
    located.turn(call, "East 4th, apartment 2.")
    result = located.tool("find_address", call, street="199 East 4th Street, apartment 2")
    assert result["ok"], result


def test_a_thousands_comma_is_one_house_number(located, answers):
    broadway = Place("1500", "BROADWAY", "10036", "Manhattan", "Midtown")
    answers.book[("1500", geocode.street_match_key("Broadway"))] = [broadway]
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "It's 1,500 Broadway.")
    assert located.tool("find_address", call, street="1500 Broadway")["refused"] != (
        "address_not_heard"
    )


def test_a_number_on_its_own_line_and_a_street_much_later_is_not_an_address(located, answers):
    answers.book[("199", geocode.street_match_key("East 4 Street"))] = [EAST_4TH]
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "$199.")
    located.turn(call, "That's what the last guy charged me, can you believe it?")
    located.turn(call, "Anyway I'm on East 4th Street.")
    assert located.tool("find_address", call, street="199 East 4th Street")["refused"] == (
        "address_not_heard"
    )


FOURTH_AVE = Place("50", "4 AVENUE", "11217", "Brooklyn", "Park Slope")
SECOND_AVE = Place("20", "2 AVENUE", "10003", "Manhattan", "East Village")
FIRST_PL = Place("10", "1 PLACE", "11231", "Brooklyn", "Carroll Gardens")


@pytest.mark.parametrize(
    "said, street, place",
    [
        ("50 Fourth Avenue", "50 Fourth Avenue", FOURTH_AVE),
        ("fifty fourth avenue", "50 Fourth Avenue", FOURTH_AVE),
        ("twenty second avenue", "20 Second Avenue", SECOND_AVE),
        ("ten first place", "10 First Place", FIRST_PL),
    ],
)
def test_a_round_house_number_before_an_ordinal_street_still_matches(
    located, answers, said, street, place
):
    """PR #158 review: merging "fifty fourth" into 54 broke "50 Fourth Avenue"; both readings
    count."""
    split = geocode.split_address(street)
    answers.book[(geocode.house_key(split[0]), geocode.street_match_key(split[1]))] = [place]
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, said)
    assert located.tool("find_address", call, street=street)["refused"] != "address_not_heard"


@pytest.mark.parametrize("source, in_area", [("geosearch", True), ("census", False)])
def test_any_zip_the_citys_directory_returns_is_in_the_area(tmp_path, clock, mock, source, in_area):
    """Retell eval suite round 1: 620 Bedford Ave came back out of area, because GeoSearch gives
    11249, a USPS ZIP no census ZIP area (MODZCTA) has. Any ZIP the city's own directory finds is
    a city ZIP; one only the Census fallback found is not taken on trust. 11249 is listed now too,
    so a made-up ZIP stands in for the next one."""
    bedford = Place("620", "BEDFORD AVENUE", "11299", "Brooklyn", "Williamsburg")

    def answer(house, street):
        return [bedford]

    agent = Agent(make_server(tmp_path, clock, mock[0], geocoder=lookup([(source, answer)])), clock)
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "It's 620 Bedford Avenue in Brooklyn.")
    found = agent.tool("find_address", call, street="620 Bedford Ave", borough="Brooklyn")
    assert found["ok"] is in_area, found
    if in_area:
        assert found["data"]["zip"] == "11299" and found["data"]["area"] == "Brooklyn"
        assert agent.tool("check_service_area", call, zip="11299")["ok"]


def test_11249_is_in_the_area():
    config = yaml.safe_load((TENANT_DIR / "config.yaml").read_text())
    assert "11249" in {z for zone in config["zones"] for z in zone["zips"]}


EAST_4TH_LOT = Place("199", "EAST 4 STREET", "10009", "Manhattan", "East Village", "1004000058")


@pytest.fixture
def building(tmp_path, clock, mock, answers):
    """An agent whose lookup also knows a building's homes: nine at 199 East 4th Street."""
    answers.book[("199", geocode.street_match_key("East 4 Street"))] = [EAST_4TH_LOT]
    geocoder = geocode.Geocoder([("book", answers)], AREA.boroughs,
                                units=lambda bbl: {"1004000058": 9}.get(bbl))
    return Agent(make_server(tmp_path, clock, mock[0], geocoder=geocoder), clock)


def test_a_building_of_several_homes_needs_a_unit_and_read_back_asks_once(building):
    """Jackson's call call_2fb4e053ba8b76b57c348845990: 199 East 4th Street came back with no
    unit needed, and the read-back went ahead without one."""
    call = building.start(UNKNOWN)["data"]["call_id"]
    building.turn(call, "It's 199 East 4th Street.")
    found = building.tool("find_address", call, street="199 East 4th Street")
    assert found["ok"] and found["data"]["needs_unit"] is True
    address = {"street": "199 East 4th Street", "unit": "", "city": "New York", "zip": "10009"}
    asked = building.tool("read_back", call, address=address, phone="caller_id")
    assert asked["refused"] == "unit_needed"
    assert asked["say"] == "And what's the apartment or unit number there?"
    building.say(call, asked["say"])
    building.turn(call, "Apartment 2.")
    with_unit = building.tool("read_back", call, address={**address, "unit": "2"},
                              phone="caller_id")
    assert with_unit["ok"] and "unit two" in with_unit["say"]


def test_a_caller_who_says_there_is_no_unit_isnt_asked_again(building):
    call = building.start(UNKNOWN)["data"]["call_id"]
    building.turn(call, "It's 199 East 4th Street.")
    building.tool("find_address", call, street="199 East 4th Street")
    address = {"street": "199 East 4th Street", "unit": "", "city": "New York", "zip": "10009"}
    asked = building.tool("read_back", call, address=address, phone="caller_id")
    building.say(call, asked["say"])
    building.turn(call, "There isn't one, it's the whole building.")
    again = building.tool("read_back", call, address=address, phone="caller_id")
    assert again["ok"], again


def test_an_unknown_unit_count_asks_for_the_unit(located, answers):
    """Jackson's call call_9e278a30dcdb1ff084f39a279c8: 109 East 4th wasn't asked for a unit.
    With no unit count in the city directory, the agent asks, unless the caller said it's a house."""
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "It's 248 Duffield St.")
    found = located.tool("find_address", call, street="248 Duffield St")
    assert found["ok"] and found["data"]["needs_unit"] is True


def test_a_caller_who_said_its_a_house_isnt_flagged_for_a_unit(located, answers):
    """Phone check t2 gas-now, 6 November: "40 Fulton Street, Manhattan. It's a house" came back
    needs_unit, and the model asked "Is there a unit number?" anyway."""
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "It's 248 Duffield St, Brooklyn. It's a house, someone will be home.")
    found = located.tool("find_address", call, street="248 Duffield St")
    assert found["ok"] and found["data"]["needs_unit"] is False


def test_the_whole_house_being_cold_still_asks_for_a_unit(located, answers):
    """Review of tick 2, 6 November: "whole house" is said from apartments too."""
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "There's no heat in the whole house. It's 248 Duffield St, Brooklyn.")
    found = located.tool("find_address", call, street="248 Duffield St")
    assert found["ok"] and found["data"]["needs_unit"] is True


def test_the_units_come_from_the_lot_in_the_city_directory():
    features = [{"properties": {"housenumber": "199", "street": "EAST 4 STREET",
                                "postalcode": "10009", "borough": "Manhattan",
                                "addendum": {"pad": {"bbl": "1004000058"}}}}]
    assert geocode.geosearch_places(features)[0].bbl == "1004000058"


def test_an_address_the_city_doesnt_have_books_unverified_with_a_flag(located, answers):
    """Socket-arm call call_1bcb4106a02dff87609d8e7fd8b: the street wasn't found, the caller
    didn't know the ZIP, and the call ended in a callback. Jackson wants the visit booked."""
    from toolkit import first_open

    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "It's 410 Highland Boulevard in Staten Island.")
    first = located.tool("find_address", call, street="410 Highland Boulevard",
                         borough="Staten Island")
    assert first["refused"] == "address_not_found"
    located.say(call, first["say"])
    located.turn(call, "H, I, G, H, L, A, N, D. I don't know the ZIP, sorry.")
    second = located.tool("find_address", call, street="410 Highland Boulevard",
                          borough="Staten Island", spelled="H, I, G, H, L, A, N, D")
    assert second["ok"], second
    assert second["data"]["verified"] is False and second["data"]["zip"] == "10301"
    assert "office will confirm the address" in second["say"]
    address = {"street": second["data"]["street"], "unit": "", "city": "Staten Island",
               "zip": "10301"}
    located.confirm_read_back(call, address)
    booked = located.tool("book_appointment", call, job_type="tune_up",
                          slot=first_open(located, call),
                          details={"issue": "Yearly tune-up", "since": "due", "tried": "nothing",
                                   "who_home": "me", "approver": "me", "name": "Sam Lee"})
    assert booked["ok"], booked
    reasons = [r[0] for r in located.db().execute("SELECT reason FROM flags WHERE kind = 'person'")]
    assert any("Confirm the address before dispatch" in r for r in reasons), reasons


def test_with_no_borough_the_caller_is_asked_for_it_before_the_zip(located, answers):
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "It's 410 Highland Boulevard.")
    located.tool("find_address", call, street="410 Highland Boulevard")
    located.turn(call, "H, I, G, H, L, A, N, D.")
    second = located.tool("find_address", call, street="410 Highland Boulevard",
                          spelled="H, I, G, H, L, A, N, D")
    assert second["say"] == "And which borough is that in?"


HYLAN = Place("1250", "HYLAN BOULEVARD", "10305", "Staten Island", "Rosebank")


def test_a_street_heard_wrong_and_spelled_by_the_caller_is_looked_up_as_spelled(located, answers):
    """Socket-arm trace: speech-to-text heard "Hylan" as "Highland", the caller spelled it, and the
    model passed spelled "H, Y, L, A, N, Highland Boulevard". find_address still said not found,
    and an estimate became a callback."""
    answers.book[("1250", geocode.street_match_key("Hylan Boulevard"))] = [HYLAN]
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "It's 1250 Highland Boulevard in Staten Island.")
    first = located.tool("find_address", call, street="1250 Highland Boulevard",
                         borough="Staten Island")
    assert first["refused"] == "address_not_found"
    located.say(call, first["say"])
    located.turn(call, "H, Y, L, A, N. Hylan.")
    found = located.tool("find_address", call, street="1250 Highland Boulevard",
                         borough="Staten Island", spelled="H, Y, L, A, N, Highland Boulevard")
    assert found["ok"], found
    assert found["data"]["zip"] == "10305" and found["data"]["street"] == "1250 Hylan Boulevard"
    assert found["data"].get("verified", True) is True


def test_a_surname_the_caller_spelled_doesnt_replace_a_street_they_said(located, answers):
    from test_ts_address_lookup import DUFFIELD

    answers.book[("248", geocode.street_match_key("Duffield Street"))] = [DUFFIELD]
    call = located.start(UNKNOWN)["data"]["call_id"]
    located.turn(call, "It's 248 Duffield Street. My name is Park, P, A, R, K.")
    found = located.tool("find_address", call, street="248 Duffield Street", spelled="P, A, R, K")
    assert found["ok"] and found["data"]["street"].startswith("248 Duffield"), found


def test_just_the_house_settles_the_unit_and_it_isnt_asked_a_third_time(building):
    """Call call_51d7bc63abd9040be451a8bc596: the unit was asked, the caller answered the borough,
    it was asked again, "No, it's just the house", and the read-back asked a third time."""
    call = building.start(UNKNOWN)["data"]["call_id"]
    building.turn(call, "It's 199 East 4th Street.")
    found = building.tool("find_address", call, street="199 East 4th Street")
    assert found["data"]["needs_unit"] is True
    building.say(call, "Is there an apartment or unit number?")
    building.turn(call, "The Bronx.")
    building.say(call, "Is there an apartment or unit number at that address?")
    building.turn(call, "No, it's just the house.")
    building.say(call, "What's the best phone number to reach you?")
    building.turn(call, "This number.")
    address = {"street": "199 East 4th Street", "unit": "", "city": "New York", "zip": "10009"}
    read = building.tool("read_back", call, address=address, phone="caller_id")
    assert read["ok"], read
    assert "unit" not in read["say"]


@pytest.mark.parametrize("said, settled", [
    ("No, it's just the house.", "none"),
    ("It's just the house, no unit number.", "none"),
    ("It's a single-family home.", "none"),
    ("East Fourth Street, unit two", "unit two"),
    ("The Bronx.", ""),
])
def test_what_settles_the_unit(said, settled):
    from app.text import unit_settled

    assert unit_settled([said]) == settled
