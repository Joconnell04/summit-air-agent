"""The mailer (app/mail.py): AgentMail when its key is set, SMTP otherwise, held with neither."""

import httpx
import pytest

from app.mail import Mailer
from app.settings import Settings


def settings(**values) -> Settings:
    s = Settings()
    s.smtp_host = ""
    s.agentmail_api_key = ""
    s.mail_from = ""
    for k, v in values.items():
        setattr(s, k, v)
    return s


def test_with_nothing_set_mail_is_held():
    assert Mailer(settings()).send("a@example.com", "s", "b") == "held"


def test_with_an_agentmail_key_it_sends_from_the_inbox_in_mail_from():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["json"] = request.read().decode()
        return httpx.Response(200, json={"message_id": "x"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    s = settings(agentmail_api_key="k", mail_from="alerts@agentmail.to")
    assert Mailer(s, client).send("on-call@example.com", "Gas call", "body") == "sent"
    assert seen["url"].endswith("/v0/inboxes/alerts@agentmail.to/messages/send")
    assert seen["auth"] == "Bearer k" and '"on-call@example.com"' in seen["json"]
    assert '"html"' not in seen["json"]
    assert Mailer(s, client).send("on-call@example.com", "Gas call", "body", "<p>report</p>") == "sent"
    assert '"html":"<p>report</p>"' in seen["json"]


def test_a_failed_agentmail_send_raises_an_oserror_so_the_job_retries():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    s = settings(agentmail_api_key="k", mail_from="alerts@agentmail.to")
    with pytest.raises(OSError):
        Mailer(s, client).send("on-call@example.com", "s", "b")
