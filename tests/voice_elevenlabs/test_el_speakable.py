"""ElevenLabs' voice is handed street words spelled out and ZIP and phone digits spaced, and the
same rules hold when the text streams in small pieces."""

import pytest

from voice.archive.elevenlabs.speakable import SpeakableStream, speakable

READ_BACK = ("Let me read that back: your home at 801 Fenimore St, Brooklyn, 11206, and the best "
             "number is 347, 555, 0162. Is that right?")
SPOKEN = ("Let me read that back: your home at 801 Fenimore Street, Brooklyn, 1 1 2 0 6, and the "
          "best number is 3 4 7, 5 5 5, 0 1 6 2. Is that right?")


def test_the_read_back_is_spelled_out_for_the_voice():
    assert speakable(READ_BACK) == SPOKEN


@pytest.mark.parametrize("text,said", [
    ("809 Union St.", "809 Union Street."),
    ("41-12 Bell Blvd, Bayside", "41-12 Bell Boulevard, Bayside"),
    ("31-18 30th Ave, Astoria", "31-18 30th Ave, Astoria"),  # no capitalized name before it
    ("We're at 801 St Marks Pl", "We're at 801 St Marks Place"),
    ("call (718) 555-0147 today", "call 7 1 8, 5 5 5, 0 1 4 7 today"),
    ("hang up and call 911 from outside", "hang up and call 911 from outside"),
    ("the number ending 0114?", "the number ending 0 1 1 4?"),
    ("the one ending in 0162.", "the one ending in 0 1 6 2."),
    ("between 8 and 12 tomorrow", "between 8 and 12 tomorrow"),
])
def test_street_words_and_numbers(text, said):
    assert speakable(text) == said


@pytest.mark.parametrize("size", [1, 3, 7, 50])
def test_streamed_in_pieces_it_comes_out_the_same(size):
    stream = SpeakableStream()
    pieces = [READ_BACK[i:i + size] for i in range(0, len(READ_BACK), size)]
    out = "".join(stream.feed(p) for p in pieces) + stream.flush()
    assert out == SPOKEN


def test_plain_words_are_not_held_back():
    stream = SpeakableStream()
    assert stream.feed("Sure, I can help with") == "Sure, I can help"
    assert stream.feed(" that. ") == " with that."
