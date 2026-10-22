"""Isolates the Stripe SDK: every direct `stripe.*` call in the app lives
here, nowhere else. Also owns building Stripe's line_items from a priced
order -- that shaping is intimately tied to what Stripe's API accepts, which
is exactly this module's job, not the router's or checkout_service's.
"""

import time
from typing import Any

import stripe
from fastapi import HTTPException, status

from app.config import settings
from app.services.pricing_service import distribute_amount
from app.storage import public_url

CURRENCY = "PLN"
STRIPE_SESSION_EXPIRES_MINUTES = 40  # Stripe's own minimum is 30

stripe.api_key = settings.stripe_secret_key


def build_line_items(lines: list[dict], shipping_amount: int, shipping_method_name: str, gift_card_amount: int) -> list[dict]:
    """Stripe never sees a Coupon/discount object -- a session-level Stripe
    discount applies to ALL line items including the shipping line, which
    would silently undercharge relative to what we recorded as owed.
    Instead, discounted/gift-card-reduced amounts are baked directly into
    the per-line prices sent here, so the sum Stripe actually charges always
    exactly equals amount_due -- never approximately.

    Reduce each product line by its share of the gift card's coverage only
    when a gift card is actually partially covering the order -- the common
    no-gift-card path keeps the exact per-unit price/quantity Stripe always
    showed.
    """
    if gift_card_amount > 0:
        weights = [l["total_amount"] for l in lines] + ([shipping_amount] if shipping_amount > 0 else [])
        reductions = distribute_amount(weights, gift_card_amount)
        line_reductions = reductions[: len(lines)]
        shipping_reduction = reductions[len(lines)] if shipping_amount > 0 else 0
    else:
        line_reductions = [0] * len(lines)
        shipping_reduction = 0

    line_items = []
    for line, reduction in zip(lines, line_reductions):
        final_amount = line["total_amount"] - reduction
        if final_amount <= 0:
            continue  # fully covered by the gift card -- nothing for Stripe to charge for this line
        images = [public_url(line["image_key"])] if line["image_key"] else []
        if reduction > 0:
            # Collapsed to an exact total rather than unit_amount*quantity:
            # a discount/gift-card reduction split across multiple units
            # doesn't necessarily divide evenly, and this guarantees the sum
            # Stripe charges is exactly right rather than off by a cent.
            label = line["product_name"] + (f" x{line['quantity']}" if line["quantity"] > 1 else "")
            line_items.append({
                "price_data": {"currency": CURRENCY.lower(), "unit_amount": final_amount,
                                "product_data": {"name": label, "images": images}},
                "quantity": 1,
            })
        else:
            line_items.append({
                "price_data": {"currency": CURRENCY.lower(), "unit_amount": line["unit_price"],
                                "product_data": {"name": line["product_name"], "images": images}},
                "quantity": line["quantity"],
            })

    final_shipping = shipping_amount - shipping_reduction
    if final_shipping > 0:
        line_items.append({
            "price_data": {"currency": CURRENCY.lower(), "unit_amount": final_shipping,
                            "product_data": {"name": shipping_method_name}},
            "quantity": 1,
        })
    return line_items


def create_checkout_session(
    *, line_items: list[dict], customer_email: str | None, client_reference_id: str,
    success_url: str, cancel_url: str,
):
    try:
        return stripe.checkout.Session.create(
            mode="payment",
            line_items=line_items,
            customer_email=customer_email,
            client_reference_id=client_reference_id,
            success_url=success_url,
            cancel_url=cancel_url,
            expires_at=int(time.time()) + STRIPE_SESSION_EXPIRES_MINUTES * 60,
        )
    except stripe.StripeError as e:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Stripe error: {getattr(e, 'user_message', None) or str(e)}",
        )


def retrieve_session(session_id: str) -> dict[str, Any]:
    try:
        return stripe.checkout.Session.retrieve(session_id).to_dict()
    except stripe.StripeError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")


def retrieve_payment_method_details(payment_intent_id: str) -> dict[str, Any]:
    """Returns the bits order_repository.insert_payment needs -- isolates
    callers from the stripe SDK's own PaymentIntent/PaymentMethod object
    shape."""
    pi = stripe.PaymentIntent.retrieve(payment_intent_id, expand=["payment_method"])
    pm = pi.payment_method
    return {
        "amount": pi.amount,
        "method_type": pm.type if pm else None,
        "card_brand": pm.card.brand if pm and pm.type == "card" and pm.card else None,
        "card_last4": pm.card.last4 if pm and pm.type == "card" and pm.card else None,
    }


def verify_webhook_event(payload: bytes, sig_header: str) -> dict[str, Any]:
    try:
        return stripe.Webhook.construct_event(payload, sig_header, settings.stripe_webhook_secret)
    except (ValueError, stripe.SignatureVerificationError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid webhook signature")
