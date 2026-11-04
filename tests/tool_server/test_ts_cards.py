"""Card numbers on the phone and billing disputes (DECISIONS.md 080): card digits are taken out
before anything stores them, phone numbers, ZIPs and house numbers never are, a caller who offers a
card hears the server's line, and a billing dispute gets a callback from the office."""

import json
import re
from datetime import UTC, datetime

import pytest
from toolkit import UNKNOWN, book, count, first_open, ready_to_book

from app import cards
from app.tenant import Tenant, load_agent
from tests.tool_server.toolkit import TENANT_DIR

AGENT = load_agent(TENANT_DIR)[0]
PAYMENTS = AGENT["payments"]
CARD_STOP = " ".join(AGENT["lines"]["card_stop"].split())
GAS_LINES = " ".join(AGENT["safety"]["kinds"]["gas"]["lines"].split())
VISA = "4111 1111 1111 1111"


@pytest.mark.parametrize(
    "line",
    [
        "4111 1111 1111 1111",
        "4111-1111-1111-1111",
        "4111111111111111",
        "it's 5500 0000 0000 0004",
        "amex 3782 822463 10005",
        "3782 822463 10005",
        "four one one one one one one one one one one one one one one one",
        "4 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1",
        "my card number is 4111 1111",
        "card number is four one one one, one one one one",
        "the code on the back is 123",
        "the security code is 7 3 1",
        "my visa is 4111 1111 1111 1111 expiration date 08/28",
    ],
)
def test_card_digits_are_taken_out(line):
    cleaned = cards.redact(line)
    assert cards.MARKER in cleaned
    assert not re.search(r"\d", cleaned)


@pytest.mark.parametrize(
    "line",
    [
        "my number is (718) 555-0101",
        "718-555-0101",
        "7185550101",
        "718 555 0101",
        "+1 718 555 0101",
        "1-718-555-0101",
        "seven one eight, five five five, oh one oh one",
        "call me at 718 555 0101 or 917 555 0199",
        "718 555 0101 11222",
        "it's 145 Java Street, 11222",
        "35-20 82nd St, Jackson Heights, 11372",
        "the zip is 1 1 2 2 2",
        "apartment 4B, the gate code is 4411",
        "it's 52 degrees in here",
        "the window is 8 to 12 on 11/02",
        "the serial number is 123456789012",
        "my account number is 100234",
        "I paid with my visa last time",
        "I was charged twice on November 2nd, about 340 dollars",
    ],
)
def test_phone_numbers_zips_and_house_numbers_stay(line):
    assert cards.redact(line) == line


def test_a_card_cue_covers_the_line_up_to_a_new_subject():
    line = "my card number is 4111 1111 1111 1111 and my phone is 718 555 0101, zip 11222"
    cleaned = cards.redact(line)
    assert "4111" not in cleaned
    assert "718 555 0101" in cleaned and "11222" in cleaned


def test_redaction_is_idempotent_and_scrubs_nested_values():
    once = cards.redact(f"card number {VISA}")
    assert cards.redact(once) == once
    value = {"details": {"issue": f"wants to pay, card {VISA}", "who": ["4111111111111111"]}}
    scrubbed = cards.scrub(value)
    assert "4111" not in json.dumps(scrubbed)


@pytest.mark.parametrize(
    "line,offers",
    [
        ("Can I just pay by card now?", True),
        ("I'd like to give you my credit card", True),
        (f"here's the number, {VISA}", True),
        ("I paid by card last time and it was fine", False),
        ("My card says the heat is out", False),
    ],
)
def test_offering_a_card(line, offers):
    assert cards.offers_card(cards.redact(line), PAYMENTS) is offers


def stored_text(agent, call_id: str) -> str:
    conn = agent.db()
    try:
        rows = []
        for table in ("turns", "tool_calls", "bookings", "flags", "callbacks", "notes"):
            rows += [json.dumps(dict(r)) for r in conn.execute(
                f"SELECT * FROM {table} WHERE call_id = ?", (call_id,)
            )]
        return "\n".join(rows)
    finally:
        conn.close()


