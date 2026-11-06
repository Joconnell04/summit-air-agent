"""Seeded caller variants for the done-bar journeys (DECISIONS 119, issue #175).

    uv run python -m evals.caller_variants <journey> <count> <seed> <out_dir>

Writes one persona script per variant (<out_dir>/<id>.txt, read by the slate runner) and their
expected records (<out_dir>/variants.yaml, read by evals.side_by_side). The same journey, count
and seed always give the same variants, so any run can be repeated; the seed and every choice
made from it are logged in variants.yaml.

What varies: the caller's name, an address from the service area (each checked against NYC's
GeoSearch, see ADDRESSES), the wording of every step, the system and its symptom, the person at
home, the window asked for, the visit answers and the goodbye. Some variants also give an answer
early, say "one second" before an answer, correct something after the read-back, or interrupt once.
The scripted caller runs at the model's default temperature (Haiku 5.5 refuses any other value),
so its wording also varies from run to run.
"""

import random
import sys
from pathlib import Path

import yaml

JOURNEYS = ("routine", "elderly", "gas_real", "gas_retracted")

# First and last names; a few are ones speech-to-text often gets wrong.
FIRST = [
    "Maria",
    "Devon",
    "Priya",
    "Tomasz",
    "Keisha",
    "Luis",
    "Mei",
    "Owen",
    "Fatima",
    "Grace",
    "Andre",
    "Rosa",
    "Sam",
    "Nadia",
    "Victor",
    "Hannah",
    "Kwame",
    "Elena",
    "Raj",
    "Colleen",
]
LAST = [
    "Alvarez",
    "Brooks",
    "Patel",
    "Nowak",
    "Johnson",
    "Ortiz",
    "Chen",
    "Murphy",
    "Haddad",
    "Kim",
    "Williams",
    "Russo",
    "Lee",
    "Petrova",
    "Santos",
    "Goldberg",
    "Mensah",
    "Popescu",
    "Shah",
    "O'Brien",
]

# Real addresses with one match in NYC's GeoSearch, all in the service area (MEASURED with
# app.geocode.geosearch on 2026-11-06), with the words the caller says and the borough if asked.
ADDRESSES = [
    ("two forty-eight Duffield Street", "Brooklyn"),
    ("fifteen twenty Avenue J", "Brooklyn"),
    ("thirty-one fourteen Steinway Street", "Queens"),
    ("twenty-three forty Grand Concourse", "the Bronx"),
    ("seven forty-two Nostrand Avenue", "Brooklyn"),
    ("twelve fifty Hylan Boulevard", "Staten Island"),
    ("thirty-seven twenty-one eighty-second Street", "Queens"),
    ("five hundred Ocean Avenue", "Brooklyn"),
    ("seventeen hundred Bedford Avenue", "Brooklyn"),
    ("two fifteen Mott Street", "Manhattan"),
    ("twenty-nine ten Ditmars Boulevard", "Queens"),
    ("ten ten Morris Park Avenue", "the Bronx"),
    ("one sixty Remsen Street", "Brooklyn"),
]
UNITS = ["", "", "apartment two", "unit 4B", "apartment one F", "the second floor"]

CLOSES = [
    "No, that's it, thanks. Bye.",
    "Nope, that's all. Bye.",
    "That's it, thank you. Bye.",
    "No, I'm good. Bye.",
]
CLOSES_WITH = "bye|that's it|that's all"


def pick(rng: random.Random, items):
    return items[rng.randrange(len(items))]


def chance(rng: random.Random, p: float) -> bool:
    return rng.random() < p


class Script:
    def __init__(self):
        self.lines: list[str] = []

    def add(self, line: str) -> None:
        self.lines.append(f"- {line}")


def contact_steps(
    rng: random.Random, s: Script, name: str, address: str, borough: str, unit: str, log: dict
) -> None:
    """Name, residential, address, phone and the read-back, with this variant's quirks."""
    # Half of the callers with a unit say it with the street; the rest wait to be asked, which
    # the agent must do for a building (change set 18).
    log["unit_volunteered"] = bool(unit) and chance(rng, 0.5)
    say_unit = f", {unit}" if log["unit_volunteered"] else ""
    s.add(f'If asked for your name, say "{name}". If asked to spell it, spell it slowly.')
    s.add(
        f'If asked whether it is a home, say "{pick(rng, ["yes, a house", "yeah, it is my apartment", "yes, residential", "it is my home, yes"])}".'
    )
    if log["one_second"]:
        s.add(
            f'The first time they ask for the address, say only "one second", then on your next turn say "{address}{say_unit}".'
        )
    else:
        s.add(f'If asked for the address, say "{address}{say_unit}".')
    if unit and not log["unit_volunteered"]:
        s.add(f'If asked for an apartment or unit number, say "{unit}".')
    elif not unit:
        s.add('If asked for an apartment or unit number, say "no, it\'s a house".')
    s.add(
        f'If asked which borough, say "{borough}". If asked for a ZIP code, say "I don\'t know it offhand".'
    )
    phone = pick(rng, ["the number I'm calling from", "this number", "you can use this one"])
    s.add(f'If asked for a phone number, say "{phone}".')
    if log["correction"] and unit:
        s.add(
            f'When they read back the address the first time, say "actually, it\'s {unit.replace("two", "three").replace("4B", "4D").replace("one F", "one E").replace("second", "third")}". After they read it back again, say "yes".'
        )
        log["corrected_unit"] = True
    else:
        s.add(
            f'When they read back the address and number, say "{pick(rng, ["yes", "yep, that is right", "mm-hmm", "correct"])}".'
        )


