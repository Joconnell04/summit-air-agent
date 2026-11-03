from evals.transcribe import level_for, roughen, seeded, spell_digits


def test_easy_callers_pass_through_clean():
    assert roughen("It's 11231, OK?", "none", seeded("x", 1)) == "It's 11231, OK?"


def test_light_drops_punctuation_and_casing_and_spells_digits():
    got = roughen("My ZIP is 11201, for 4 people.", "light", seeded("x", 1))
    assert got == "my zip is one one two oh one for 4 people"


def test_ages_and_temperatures_stay_as_numbers():
    got = roughen(
        "My 104-year-old dad, it's 52 degrees, call 718 555 0142.", "light", seeded("x", 1)
    )
    assert got == (
        "my 104 year old dad it's 52 degrees call seven one eight five five five oh one four two"
    )


def test_heavy_is_rougher_but_the_same_for_the_same_seed():
    line = "So um the heat is out and there is no hot water for the kids, it's freezing here"
    a = roughen(line, "heavy", seeded("s", 1))
    assert a == roughen(line, "heavy", seeded("s", 1))
    assert a != roughen(line, "light", seeded("s", 1))


def test_level_follows_difficulty_and_the_transcription_trait():
    assert level_for("easy", []) == "none"
    assert level_for("rigorous", []) == "light"
    assert level_for("easy", ["transcription-errors"]) == "heavy"


def test_spell_digits():
    assert spell_digits("718").split() == ["seven", "one", "eight"]
    assert spell_digits("an 84-year-old") == "an 84-year-old"
