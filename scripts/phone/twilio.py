"""The few Twilio REST calls the phone wiring needs. Credentials come from the environment."""

import os

import httpx

API = "https://api.twilio.com/2010-04-01"
TRUNKING = "https://trunking.twilio.com/v1"


def client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    sid, token = os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]
    return httpx.Client(auth=(sid, token), timeout=20, transport=transport)


def account() -> str:
    return os.environ["TWILIO_ACCOUNT_SID"]


def number(http: httpx.Client, e164: str) -> dict:
    found = (
        http.get(
            f"{API}/Accounts/{account()}/IncomingPhoneNumbers.json", params={"PhoneNumber": e164}
        )
        .raise_for_status()
        .json()["incoming_phone_numbers"]
    )
    if not found:
        raise SystemExit(f"{e164} is not on this Twilio account")
    return found[0]


def update_number(http: httpx.Client, sid: str, **fields) -> dict:
    url = f"{API}/Accounts/{account()}/IncomingPhoneNumbers/{sid}.json"
    return http.post(url, data=fields).raise_for_status().json()
