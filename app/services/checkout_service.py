"""Order lifecycle: pending -> placed/paid, or pending -> cancelled.

The order is created as status='pending'/payment_status='unpaid' *before* the
customer ever reaches Stripe's page -- not as a side effect of payment
succeeding. This matches what orders.status's enum was built for
(draft -> pending -> placed -> completed/cancelled) and avoids a real gap: if
a webhook-only design's webhook never lands (bad endpoint config, dropped
request), a successful Stripe payment would leave no order behind at all. A
pre-created pending order is always there to reconcile, and abandoned
checkouts become visible in the admin panel instead of vanishing.

Abandoned pending orders are cleaned up opportunistically (no scheduler
exists in this project): every new checkout attempt first sweeps stale
pending orders, cancelling them and refunding whatever gift card hold / stock
reservation they had. STALE_PENDING_MINUTES is kept comfortably above
stripe_service.STRIPE_SESSION_EXPIRES_MINUTES so this never cancels an order
that's still genuinely payable.
"""

import uuid
from typing import Any

from fastapi import HTTPException, status

from app.repositories import order_repository, pricing_repository, promotion_repository
from app.schemas.checkout import CheckoutSessionIn
from app.schemas.customer import CurrentCustomer
from app.services import gift_card_service, order_email_service, pricing_service
from app.services.stripe_service import retrieve_payment_method_details

LOCALE = "pl"
CURRENCY = "PLN"
STALE_PENDING_MINUTES = 45  # must stay comfortably above stripe_service.STRIPE_SESSION_EXPIRES_MINUTES


async def sweep_stale_pending_orders(conn) -> None:
    stale_ids = await order_repository.find_stale_pending_order_ids(conn, STALE_PENDING_MINUTES)
    for order_id in stale_ids:
        await cancel_pending_order(conn, order_id, "expired")


async def cancel_pending_order(conn, order_id: uuid.UUID, reason: str) -> None:
    cancelled = await order_repository.mark_order_cancelled(conn, order_id, reason)
    if not cancelled:
        return
    await gift_card_service.refund(conn, order_id, reason)
    # Restock exactly the lines that actually had stock decremented at
    # order-creation time (order_lines.stock_reserved) -- not re-derived
    # from the variant's current track_inventory/allow_backorder, which may
    # have changed since.
    reserved_lines = await order_repository.get_reserved_order_lines(conn, order_id)
    for line in reserved_lines:
        await pricing_repository.increment_variant_stock(conn, line["variant_id"], line["quantity"])


async def create_pending_order(conn, body: CheckoutSessionIn, customer: CurrentCustomer | None) -> dict:
    email = customer.email if customer else body.email
    if not email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "email is required")

    method_row = await pricing_repository.get_active_shipping_method(conn, body.shipping_method_id)
    if method_row is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown or inactive shipping method")
    if method_row["requires_pickup_point"] and body.pickup_point is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "pickup_point is required for this shipping method")
    if not method_row["requires_pickup_point"] and body.address is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "address is required for this shipping method")

    pricing = await pricing_service.price_cart(
        conn, body.items, body.shipping_method_id, body.promo_code, body.gift_card_code,
        customer, email, lock=True, strict=True,
    )
    lines = pricing["lines"]
    if not lines:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cart is empty")

    pickup_point = (
        {
            "point_name": body.pickup_point.point_name, "point_address": body.pickup_point.point_address,
            "first_name": body.pickup_point.first_name, "last_name": body.pickup_point.last_name,
            "phone": body.pickup_point.phone,
        }
        if body.pickup_point else None
    )
    method = pricing["shipping_method"]

    order_row = await order_repository.insert_order(
        conn,
        customer_id=customer.id if customer else None, email=email,
        phone=body.pickup_point.phone if body.pickup_point else (body.address.phone if body.address else None),
        subtotal_amount=pricing["subtotal"], discount_amount=pricing["discount_amount"],
        shipping_amount=pricing["shipping_amount"], tax_amount=pricing["tax_amount"],
        total_amount=pricing["total_amount"], shipping_method_id=method["id"],
        shipping_method_code=method["code"], shipping_method_name=method["name"],
        shipping_carrier_code=method["carrier_code"], pickup_point=pickup_point,
    )
    order_id = order_row["id"]

    for line in lines:
        await order_repository.insert_order_line(conn, order_id, line)

    if body.address:
        await order_repository.insert_order_address(conn, order_id, body.address)

    if pricing["promo"]:
        await promotion_repository.insert_order_promotion(
            conn, order_id, pricing["promo"]["promotion_id"], pricing["promo"]["code_id"],
            pricing["discount_amount"],
        )

    if pricing["gift_card"] and pricing["gift_card_amount"] > 0:
        await gift_card_service.redeem(conn, pricing["gift_card"]["id"], pricing["gift_card_amount"], order_id)

    return {
        "id": order_id, "number": order_row["number"], "lines": lines,
        "discount_amount": pricing["discount_amount"], "shipping_amount": pricing["shipping_amount"],
        "shipping_method_name": method["name"], "total_amount": pricing["total_amount"],
        "gift_card_amount": pricing["gift_card_amount"], "amount_due": pricing["amount_due"],
    }


async def finalize_zero_dollar_order(conn, order_id: uuid.UUID) -> None:
    """A gift card covered the whole order -- Stripe is never involved."""
    placed = await order_repository.mark_order_placed_paid(conn, order_id)
    if not placed:
        return
    await gift_card_service.record_payment_if_redeemed(conn, order_id)
    await order_email_service.queue_order_confirmation_emails(conn, order_id)


async def mark_order_paid(conn, order_id: uuid.UUID, session: dict[str, Any]) -> bool:
    """Idempotent: the UPDATE only matches a still-'pending' order, so a
    retried webhook (or the success-page fallback racing it) is a harmless
    no-op the second time. Returns True if this call is the one that
    actually marked it paid."""
    placed = await order_repository.mark_order_placed_paid(conn, order_id)
    if not placed:
        return False

    # Shipping address / pickup point were already persisted at
    # order-creation time (our own checkout page collected them) -- nothing
    # to fill in from Stripe here, it only ever handled payment.
    await gift_card_service.record_payment_if_redeemed(conn, order_id)

    payment_intent_id = session["payment_intent"]
    details = retrieve_payment_method_details(payment_intent_id)
    await order_repository.insert_payment(
        conn, order_id=order_id, provider="stripe", provider_payment_id=payment_intent_id,
        method_type=details["method_type"], amount=details["amount"],
        card_brand=details["card_brand"], card_last4=details["card_last4"],
    )
    await order_email_service.queue_order_confirmation_emails(conn, order_id)
    return True
