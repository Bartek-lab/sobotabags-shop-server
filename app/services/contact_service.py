"""Contact form: sends two emails (an acknowledgement to the customer, a
notification to the shop owner) and hands back a ticket id for the
confirmation UI. Not persisted to the DB -- there's no admin screen that
would read a contact_messages table, so a row with nowhere to be seen would
just be dead data; the email *is* the record, same as how a real inbox
works."""

import html
import random

from fastapi import HTTPException

from app import email_templates
from app.services import email_service

SHOP_OWNER_EMAIL = "sobotabags@gmail.com"

TOPIC_LABELS = {
    "zamowienie-indywidualne": "Zamówienie indywidualne",
    "pytanie-o-produkt": "Pytanie o produkt",
    "zamowienie-i-dostawa": "Zamówienie i dostawa",
    "inne": "Inne",
}


def _generate_ticket_id() -> str:
    return f"WIAD-{random.randint(10000, 99999)}"


async def submit(name: str, email: str, phone: str | None, topic: str, message: str) -> str:
    ticket_id = _generate_ticket_id()

    # Customer-supplied text is escaped before going into the HTML fragment
    # -- this is the one place in the app that renders raw user input
    # straight into an email, so it can't be trusted the way server-built
    # fragments (order line HTML, etc.) can.
    message_html = html.escape(message).replace("\n", "<br>")

    # This one has to succeed -- it's the actual delivery of the message to
    # the shop owner, the entire point of the form. Let a failure here
    # propagate as a real error.
    email_service.send_template(
        template_id=email_templates.CONTACT_FORM_NOTIFY,
        to=SHOP_OWNER_EMAIL,
        reply_to=email,
        variables={
            "CUSTOMER_NAME": html.escape(name),
            "CUSTOMER_EMAIL": html.escape(email),
            "CUSTOMER_PHONE": html.escape(phone) if phone else "--",
            "TOPIC": TOPIC_LABELS.get(topic, topic),
            "MESSAGE_HTML": message_html,
        },
    )

    # This one is a courtesy, not the point of the request -- a typo'd
    # customer email or a transient Resend hiccup here shouldn't fail a
    # submission that already successfully reached the shop owner.
    try:
        email_service.send_template(
            template_id=email_templates.CONTACT_FORM_ACK,
            to=email,
            variables={"CUSTOMER_NAME": html.escape(name)},
        )
    except HTTPException:
        pass

    return ticket_id
