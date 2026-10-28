from fastapi import HTTPException

from app.services import contact_service, email_service
from app import email_templates


def _payload(**overrides):
    body = {"name": "Jan Kowalski", "email": "jan@example.com", "phone": None,
             "topic": "pytanie-o-produkt", "message": "Czy torba jest dostępna w czarnym?"}
    body.update(overrides)
    return body


def test_contact_form_happy_path_sends_both_emails(client, monkeypatch):
    # send_template is sync in this codebase (direct resend.Emails.send call)
    from unittest.mock import MagicMock
    send_mock = MagicMock()
    monkeypatch.setattr(email_service, "send_template", send_mock)

    resp = client.post("/contact", json=_payload())
    assert resp.status_code == 201
    assert resp.json()["ticket_id"].startswith("WIAD-")

    assert send_mock.call_count == 2
    notify_call = send_mock.call_args_list[0]
    assert notify_call.kwargs["template_id"] == email_templates.CONTACT_FORM_NOTIFY
    assert notify_call.kwargs["to"] == contact_service.SHOP_OWNER_EMAIL
    assert notify_call.kwargs["reply_to"] == "jan@example.com"
    ack_call = send_mock.call_args_list[1]
    assert ack_call.kwargs["template_id"] == email_templates.CONTACT_FORM_ACK
    assert ack_call.kwargs["to"] == "jan@example.com"


def test_contact_form_escapes_message_html(client, monkeypatch):
    from unittest.mock import MagicMock
    send_mock = MagicMock()
    monkeypatch.setattr(email_service, "send_template", send_mock)

    resp = client.post("/contact", json=_payload(message="<script>alert(1)</script>\nline two"))
    assert resp.status_code == 201
    notify_call = send_mock.call_args_list[0]
    message_html = notify_call.kwargs["variables"]["MESSAGE_HTML"]
    assert "<script>" not in message_html
    assert "&lt;script&gt;" in message_html
    assert "<br>" in message_html


def test_contact_form_owner_notify_failure_propagates(client, monkeypatch):
    """The notification to the shop owner IS the point of the form -- unlike
    the customer ack, a failure here must surface as a real error, not be
    swallowed."""
    from unittest.mock import MagicMock
    send_mock = MagicMock(side_effect=HTTPException(502, "resend down"))
    monkeypatch.setattr(email_service, "send_template", send_mock)

    resp = client.post("/contact", json=_payload())
    assert resp.status_code == 502
    send_mock.assert_called_once()  # ack was never attempted


def test_contact_form_customer_ack_failure_is_swallowed(client, monkeypatch):
    """The ack to the customer is a courtesy -- a failure there must not
    fail a submission that already successfully reached the shop owner."""
    from unittest.mock import MagicMock
    send_mock = MagicMock(side_effect=[None, HTTPException(502, "resend down")])
    monkeypatch.setattr(email_service, "send_template", send_mock)

    resp = client.post("/contact", json=_payload())
    assert resp.status_code == 201
    assert send_mock.call_count == 2


def test_contact_form_rate_limited_after_five_per_minute(client, monkeypatch):
    from unittest.mock import MagicMock
    monkeypatch.setattr(email_service, "send_template", MagicMock())

    for _ in range(5):
        resp = client.post("/contact", json=_payload())
        assert resp.status_code == 201
    resp = client.post("/contact", json=_payload())
    assert resp.status_code == 429
