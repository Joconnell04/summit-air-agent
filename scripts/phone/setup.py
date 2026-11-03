"""Wire the phone number to the LiveKit worker. Safe to run again: everything is found by name and
created only when missing.

    uv run --env-file .env python -m scripts.phone.setup

1. Twilio Elastic SIP Trunk "summit-air": origination to LiveKit's SIP host, termination with a
   credential list so LiveKit can place the journey 12 calls, and the number attached to it.
2. LiveKit inbound trunk for the number, and a dispatch rule that puts each caller in their own
   room with the worker (AGENT_NAME).
3. LiveKit outbound trunk to Twilio's termination domain.

Needs, besides the Twilio and LiveKit keys: TWILIO_PHONE_NUMBER, LIVEKIT_SIP_HOST (the project's
SIP host from the LiveKit dashboard), and TWILIO_SIP_USERNAME / TWILIO_SIP_PASSWORD for the
termination credential. Prints the trunk ids; LIVEKIT_OUTBOUND_TRUNK_ID goes in the server's .env.
"""

import asyncio
import os

from livekit import api

from . import twilio

NAME = "summit-air"


def e164(raw: str) -> str:
    digits = "".join(c for c in raw if c.isdigit())
    return f"+1{digits[-10:]}"


def twilio_trunk(http, sip_host: str, username: str, password: str) -> dict:
    trunks = http.get(f"{twilio.TRUNKING}/Trunks").raise_for_status().json()["trunks"]
    trunk = next((t for t in trunks if t["friendly_name"] == NAME), None)
    if trunk is None:
        domain = f"{NAME}-{twilio.account()[-6:].lower()}.pstn.twilio.com"
        trunk = (
            http.post(
                f"{twilio.TRUNKING}/Trunks", data={"FriendlyName": NAME, "DomainName": domain}
            )
            .raise_for_status()
            .json()
        )
    sid = trunk["sid"]

    origination = f"sip:{sip_host};transport=tcp"
    urls = http.get(f"{twilio.TRUNKING}/Trunks/{sid}/OriginationUrls").json()["origination_urls"]
    for stale in (u for u in urls if u["sip_url"] != origination):
        http.delete(f"{twilio.TRUNKING}/Trunks/{sid}/OriginationUrls/{stale['sid']}")
    if not any(u["sip_url"] == origination for u in urls):
        http.post(
            f"{twilio.TRUNKING}/Trunks/{sid}/OriginationUrls",
            data={
                "FriendlyName": "livekit",
                "SipUrl": origination,
                "Priority": 1,
                "Weight": 1,
                "Enabled": True,
            },
        ).raise_for_status()

    lists_url = f"{twilio.API}/Accounts/{twilio.account()}/SIP/CredentialLists.json"
    lists = http.get(lists_url).raise_for_status().json()["credential_lists"]
    creds = next((c for c in lists if c["friendly_name"] == f"{NAME}-livekit"), None)
    if creds is None:
        creds = http.post(lists_url, data={"FriendlyName": f"{NAME}-livekit"}).json()
        http.post(
            f"{twilio.API}/Accounts/{twilio.account()}/SIP/CredentialLists/{creds['sid']}"
            "/Credentials.json",
            data={"Username": username, "Password": password},
        ).raise_for_status()
    attached = http.get(f"{twilio.TRUNKING}/Trunks/{sid}/CredentialLists").json()
    if not any(c["sid"] == creds["sid"] for c in attached["credential_lists"]):
        http.post(
            f"{twilio.TRUNKING}/Trunks/{sid}/CredentialLists",
            data={"CredentialListSid": creds["sid"]},
        ).raise_for_status()
    return trunk


async def livekit_trunks(number: str, termination: str, username: str, password: str) -> dict:
    lk = api.LiveKitAPI()
    try:
        inbound = next(
            (
                t
                for t in (await lk.sip.list_inbound_trunk(api.ListSIPInboundTrunkRequest())).items
                if t.name == NAME
            ),
            None,
        )
        if inbound is None:
            inbound = await lk.sip.create_inbound_trunk(
                api.CreateSIPInboundTrunkRequest(
                    trunk=api.SIPInboundTrunkInfo(name=NAME, numbers=[number])
                )
            )
        rules = (await lk.sip.list_dispatch_rule(api.ListSIPDispatchRuleRequest())).items
        if not any(r.name == NAME for r in rules):
            agent_name = os.environ.get("AGENT_NAME", NAME)
            await lk.sip.create_dispatch_rule(
                api.CreateSIPDispatchRuleRequest(
                    dispatch_rule=api.SIPDispatchRuleInfo(
                        name=NAME,
                        trunk_ids=[inbound.sip_trunk_id],
                        rule=api.SIPDispatchRule(
                            dispatch_rule_individual=api.SIPDispatchRuleIndividual(
                                room_prefix="call-"
                            )
                        ),
                        room_config=api.RoomConfiguration(
                            agents=[api.RoomAgentDispatch(agent_name=agent_name)]
                        ),
                    )
                )
            )
        outbound = next(
            (
                t
                for t in (await lk.sip.list_outbound_trunk(api.ListSIPOutboundTrunkRequest())).items
                if t.name == NAME
            ),
            None,
        )
        if outbound is None:
            outbound = await lk.sip.create_outbound_trunk(
                api.CreateSIPOutboundTrunkRequest(
                    trunk=api.SIPOutboundTrunkInfo(
                        name=NAME,
                        address=termination,
                        numbers=[number],
                        auth_username=username,
                        auth_password=password,
                    )
                )
            )
        return {"inbound": inbound.sip_trunk_id, "outbound": outbound.sip_trunk_id}
    finally:
        await lk.aclose()


def main() -> None:
    number = e164(os.environ["TWILIO_PHONE_NUMBER"])
    sip_host = os.environ["LIVEKIT_SIP_HOST"]
    username, password = os.environ["TWILIO_SIP_USERNAME"], os.environ["TWILIO_SIP_PASSWORD"]
    with twilio.client() as http:
        trunk = twilio_trunk(http, sip_host, username, password)
        ids = asyncio.run(livekit_trunks(number, trunk["domain_name"], username, password))
        line = twilio.number(http, number)
        if line["trunk_sid"] != trunk["sid"]:
            twilio.update_number(http, line["sid"], TrunkSid=trunk["sid"])
    print(f"Twilio trunk {trunk['sid']} ({trunk['domain_name']}), {number} attached")
    print(f"LiveKit inbound trunk {ids['inbound']}, outbound trunk {ids['outbound']}")
    print(f"Set LIVEKIT_OUTBOUND_TRUNK_ID={ids['outbound']} on the server.")


if __name__ == "__main__":
    main()
