"""Sending email alerts. With no SMTP_HOST set the mailer holds every message instead of sending
it, so development and tests never send mail; held alerts still show on the owner view."""

import smtplib
from email.message import EmailMessage

from .settings import Settings


class Mailer:
    def __init__(self, settings: Settings):
        self.settings = settings

    def send(self, to: str, subject: str, body: str) -> str:
        """Returns "sent" or "held". Raises OSError or smtplib.SMTPException when sending fails."""
        s = self.settings
        if not s.smtp_host:
            return "held"
        message = EmailMessage()
        message["From"] = s.mail_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
            smtp.starttls()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(message)
        return "sent"
