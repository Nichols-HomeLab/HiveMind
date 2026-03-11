"""Notification helpers"""

import logging
import smtplib
from email.message import EmailMessage

logger = logging.getLogger("hivemind.notifier")


class SMTPNotifier:
    """Send update notifications via SMTP."""

    def __init__(self, config: dict):
        self.host = config.get("host")
        self.port = int(config.get("port", 25))
        self.username = config.get("username")
        self.password = config.get("password")
        self.from_addr = config.get("from")
        self.to_addr = config.get("to")
        self.use_tls = bool(config.get("tls", False))
        self.priority = int(config.get("priority", 1))

    def send(self, subject: str, body: str):
        if not self.host or not self.to_addr or not self.from_addr:
            logger.warning("SMTP notifier not fully configured, skipping send")
            return

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.from_addr
        msg["To"] = self.to_addr
        msg["Priority"] = str(self.priority)
        msg["X-Priority"] = str(self.priority)
        msg.set_content(body)

        try:
            with smtplib.SMTP(self.host, self.port, timeout=10) as server:
                if self.use_tls:
                    server.starttls()
                if self.username and self.password:
                    server.login(self.username, self.password)
                server.send_message(msg)
        except Exception as exc:
            logger.error("Failed to send SMTP notification: %s", exc, exc_info=True)