def test_a_caller_reading_a_card_hears_the_stop_line_and_nothing_stores_the_digits(agent):
    call_id = agent.start(call_id="call-1")["data"]["call_id"]
    result = agent.turn(call_id, f"Can I pay now? My card number is {VISA}, expires 08/28.")
    assert result["say"] == CARD_STOP
    assert result["data"]["spoken"] == {"kind": "card", "text": CARD_STOP}
    assert result["data"]["safety"] is None
    stored = stored_text(agent, call_id)
    assert "4111" not in stored
    assert cards.MARKER in stored
    # The recording stops, with no line of its own, and is deleted later (recording_declines).
    assert result["data"]["recording"] == {"action": "stop", "text": ""}
    assert count(agent, "SELECT COUNT(*) FROM recording_declines WHERE call_id = ?", call_id) == 1
    assert count(
        agent, "SELECT COUNT(*) FROM flags WHERE call_id = ? AND kind = 'payment'", call_id
    ) == 1


def test_asking_to_pay_by_card_gets_the_line_once_flagged_and_keeps_the_recording(agent):
    call_id = agent.start(call_id="call-1")["data"]["call_id"]
    first = agent.turn(call_id, "Can I pay by card over the phone?")
    again = agent.turn(call_id, "So I can't pay with my card?")
    assert first["say"] == again["say"] == CARD_STOP
    assert first["data"]["recording"] is None
    assert count(
        agent, "SELECT COUNT(*) FROM flags WHERE call_id = ? AND kind = 'payment'", call_id
    ) == 1


def test_a_phone_number_and_zip_on_a_turn_are_stored_as_said(agent):
    call_id = agent.start(call_id="call-1")["data"]["call_id"]
    result = agent.turn(call_id, "Call me at 718 555 0101, it's 145 Java Street, 11222.")
    assert result["data"]["spoken"] is None
    stored = stored_text(agent, call_id)
    assert "718 555 0101" in stored and "145 Java Street, 11222" in stored
    assert cards.MARKER not in stored


def test_card_digits_the_model_copies_into_a_tool_never_reach_the_job_or_the_log(agent):
    call_id = ready_to_book(agent)
    slot = first_open(agent, call_id)
    booked = book(
        agent, call_id, slot, issue=f"Tune-up; caller wanted to pay with card {VISA}"
    )
    assert booked["ok"], booked
    flagged = agent.tool(
        "flag_person", call_id, reason="Caller read card 4111111111111111", callback_in_minutes=60
    )
    assert flagged["ok"], flagged
    assert "4111" not in stored_text(agent, call_id)


def test_gas_lines_still_come_first_on_a_line_with_card_digits(agent):
    call_id = agent.start(call_id="call-1")["data"]["call_id"]
    result = agent.turn(call_id, f"I smell gas, and can I pay with my card {VISA}?")
    assert result["say"] == GAS_LINES
    assert result["data"]["safety"]["action"] == "speak_lines"
    assert result["data"]["spoken"] is None
    assert "4111" not in stored_text(agent, call_id)


def test_a_billing_dispute_gets_a_callback_from_the_office(agent):
    call_id = agent.start(call_id="call-1")["data"]["call_id"]
    result = agent.turn(call_id, "Hi, I was charged twice for the tune-up last week.")
    spoken = result["data"]["spoken"]
    assert spoken["kind"] == "billing"
    assert "office" in spoken["text"] and "call you by" in spoken["text"]
    conn = agent.db()
    try:
        callback = conn.execute("SELECT * FROM callbacks WHERE call_id = ?", (call_id,)).fetchone()
        flag = conn.execute(
            "SELECT * FROM flags WHERE call_id = ? AND kind = 'billing'", (call_id,)
        ).fetchone()
    finally:
        conn.close()
    assert callback["owner"] == "office" and flag is not None
    # Said once: the next mention of the charge goes to the model, which already said it.
    assert agent.turn(call_id, "Yeah, charged twice, it's annoying")["data"]["spoken"] is None