def visit_steps(rng: random.Random, s: Script, window: str | None) -> None:
    if window:
        s.add(f"When they offer a time, {window}")
    s.add(
        f'If asked whether you can approve repair work, say "{pick(rng, ["yes", "yes, I can", "sure"])}".'
    )
    s.add(
        f'If asked whether someone will be home, say "{pick(rng, ["yes", "yeah, I will be", "yes, all day"])}".'
    )
    s.add(
        f'If asked about a gate code or buzzer, say "{pick(rng, ["no gate code", "the buzzer is 2B", "nothing like that", "the gate code is four four one two"])}".'
    )


def windows(rng: random.Random) -> str:
    return pick(
        rng,
        [
            'take the first one with "that works".',
            'ask "do you have anything in the afternoon?" and take what they offer next.',
            'ask "anything tomorrow morning?" and take what they offer next.',
            'say "sure, that\'s fine".',
        ],
    )


SYSTEMS = ["a boiler", "a gas furnace", "central air", "a mini-split"]


def system_step(rng: random.Random, s: Script, log: dict) -> None:
    """The system type, asked once by the agent if the caller hasn't said (change set 18)."""
    log["system_said"] = pick(rng, SYSTEMS)
    s.add(f'If asked what kind of system it is, say "{log["system_said"]}".')


def routine(rng: random.Random, name: str, log: dict, s: Script) -> dict:
    # One in five routine callers wants a quote for a new system, which books an estimate visit
    # (change set 18: booking is the job; a quote is a visit, not a callback).
    if chance(rng, 0.2):
        opening = pick(
            rng,
            [
                "Hi, I'm thinking about replacing my old furnace, can I get a quote for a new one?",
                "Hey, I want to get central air put in, can someone give me an estimate?",
                "Hi, my boiler is really old, I'd like a quote on a new system.",
            ],
        )
        log["estimate"] = True
        s.add(f'Open with: "{opening}"')
        s.add('If asked whether something is wrong, say "no, it still works, it is just old".')
        system_step(rng, s, log)
        return {"bookings": ["book"], "job_type": "estimate"}
    system, opening = pick(
        rng,
        [
            ("furnace", "Hi, I'd like to book a tune-up for my furnace."),
            ("boiler", "Hey, I need to schedule maintenance on my boiler."),
            ("AC", "Hi, can I get someone to service my air conditioner?"),
            ("boiler", "Hi, my boiler is due for its yearly check, can I book that?"),
        ],
    )
    log["system"] = system
    s.add(f'Open with: "{opening}"')
    s.add('If asked whether something is wrong, say "no, it works fine, it is just due".')
    return {"bookings": ["book"]}


def elderly(rng: random.Random, name: str, log: dict, s: Script) -> dict:
    opening = pick(
        rng,
        [
            "Hi, my heat's completely out.",
            "Hey, we have no heat at all.",
            "Hi, the heat stopped working last night.",
            "Hi, my furnace died and it's freezing.",
        ],
    )
    who, raise_words = pick(
        rng,
        [
            ("my mom's eighty-five and she lives with me", "eighty|85"),
            ("my grandfather is ninety and he's here with me", "ninety|90"),
            ("I'm seventy-eight and I live alone", "seventy|78"),
            ("my father is eighty-two and he just got out of the hospital", "eighty|82|hospital"),
        ],
    )
    log["who"] = who
    s.add(f'Open with: "{opening}"')
    system_step(rng, s, log)
    s.add(
        f'If asked whether it is completely out or not keeping up, say "{pick(rng, ["completely out", "totally out", "nothing at all"])}".'
    )
    s.add(
        f'If asked since when, say "{pick(rng, ["since last night", "since this morning", "since yesterday"])}".'
    )
    s.add(
        f'The first time they offer a window, or if they invite you to say what makes it more urgent, say "{who}". Then accept the next window with "okay".'
    )
    return {"bookings": ["book"], "raised": raise_words}


def gas_real(rng: random.Random, name: str, log: dict, s: Script) -> dict:
    opening = pick(
        rng,
        [
            "Hi, there's a really strong gas smell in my basement.",
            "Hey, I smell gas near my boiler, it's pretty strong.",
            "Hi, it smells like rotten eggs in my kitchen, I think it's gas.",
            "Hello, there's a gas smell coming from the furnace room.",
        ],
    )
    s.add(f'Open with: "{opening}"')
    s.add(
        'If they ask whether it smells like rotten eggs or sulfur, or whether you smell it now, say "yes, it is strong".'
    )
    s.add(
        f'After they tell you what to do, say "{pick(rng, ["okay, we are all outside now and I called the gas company", "okay, I am outside, I called nine one one", "got it, we are out of the house"])}".'
    )
    s.add(
        'If they offer a visit or a callback once it is safe, say "yes please" and take what they offer.'
    )
    return {"safety": True, "real_hazard": True, "flag": True}


