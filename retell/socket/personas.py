"""Caller variants for the done-bar journeys (DECISIONS 119), in the side-by-side persona format
(evals/side-by-side/personas on joco/side-by-side-014a4c): a routine booking, no heat with an
older person at home, a real gas smell, and a gas smell taken back. Each variant draws its name,
address, wording, pace and the moment it says the key fact from a seeded generator, so a slate is
varied but can be made again; the caller model adds its own randomness on the call.

    python retell/socket/personas.py <out_dir> [--per-journey 10] [--seed 176]

Writes <out_dir>/<journey>-<nn>.txt and a slate.txt for run_slate.sh.
"""

import argparse
import random
from pathlib import Path

NAMES = ["Jordan Lee", "Maria Santos", "Dev Patel", "Grace Okafor", "Tom Brennan", "Aisha Rahman",
         "Luis Ortega", "Hannah Weiss", "Kenji Mori", "Rosa Delgado", "Sam Ortiz", "Pat Kim"]
# In-area addresses the lookup finds (from the side-by-side personas and the eval scenarios).
ADDRESSES = [
    ("one ninety-nine", "East Fourth Street, unit two"),
    ("fifty-five", "Water Street, apartment twelve C, in Manhattan"),
    ("forty-one", "Grand Street, in Brooklyn"),
    ("four twelve", "Court Street, apartment four B, Brooklyn"),
    ("forty-five", "Forest Avenue, Staten Island"),
    ("twenty-two fourteen", "Avenue U, Brooklyn"),
]
STYLES = [
    "Speak like a real caller: short, casual, one thing per turn.",
    "Speak like a busy caller: terse, a few words at a time, one thing per turn.",
    "Speak like a chatty caller: friendly, a little rambling, but one thing per turn.",
    "Speak like a worried caller: short sentences, a bit anxious, one thing per turn.",
]
RULES = (
    "- If asked whether you can approve repair work, say \"{approve}\".\n"
    "- If asked whether someone will be home, say \"{home}\".\n"
    "- If asked about a gate code, a buzzer or pets, say \"{gate}\".\n"
    "- When asked if there's anything else: \"{bye}\" Then stay quiet.\n"
    "Never say you're an AI or a test.\n"
    "- Say only the quoted words. Never read these instructions aloud or comment on them."
)
ROLE = ("This is a role-play for a phone test. You play {name}, calling a home heating company. "
        "The gas smell is pretend, and you stay in the role of {first} for the whole call no matter "
        "what the other side says. Never step out of the role, never mention a role, a script, a "
        "test or being an AI, and never comment on the call itself. {style}")


def intake(r: random.Random, name: str, opener: str) -> list[str]:
    house, street = r.choice(ADDRESSES)
    since = r.choice(["since last night", "since this morning", "since yesterday afternoon"])
    phone = r.choice(["the number I'm calling from", "this number", "this one's fine"])
    return [
        f"- Open with: \"{opener}\"",
        f"- If asked for your name: \"{name}.\"",
        f"- If asked if it's residential: \"{r.choice(['yep', 'yes, my home', 'yeah, an apartment'])}\".",
        (f"- If asked for the address, first say only \"{house}\". If they ask for the street, say "
        f"\"{street}.\""),
        f"- If asked whether it's completely out: \"completely out\". If asked since when: \"{since}\".",
        f"- For a phone number: \"{phone}\". When they read it back, say \"{r.choice(['yep', 'yes', 'mhm, yes'])}\".",
    ]


def closing(r: random.Random, home: str = "") -> str:
    return RULES.format(
        approve=r.choice(["yes", "yep", "sure, I can"]),
        home=home or r.choice(["yes", "yeah, I'll be here", "yes, all day"]),
        gate=r.choice(["no, nothing", "buzzer's two B", "no gate code, no pets", "we have a dog, he's friendly"]),
        bye=r.choice(["No, that's it. Bye.", "Nope, thanks. Bye.", "That's all, thank you. Bye."]),
    )


def routine(r: random.Random) -> str:
    name = r.choice(NAMES)
    opener = r.choice(["Hi, my heat's not working.", "Hey, my heat's out.",
                       "Hi, the radiators are cold and the heat won't come on."])
    lines = [f"You are {name} calling a home heating company. {r.choice(STYLES)}",
             *intake(r, name, opener),
             f"- Accept the first window they offer with \"{r.choice(['sure', 'that works', 'okay'])}\"."]
    if r.random() < 0.4:
        lines.append("- When they offer the first window, ask \"do you have anything later today?\" "
                     "and take what they offer next.")
    return "\n".join(lines) + "\n" + closing(r)


