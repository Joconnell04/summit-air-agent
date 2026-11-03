"""Switch the number between the LiveKit worker and a recorded fallback, for when the worker is
down but LiveKit is up (Twilio won't fail over on its own, docs/architecture.md, hosting).

    uv run --env-file .env python -m scripts.phone.fallback on|off|status

On: the number leaves the SIP trunk and answers with the tenant's recorded fallback line and a
voicemail, served by Twilio itself so it works even when this server is down. Off: back on the
trunk. Run by deploy/summit-air/watchdog.sh.
"""

import os
import sys
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

import yaml

from . import twilio
from .setup import NAME, e164

ROOT = Path(__file__).resolve().parents[2]


def twiml(line: str) -> str:
    return (
        f"<Response><Say>{escape(' '.join(line.split()))}</Say>"
        '<Record maxLength="120" playBeep="true"/></Response>'
    )


def fallback_url(tenant: str) -> str:
    voice = yaml.safe_load((ROOT / "tenants" / tenant / "agent.yaml").read_text())["voice"]
    return "https://twimlets.com/echo?Twiml=" + quote(twiml(voice["fallback"]))


def tenant_name() -> str:
    """DEFAULT_TENANT, or the only tenant folder."""
    if os.environ.get("DEFAULT_TENANT"):
        return os.environ["DEFAULT_TENANT"]
    [only] = [p.name for p in (ROOT / "tenants").iterdir() if (p / "agent.yaml").exists()]
    return only


def main(mode: str) -> None:
    tenant = tenant_name()
    number = e164(os.environ["TWILIO_PHONE_NUMBER"])
    with twilio.client() as http:
        line = twilio.number(http, number)
        if mode == "status":
            print("worker" if line["trunk_sid"] else "fallback")
            return
        if mode == "on" and line["trunk_sid"]:
            twilio.update_number(http, line["sid"], TrunkSid="", VoiceUrl=fallback_url(tenant))
        elif mode == "off" and not line["trunk_sid"]:
            trunks = http.get(f"{twilio.TRUNKING}/Trunks").raise_for_status().json()["trunks"]
            trunk = next(t for t in trunks if t["friendly_name"] == NAME)
            twilio.update_number(http, line["sid"], TrunkSid=trunk["sid"])
    print(f"fallback {mode}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("on", "off", "status"):
        raise SystemExit("usage: python -m scripts.phone.fallback on|off|status")
    main(sys.argv[1])
