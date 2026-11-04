"""How ElevenLabs' voice is handed our text, so it reads addresses and numbers back the way Cartesia
does on our stack.

Round 1 of the audio bake-off found ElevenLabs reading "Fenimore St" as "Fenimore Saint" and
running ZIP and phone digits together, so callers could not confirm the read-back
(evals/rounds/bakeoff-audio-01.md, section 4). These rules spell out a street abbreviation after a
street name, and space the digits of a ZIP code, a phone number and its last four so each is said
on its own.

As with the tenant's local names (DECISIONS.md 023), the change is made only on the way to the
voice. The tool server is sent the text as the brain wrote it, so the stored read-back and the
check on the safety lines' wording see exactly what they did before.
"""

import re

# Expanded only after a capitalized word, the street's name: "Fenimore St" is Street, while "801 St
# Marks Pl" (a number before it) and "on St Johns" (a lowercase word) stay Saint.
STREET_WORDS = {"St": "Street", "Ave": "Avenue", "Blvd": "Boulevard", "Rd": "Road", "Pl": "Place",
                "Pkwy": "Parkway"}
STREET = re.compile(r"\b([A-Z][a-z']+) (St|Ave|Blvd|Rd|Pl|Pkwy)\b(?=[\s.,;:!?]|$)")
PHONE = re.compile(r"\(?\b(\d{3})\)?[\s,.-]+(\d{3})[\s,.-]+(\d{4})\b")
ZIP = re.compile(r"(?<![\d-])\b(\d{5})\b(?![\d-])")
# "the number ending 0114": the last four of a phone number, said digit by digit.
LAST_FOUR = re.compile(r"\b(ending(?: in)?) (\d{4})\b")
# Text that may change once more of it arrives: a digit or a street abbreviation.
RISKY = re.compile(r"\d|\b(?:St|Ave|Blvd|Rd|Pl|Pkwy)\b")
SENTENCE_END = re.compile(r"[.!?](?=\s)")


def spaced(digits: str) -> str:
    return " ".join(digits)


def speakable(text: str) -> str:
    text = STREET.sub(lambda m: f"{m.group(1)} {STREET_WORDS[m.group(2)]}", text)
    text = PHONE.sub(lambda m: ", ".join(spaced(g) for g in m.groups()), text)
    text = LAST_FOUR.sub(lambda m: f"{m.group(1)} {spaced(m.group(2))}", text)
    return ZIP.sub(lambda m: spaced(m.group(1)), text)


class SpeakableStream:
    """The same rules on text that arrives a few characters at a time. Plain words go out as soon
    as a word is complete; a stretch with digits or an abbreviation waits for the end of its
    sentence, so a ZIP or a phone number split across pieces is still caught whole."""

    def __init__(self):
        self.held = ""

    def feed(self, piece: str) -> str:
        self.held += piece
        out = []
        while self.held:
            risky = RISKY.search(self.held)
            if risky is None:
                # Keep the last word: the next piece may make it "Fenimore St".
                cut = max(self.held.rfind(" "), self.held.rfind("\n"))
                if cut > 0:
                    out.append(self.held[:cut])
                    self.held = self.held[cut:]
                break
            end = SENTENCE_END.search(self.held, risky.end())
            if end is None:
                # Send what comes before the sentence that holds the risky text.
                starts = [m.end() for m in SENTENCE_END.finditer(self.held, 0, risky.start())]
                if starts:
                    out.append(self.held[:starts[-1]])
                    self.held = self.held[starts[-1]:]
                break
            out.append(speakable(self.held[:end.end()]))
            self.held = self.held[end.end():]
        return "".join(out)

    def flush(self) -> str:
        rest, self.held = self.held, ""
        return speakable(rest)