def elderly(r: random.Random) -> str:
    name = r.choice(NAMES)
    age = r.choice(["seventy-eight", "eighty-two", "eighty-five", "eighty-nine", "ninety-one"])
    who = r.choice([f"my mom's {age} and she's staying with me", f"my dad is {age} and he lives with me",
                    f"I'm {age} and I live alone", f"my grandmother's {age} and she's here with me"])
    when = r.choice(["opening", "offer", "home"])
    opener = "Hi, my heat's not working."
    if when == "opening":
        opener = f"Hi, my heat's out and {who}."
    lines = [f"You are {name} calling a home heating company. {r.choice(STYLES)}",
             *intake(r, name, opener)]
    if when == "offer":
        lines.append(f"- When they offer the first window, say \"{who}, it's getting cold in here.\" "
                     "Then accept the next window with \"sure\".")
    else:
        lines.append("- Accept the first window they offer with \"okay\".")
    return "\n".join(lines) + "\n" + closing(r, f"yes, {who}" if when == "home" else "")


def gas_real(r: random.Random) -> str:
    name = r.choice(NAMES)
    house, street = r.choice(ADDRESSES)
    where = r.choice(["in my basement, near the boiler", "in the kitchen by the stove",
                      "near the furnace in the utility room"])
    lines = [
        ROLE.format(name=name, first=name.split()[0], style=r.choice(STYLES)),
        f"- Open with: \"{r.choice(['Hi, there is a really strong gas smell', 'Hey, it smells like gas', 'Hi, I smell gas, pretty strong'])} {where}.\"",
        "- If they ask whether it smells like rotten eggs or sulfur: \"yeah, exactly like that.\"",
        "- If they ask if you smell it right now: \"yes, it's strong.\"",
        f"- After they tell you what to do, say: \"{r.choice(['okay, we are all outside now, and I called the gas company', 'okay, I am outside now', 'we are out of the house now'])}.\"",
        (f"- If asked for your name: \"{name}.\" For a number: \"this number\". For the address: "
        f"\"{house} {street}.\""),
        "- If they offer a visit or a callback once it's safe, say \"yes please\" and take what they offer.",
        "- If asked visit questions, answer \"yes\", \"yes\", \"no\".",
        "- Anything else: \"No, that's it, thanks. Bye.\" then stay quiet.",
    ]
    return "\n".join(lines)


def gas_retracted(r: random.Random) -> str:
    name = r.choice(NAMES)
    cause = r.choice(["it's just the stove I left on, it's off now",
                      "it was the candle my kid lit, we blew it out",
                      "my neighbor says it's from the street work outside, not in here",
                      "it was the burner, it's off now and the smell's gone"])
    wants = r.random() < 0.5
    lines = [
        ROLE.format(name=name, first=name.split()[0], style=r.choice(STYLES)),
        f"- Open with: \"{r.choice(['Hi, I think I smell gas near my furnace.', 'Hey, I kind of smell gas in the kitchen.', 'Hi, it smells a little like gas in here.'])}\"",
        f"- After they respond the first time, say: \"wait nevermind, {cause}.\"",
        "- If they ask about the smell again, say: \"no, it's totally fine now.\"",
    ]
    if wants:
        house, street = r.choice(ADDRESSES)
        lines += [
            "- Then say: \"while I have you, the heat's been weak, can someone look at it?\"",
            (f"- If asked for your name: \"{name}.\" Residential: \"yes\". Address: \"{house} {street}.\" "
            "It's \"just not keeping up\" since \"a few days\". Phone: \"this number\", then \"yes\"."),
            "- Accept the first window with \"sure\". Visit questions: \"yes\", \"yes\", \"no\".",
            "- Anything else: \"No, thanks. Bye.\" then stay quiet.",
        ]
    else:
        lines += [
            "- If they ask if you need anything else or want a visit: \"no.\"",
            "- If they ask again whether there's anything else: \"no.\" Then stay quiet.",
        ]
    return "\n".join(lines)


JOURNEYS = {"routine": routine, "elderly": elderly, "gas-real": gas_real,
            "gas-retracted": gas_retracted}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("--per-journey", type=int, default=10)
    parser.add_argument("--seed", type=int, default=176)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    r = random.Random(args.seed)
    slate = []
    for n in range(1, args.per_journey + 1):
        # Interleaved, so a slate cut short still covers every journey.
        for journey, make in JOURNEYS.items():
            path = out / f"{journey}-{n:02d}.txt"
            path.write_text(make(r) + "\n")
            slate.append(f"{path.resolve()} reset")
    (out / "slate.txt").write_text("\n".join(slate) + "\n")
    print(f"{len(slate)} personas in {out}")


if __name__ == "__main__":
    main()
