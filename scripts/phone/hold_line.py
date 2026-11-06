"""The placeholder office line for the warm transfer (issue #184), until Summit Air has a real one.

A Twilio number that answers with fixed TwiML: a short "Summit Air, one moment." (Retell's warm
transfer waits for a voice before it bridges, so the caller may not hear it), then "Thanks for
holding. Please wait for the next available team member.", then quiet hold with "Thanks for
holding, someone will be with you shortly." about every 40 seconds, for about six minutes.

No server of ours serves it: the TwiML rides in the number's voice URL through Twilio's echo
twimlet, which returns it as given. So nothing to deploy, and the line works with the tool server
down.

    uv run --env-file .env python -m scripts.phone.hold_line --number +1XXXXXXXXXX

The number then goes in the server's .env as TRANSFER_OFFICE_NUMBER. Point that variable at the
real office line when there is one; nothing else changes.
"""

import argparse
import sys
from urllib.parse import quote
from xml.sax.saxutils import escape

from . import twilio

# Another project's number on the same Twilio account (CLAUDE.md for this lane): never touched.
NOT_OURS = {"+16787376865"}
VOICE = "Polly.Matthew-Neural"
FIRST = "Summit Air, one moment."
HOLD = "Thanks for holding. Please wait for the next available team member."
AGAIN = "Thanks for holding, someone will be with you shortly."
# ASSUMED: one reminder every 40 s for about six minutes; a caller still holding then hears
# goodbye. Nine keeps the voice URL under 2,048 characters.
GAP_SECONDS = 40
REPEATS = 9
# Time for Retell to whisper the call's summary to the line before it bridges the caller. At 6 s
# the caller missed "Thanks for" (MEASURED, test call call_9ac28afce19519a5f4dc7dd0554: the
# bridge came about 0.6 s into the line), so 8.
WHISPER_SECONDS = 8
GOODBYE = "Sorry, nobody is free to take your call. Someone from Summit Air will call you back."
ECHO = "https://twimlets.com/echo?Twiml="


def twiml() -> str:
    def say(text: str) -> str:
        return f'<Say voice="{VOICE}">{escape(text)}</Say>'

    hold = f'<Pause length="{GAP_SECONDS}"/>{say(AGAIN)}' * REPEATS
    return (
        f'<Response>{say(FIRST)}<Pause length="{WHISPER_SECONDS}"/>{say(HOLD)}{hold}'
        f"{say(GOODBYE)}<Hangup/></Response>"
    )


def voice_url() -> str:
    return ECHO + quote(twiml(), safe="")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--number", required=True, help="the Twilio number, E.164")
    args = parser.parse_args()
    if args.number in NOT_OURS:
        print(f"{args.number} belongs to another project; not touching it", file=sys.stderr)
        return 2
    with twilio.client() as http:
        found = twilio.number(http, args.number)
        if found.get("trunk_sid"):
            print(f"{args.number} is on SIP trunk {found['trunk_sid']}; not changing it",
                  file=sys.stderr)
            return 2
        twilio.update_number(
            http, found["sid"], VoiceUrl=voice_url(), VoiceMethod="GET",
            FriendlyName="Summit Air hold placeholder",
        )
    print(f"{args.number} now answers with the hold line; set TRANSFER_OFFICE_NUMBER={args.number}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
