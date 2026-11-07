"""A tenant's local names (tenants/<slug>/names.yaml): how the voice says them and the spellings the
ears listen for. Every voice stack is fed from this one list: Cartesia gets a pronunciation
dictionary, Deepgram gets keyterms, Retell gets both in its own fields.

The replacement happens inside the voice provider, so the text the server writes (the read-back
included) is stored exactly as written. docs/explorations/011-local-pronunciation.md has the sources.

    uv run --env-file .env python -m voice.names push [tenant]   # create or update the dictionary
"""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yaml

FILE = "names.yaml"
CARTESIA_URL = "https://api.cartesia.ai/pronunciation-dicts/"
# The dictionary endpoints take only this version (SOURCE: Cartesia API reference, create and
# update). Speech requests may send another; voice/livekit/README.md says which works.
CARTESIA_DICT_VERSION = "2026-08-14"
# Deepgram keyterm limits for Nova-3 (SOURCE: developers.deepgram.com/docs/keyterm).
MAX_KEYTERMS = 100
MAX_KEYTERM_TOKENS = 500
# Cartesia Ink-2 takes the same 100 terms and 1,200 characters (SOURCE:
# https://docs.cartesia.ai/use-the-api/stt/keyterms.md), so a list inside Deepgram's limits fits it.
# Cartesia's Sonic 3 English sounds and its two stress marks (SOURCE: Cartesia IPA phonemes guide).
SOUNDS = {
    "p", "b", "t", "d", "k", "ɡ", "tʃ", "dʒ", "f", "v", "θ", "ð", "s", "z", "ʃ", "ʒ", "h", "m",
    "n", "ŋ", "l", "ɹ", "j", "w",
    "i", "ɪ", "eɪ", "ɛ", "æ", "ɑ", "ɔ", "oʊ", "ʊ", "u", "ə", "ɝ", "aɪ", "aʊ", "ɔɪ",
    "ˈ", "ˌ",
}


@dataclass(frozen=True)
class Name:
    written: str
    match: list[str]
    ipa: str
    says: str
    heard_as: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Names:
    names: list[Name]
    cartesia_dictionary_id: str | None = None

    def keyterms(self) -> list[str]:
        """The spellings Deepgram is told to listen for."""
        return [n.written for n in self.names]

    def cartesia_items(self) -> list[dict]:
        """Case-sensitive, because sonic-3 matches no other way (case-insensitive needs sonic-3.6)."""
        return [
            {"text": key, "pronunciation": n.ipa, "case_sensitive": True}
            for n in self.names
            for key in n.match
        ]

    def retell_dictionary(self) -> list[dict]:
        """Retell's pronunciation_dictionary field: plain IPA, no brackets or bars."""
        return [
            {"word": key, "alphabet": "ipa", "phoneme": plain_ipa(n.ipa)}
            for n in self.names
            for key in n.match
        ]


def load(tenant_dir: Path) -> Names:
    """The tenant's list, or an empty one when it has none. A list that breaks a rule in
    problems() (Deepgram's limits included) is refused here, before any call uses it."""
    path = Path(tenant_dir) / FILE
    if not path.exists():
        return Names([])
    raw = yaml.safe_load(path.read_text())
    names = [
        Name(n["written"], list(n["match"]), n["ipa"], n["says"], list(n.get("heard_as", [])))
        for n in raw["names"]
    ]
    loaded = Names(names, raw.get("cartesia_dictionary_id"))
    wrong = problems(loaded)
    if wrong:
        raise ValueError(f"{path}: " + "; ".join(wrong))
    return loaded


def plain_ipa(ipa: str) -> str:
    return re.sub(r"<<|>>|\|", "", ipa)


def matches(key: str, text: str) -> bool:
    """Cartesia's case-sensitive rule: a lowercase key also matches a capital first letter; any
    other key matches only its own spelling."""
    forms = {key, key[0].upper() + key[1:]} if key == key.lower() else {key}
    return any(re.search(rf"(?<!\w){re.escape(f)}(?!\w)", text) for f in forms)


def problems(names: Names) -> list[str]:
    """Why the list would not work as written; empty when it is fine."""
    found = []
    keys = [key for n in names.names for key in n.match]
    if len(keys) != len(set(keys)):
        found.append("a match key is listed twice")
    for n in names.names:
        if not n.match:
            found.append(f"{n.written}: no match key")
        if not any(matches(key, n.written) for key in n.match):
            found.append(f"{n.written}: no match key catches the written form")
        groups = re.findall(r"<<(.*?)>>", n.ipa)
        if not groups or re.sub(r"<<.*?>>|\s", "", n.ipa):
            found.append(f"{n.written}: ipa must be <<...>> groups, one per word")
        for group in groups:
            sounds = group.split("|")
            if set(sounds) - SOUNDS:
                found.append(f"{n.written}: {sorted(set(sounds) - SOUNDS)} not in Sonic 3 English")
            if "ˈ" not in sounds and "ˌ" not in sounds:
                found.append(f"{n.written}: every word needs a stress mark")
    terms = names.keyterms()
    if len(terms) > MAX_KEYTERMS:
        found.append(f"{len(terms)} keyterms, over Deepgram's {MAX_KEYTERMS}")
    # A token is at least one character, so the characters bound the token count from above.
    if sum(len(t) for t in terms) > MAX_KEYTERM_TOKENS:
        found.append(f"keyterms may pass Deepgram's {MAX_KEYTERM_TOKENS} tokens")
    return found


def push(tenant_dir: Path, api_key: str, client: httpx.Client) -> str:
    """Create the tenant's Cartesia dictionary, or replace the items of the one it has, and save its
    id in names.yaml."""
    names = load(tenant_dir)
    headers = {"X-API-Key": api_key, "Cartesia-Version": CARTESIA_DICT_VERSION}
    body = {"items": names.cartesia_items()}
    if names.cartesia_dictionary_id:
        url = CARTESIA_URL + names.cartesia_dictionary_id
        response = client.patch(url, headers=headers, json=body, timeout=30)
    else:
        body["name"] = f"{Path(tenant_dir).name} local names"
        response = client.post(CARTESIA_URL, headers=headers, json=body, timeout=30)
    response.raise_for_status()
    dict_id = response.json()["id"]
    path = Path(tenant_dir) / FILE
    text = path.read_text()
    path.write_text(
        re.sub(r"(?m)^cartesia_dictionary_id: .*$", f"cartesia_dictionary_id: {dict_id}", text)
    )
    return dict_id


if __name__ == "__main__":
    import os

    root = Path(__file__).resolve().parent.parent
    if sys.argv[1:2] != ["push"]:
        sys.exit("usage: python -m voice.names push [tenant]")
    tenant = root / "tenants" / (sys.argv[2] if len(sys.argv) > 2 else "summit-air")
    with httpx.Client() as http:
        print(push(tenant, os.environ["CARTESIA_API_KEY"], http))
