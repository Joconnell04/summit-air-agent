"""Point the Twilio number at one voice stack, for single live calls per stack on one number.

    uv run --env-file .env python -m scripts.route_number livekit|retell|retell_native|deepgram|vapi|gpt_live|status

`livekit` restores the setup scripts/phone/setup.py makes, as saved before the first change in
deploy/phone/routing-before.json: the number on the "summit-air" Elastic SIP trunk, whose
origination goes to LiveKit. Every other entry moves the number off that trunk and leaves the
trunk itself untouched, so going back is one field.

- `retell`: the number on its own trunk, "summit-air-retell", whose origination is Retell's SIP
  server, with the number imported into Retell on the production voice agent and the bridge's
  inbound webhook (SOURCE: https://docs.retellai.com/deploy/custom-telephony.md).
- `retell_native`: the same, on the native Retell agent (voice/retell_native).
- `deepgram`: no trunk; the number's voice URL returns TwiML that streams the call to the Deepgram
  Voice Agent bridge on the server (voice/deepgram_agent/).
- `vapi`: no trunk; the voice URL is Vapi's Twilio inbound endpoint, once the number is imported
  into Vapi.
- `gpt_live`: the number on its own trunk, "summit-air-openai", whose origination is OpenAI's SIP
  endpoint for the project in OPENAI_PROJECT_ID.

The watchdog (deploy/summit-air/watchdog.sh) can move the number back to the LiveKit trunk: after
two failed LiveKit worker checks it marks the fallback, and on the next passing check it attaches
the number to the trunk again. Run `status` before a call.
"""

import json
import os
import sys
from pathlib import Path

import httpx

from scripts.phone import twilio
from scripts.phone.setup import NAME, e164

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "deploy" / "phone" / "routing-before.json"
RETELL = "https://api.retellai.com"
RETELL_SIP = "sip:sip.retellai.com;transport=tcp"
RETELL_AGENT = "agent_5c00ceef6a443a4ffa7f7cbeed"
# The native Retell entry (voice/retell_native), found by name since push.py may recreate it.
RETELL_NATIVE_NAME = "Summit Air (Retell native)"
VAPI_INBOUND = "https://api.vapi.ai/twilio/inbound_call"
# The Deepgram entry's own hostname (deploy/deepgram_agent/Caddyfile).
DEEPGRAM_TWIML = "https://deepgram.45-77-105-247.sslip.io/deepgram/twiml"
NUMBER_FIELDS = ("trunk_sid", "voice_url", "voice_method", "voice_application_sid",
                 "voice_fallback_url")
ENTRIES = ("livekit", "retell", "retell_native", "deepgram", "vapi", "gpt_live")


def bridge_url() -> str:
    return os.environ.get("RETELL_BRIDGE_URL", "https://45-77-105-247.sslip.io").rstrip("/")


def trunks(http) -> list[dict]:
    return http.get(f"{twilio.TRUNKING}/Trunks").raise_for_status().json()["trunks"]


def origination(http, trunk_sid: str) -> list[str]:
    urls = http.get(f"{twilio.TRUNKING}/Trunks/{trunk_sid}/OriginationUrls").raise_for_status()
    return [u["sip_url"] for u in urls.json()["origination_urls"]]


def snapshot(http, line: dict) -> dict:
    """The number and its trunk as they are, written once, before the first change."""
    if SNAPSHOT.exists():
        return json.loads(SNAPSHOT.read_text())
    trunk = next(t for t in trunks(http) if t["sid"] == line["trunk_sid"])
    saved = {
        "number": {"phone_number": line["phone_number"], "sid": line["sid"],
                   **{k: line[k] for k in NUMBER_FIELDS}},
        "trunk": {"sid": trunk["sid"], "friendly_name": trunk["friendly_name"],
                  "domain_name": trunk["domain_name"],
                  "origination": origination(http, trunk["sid"])},
    }
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(saved, indent=1) + "\n")
    print(f"saved the current setup to {SNAPSHOT.relative_to(ROOT)}")
    return saved


def own_trunk(http, name: str, sip_url: str, credentials_from: str) -> dict:
    """A trunk with one origination URL and the LiveKit trunk's termination credentials, made
    only when missing."""
    trunk = next((t for t in trunks(http) if t["friendly_name"] == name), None)
    if trunk is None:
        domain = f"{name}-{twilio.account()[-6:].lower()}.pstn.twilio.com"
        trunk = http.post(f"{twilio.TRUNKING}/Trunks",
                          data={"FriendlyName": name, "DomainName": domain}).raise_for_status().json()
        print(f"created trunk {name} ({trunk['sid']})")
    sid = trunk["sid"]
    if origination(http, sid) != [sip_url]:
        for old in http.get(f"{twilio.TRUNKING}/Trunks/{sid}/OriginationUrls").json()["origination_urls"]:
            http.delete(f"{twilio.TRUNKING}/Trunks/{sid}/OriginationUrls/{old['sid']}")
        http.post(f"{twilio.TRUNKING}/Trunks/{sid}/OriginationUrls",
                  data={"FriendlyName": name, "SipUrl": sip_url, "Priority": 1, "Weight": 1,
                        "Enabled": True}).raise_for_status()
        print(f"{name}: origination {sip_url}")
    lists = http.get(f"{twilio.TRUNKING}/Trunks/{credentials_from}/CredentialLists").json()
    attached = {c["sid"] for c in http.get(
        f"{twilio.TRUNKING}/Trunks/{sid}/CredentialLists").json()["credential_lists"]}
    for creds in lists["credential_lists"]:
        if creds["sid"] not in attached:
            http.post(f"{twilio.TRUNKING}/Trunks/{sid}/CredentialLists",
                      data={"CredentialListSid": creds["sid"]}).raise_for_status()
    return trunk


