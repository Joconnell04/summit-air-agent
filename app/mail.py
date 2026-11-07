"""Sending email alerts. With AGENTMAIL_API_KEY set, mail goes out through the AgentMail inbox
named in MAIL_FROM; otherwise through SMTP_HOST. With neither, the mailer holds every message
instead of sending it, so development and tests never send mail; held alerts still show on the
owner view."""

import smtplib
from email.message import EmailMessage

import httpx

from .settings import Settings

AGENTMAIL_URL = "https://api.agentmail.to/v0/inboxes/{inbox}/messages/send"


class Mailer:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client

    def send(self, to: str, subject: str, body: str, html: str = "") -> str:
        """Returns "sent" or "held". Raises OSError or smtplib.SMTPException when sending fails.
        `html`, when given, goes beside the plain text; mail clients show whichever they can."""
        s = self.settings
        if s.agentmail_api_key and s.mail_from:
            return self._agentmail(to, subject, body, html)
        if not s.smtp_host:
            return "held"
        message = EmailMessage()
        message["From"] = s.mail_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        if html:
            message.add_alternative(html, subtype="html")
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
            smtp.starttls()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(message)
        return "sent"

    def _agentmail(self, to: str, subject: str, body: str, html: str = "") -> str:
        client = self.client or httpx.Client(timeout=10)
        try:
            response = client.post(
                AGENTMAIL_URL.format(inbox=self.settings.mail_from),
                headers={"Authorization": f"Bearer {self.settings.agentmail_api_key}"},
                json={"to": [to], "subject": subject, "text": body, **({"html": html} if html else {})},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # The caller retries an OSError on its next run, as it does a failed SMTP send.
            raise OSError(f"AgentMail send failed: {exc}") from exc
        finally:
            if self.client is None:
                client.close()
        return "sent"
