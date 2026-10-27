"""Builds the order-confirmation and new-order-alert outbox rows. Called
from inside mark_order_paid's transaction -- the email is queued in the
same atomic unit as "payment recorded", so a crash right after either both
happened or neither did (see email_outbox's own migration comment for the
full rationale).

This module computes every template variable itself and hands a fully
resolved payload to the outbox row -- the Edge Function that eventually
sends it doesn't know anything about orders, it just dispatches whatever
it's given.
"""

import html
import uuid

from app import email_templates
from app.repositories import order_repository

SHOP_OWNER_EMAIL = "sobotabags@gmail.com"
CURRENCY = "PLN"


def _money(amount: int) -> str:
    return f"{amount / 100:.2f}".replace(".", ",") + " zł"


def _items_html(lines: list) -> str:
    rows = []
    for line in lines:
        label = html.escape(line["product_name"])
        if line["variant_label"]:
            label += f" ({html.escape(line['variant_label'])})"
        rows.append(
            f'<tr><td style="padding:4px 0;">{label} × {line["quantity"]}</td>'
            f'<td align="right" style="padding:4px 0;">{_money(line["total_amount"])}</td></tr>'
        )
    return "".join(rows)


def _delivery_html(order, address) -> str:
    if order["pickup_point"]:
        pp = order["pickup_point"]
        return (
            f'<p style="margin:0;">Paczkomat: <strong>{html.escape(pp["point_name"])}</strong></p>'
            f'<p style="margin:0;">Odbiorca: {html.escape(pp["first_name"])} {html.escape(pp["last_name"])}</p>'
        )
    if address:
        line2 = f", {html.escape(address['line2'])}" if address["line2"] else ""
        return (
            f'<p style="margin:0;">{html.escape(address["first_name"])} {html.escape(address["last_name"])}</p>'
            f'<p style="margin:0;">{html.escape(address["line1"])}{line2}</p>'
            f'<p style="margin:0;">{html.escape(address["postal_code"])} {html.escape(address["city"])}</p>'
        )
    return ""


async def queue_order_confirmation_emails(conn, order_id: uuid.UUID) -> None:
    order = await order_repository.get_order_by_id(conn, order_id)
    lines = await order_repository.list_order_lines(conn, order_id)
    address = await order_repository.get_order_address(conn, order_id)

    customer_name = None
    if order["pickup_point"]:
        customer_name = f"{order['pickup_point']['first_name']} {order['pickup_point']['last_name']}"
    elif address:
        customer_name = f"{address['first_name']} {address['last_name']}"

    order_confirmation_variables: dict[str, str | int] = {
        "ORDER_NUMBER": order["number"],
        "ITEMS_HTML": _items_html(lines),
        "SUBTOTAL": _money(order["subtotal_amount"]),
        "SHIPPING_AMOUNT": _money(order["shipping_amount"]),
        "TOTAL": _money(order["total_amount"]),
        "DELIVERY_HTML": _delivery_html(order, address),
    }
    if order["discount_amount"]:
        order_confirmation_variables["DISCOUNT_ROW_HTML"] = (
            f'<tr><td style="padding:4px 0;">Rabat</td>'
            f'<td align="right" style="padding:4px 0;">-{_money(order["discount_amount"])}</td></tr>'
        )
    if customer_name:
        order_confirmation_variables["CUSTOMER_NAME"] = customer_name

    await order_repository.insert_outbox_email(
        conn,
        template_id=email_templates.ORDER_CONFIRMATION,
        to_email=order["email"],
        reply_to=None,
        variables=order_confirmation_variables,
    )
    await order_repository.insert_outbox_email(
        conn,
        template_id=email_templates.NEW_ORDER_ALERT,
        to_email=SHOP_OWNER_EMAIL,
        reply_to=order["email"],
        variables={
            "ORDER_NUMBER": order["number"],
            "CUSTOMER_EMAIL": order["email"],
            "TOTAL": _money(order["total_amount"]),
        },
    )
