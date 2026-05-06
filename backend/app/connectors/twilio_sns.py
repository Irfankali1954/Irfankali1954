"""SMS adapter — Twilio (default) or AWS SNS, selected by env.

In dev / CI we operate in *capture mode*: messages are appended to an
in-memory ring buffer instead of being sent so tests can assert dispatch
without touching a real provider. Set ``SMS_CAPTURE_ONLY=0`` and provide
TWILIO_* / AWS_* creds to actually send.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class SMSResult:
    ok: bool
    provider: str
    to: str
    sid: str | None
    error: str | None = None


# Capture buffer used by tests + the dev dashboard.
SENT: list[dict] = []
_MAX = 200


def _capture_only() -> bool:
    return os.environ.get("SMS_CAPTURE_ONLY", "1") != "0"


def send(*, to: str, body: str, from_: str | None = None) -> SMSResult:
    if _capture_only():
        rec = {"provider": "capture", "to": to, "body": body, "from": from_}
        SENT.append(rec)
        if len(SENT) > _MAX:
            del SENT[: len(SENT) - _MAX]
        return SMSResult(ok=True, provider="capture", to=to, sid=f"cap-{len(SENT)}")

    # Real Twilio dispatch, kept behind the import so the test runtime
    # need not have twilio installed.
    try:  # pragma: no cover
        from twilio.rest import Client  # type: ignore
        client = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
        msg = client.messages.create(
            to=to, from_=from_ or os.environ["TWILIO_FROM"], body=body,
        )
        return SMSResult(ok=True, provider="twilio", to=to, sid=msg.sid)
    except Exception as e:  # pragma: no cover
        return SMSResult(ok=False, provider="twilio", to=to, sid=None, error=str(e))