def native_agent(retell: httpx.Client) -> str:
    listed = retell.post("/v2/list-agents", json={}).raise_for_status().json()
    items = listed.get("items", []) if isinstance(listed, dict) else listed
    found = [a["agent_id"] for a in items if a.get("agent_name") == RETELL_NATIVE_NAME]
    if len(set(found)) != 1:
        raise SystemExit(f"retell_native: want one agent named {RETELL_NATIVE_NAME!r}, found {found}")
    return found[0]


def retell_import(number: str, trunk: dict, native: bool = False) -> None:
    """The number in Retell, on the baseline or the native voice agent, ringing its latest
    version."""
    headers = {"Authorization": f"Bearer {os.environ['RETELL_API_KEY']}"}
    with httpx.Client(base_url=RETELL, headers=headers, timeout=20) as retell:
        listed = retell.get("/v2/list-phone-numbers").raise_for_status().json()
        items = listed.get("items", []) if isinstance(listed, dict) else listed
        agent = native_agent(retell) if native else RETELL_AGENT
        agents = [{"agent_id": agent, "agent_version": "latest", "weight": 1}]
        webhook = f"{bridge_url()}/retell/inbound"
        if any(n["phone_number"] == number for n in items):
            retell.patch(f"/update-phone-number/{number}",
                         json={"inbound_agents": agents, "inbound_webhook_url": webhook}
                         ).raise_for_status()
            print(f"Retell answers {number} with {agent}")
            return
        retell.post("/import-phone-number", json={
            "phone_number": number,
            "termination_uri": trunk["domain_name"],
            "sip_trunk_auth_username": os.environ["TWILIO_SIP_USERNAME"],
            "sip_trunk_auth_password": os.environ["TWILIO_SIP_PASSWORD"],
            "inbound_agents": agents,
            "inbound_webhook_url": webhook,
            "nickname": "Summit Air on the 646 number (live-call slate)",
        }).raise_for_status()
        print(f"imported {number} into Retell on {agent}")


def target(http, entry: str, saved: dict, number: str) -> dict:
    """The number fields for an entry."""
    off_trunk = {"trunk_sid": None, "voice_method": "POST", "voice_application_sid": None,
                 "voice_fallback_url": None}
    if entry == "livekit":
        return {k: saved["number"][k] for k in NUMBER_FIELDS}
    if entry in ("retell", "retell_native"):
        trunk = own_trunk(http, f"{NAME}-retell", RETELL_SIP, saved["trunk"]["sid"])
        retell_import(number, trunk, native=entry == "retell_native")
        return {**off_trunk, "trunk_sid": trunk["sid"], "voice_url": None}
    if entry == "deepgram":
        return {**off_trunk, "voice_url": os.environ.get("DEEPGRAM_TWIML_URL", DEEPGRAM_TWIML)}
    if entry == "vapi":
        if not os.environ.get("VAPI_API_KEY"):
            raise SystemExit("vapi: no VAPI_API_KEY yet, so the number isn't imported into Vapi")
        return {**off_trunk, "voice_url": VAPI_INBOUND}
    if entry == "gpt_live":
        project = os.environ.get("OPENAI_PROJECT_ID")
        if not project:
            raise SystemExit("gpt_live: set OPENAI_PROJECT_ID (OpenAI's SIP address is per project)")
        sip = f"sip:{project}@sip.api.openai.com;transport=tls"
        trunk = own_trunk(http, f"{NAME}-openai", sip, saved["trunk"]["sid"])
        return {**off_trunk, "trunk_sid": trunk["sid"], "voice_url": None}
    raise SystemExit(f"unknown entry {entry!r}; one of {', '.join(ENTRIES)}, or status")


def where(http, line: dict, saved: dict) -> str:
    if line["trunk_sid"] == saved["trunk"]["sid"]:
        return "livekit"
    names = {t["sid"]: t["friendly_name"] for t in trunks(http)}
    if line["trunk_sid"]:
        return {f"{NAME}-retell": "retell", f"{NAME}-openai": "gpt_live"}.get(
            names.get(line["trunk_sid"]), f"trunk {line['trunk_sid']}")
    url = line["voice_url"] or ""
    if url.endswith("/deepgram/twiml"):
        return "deepgram"
    return "vapi" if url == VAPI_INBOUND else f"voice URL {url or '(none)'}"


def main(entry: str) -> None:
    number = e164(os.environ["TWILIO_PHONE_NUMBER"])
    with twilio.client() as http:
        line = twilio.number(http, number)
        saved = snapshot(http, line)
        if entry == "status":
            print(f"{number} -> {where(http, line, saved)}")
            return
        want = target(http, entry, saved, number)
        # Twilio returns "" for a field it holds empty.
        changed = {k: (line[k] or None, v) for k, v in want.items() if (line[k] or None) != v}
        if changed:
            fields = {"TrunkSid": want["trunk_sid"] or "", "VoiceUrl": want["voice_url"] or "",
                      "VoiceMethod": want["voice_method"] or "POST",
                      "VoiceApplicationSid": want["voice_application_sid"] or "",
                      "VoiceFallbackUrl": want["voice_fallback_url"] or ""}
            line = twilio.update_number(http, line["sid"], **fields)
        for key, (before, after) in changed.items():
            print(f"{key}: {before} -> {after}")
        print(f"{number} -> {where(http, line, saved)}" + ("" if changed else " (no change)"))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: python -m scripts.route_number {'|'.join(ENTRIES)}|status")
    main(sys.argv[1])
