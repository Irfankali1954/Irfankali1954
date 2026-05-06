"""Email adapter — SMTP in production, capture buffer in dev/CI.

Same contract as :mod:`app.connectors.twilio_sns`. Set
``EMAIL_CAPTURE_ONLY=0`` to actually send via SMTP.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class EmailResult:
    ok: bool
    to: str
    error: str | None = None


SENT: list[dict] = []
_MAX = 200


def _capture_only() -> bool:
    return os.environ.get("EMAIL_CAPTURE_ONLY", "1") != "0"


def send(*, to: str, subject: str, body: str, from_: str | None = None) -> EmailResult:
    if _capture_only():
        SENT.append({"to": to, "subject": subject, "body": body, "from": from_})
        if len(SENT) > _MAX:
            del SENT[: len(SENT) - _MAX]
        return EmailResult(ok=True, to=to)
    try:  # pragma: no cover
        import smtplib
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["From"] = from_ or os.environ.get("SMTP_FROM", "agent@epc.local")
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        host = os.environ.get("SMTP_HOST", "localhost")
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 25))) as s:
            s.send_message(msg)
        return EmailResult(ok=True, to=to)
    except Exception as e:  # pragma: no cover
        return EmailResult(ok=False, to=to, error=str(e))