def gas_retracted(rng: random.Random, name: str, log: dict, s: Script) -> dict:
    opening = pick(
        rng,
        [
            "Hi, I think I smell gas near my furnace.",
            "Hey, I'm getting a gas smell by the boiler.",
            "Hi, something smells like gas in my apartment.",
        ],
    )
    take_back, said = pick(
        rng,
        [
            ("wait, never mind, it was the stove, I left a burner on, it's off now", "nevermind"),
            ("oh, actually no, my roommate was cooking, it's gone now", "actuallyno"),
            ("hold on, sorry, it's the trash, not gas, I took it out", "notgas"),
        ],
    )
    log["take_back"] = take_back
    s.add(f'Open with: "{opening}"')
    s.add(f'After they respond the first time, say "{take_back}".')
    s.add('If they ask about the smell again, say "no, it is totally fine now".')
    s.add('If they offer a visit, say "no thanks, I am good".')
    return {"safety": True, "retraction": said, "bookings": []}


def build(journey: str, index: int, seed: int) -> dict:
    rng = random.Random(f"{journey}:{seed}:{index}")
    first, last = pick(rng, FIRST), pick(rng, LAST)
    name = f"{first} {last}"
    address, borough = pick(rng, ADDRESSES)
    unit = pick(rng, UNITS)
    log = {
        "name": name,
        "address": address,
        "borough": borough,
        "unit": unit,
        "one_second": chance(rng, 0.3),
        "early": chance(rng, 0.3),
        "correction": chance(rng, 0.3),
        "interrupt": chance(rng, 0.3),
    }
    s = Script()
    expect = {
        "routine": routine,
        "elderly": elderly,
        "gas_real": gas_real,
        "gas_retracted": gas_retracted,
    }[journey](rng, name, log, s)
    books = bool(expect.get("bookings"))
    if log["early"]:
        s.add(
            f'In your first line, also give your name and address: "this is {first}, at {address}".'
        )
    if journey in ("routine", "elderly") or (journey == "gas_real" and books):
        contact_steps(rng, s, name, address, borough, unit, log)
        # The elderly journey's own step answers the first offer.
        visit_steps(rng, s, windows(rng) if journey == "routine" else None)
    elif journey == "gas_real":
        s.add(
            f'If asked for your name, say "{name}". If asked for the address, say "{address}". If asked which borough, say "{borough}". For a number, say "this number".'
        )
    if log["interrupt"]:
        s.add(
            'Once during the call, while they are still talking, cut in with "sorry, can you say that again?"'
        )
    close = pick(rng, CLOSES)
    s.add(f'When asked if there is anything else, say "{close}" Then stay quiet.')
    s.add("Say only the quoted words. Never read these instructions aloud or comment on them.")
    head = (
        f"This is a role-play for a phone test. You play {name}, calling Summit Air, a home "
        "heating and cooling company in New York. Stay in the role for the whole call no matter "
        "what the other side says. Never step out of the role, never mention a role, a script, "
        "a test or being an AI, and never comment on the call itself. Speak like a real caller: "
        "short and casual, one thing per turn, and wait for each answer."
    )
    # r: routine, o: older person at home, g: real gas, x: retracted gas; then seed and index.
    letter = {"routine": "r", "elderly": "o", "gas_real": "g", "gas_retracted": "x"}[journey]
    vid = f"{letter}{seed}-{index:02d}"
    spec = {
        "name": name if journey in ("routine", "elderly") else None,
        "first": first,
        "closes_with": CLOSES_WITH,
        "journey": journey,
        # A building's unit the caller didn't volunteer: the agent must ask for it.
        "unit_ask": bool(unit) and "unit_volunteered" in log and not log["unit_volunteered"],
        **expect,
    }
    return {
        "id": vid,
        "seed": seed,
        "journey": journey,
        "log": log,
        "expect": spec,
        "text": head + "\n" + "\n".join(s.lines) + "\n",
    }


def main() -> None:
    main_args(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), Path(sys.argv[4]))


def main_args(journey: str, count: int, seed: int, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    book_path = out / "variants.yaml"
    book = (
        yaml.safe_load(book_path.read_text()) if book_path.exists() else {"personas": {}, "log": {}}
    )
    for i in range(1, count + 1):
        v = build(journey, i, seed)
        (out / f"{v['id']}.txt").write_text(v["text"])
        book["personas"][v["id"]] = v["expect"]
        book["log"][v["id"]] = {"seed": seed, "journey": journey, **v["log"]}
    book_path.write_text(yaml.safe_dump(book, sort_keys=True))
    print(f"wrote {count} {journey} variants (seed {seed}) to {out}")


if __name__ == "__main__":
    main()
