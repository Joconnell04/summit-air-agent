"""Roughen a caller's line the way phone speech-to-text does, before the agent sees it.

Seeded, so a scenario produces the same transcript every time. Easy scenarios pass through clean;
harder ones lose punctuation and casing and have digits spelled out ("for one one two oh one");
the transcription-errors trait also swaps sound-alike words and drops the odd word.
"""
import random
import re

DIGITS = ["oh", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
SOUND_ALIKES = {
    "for": "four", "to": "two", "two": "to", "four": "for", "there": "their", "their": "there",
    "right": "write", "here": "hear", "no": "know", "eight": "ate", "by": "buy", "street": "st",
    "heat": "heats", "an": "and", "it's": "its", "you're": "your", "the": "a",
}
FILLERS = {"um", "uh", "like", "so"}


def level_for(difficulty: str, traits: list[str]) -> str:
    if "transcription-errors" in traits:
        return "heavy"
    return "none" if difficulty == "easy" else "light"


def spell_digits(text: str) -> str:
    return re.sub(r"\d", lambda m: f" {DIGITS[int(m.group())]} ", text)


def roughen(text: str, level: str, rng: random.Random) -> str:
    if level == "none":
        return text
    out = spell_digits(text).lower()
    out = re.sub(r"[^\w\s']", " ", out)
    tokens = out.split()
    if level == "heavy":
        changed = []
        for token in tokens:
            if token in FILLERS and rng.random() < 0.5:
                continue
            if len(tokens) > 6 and rng.random() < 0.04:
                continue
            if token in SOUND_ALIKES and rng.random() < 0.3:
                token = SOUND_ALIKES[token]
            changed.append(token)
        tokens = changed
    return " ".join(tokens)


def seeded(scenario_id: str, sample: int) -> random.Random:
    return random.Random(f"{scenario_id}/{sample}")