def test_the_office_callback_falls_inside_office_hours():
    from app.lifecycle import office_callback_due

    tenant = Tenant.load(TENANT_DIR)
    late = datetime(2026, 11, 2, 23, 30, tzinfo=UTC)  # 6:30 PM in New York, after hours
    due = office_callback_due(tenant, late)
    assert tenant.is_open(due)
    assert due > late


def test_billing_words_on_a_gas_line_get_the_gas_lines_but_the_callback_is_still_stored(agent):
    call_id = agent.start(caller_id=UNKNOWN, call_id="call-1")["data"]["call_id"]
    result = agent.turn(call_id, "I smell gas and also I was overcharged")
    assert result["say"] == GAS_LINES and result["data"]["spoken"] is None
    assert count(
        agent, "SELECT COUNT(*) FROM flags WHERE call_id = ? AND kind = 'billing'", call_id
    ) == 1
    assert count(agent, "SELECT COUNT(*) FROM callbacks WHERE call_id = ?", call_id) == 1


def test_after_the_gas_lines_a_card_offer_still_hears_the_card_stop(agent):
    call_id = agent.start(call_id="call-1")["data"]["call_id"]
    lines = agent.turn(call_id, "I smell gas")
    agent.say(call_id, lines["say"])
    result = agent.turn(call_id, "Okay. Can I pay by card for the visit?")
    assert result["say"] == CARD_STOP
    assert result["data"]["spoken"]["kind"] == "card"


@pytest.mark.parametrize(
    "line",
    ["1111 1111", "uh 1111, 1111", "one one one one, one one one one", "oh eight twenty eight",
     "0828 and 123"],
)
def test_the_rest_of_a_card_on_the_next_line_is_taken_out(line):
    assert cards.MARKER in cards.redact(line, continuing=True)
    assert not re.search(r"\d", cards.redact(line, continuing=True))


@pytest.mark.parametrize(
    "line",
    [
        "my number is 718 555 0101",
        "7185550101",
        "seven one eight five five five oh one oh one",
        "the zip is 11222",
        "it's 4410 8th Ave in Brooklyn, near the park, by the school",
        "okay one more sec",
    ],
)
def test_after_a_card_a_phone_zip_or_address_line_stays(line):
    assert cards.redact(line, continuing=True) == line


def test_a_card_split_across_two_turns_is_never_stored(agent):
    call_id = agent.start(call_id="call-1")["data"]["call_id"]
    agent.turn(call_id, "Can I pay now? My card number is 4111 1111")
    agent.turn(call_id, "1111 1111")
    after = agent.turn(call_id, "my number is 718 555 0101")
    stored = stored_text(agent, call_id)
    assert "1111" not in stored
    assert "718 555 0101" in stored
    assert after["data"]["spoken"] is None


@pytest.mark.parametrize(
    "first,second",
    [
        ("4111 1111 1111", "1111"),
        ("four one one one, one one one one, one one one one", "one one one one"),
        ("my card number is 4111 1111", "1111 1111"),
    ],
)
def test_a_card_split_across_caller_lines_is_never_stored(agent, first, second):
    call_id = agent.start(call_id="call-1")["data"]["call_id"]
    agent.turn(call_id, first)
    agent.turn(call_id, second)
    conn = agent.db()
    try:
        texts = [r["text"] for r in conn.execute(
            "SELECT text FROM turns WHERE call_id = ? AND speaker = 'caller'", (call_id,)
        )]
    finally:
        conn.close()
    assert all(cards.MARKER in t for t in texts)
    assert not any(ch.isdigit() for t in texts for ch in t)
    assert "one one" not in " ".join(texts)


def test_a_luhn_invalid_card_length_run_is_taken_out_as_a_likely_slip():
    assert cards.redact("4111 1111 1111 1112") == cards.MARKER
    assert cards.redact("4111111111111112") == cards.MARKER


@pytest.mark.parametrize(
    "line",
    [
        "I pay by Visa, I'm at 145 Union Street",
        "my credit card was charged twice on 11/02",
        "it's on my Visa, the zip is 11222",
        "my credit card company called at 3:30 about 189 dollars",
    ],
)
def test_a_card_word_leaves_house_numbers_zips_dates_times_and_amounts(line):
    assert cards.redact(line) == line
