"""One scripted call from a persona file: voice.archive.livekit.test_call with its caller persona swapped.

    PYTHONPATH=~/summit-air/app uv run --env-file ../.env python run_persona.py <persona.txt> \
        [--to +16783366730] [--minutes 5.5]

Copied from the coordinator's /tmp/qa wrapper so the side-by-side runs the same caller, with --to
added so it can dial either line (TWILIO_PHONE_NUMBER, the 646 number, by default).
"""

import argparse
import asyncio
import os
from pathlib import Path

import voice.archive.livekit.test_call as t

parser = argparse.ArgumentParser()
parser.add_argument("persona")
parser.add_argument("--to", default=os.environ.get("TWILIO_PHONE_NUMBER", ""))
parser.add_argument("--minutes", type=float, default=5.5)
args = parser.parse_args()
t.PERSONA = Path(args.persona).read_text().replace("{", "(").replace("}", ")")
t.address_in_area = lambda tenant: ""
asyncio.run(t.main(args.to, args.minutes, "summit-air"))
