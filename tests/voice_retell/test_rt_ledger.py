"""Retell sends the whole transcript with every event; each line reaches /calls/{id}/turn once,
in order, with the reply delay worked out from Retell's word timings."""

from retell_kit import utterance

from voice.retell.ledger import Ledger
from voice.retell.timings import REPLY_DELAY, call_summary
from voice.retell.transcript import lines, our_call_id


class Posts:
    def __init__(self, fail_after: int | None = None):
        self.sent: list[tuple[str, str, dict]] = []
        self.fail_after = fail_after

    def __call__(self, call_id, speaker, text, timings):
        if self.fail_after is not None and len(self.sent) >= self.fail_after:
            return None
        self.sent.append((speaker, text, timings))
        return {"ok": True, "data": {}}


def call(*entries):
    return {"call_id": "abc", "transcript_object": list(entries)}


def test_our_call_id_comes_from_the_dynamic_variable_or_the_retell_id():
    assert our_call_id({"call_id": "abc"}) == "retell_abc"
    assert our_call_id({"chat_id": "chat_9"}) == "retell_chat_9"
    tagged = {"call_id": "abc", "retell_llm_dynamic_variables": {"summit_call_id": "eval-7"}}
    assert our_call_id(tagged) == "eval-7"


def test_each_line_is_posted_once_however_many_events_repeat_it(tmp_path):
    ledger, posts = Ledger(tmp_path / "l.db"), Posts()
    first = call(utterance("agent", "Thanks for calling."), utterance("user", "My heat is out."))
    ledger.sync("c1", lines(first), posts)
    ledger.sync("c1", lines(first), posts)
    later = call(*first["transcript_object"], utterance("agent", "Sorry to hear that."))
    ledger.sync("c1", lines(later), posts)
    assert [(s, t) for s, t, _ in posts.sent] == [
        ("agent", "Thanks for calling."),
        ("caller", "My heat is out."),
        ("agent", "Sorry to hear that."),
    ]


def test_a_caller_line_that_grew_is_posted_again_whole(tmp_path):
    ledger, posts = Ledger(tmp_path / "l.db"), Posts()
    ledger.sync("c1", lines(call(utterance("user", "I think I smell"))), posts)
    ledger.sync("c1", lines(call(utterance("user", "I think I smell gas"))), posts)
    assert [t for _, t, _ in posts.sent] == ["I think I smell", "I think I smell gas"]


def test_an_agent_line_that_grew_around_a_tool_call_posts_only_the_new_part(tmp_path):
    ledger, posts = Ledger(tmp_path / "l.db"), Posts()
    ledger.sync("c1", lines(call(utterance("agent", "Let me check."))), posts)
    joined = utterance("agent", "Let me check. You're all set for Tuesday.")
    ledger.sync("c1", lines(call(joined)), posts)
    assert [t for _, t, _ in posts.sent] == ["Let me check.", "You're all set for Tuesday."]


def test_a_line_the_tool_server_did_not_take_is_tried_again_next_time(tmp_path):
    ledger = Ledger(tmp_path / "l.db")
    both = lines(call(utterance("user", "Hello?"), utterance("agent", "Hi there.")))
    failing = Posts(fail_after=1)
    ledger.sync("c1", both, failing)
    assert [t for _, t, _ in failing.sent] == ["Hello?"]
    working = Posts()
    ledger.sync("c1", both, working)
    assert [t for _, t, _ in working.sent] == ["Hi there."]


def test_calls_do_not_share_a_record(tmp_path):
    ledger, posts = Ledger(tmp_path / "l.db"), Posts()
    same = lines(call(utterance("user", "Hello?")))
    ledger.sync("c1", same, posts)
    ledger.sync("c2", same, posts)
    assert len(posts.sent) == 2


def test_an_agent_turn_carries_the_reply_delay_from_the_word_timings(tmp_path):
    ledger, posts = Ledger(tmp_path / "l.db"), Posts()
    caller = utterance("user", "my zip is one one two three one", start=2.0)  # last word ends 4.35
    agent = utterance("agent", "Thanks, that's in our area.", start=5.15)
    ledger.sync("c1", lines(call(caller, agent)), posts)
    assert posts.sent[0][2] == {}
    assert posts.sent[1][2] == {REPLY_DELAY: 800.0}


def test_the_reply_delay_key_is_the_one_the_livekit_worker_writes():
    # voice/livekit/turns.py agent_timings: "reply_ms", from the caller going quiet to first audio.
    assert REPLY_DELAY == "reply_ms"


def test_agent_text_not_yet_spoken_has_no_timing(tmp_path):
    ledger, posts = Ledger(tmp_path / "l.db"), Posts()
    caller = utterance("user", "yes that's right", start=1.0)
    ledger.sync("c1", lines(call(caller, utterance("agent", "Booking that now."))), posts)
    assert posts.sent[1][2] == {}


def test_the_call_summary_maps_retell_latency_and_cost():
    retell_call = {
        "latency": {
            "e2e": {"p50": 910, "p90": 1400, "values": [900, 920]},
            "llm": {"p50": 420, "p90": 610},
            "tts": {"p50": 140, "p90": 190},
            "knowledge_base": {},
        },
        "call_cost": {"combined_cost": 23.4, "total_duration_seconds": 150},
    }
    assert call_summary(retell_call) == {
        "e2e_p50_ms": 910,
        "e2e_p90_ms": 1400,
        "llm_p50_ms": 420,
        "llm_p90_ms": 610,
        "tts_p50_ms": 140,
        "tts_p90_ms": 190,
        "cost_usd": 0.234,
        "duration_s": 150,
    }


def test_transcript_with_tool_calls_gives_the_same_lines_without_the_tool_entries():
    spoken = [utterance("agent", "Hi."), utterance("user", "My heat is out.")]
    with_tools = [
        spoken[0],
        spoken[1],
        {"role": "tool_call_invocation", "name": "find_slots", "arguments": "{}"},
        {"role": "tool_call_result", "content": "{}"},
        utterance("agent", "I can help."),
    ]
    a = lines({"transcript_object": [*spoken, utterance("agent", "I can help.")]})
    b = lines({"transcript_with_tool_calls": with_tools})
    assert [(x.speaker, x.text) for x in a] == [(x.speaker, x.text) for x in b]


def test_the_rest_of_a_reply_split_into_two_lines_has_no_reply_delay(tmp_path):
    ledger, posts = Ledger(tmp_path / "l.db"), Posts()
    caller = utterance("user", "my heat is out", start=1.0)
    first = utterance("agent", "Sorry", start=3.0)
    rest = utterance("agent", "about that, let me check.", start=4.0)
    ledger.sync("c1", lines(call(caller, first, rest)), posts)
    assert posts.sent[1][2] == {REPLY_DELAY: 850.0}
    assert posts.sent[2][2] == {}
