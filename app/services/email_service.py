"""Isolates the Resend SDK -- every direct `resend.*` call in this app lives
here, nowhere else. Templates themselves are provisioned once via
scripts/provision_resend_templates.py (see app/email_templates.py for the
resulting IDs); this module only sends against already-published templates.

Not wrapped in a DB outbox/retry mechanism -- the contact form is the only
thing calling this today, and a failed send there just means the customer's
browser request fails and they can retry, same as any other form submission.
Order-confirmation/shipment-notification emails (not wired yet) would
warrant that durability; this doesn't.
"""

import resend
from fastapi import HTTPException, status

from app.config import settings

resend.api_key = settings.resend_api_key

FROM_ADDRESS = "KS <shop@sobotabags.com>"


def send_template(
    *, template_id: str, to: str, variables: dict[str, str | int], reply_to: str | None = None
) -> None:
    params = {
        "from": FROM_ADDRESS,
        "to": [to],
        "template": {"id": template_id, "variables": variables},
    }
    if reply_to:
        params["reply_to"] = reply_to
    try:
        resend.Emails.send(params)
    except resend.exceptions.ResendError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Nie udało się wysłać wiadomości e-mail: {e}")
